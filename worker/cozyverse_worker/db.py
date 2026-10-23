"""SQLite persistence (stdlib sqlite3, one connection guarded by a lock)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Optional

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS schema_version (v INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, slug TEXT NOT NULL,
  scene_mode TEXT NOT NULL DEFAULT 'DIORAMA', created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS assets (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  code TEXT NOT NULL, name TEXT NOT NULL, category TEXT NOT NULL, fixture TEXT NOT NULL,
  model_revision TEXT, mesh_status TEXT NOT NULL DEFAULT 'NOT_CREATED',
  params_json TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL,
  UNIQUE(project_id, code));
CREATE TABLE IF NOT EXISTS view_renders (
  asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  view TEXT NOT NULL, status TEXT NOT NULL, path TEXT, sha256 TEXT,
  model_revision TEXT, meta_json TEXT, qa_json TEXT, updated_at REAL NOT NULL,
  PRIMARY KEY (asset_id, view));
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, subject_id TEXT, state TEXT NOT NULL,
  progress REAL NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '',
  idempotency_key TEXT, result_json TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS export_runs (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, status TEXT NOT NULL, path TEXT,
  report_json TEXT, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS app_settings (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            if self._conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 0:
                self._conn.execute("INSERT INTO schema_version VALUES (1)")
            self._conn.commit()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> None:
        with self._lock:
            self._conn.execute(sql, tuple(params))
            self._conn.commit()

    def one(self, sql: str, params: Iterable[Any] = ()) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(sql, tuple(params)).fetchone()
        return dict(row) if row else None

    def all(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # settings helpers
    def get_setting(self, key: str) -> Optional[str]:
        row = self.one("SELECT v FROM app_settings WHERE k=?", (key,))
        return row["v"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        self.execute("INSERT INTO app_settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                     (key, value))


def now() -> float:
    return time.time()


def dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True)
