from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    """SQLite 历史库。每个样本使用独立 sample_id，永不覆盖历史记录。"""

    def __init__(self, path: str | Path = "data/geonow.sqlite3"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.init_schema()

    def init_schema(self) -> None:
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
          run_id TEXT PRIMARY KEY,
          batch_id TEXT NOT NULL,
          source TEXT NOT NULL,
          provider TEXT NOT NULL,
          started_at TEXT NOT NULL,
          finished_at TEXT,
          mode_settings_json TEXT NOT NULL,
          status TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS samples (
          sample_id TEXT PRIMARY KEY,
          run_id TEXT NOT NULL REFERENCES runs(run_id),
          batch_id TEXT NOT NULL,
          sample_no INTEGER NOT NULL,
          question_id TEXT NOT NULL,
          question_text TEXT NOT NULL,
          provider TEXT NOT NULL,
          source TEXT NOT NULL,
          started_at TEXT NOT NULL,
          finished_at TEXT,
          status TEXT NOT NULL,
          valid INTEGER NOT NULL DEFAULT 0,
          raw_answer TEXT,
          answer_text TEXT,
          citations_json TEXT NOT NULL DEFAULT '[]',
          screenshot_path TEXT,
          mode_settings_json TEXT NOT NULL DEFAULT '{}',
          error TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_samples_batch ON samples(batch_id);
        CREATE INDEX IF NOT EXISTS idx_samples_group ON samples(source, provider, batch_id, question_id);
        """)
        self.conn.commit()

    def new_run(self, batch_id: str, source: str, provider: str, mode_settings: dict[str, Any]) -> str:
        run_id = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?, NULL, ?, ?)",
            (run_id, batch_id, source, provider, now_iso(), json.dumps(mode_settings, ensure_ascii=False), "running"),
        )
        self.conn.commit()
        return run_id

    def finish_run(self, run_id: str, status: str = "completed") -> None:
        self.conn.execute("UPDATE runs SET finished_at=?, status=? WHERE run_id=?", (now_iso(), status, run_id))
        self.conn.commit()

    def insert_sample(self, sample: dict[str, Any]) -> str:
        sample_id = sample.get("sample_id") or str(uuid.uuid4())
        fields = {
            "sample_id": sample_id,
            "run_id": sample["run_id"],
            "batch_id": sample["batch_id"],
            "sample_no": int(sample["sample_no"]),
            "question_id": sample["question_id"],
            "question_text": sample["question_text"],
            "provider": sample["provider"],
            "source": sample.get("source", "web"),
            "started_at": sample.get("started_at", now_iso()),
            "finished_at": sample.get("finished_at", now_iso()),
            "status": sample.get("status", "failed"),
            "valid": int(bool(sample.get("valid", False))),
            "raw_answer": sample.get("raw_answer"),
            "answer_text": sample.get("answer_text"),
            "citations_json": json.dumps(sample.get("citations", []), ensure_ascii=False),
            "screenshot_path": sample.get("screenshot_path"),
            "mode_settings_json": json.dumps(sample.get("mode_settings", {}), ensure_ascii=False),
            "error": sample.get("error"),
        }
        self.conn.execute("""INSERT INTO samples
          (sample_id,run_id,batch_id,sample_no,question_id,question_text,provider,source,
           started_at,finished_at,status,valid,raw_answer,answer_text,citations_json,
           screenshot_path,mode_settings_json,error)
          VALUES (:sample_id,:run_id,:batch_id,:sample_no,:question_id,:question_text,:provider,:source,
           :started_at,:finished_at,:status,:valid,:raw_answer,:answer_text,:citations_json,
           :screenshot_path,:mode_settings_json,:error)""", fields)
        self.conn.commit()
        return sample_id

    def has_identity(self, batch_id: str, provider: str, question_id: str, sample_no: int) -> bool:
        row = self.conn.execute("""SELECT 1 FROM samples
          WHERE batch_id=? AND provider=? AND question_id=? AND sample_no=? LIMIT 1""",
          (batch_id, provider, question_id, sample_no)).fetchone()
        return row is not None

    def rows(self, where: str = "", params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        query = "SELECT * FROM samples"
        if where:
            query += " WHERE " + where
        query += " ORDER BY started_at, sample_no"
        return list(self.conn.execute(query, tuple(params)))

    def close(self) -> None:
        self.conn.close()
