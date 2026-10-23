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


MIGRATIONS = {
    2: """
    ALTER TABLE projects ADD COLUMN output_goal TEXT NOT NULL DEFAULT '3D_RECONSTRUCTION';
    ALTER TABLE projects ADD COLUMN base_mode TEXT NOT NULL DEFAULT 'NONE';
    ALTER TABLE projects ADD COLUMN approved_source_id TEXT;
    ALTER TABLE projects ADD COLUMN style_recipe_id TEXT;
    CREATE TABLE source_revisions (
      id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
      parent_id TEXT, origin TEXT NOT NULL, rel_path TEXT NOT NULL, sha256 TEXT NOT NULL,
      width INTEGER NOT NULL, height INTEGER NOT NULL, fmt TEXT NOT NULL, bytes INTEGER NOT NULL,
      meta_json TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL, approved_at REAL);
    ALTER TABLE assets ADD COLUMN parent_id TEXT;
    ALTER TABLE assets ADD COLUMN source_revision_id TEXT;
    ALTER TABLE assets ADD COLUMN bbox_json TEXT;
    ALTER TABLE assets ADD COLUMN polygon_json TEXT;
    ALTER TABLE assets ADD COLUMN count INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE assets ADD COLUMN visibility TEXT NOT NULL DEFAULT 'partially_visible';
    ALTER TABLE assets ADD COLUMN confidence REAL NOT NULL DEFAULT 1.0;
    ALTER TABLE assets ADD COLUMN scene_zone TEXT NOT NULL DEFAULT '';
    ALTER TABLE assets ADD COLUMN notes TEXT NOT NULL DEFAULT '';
    ALTER TABLE assets ADD COLUMN status TEXT NOT NULL DEFAULT 'confirmed';
    ALTER TABLE assets ADD COLUMN goal TEXT NOT NULL DEFAULT '3D_RECONSTRUCTION';
    ALTER TABLE assets ADD COLUMN kind TEXT NOT NULL DEFAULT 'modelable';
    CREATE TABLE asset_instances (
      id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
      bbox_json TEXT NOT NULL, polygon_json TEXT, created_at REAL NOT NULL);
    CREATE TABLE inventory_history (
      id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, asset_id TEXT, action TEXT NOT NULL,
      before_json TEXT, after_json TEXT, at REAL NOT NULL);
    """,
    3: """
    ALTER TABLE assets ADD COLUMN mesh_source TEXT NOT NULL DEFAULT 'proxy';
    """,
    4: """
    CREATE TABLE style_recipes (id TEXT PRIMARY KEY, name TEXT NOT NULL, recipe_json TEXT NOT NULL,
      version INTEGER NOT NULL DEFAULT 1, favorite INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL DEFAULT 0,
      created_at REAL NOT NULL, updated_at REAL NOT NULL);
    CREATE TABLE style_recipe_history (id INTEGER PRIMARY KEY AUTOINCREMENT, recipe_id TEXT NOT NULL,
      version INTEGER NOT NULL, recipe_json TEXT NOT NULL, at REAL NOT NULL);
    CREATE TABLE style_favorites (style_id TEXT PRIMARY KEY);
    CREATE TABLE model_registry (provider TEXT NOT NULL, model_id TEXT NOT NULL, status TEXT NOT NULL,
      status_detail TEXT NOT NULL DEFAULT '', last_checked REAL, PRIMARY KEY (provider, model_id));
    CREATE TABLE provider_usage (id TEXT PRIMARY KEY, provider TEXT NOT NULL, model_id TEXT NOT NULL,
      kind TEXT NOT NULL, job_id TEXT, est_usd REAL, actual_usd REAL, units TEXT, remote_task_id TEXT,
      state TEXT NOT NULL, at REAL NOT NULL);
    CREATE TABLE estimates (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, request_hash TEXT NOT NULL,
      request_json TEXT NOT NULL, estimate_json TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL,
      used INTEGER NOT NULL DEFAULT 0);
    """,
    5: """
    CREATE TABLE artifact_reports (asset_id TEXT NOT NULL, view TEXT NOT NULL, report_json TEXT NOT NULL,
      sha256 TEXT NOT NULL, at REAL NOT NULL, PRIMARY KEY (asset_id, view));
    CREATE TABLE view_repairs (id TEXT PRIMARY KEY, asset_id TEXT NOT NULL, view TEXT NOT NULL, kind TEXT NOT NULL,
      params_json TEXT NOT NULL, metrics_json TEXT NOT NULL, original_sha TEXT NOT NULL, result_sha TEXT NOT NULL,
      rel_dir TEXT NOT NULL, created_at REAL NOT NULL, accepted_at REAL, note TEXT NOT NULL DEFAULT '');
    """,
}


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            if self._conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 0:
                self._conn.execute("INSERT INTO schema_version VALUES (1)")
            self._conn.commit()
            self._migrate()

    def _migrate(self) -> None:
        current = self._conn.execute("SELECT v FROM schema_version").fetchone()[0]
        for ver in sorted(MIGRATIONS):
            if ver <= current:
                continue
            try:
                self._conn.execute("BEGIN")
                for stmt in [x for x in MIGRATIONS[ver].split(";") if x.strip()]:
                    self._conn.execute(stmt)
                self._conn.execute("UPDATE schema_version SET v=?", (ver,))
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

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
