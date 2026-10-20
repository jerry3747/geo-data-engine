# -*- coding: utf-8 -*-
"""geo-data-engine 管理后台（FastAPI + SQLite）。

功能：
  - 建任务：关键词列表 x 平台 -> 切分批次入 Redis 队列（worker 领取采集）
  - 任务/单元进度查看
  - 重跑失败单元 / 重新入队
  - 在线 worker 列表（读 Redis 心跳）
  - CSV 导出（按任务或按天）
  - 简易 Web UI

启动：
  python -m admin.main            # 或 uvicorn admin.main:app --host 0.0.0.0 --port 8843
配置：读取根目录 worker_config.py 的 REDIS_URL（与 worker 共用）。
"""
import csv
import datetime
import io
import json
import os
import sys

# 让根目录 worker_config 可导入
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from . import database as db
from . import redis_client as rc
from .consumer import ResultConsumer

app = FastAPI(title="geo-data-engine Admin", version="1.0.0")

# 平台清单：与 scripts/search.py 的 PLATFORMS 保持一致（避免依赖 scripts 包导入）
SUPPORTED_PLATFORMS = [
    "doubao", "deepseek", "kimi", "qwen", "metaso", "yuanbao", "xiaohongshu",
]
def _load_redis_url():
    try:
        import worker_config
        url = getattr(worker_config, "REDIS_URL", "")
    except Exception:
        url = os.environ.get("REDIS_URL", "")
    return url or ""


def _redis_or_none():
    url = _load_redis_url()
    return rc.make_redis(url) if url else None


REDIS_URL = _load_redis_url()
_consumer = None


@app.on_event("startup")
def _startup():
    db.init_db()
    global _consumer
    if REDIS_URL:
        _consumer = ResultConsumer(REDIS_URL)
        _consumer.start()
        print("[admin] 结果消费者已启动")
    else:
        print("[admin] 未配置 REDIS_URL，消费者未启动（仅 UI/任务查看可用）")


@app.on_event("shutdown")
def _shutdown():
    if _consumer is not None:
        _consumer.stop()


# ---------------- 请求模型 ----------------

class CreateTaskRequest(BaseModel):
    keywords: list[str] = Query(..., description="关键词列表")
    platforms: list[str] = Query(default=None, description="平台列表，默认全部")
    wait_rounds: int = 60
    round_secs: float = 0.1
    chunk_size: int = 8


# ---------------- API ----------------

@app.get("/", response_class=HTMLResponse)
def index():
    """简易管理页面。"""
    html = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "index.html")
    with open(html, "r", encoding="utf-8") as f:
        return f.read()


@app.get("/api/health")
def health():
    r = _redis_or_none()
    if r is None:
        return {"ok": True, "redis": False, "queues": None, "error": "未配置 REDIS_URL"}
    try:
        r.ping()
        redis_ok = True
    except Exception:
        redis_ok = False
    finally:
        try:
            r.close()
        except Exception:
            pass
    return {"ok": True, "redis": redis_ok, "queues": rc.queue_sizes(r) if redis_ok else None}


@app.post("/api/tasks")
def create_task(req: CreateTaskRequest):
    """建任务：切分批次入队。"""
    if not req.keywords:
        raise HTTPException(400, "keywords 不能为空")
    platforms = req.platforms or SUPPORTED_PLATFORMS
    for p in platforms:
        if p not in SUPPORTED_PLATFORMS:
            raise HTTPException(400, "未知平台: %s" % p)

    task_id, units = db.create_task(req.keywords, platforms)
    chunks = rc.build_chunks(task_id, units, chunk_size=max(1, req.chunk_size),
                             wait_rounds=req.wait_rounds, round_secs=req.round_secs)
    r = _redis_or_none()
    if r is None:
        raise HTTPException(503, "未配置 REDIS_URL，任务已建但未入队")
    try:
        rc.enqueue_chunks(r, chunks)
    finally:
        try:
            r.close()
        except Exception:
            pass
    return {"task_id": task_id, "chunks": len(chunks), "units": len(units),
            "status": "created", "message": "任务已创建并入队，等待 worker 领取"}


