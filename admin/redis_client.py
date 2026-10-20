# -*- coding: utf-8 -*-
"""Redis 操作封装：队列、心跳、全局判重。

与 worker.py 的契约严格对齐：
  ai_search:chunks        任务批次队列（worker 用 brpoplpush 抢）
  ai_search:results       结果消息队列（worker 推，后台消费者读）
  ai_search:worker:<id>   心跳 key（hset，TTL 120s）
  ai_search:processing:<worker_id>  worker 进行中批次（崩溃恢复用）
  ai_search:collected:<YYYYMMDD>:<platform>  全局判重集合（sadd keyword）
"""
import datetime
import json
import redis as redis_py

CHUNK_QUEUE = "ai_search:chunks"
RESULT_QUEUE = "ai_search:results"
WORKER_KEY_PREFIX = "ai_search:worker:"
PROCESSING_PREFIX = "ai_search:processing:"
COLLECTED_PREFIX = "ai_search:collected:"


def make_redis(redis_url):
    """创建 Redis 客户端：TCP keepalive + 健康检查，对齐 worker.py。"""
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
    return redis_py.Redis.from_url(redis_url, **kwargs)


def enqueue_chunks(r, chunks):
    """把一批 (keyword, platform) 单元切成 chunk 推入任务队列。

    chunk payload 与 worker._process_chunk 的解析一致：
      {task_id, seq, keywords, platforms, wait_rounds, round_secs}
    按 seq 顺序入队（rpush），worker 按 FIFO 领取。
    """
    for chunk in chunks:
        r.rpush(CHUNK_QUEUE, json.dumps(chunk, ensure_ascii=False))


def build_chunks(task_id, units, chunk_size=8, wait_rounds=60, round_secs=0.1):
    """把 (keyword, platform) 单元列表切成批次 chunk。

    对齐 worker 的语义：worker 把 chunk 里的 keywords x platforms 当笛卡尔积
    （for kw in keywords: for platform in platforms）。因此**一个 chunk 只含一个
    关键词**，platforms 为该关键词需要采集的平台子集，避免跨关键词的笛卡尔积误采。
    平台数超过 chunk_size 时按 chunk_size 切分平台子集。
    返回 chunk 列表（每个都是可直接入队的 dict）。
    """
    from collections import OrderedDict
    groups = OrderedDict()
    for kw, platform in units:
        groups.setdefault(kw, []).append(platform)

    chunks = []
    seq = 0
    for kw, plats in groups.items():
        for i in range(0, len(plats), chunk_size):
            sub = plats[i:i + chunk_size]
            chunks.append({
                "task_id": task_id,
                "seq": seq,
                "keywords": [kw],
                "platforms": sub,
                "wait_rounds": wait_rounds,
                "round_secs": round_secs,
            })
            seq += 1
    return chunks


def list_workers(r):
    """列出在线 worker（心跳未过期的）。返回 [{id, hostname, ip, status, current_chunk_seq, version, started_at, last_heartbeat}]。"""
    out = []
    try:
        keys = list(r.scan_iter(match=WORKER_KEY_PREFIX + "*", count=100))
    except Exception:
        return out
    for key in keys:
        try:
            h = r.hgetall(key)
        except Exception:
            continue
        h["id"] = key[len(WORKER_KEY_PREFIX):]
        out.append(h)
    return out


def worker_online_count(r):
    return len(list_workers(r))


def queue_sizes(r):
    """返回任务队列与结果队列的长度。"""
    try:
        chunk_len = r.llen(CHUNK_QUEUE)
        result_len = r.llen(RESULT_QUEUE)
    except Exception:
        chunk_len = result_len = -1
    return {"chunks": chunk_len, "results": result_len}


def pop_result(r, timeout=5):
    """阻塞读取一条结果消息（供消费者线程使用）。超时返回 None。"""
    try:
        raw = r.blpop(RESULT_QUEUE, timeout=timeout)
    except Exception:
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw[1])
    except Exception:
        return None


def reenqueue_unit(r, task_id, keyword, platform, wait_rounds=60, round_secs=0.1, seq=0):
    """把一个单元重新入队（重跑单个失败单元）。"""
    chunk = {
        "task_id": task_id,
        "seq": seq,
        "keywords": [keyword],
        "platforms": [platform],
        "wait_rounds": wait_rounds,
        "round_secs": round_secs,
    }
    r.rpush(CHUNK_QUEUE, json.dumps(chunk, ensure_ascii=False))


def collected_keys(r):
    """返回今天的全局判重集合（{date: {platform: [keywords]}}）。"""
    out = {}
    try:
        keys = list(r.scan_iter(match=COLLECTED_PREFIX + "*", count=100))
    except Exception:
        return out
    for key in keys:
        try:
            members = r.smembers(key)
        except Exception:
            continue
        date, platform = key[len(COLLECTED_PREFIX):].split(":", 1)
        out.setdefault(date, {})[platform] = sorted(members)
    return out
