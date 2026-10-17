"""SQLite-lager. En anslutning, ett lås, WAL-läge."""
import contextlib
import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from .config import CFG

_lock = threading.RLock()
_conn = None

SCHEMA = [
    # version 1
    """
    CREATE TABLE portals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        url TEXT NOT NULL,
        username TEXT NOT NULL DEFAULT '',
        password TEXT NOT NULL DEFAULT '',
        priority INTEGER NOT NULL DEFAULT 100,
        enabled INTEGER NOT NULL DEFAULT 1,
        created TEXT NOT NULL
    );
    CREATE TABLE checks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        portal_id INTEGER NOT NULL,
        tcp_ok INTEGER NOT NULL,
        connect_ms INTEGER,
        api_ok INTEGER NOT NULL DEFAULT 0,
        api_ms INTEGER,
        error_kind TEXT,
        error TEXT,
        account_status TEXT,
        exp_date INTEGER,
        max_connections INTEGER,
        active_cons INTEGER,
        formats TEXT
    );
    CREATE INDEX idx_checks_portal ON checks(portal_id, id);
    CREATE TABLE net_checks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        ok INTEGER NOT NULL,
        ms INTEGER,
        detail TEXT
    );
    CREATE TABLE categories (
        type TEXT NOT NULL,
        category_id TEXT NOT NULL,
        name TEXT NOT NULL,
        sort INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (type, category_id)
    );
    CREATE TABLE items (
        type TEXT NOT NULL,
        item_id TEXT NOT NULL,
        name TEXT NOT NULL,
        name_norm TEXT NOT NULL,
        category_id TEXT,
        icon TEXT,
        ext TEXT,
        rating TEXT,
        added INTEGER,
        num INTEGER,
        epg_id TEXT,
        PRIMARY KEY (type, item_id)
    );
    CREATE INDEX idx_items_cat ON items(type, category_id, num);
    CREATE TABLE sync_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        started TEXT NOT NULL,
        finished TEXT,
        portal_id INTEGER,
        status TEXT NOT NULL,
        trigger TEXT,
        detail TEXT
    );
    CREATE TABLE favorites (
        type TEXT NOT NULL,
        item_id TEXT NOT NULL,
        added TEXT NOT NULL,
        PRIMARY KEY (type, item_id)
    );
    CREATE TABLE groups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        sort INTEGER NOT NULL DEFAULT 100,
        created TEXT NOT NULL
    );
    CREATE TABLE group_items (
        group_id INTEGER NOT NULL,
        type TEXT NOT NULL,
        item_id TEXT NOT NULL,
        sort INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (group_id, type, item_id)
    );
    CREATE TABLE devices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        model TEXT,
        app_version TEXT,
        token_hash TEXT NOT NULL UNIQUE,
        created TEXT NOT NULL,
        last_seen TEXT,
        last_ip TEXT
    );
    CREATE TABLE pair_codes (
        code TEXT PRIMARY KEY,
        expires TEXT NOT NULL
    );
    CREATE TABLE sessions (
        id TEXT PRIMARY KEY,
        device_id INTEGER NOT NULL,
        type TEXT NOT NULL,
        item_id TEXT NOT NULL,
        item_name TEXT,
        portal_id INTEGER,
        started TEXT NOT NULL,
        last_heartbeat TEXT NOT NULL,
        ended TEXT,
        end_reason TEXT,
        stop_requested INTEGER NOT NULL DEFAULT 0,
        metrics TEXT
    );
    CREATE INDEX idx_sessions_active ON sessions(ended, last_heartbeat);
    CREATE TABLE events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        device_id INTEGER,
        session_id TEXT,
        kind TEXT NOT NULL,
        detail TEXT
    );
    CREATE INDEX idx_events_ts ON events(ts);
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    """,
]


def now():
    return datetime.now(timezone.utc)


def iso(dt=None):
    return (dt or now()).isoformat(timespec="seconds")


def ago(**kwargs):
    return iso(now() - timedelta(**kwargs))


def later(**kwargs):
    return iso(now() + timedelta(**kwargs))


def parse_iso(value):
    return datetime.fromisoformat(value) if value else None


def connect():
    global _conn
    os.makedirs(CFG["data_dir"], exist_ok=True)
    path = os.path.join(CFG["data_dir"], "tvhub.db")
    _conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA foreign_keys=OFF")
    version = _conn.execute("PRAGMA user_version").fetchone()[0]
    for index, script in enumerate(SCHEMA, start=1):
        if version < index:
            with tx():
                for statement in script.split(";"):
                    if statement.strip():
                        _conn.execute(statement)
                _conn.execute(f"PRAGMA user_version = {index}")
    return _conn


@contextlib.contextmanager
def tx():
    """Transaktion. Låset hålls hela vägen."""
    with _lock:
        _conn.execute("BEGIN")
        try:
            yield _conn
        except BaseException:
            _conn.execute("ROLLBACK")
            raise
        else:
            _conn.execute("COMMIT")


def q(sql, args=()):
    with _lock:
        return _conn.execute(sql, args).fetchall()


def q1(sql, args=()):
    with _lock:
        return _conn.execute(sql, args).fetchone()


def x(sql, args=()):
    with _lock:
        return _conn.execute(sql, args)


def rows(result):
    return [dict(row) for row in result]


def get_setting(key, default=None):
    row = q1("SELECT value FROM settings WHERE key = ?", (key,))
    return json.loads(row["value"]) if row else default


def set_setting(key, value):
    x(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )


def log_event(kind, detail=None, device_id=None, session_id=None):
    x(
        "INSERT INTO events (ts, device_id, session_id, kind, detail) VALUES (?, ?, ?, ?, ?)",
        (iso(), device_id, session_id, kind, json.dumps(detail or {}, ensure_ascii=False)),
    )
