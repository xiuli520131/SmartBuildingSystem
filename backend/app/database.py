"""SQLite 访问层：线程安全（全局锁 + 每次独立连接）。

所有表按实施计划第 4 节定义：users / face_samples / permissions /
devices / access_events / alerts。初始化时自动建表并播种默认设备 D001。
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator, Sequence

from .config import settings

_db_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_no TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    recognition_threshold REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS face_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_no TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    com_port TEXT,
    baud INTEGER DEFAULT 115200,
    online INTEGER NOT NULL DEFAULT 0,
    lock_state TEXT NOT NULL DEFAULT 'LOCKED',
    last_heartbeat TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS permissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    valid_from TEXT NOT NULL,
    valid_to TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS access_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_time TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    user_id INTEGER,
    device_id INTEGER,
    recognition TEXT NOT NULL,
    confidence REAL,
    recognized_no TEXT,
    recognized_name TEXT,
    authorization TEXT NOT NULL,
    auth_reason TEXT,
    request_id TEXT,
    execution TEXT,
    raw TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_time TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    type TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'warning',
    user_id INTEGER,
    message TEXT NOT NULL,
    resolved INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_events_time ON access_events(event_time);
CREATE INDEX IF NOT EXISTS idx_events_req ON access_events(request_id);
CREATE INDEX IF NOT EXISTS idx_events_user ON access_events(user_id);
CREATE INDEX IF NOT EXISTS idx_alerts_resolved ON alerts(resolved);
CREATE INDEX IF NOT EXISTS idx_perms_user ON permissions(user_id);

CREATE TABLE IF NOT EXISTS edge_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('train','validation','unknown')),
    algorithm TEXT NOT NULL,
    data BLOB NOT NULL,
    source TEXT NOT NULL DEFAULT 'board',
    image_sha256 TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS edge_thresholds (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    algorithm TEXT NOT NULL,
    threshold REAL NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    calibrated_at TEXT NOT NULL
);
"""


def now_local() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """每次调用获取独立连接；写操作在全局锁内串行，读操作并发安全。"""
    with _db_lock:
        conn = sqlite3.connect(str(settings.db_path), check_same_thread=False)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()


def init_db() -> None:
    settings.ensure_dirs()
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        # Existing databases retain all samples and their current calibration.
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(edge_samples)")}
        if "source" not in columns:
            conn.execute("ALTER TABLE edge_samples ADD COLUMN source TEXT NOT NULL DEFAULT 'board'")
        if "image_sha256" not in columns:
            conn.execute("ALTER TABLE edge_samples ADD COLUMN image_sha256 TEXT")
        # 播种默认门禁设备
        cur = conn.execute(
            "SELECT id FROM devices WHERE device_no = ?", (settings.default_device_no,)
        )
        if cur.fetchone() is None:
            conn.execute(
                "INSERT INTO devices(device_no, name, com_port, baud) VALUES (?,?,?,?)",
                (settings.default_device_no, "RISC-V 门禁 (D001)",
                 settings.serial_port, settings.serial_baud),
            )
        conn.commit()


# ---------- 通用查询 ----------

def fetch_one(query: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(query, params).fetchone()
        return dict(row) if row else None


def fetch_all(query: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def execute(query: str, params: Sequence[Any] = ()) -> int:
    with get_conn() as conn:
        cur = conn.execute(query, params)
        conn.commit()
        return cur.lastrowid or 0