@app.get("/api/tasks")
def tasks(limit: int = 50):
    return db.list_tasks(limit)


@app.get("/api/tasks/{task_id}")
def task_detail(task_id: int):
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    units = db.list_units(task_id)
    return {"task": task, "units": units}


@app.post("/api/tasks/{task_id}/rerun-failed")
def rerun_failed(task_id: int, wait_rounds: int = 60, round_secs: float = 0.1):
    """把 failed/blocked 单元重置回 pending 并重新入队。"""
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    reset = db.reset_units_to_pending(task_id)
    if reset == 0:
        return {"task_id": task_id, "reset": 0, "message": "没有可重跑的失败单元"}
    # 重新入队：按 pending 单元切块
    units = db.list_units(task_id)
    pending = [(u["keyword"], u["platform"]) for u in units if u["status"] == "pending"]
    chunks = rc.build_chunks(task_id, pending, wait_rounds=wait_rounds, round_secs=round_secs)
    r = _redis_or_none()
    if r is None:
        raise HTTPException(503, "未配置 REDIS_URL，单元已重置但未入队")
    try:
        rc.enqueue_chunks(r, chunks)
    finally:
        try:
            r.close()
        except Exception:
            pass
    db.update_task_status(task_id, status="pending")
    return {"task_id": task_id, "reset": reset, "chunks": len(chunks), "message": "已重置并重新入队"}


@app.post("/api/tasks/{task_id}/units/{unit_id}/reenqueue")
def reenqueue_unit(task_id: int, unit_id: int):
    """重跑单个单元。"""
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT * FROM units WHERE id=? AND task_id=?", (unit_id, task_id)).fetchone()
        unit = dict(row) if row else None
    finally:
        conn.close()
    if not unit:
        raise HTTPException(404, "单元不存在")
    db.update_unit_status(unit["id"], "pending", error="")
    r = _redis_or_none()
    if r is None:
        raise HTTPException(503, "未配置 REDIS_URL，单元已重置但未入队")
    try:
        rc.reenqueue_unit(r, task_id, unit["keyword"], unit["platform"])
    finally:
        try:
            r.close()
        except Exception:
            pass
    return {"ok": True, "message": "已重新入队: %s/%s" % (unit["platform"], unit["keyword"])}


@app.get("/api/workers")
def workers():
    r = _redis_or_none()
    if r is None:
        return {"workers": [], "error": "未配置 REDIS_URL"}
    try:
        return {"workers": rc.list_workers(r)}
    finally:
        try:
            r.close()
        except Exception:
            pass


@app.get("/api/collected")
def collected():
    r = _redis_or_none()
    if r is None:
        return {}
    try:
        return rc.collected_keys(r)
    finally:
        try:
            r.close()
        except Exception:
            pass


@app.get("/api/export/tasks/{task_id}.csv")
def export_task_csv(task_id: int):
    """导出某任务的全部 ok 单元为 CSV（UTF-8 BOM）。"""
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    units = db.list_units(task_id)
    rows = [
        [u["platform"], u["keyword"], u["answer"] or "", u["url"] or "", u["queried_at"] or ""]
        for u in units if u["status"] == "ok" and (u["answer"] or "").strip()
    ]
    return _csv_response(rows, "task_%d.csv" % task_id)


def _csv_response(rows, filename):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["平台", "关键词", "答案", "链接", "查询时间"])
    writer.writerows(rows)
    # UTF-8 BOM
    data = buf.getvalue().encode("utf-8-sig")
    return StreamingResponse(
        io.BytesIO(data),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=%s" % filename},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("admin.main:app", host="0.0.0.0", port=8843, reload=False)
