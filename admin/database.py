# -*- coding: utf-8 -*-
"""SQLite 数据层：任务与单元的状态存储。

表结构：
  tasks   任务主表（一次建任务 = 一批关键词 x 平台）
  units   每个 (keyword, platform) 单元
"""
import datetime
import json
import os
import sqlite3
import threading

ADMIN_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(os.path.dirname(ADMIN_DIR), "admin.db")

# 单进程内使用，加锁避免并发写冲突（FastAPI 线程 + 消费者线程）
_db_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    keywords     TEXT NOT NULL,             -- 逗号分隔原始关键词列表
    platforms    TEXT NOT NULL,             -- 逗号分隔平台列表
    total_units  INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'pending',  -- pending / running / done / partial_failed
    created_at   TEXT NOT NULL,
    finished_at  TEXT
);

CREATE TABLE IF NOT EXISTS units (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id       INTEGER NOT NULL,
    seq           INTEGER NOT NULL,          -- 批次序号（对应 ai_search:chunks 里的 seq）
    keyword       TEXT NOT NULL,
    platform      TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',  -- pending/running/ok/failed/blocked/skipped
    answer        TEXT,
    error         TEXT,
    references_json TEXT,
    url           TEXT,
    queried_at    TEXT,
    worker_id     TEXT,
    updated_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_units_task ON units(task_id);
CREATE INDEX IF NOT EXISTS idx_units_status ON units(status);
"""


def now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _db_lock:
        conn = get_conn()
        try:
            conn.executescript(SCHEMA)
            conn.commit()
        finally:
            conn.close()


# ---------------- tasks ----------------

def create_task(keywords, platforms):
    """创建任务并返回 (task_id, units)。units 为 (keyword, platform) 列表。"""
    units = [(k.strip(), p) for k in keywords if k.strip() for p in platforms]
    with _db_lock:
        conn = get_conn()
        try:
            cur = conn.execute(
                "INSERT INTO tasks (keywords, platforms, total_units, status, created_at) "
                "VALUES (?, ?, ?, 'pending', ?)",
                (",".join(keywords), ",".join(platforms), len(units), now_str()),
            )
            task_id = cur.lastrowid
            conn.executemany(
                "INSERT INTO units (task_id, seq, keyword, platform, status, updated_at) "
                "VALUES (?, ?, ?, ?, 'pending', ?)",
                [(task_id, i, k, p, now_str()) for i, (k, p) in enumerate(units)],
            )
            conn.commit()
            return task_id, units
        finally:
            conn.close()


def get_task(task_id):
    with _db_lock:
        conn = get_conn()
        try:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def list_tasks(limit=50):
    with _db_lock:
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT t.*, "
                "  SUM(CASE WHEN u.status='ok' THEN 1 ELSE 0 END) AS ok_units, "
                "  SUM(CASE WHEN u.status='failed' THEN 1 ELSE 0 END) AS failed_units, "
                "  SUM(CASE WHEN u.status='blocked' THEN 1 ELSE 0 END) AS blocked_units "
                "FROM tasks t LEFT JOIN units u ON u.task_id = t.id "
                "GROUP BY t.id ORDER BY t.id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def list_units(task_id):
    with _db_lock:
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM units WHERE task_id = ? ORDER BY seq", (task_id,)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def get_unit(task_id, keyword, platform):
    with _db_lock:
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM units WHERE task_id = ? AND keyword = ? AND platform = ?",
                (task_id, keyword, platform),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def update_unit_status(unit_id, status, answer=None, error=None, refs=None,
                       url=None, queried_at=None, worker_id=None):
    with _db_lock:
        conn = get_conn()
        try:
            conn.execute(
                "UPDATE units SET status=?, answer=COALESCE(?, answer), "
                "error=COALESCE(?, error), references_json=COALESCE(?, references_json), "
                "url=COALESCE(?, url), queried_at=COALESCE(?, queried_at), "
                "worker_id=COALESCE(?, worker_id), updated_at=? WHERE id=?",
                (status, answer, error,
                 json.dumps(refs, ensure_ascii=False) if refs is not None else None,
                 url, queried_at, worker_id, now_str(), unit_id),
            )
            conn.commit()
        finally:
            conn.close()


def update_task_status(task_id, status=None, finished_at=None):
    """任务状态推进：统计 units 终态后更新。"""
    with _db_lock:
        conn = get_conn()
        try:
            if status is not None:
                conn.execute("UPDATE tasks SET status=?, finished_at=? WHERE id=?",
                             (status, finished_at, task_id))
            else:
                row = conn.execute(
                    "SELECT status, COUNT(*) AS c FROM units WHERE task_id=? "
                    "AND status IN ('ok','failed','blocked','skipped')",
                    (task_id,),
                ).fetchone()
                total = conn.execute("SELECT total_units FROM tasks WHERE id=?", (task_id,)).fetchone()[0]
                done = row["c"] if row else 0
                if done >= total:
                    fail = conn.execute(
                        "SELECT COUNT(*) AS c FROM units WHERE task_id=? AND status IN ('failed','blocked')",
                        (task_id,),
                    ).fetchone()["c"]
                    new_status = "partial_failed" if fail else "done"
                    conn.execute("UPDATE tasks SET status=?, finished_at=? WHERE id=?",
                                 (new_status, now_str(), task_id))
            conn.commit()
        finally:
            conn.close()


def reset_units_to_pending(task_id, statuses=("failed", "blocked")):
    """把指定终态的单元重置回 pending（用于重跑/重新入队）。返回重置数量。"""
    with _db_lock:
        conn = get_conn()
        try:
            placeholders = ",".join("?" * len(statuses))
            cur = conn.execute(
                "UPDATE units SET status='pending', error='', updated_at=? "
                "WHERE task_id=? AND status IN (%s)" % placeholders,
                (now_str(), task_id, *statuses),
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()
