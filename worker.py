#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分布式 AI 搜索爬虫 worker（零本地落盘）

每台爬虫电脑上运行本脚本：从 Redis 队列抢"关键词批次"，按 关键词 -> 平台 串行
执行现有 6 平台采集逻辑，每个 (关键词, 平台) 的结果消息推到 Redis，由 flasky
后台消费者落 SQL Server。本进程不连 SQL Server、不写任何本地数据文件。

配置：全部写在同目录（项目根目录）worker_config.py（参考 worker_config.example.py）：
  REDIS_URL        Redis 连接串（必填）
  WORKER_ID        本机 worker 标识（默认 主机名-随机后缀）
  WAIT_ROUNDS      单次采集最长等待轮数（默认 60）
  ROUND_SECS       每轮等待秒数（默认 3）
  POLL_TIMEOUT     队列空时阻塞等待秒数（默认 30）

注意：worker_config.py 含真实凭据，已被 .gitignore 忽略，请勿提交到版本库。

环境变量：PLAYWRIGHT_MCP_EXTENSION_TOKEN 沿用现有爬虫（MCP 扩展 token）。
前置：本机已登录 Chrome + Playwright MCP 扩展，pip install -r requirements.txt 已装。

用法：
  python worker.py
"""

import asyncio
import datetime
import json
import os
import socket
import threading
import sys
import time
import traceback
import uuid

# 项目根目录（worker.py 所在处，放 worker_config.py）与 scripts/（爬虫脚本）都加入导入路径
WORKER_ROOT = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(WORKER_ROOT, "scripts")
sys.path.insert(0, WORKER_ROOT)
sys.path.insert(0, SCRIPTS_DIR)

import base
from search import run_batch_async

WORKER_VERSION = "1.0.0"
CHUNK_QUEUE = "ai_search:chunks"
RESULT_QUEUE = "ai_search:results"
WORKER_KEY_PREFIX = "ai_search:worker:"
COLLECTED_PREFIX = "ai_search:collected:"
HEARTBEAT_TTL = 120   # 心跳 key 存活时间（秒），留足余量
HB_INTERVAL = 15      # 心跳线程刷新间隔（秒）


def load_config():
    """全部配置从同目录 worker_config.py 读取（参考 worker_config.example.py）。"""
    try:
        import worker_config
    except Exception as e:
        print("错误：找不到或无法加载 worker_config.py（%s）。请把 worker_config.example.py 复制为 worker_config.py 并填写配置。" % e)
        sys.exit(2)

    def cfg(name, default=None):
        return getattr(worker_config, name, default)

    redis_url = cfg("REDIS_URL", "")
    if not redis_url:
        print("错误：worker_config.py 未配置 REDIS_URL（Redis 连接串）。")
        sys.exit(2)

    worker_id = cfg("WORKER_ID", "") or ""
    if not worker_id:
        worker_id = "%s-%s" % (socket.gethostname(), uuid.uuid4().hex[:6])

    wait_rounds = int(cfg("WAIT_ROUNDS", 60) or 60)
    round_secs = int(cfg("ROUND_SECS", 3) or 3)
    poll_timeout = int(cfg("POLL_TIMEOUT", 30) or 30)

    return {
        "redis_url": redis_url,
        "worker_id": worker_id,
        "wait_rounds": wait_rounds,
        "round_secs": round_secs,
        "poll_timeout": poll_timeout,
    }


class Worker:
    def __init__(self, redis_url, worker_id, wait_rounds=60, round_secs=3, poll_timeout=30):
        import redis as redis_py
        self.redis_url = redis_url
        self.worker_id = worker_id
        self.wait_rounds = wait_rounds
        self.round_secs = round_secs
        self.poll_timeout = poll_timeout
        self.r = self._make_redis()
        self.processing_key = "ai_search:processing:%s" % worker_id
        self.worker_key = WORKER_KEY_PREFIX + worker_id
        self.running = True
        # 独立心跳线程：不依赖主循环，长批次/异常重试期间也保持在线
        self._hb_status = "idle"
        self._hb_stop = threading.Event()
        threading.Thread(target=self._hb_loop, name="worker-hb", daemon=True).start()
        # 分布式模式：全局关闭本地落盘（json/md/png 一律不写）
        base.ENABLE_LOCAL_SAVE = False

    def _make_redis(self):
        """创建 Redis 客户端：开启 TCP keepalive 与健康检查，避免长闲置连接被服务端/代理断开。"""
        import redis as redis_py
        import socket as socket_mod
        ka_opts = {}
        for name, val in (("TCP_KEEPIDLE", 30), ("TCP_KEEPINTVL", 10), ("TCP_KEEPCNT", 3)):
            if hasattr(socket_mod, name):
                ka_opts[getattr(socket_mod, name)] = val
        kwargs = {
            "decode_responses": True,
            "socket_connect_timeout": 10,
            "socket_keepalive": True,
            "health_check_interval": 30,
        }
        if ka_opts:
            kwargs["socket_keepalive_options"] = ka_opts
        return redis_py.Redis.from_url(self.redis_url, **kwargs)

    # ---------- 心跳 ----------
    def heartbeat(self, status="idle", chunk_seq=None):
        self._hb_status = status
        try:
            self._write_heartbeat(status, chunk_seq)
        except Exception as e:
            print("[worker %s] 心跳失败：%s" % (self.worker_id, str(e)[:120]))

    def _write_heartbeat(self, status, chunk_seq=None):
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.r.hset(self.worker_key, mapping={
            "hostname": socket.gethostname(),
            "ip": self._local_ip(),
            "status": status,
            "current_chunk_seq": str(chunk_seq or ""),
            "version": WORKER_VERSION,
            "started_at": getattr(self, "_started_at", now),
            "last_heartbeat": now,
        })
        self.r.expire(self.worker_key, HEARTBEAT_TTL)

    def _hb_loop(self):
        while not self._hb_stop.wait(HB_INTERVAL):
            try:
                self._write_heartbeat(self._hb_status)
            except Exception as e:
                print("[worker %s] 心跳线程失败：%s" % (self.worker_id, str(e)[:120]))

    @staticmethod
    def _local_ip():
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "unknown"

    # ---------- 全局同日判重 ----------
    @staticmethod
    def _dedup_key(date_str, platform):
        return "%s%s:%s" % (COLLECTED_PREFIX, date_str.replace("-", ""), platform)

    def dedup_fn(self, date_str, platform, keyword):
        try:
            return bool(self.r.sismember(self._dedup_key(date_str, platform), keyword))
        except Exception as e:
            print("[worker %s] 判重查询失败（按未采集处理）：%s" % (self.worker_id, str(e)[:120]))
            return False

    # ---------- 结果上报 ----------
    def report_unit(self, result, task_id):
        platform = result.get("platform")
        keyword = result.get("keyword")
        status = result.get("status")
        date_str = datetime.date.today().isoformat()
        try:
            if status == "ok":
                # 成功即写全局判重集，跨机器/跨任务当天不再重复采集
                self.r.sadd(self._dedup_key(date_str, platform), keyword)
            msg = {
                "task_id": task_id,
                "platform": platform,
                "keyword": keyword,
                "status": status,
                "error": result.get("error") or "",
                "answer": result.get("answer") or "",
                "references": result.get("references") or [],
                "url": result.get("url") or "",
                "queried_at": result.get("queried_at") or datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "worker_id": self.worker_id,
            }
            self.r.rpush(RESULT_QUEUE, json.dumps(msg, ensure_ascii=False))
            print("[worker %s] 上报：task=%s %s/%s -> %s" % (self.worker_id, task_id, platform, keyword, status))
        except Exception as e:
            print("[worker %s] 结果上报失败：%s" % (self.worker_id, str(e)[:120]))

    # ---------- 批次处理 ----------
    def _fail_remaining(self, task_id, keywords, platforms, error):
        """批次整体失败时，把未成功单元全部标记为 failed（消费者不会降级已 ok 的单元）。"""
        for kw in keywords:
            for platform in platforms:
                self.report_unit({
                    "platform": platform,
                    "keyword": kw,
                    "status": "failed",
                    "error": error,
                    "answer": "",
                    "references": [],
                    "url": "",
                    "queried_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }, task_id)

    def _process_chunk(self, payload):
        task_id = payload.get("task_id")
        seq = payload.get("seq")
        keywords = payload.get("keywords") or []
        platforms = payload.get("platforms") or []
        wait_rounds = int(payload.get("wait_rounds") or self.wait_rounds)
        round_secs = int(payload.get("round_secs") or self.round_secs)
        print("\n[worker %s] 领取 task=%s seq=%s：%d 个关键词 x %d 个平台（wait=%s round_secs=%s）"
              % (self.worker_id, task_id, seq, len(keywords), len(platforms), wait_rounds, round_secs))
        self.heartbeat("running", seq)
        try:
            stats = asyncio.run(run_batch_async(
                keywords, platforms, wait_rounds, round_secs, False,
                dedup_fn=self.dedup_fn,
                on_unit_done=lambda result: self.report_unit(result, task_id),
            ))
            if stats is None:
                self._fail_remaining(task_id, keywords, platforms,
                                     "Playwright MCP 连接失败（请确认 Chrome 已启动且安装了 Playwright MCP 扩展）")
        except Exception as e:
            traceback.print_exc()
            self._fail_remaining(task_id, keywords, platforms, "%s: %s" % (type(e).__name__, str(e)))

    # ---------- 崩溃恢复：先处理自己的遗留批次 ----------
    def recover_processing(self):
        try:
            items = self.r.lrange(self.processing_key, 0, -1)
        except Exception as e:
            print("[worker %s] 读取遗留批次失败：%s" % (self.worker_id, str(e)[:120]))
            return
        for chunk in items:
            try:
                payload = json.loads(chunk)
            except Exception:
                self.r.lrem(self.processing_key, 0, chunk)
                continue
            print("[worker %s] 恢复处理遗留批次 task=%s seq=%s" % (self.worker_id, payload.get("task_id"), payload.get("seq")))
            try:
                self._process_chunk(payload)
            finally:
                self.r.lrem(self.processing_key, 0, chunk)

    # ---------- 主循环 ----------
    def run(self):
        self._started_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print("[worker %s] 启动（Redis=%s，落盘已关闭）" % (self.worker_id, self.redis_url.split("@")[-1]))
        # 启动自检：必须能连上 Redis 才继续，否则立刻报错退出，
        # 避免"进程活着但心跳/任务全失败、前端显示离线"的假运行状态。
        try:
            self.r.ping()
            try:
                ver = (self.r.info("server") or {}).get("redis_version", "unknown")
            except Exception:
                ver = "unknown"
            print("[worker %s] Redis 连接成功（版本 %s）" % (self.worker_id, ver))
        except Exception as e:
            print("[worker %s] 启动失败：无法连接 Redis（%s）。"
                  "请确认 worker_config.py 的 REDIS_URL 与 flasky 配置一致、网络可达、密码正确。" % (self.worker_id, str(e)[:160]))
            sys.exit(2)
        self.recover_processing()
        self.heartbeat("idle")
        while self.running:
            try:
                chunk = self.r.brpoplpush(CHUNK_QUEUE, self.processing_key, timeout=self.poll_timeout)
                if chunk is None:
                    self.heartbeat("idle")
                    continue
                try:
                    payload = json.loads(chunk)
                except Exception:
                    print("[worker %s] 丢弃非法批次数据" % self.worker_id)
                    self.r.lrem(self.processing_key, 0, chunk)
                    continue
                try:
                    self._process_chunk(payload)
                finally:
                    self.r.lrem(self.processing_key, 0, chunk)
                self.heartbeat("idle")
            except KeyboardInterrupt:
                self.running = False
                print("\n[worker %s] 收到退出信号，停止领取新批次" % self.worker_id)
                break
            except Exception as e:
                print("[worker %s] 主循环异常：%s" % (self.worker_id, str(e)[:150]))
                # 长时间闲置后 Redis 连接可能被服务端/代理断开：重建客户端自愈，无需重启
                try:
                    self.r.close()
                except Exception:
                    pass
                try:
                    self.r = self._make_redis()
                    print("[worker %s] 已重建 Redis 连接" % self.worker_id)
                except Exception as e2:
                    print("[worker %s] 重建 Redis 连接失败：%s" % (self.worker_id, str(e2)[:120]))
                time.sleep(5)
        self._hb_stop.set()
        try:
            self.heartbeat("offline")
        except Exception:
            pass


def main():
    cfg = load_config()
    worker = Worker(
        redis_url=cfg["redis_url"],
        worker_id=cfg["worker_id"],
        wait_rounds=cfg["wait_rounds"],
        round_secs=cfg["round_secs"],
        poll_timeout=cfg["poll_timeout"],
    )
    worker.run()


if __name__ == "__main__":
    main()
