# -*- coding: utf-8 -*-
"""后台消费者：阻塞读 ai_search:results 队列，把结果消息落 SQLite。

由 FastAPI 应用在 startup 时启动，进程退出时停止。
结果消息字段（对齐 worker.report_unit）：
  task_id, platform, keyword, status, error, answer, references, url, queried_at, worker_id
"""
import datetime
import json
import threading
import traceback

from . import database as db
from . import redis_client as rc


class ResultConsumer:
    def __init__(self, redis_url):
        self.redis_url = redis_url
        self.r = None
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="result-consumer", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)

    def _run(self):
        print("[consumer] 结果消费者启动（%s）" % self.redis_url.split("@")[-1])
        while not self._stop.is_set():
            try:
                if self.r is None:
                    self.r = rc.make_redis(self.redis_url)
                msg = rc.pop_result(self.r, timeout=2)
                if msg is None:
                    continue
                self._handle(msg)
            except Exception:
                traceback.print_exc()
                # 连接异常：重建
                try:
                    if self.r is not None:
                        self.r.close()
                except Exception:
                    pass
                self.r = None
                self._stop.wait(3)

    def _handle(self, msg):
        task_id = msg.get("task_id")
        keyword = msg.get("keyword") or ""
        platform = msg.get("platform") or ""
        status = msg.get("status") or "failed"
        if task_id is None:
            return
        unit = db.get_unit(task_id, keyword, platform)
        if unit is None:
            # 分布式模式下可能是外部后台建的任务，这里未知 -> 忽略
            print("[consumer] 忽略未知单元 task=%s %s/%s" % (task_id, platform, keyword))
            return
        db.update_unit_status(
            unit_id=unit["id"],
            status=status,
            answer=msg.get("answer"),
            error=msg.get("error"),
            refs=msg.get("references") or [],
            url=msg.get("url"),
            queried_at=msg.get("queried_at"),
            worker_id=msg.get("worker_id"),
        )
        db.update_task_status(task_id)
        print("[consumer] task=%s %s/%s -> %s" % (task_id, platform, keyword, status))
