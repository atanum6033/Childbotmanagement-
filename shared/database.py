"""
Shared database module for Admin Bot and Child Bots.
Uses SQLite with WAL mode for high-speed, concurrent access.
Designed for lightweight, duplicate-free storage.
"""

import sqlite3
import json
import time
import logging
from pathlib import Path
from contextlib import contextmanager
from typing import Optional, Any

logger = logging.getLogger(__name__)

DB_PATH = Path("data/botmanager.db")


def get_db_path() -> Path:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return DB_PATH


@contextmanager
def get_conn(db_path: Optional[Path] = None):
    path = db_path or get_db_path()
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA cache_size=-64000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_admin_db():
    """Initialize all tables for the Admin Bot."""
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS admins (
                id          INTEGER PRIMARY KEY,
                user_id     INTEGER NOT NULL UNIQUE,
                username    TEXT,
                full_name   TEXT NOT NULL,
                is_owner    INTEGER NOT NULL DEFAULT 0,
                added_at    INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            );

            CREATE TABLE IF NOT EXISTS child_bots (
                id          INTEGER PRIMARY KEY,
                bot_token   TEXT NOT NULL UNIQUE,
                bot_username TEXT NOT NULL UNIQUE,
                bot_name    TEXT NOT NULL,
                is_running  INTEGER NOT NULL DEFAULT 0,
                added_by    INTEGER NOT NULL,
                added_at    INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                FOREIGN KEY (added_by) REFERENCES admins(user_id)
            );

            CREATE INDEX IF NOT EXISTS idx_child_bots_username ON child_bots(bot_username);
            CREATE INDEX IF NOT EXISTS idx_admins_user_id ON admins(user_id);
        """)
    logger.info("Admin DB initialized.")


def init_child_db(db_path: Path):
    """Initialize tables for a specific Child Bot."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with get_conn(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS bot_users (
                id              INTEGER PRIMARY KEY,
                user_id         INTEGER NOT NULL UNIQUE,
                username        TEXT,
                full_name       TEXT NOT NULL,
                is_active       INTEGER NOT NULL DEFAULT 1,
                is_blocked      INTEGER NOT NULL DEFAULT 0,
                joined_at       INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                last_seen       INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            );

            CREATE TABLE IF NOT EXISTS bot_settings (
                key     TEXT PRIMARY KEY,
                value   TEXT
            );

            CREATE TABLE IF NOT EXISTS channels (
                id          INTEGER PRIMARY KEY,
                channel_id  TEXT NOT NULL UNIQUE,
                title       TEXT,
                link        TEXT NOT NULL,
                added_at    INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            );

            CREATE TABLE IF NOT EXISTS broadcast_log (
                id          INTEGER PRIMARY KEY,
                sent_at     INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                total       INTEGER NOT NULL DEFAULT 0,
                success     INTEGER NOT NULL DEFAULT 0,
                failed      INTEGER NOT NULL DEFAULT 0,
                blocked     INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_bot_users_user_id ON bot_users(user_id);
            CREATE INDEX IF NOT EXISTS idx_bot_users_active ON bot_users(is_active, is_blocked);
        """)
    logger.info(f"Child DB initialized at {db_path}.")


# ─── Admin DB helpers ───────────────────────────────────────────────────────

def get_admin(user_id: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM admins WHERE user_id = ?", (user_id,)).fetchone()


def is_admin(user_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,)).fetchone()
        return row is not None


def is_owner(user_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT 1 FROM admins WHERE user_id = ? AND is_owner = 1", (user_id,)).fetchone()
        return row is not None


def add_admin(user_id: int, username: Optional[str], full_name: str, is_owner_flag: bool = False):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO admins (user_id, username, full_name, is_owner) VALUES (?, ?, ?, ?)",
            (user_id, username, full_name, 1 if is_owner_flag else 0)
        )


def remove_admin(user_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM admins WHERE user_id = ? AND is_owner = 0", (user_id,))
        return cur.rowcount > 0


def list_admins() -> list:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM admins ORDER BY added_at ASC").fetchall()


# ─── Child Bot DB helpers ────────────────────────────────────────────────────

def add_child_bot(token: str, bot_username: str, bot_name: str, added_by: int) -> bool:
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO child_bots (bot_token, bot_username, bot_name, added_by) VALUES (?, ?, ?, ?)",
                (token, bot_username, bot_name, added_by)
            )
        return True
    except sqlite3.IntegrityError:
        return False


def remove_child_bot(bot_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM child_bots WHERE id = ?", (bot_id,))
        return cur.rowcount > 0


def get_child_bot(bot_id: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM child_bots WHERE id = ?", (bot_id,)).fetchone()


def get_child_bot_by_token(token: str) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM child_bots WHERE bot_token = ?", (token,)).fetchone()


def list_child_bots() -> list:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM child_bots ORDER BY added_at ASC").fetchall()


def count_child_bots() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM child_bots").fetchone()[0]


def set_child_bot_running(bot_id: int, running: bool):
    with get_conn() as conn:
        conn.execute("UPDATE child_bots SET is_running = ? WHERE id = ?", (1 if running else 0, bot_id))


# ─── Child Bot user helpers ──────────────────────────────────────────────────

def get_child_db_path(bot_username: str) -> Path:
    return Path(f"data/child_{bot_username}.db")


def upsert_user(db_path: Path, user_id: int, username: Optional[str], full_name: str) -> bool:
    """Insert or update user. Returns True if new user."""
    with get_conn(db_path) as conn:
        existing = conn.execute("SELECT id, is_active FROM bot_users WHERE user_id = ?", (user_id,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE bot_users SET username=?, full_name=?, last_seen=strftime('%s','now'), is_active=1 WHERE user_id=?",
                (username, full_name, user_id)
            )
            return False
        else:
            conn.execute(
                "INSERT INTO bot_users (user_id, username, full_name) VALUES (?, ?, ?)",
                (user_id, username, full_name)
            )
            return True


def get_user(db_path: Path, user_id: int) -> Optional[sqlite3.Row]:
    with get_conn(db_path) as conn:
        return conn.execute("SELECT * FROM bot_users WHERE user_id = ?", (user_id,)).fetchone()


def set_user_blocked(db_path: Path, user_id: int, blocked: bool):
    with get_conn(db_path) as conn:
        conn.execute("UPDATE bot_users SET is_blocked = ? WHERE user_id = ?", (1 if blocked else 0, user_id))


def set_user_inactive(db_path: Path, user_id: int):
    with get_conn(db_path) as conn:
        conn.execute("UPDATE bot_users SET is_active = 0 WHERE user_id = ?", (user_id,))


def count_users(db_path: Path) -> dict:
    with get_conn(db_path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM bot_users").fetchone()[0]
        active = conn.execute("SELECT COUNT(*) FROM bot_users WHERE is_active=1 AND is_blocked=0").fetchone()[0]
        inactive = conn.execute("SELECT COUNT(*) FROM bot_users WHERE is_active=0").fetchone()[0]
        blocked = conn.execute("SELECT COUNT(*) FROM bot_users WHERE is_blocked=1").fetchone()[0]
    return {"total": total, "active": active, "inactive": inactive, "blocked": blocked}


def get_active_users(db_path: Path) -> list:
    with get_conn(db_path) as conn:
        return conn.execute(
            "SELECT user_id FROM bot_users WHERE is_active=1 AND is_blocked=0"
        ).fetchall()


def get_all_users_paginated(db_path: Path, page: int = 1, per_page: int = 10) -> tuple:
    offset = (page - 1) * per_page
    with get_conn(db_path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM bot_users").fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM bot_users ORDER BY joined_at DESC LIMIT ? OFFSET ?",
            (per_page, offset)
        ).fetchall()
    return rows, total


# ─── Bot settings helpers ────────────────────────────────────────────────────

def get_setting(db_path: Path, key: str, default: Any = None) -> Any:
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT value FROM bot_settings WHERE key = ?", (key,)).fetchone()
    if row:
        try:
            return json.loads(row["value"])
        except Exception:
            return row["value"]
    return default


def set_setting(db_path: Path, key: str, value: Any):
    serialized = json.dumps(value) if not isinstance(value, str) else value
    with get_conn(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)",
            (key, serialized)
        )


# ─── Channel helpers ────────────────────────────────────────────────────────

def add_channel(db_path: Path, channel_id: str, title: str, link: str) -> bool:
    try:
        with get_conn(db_path) as conn:
            conn.execute(
                "INSERT INTO channels (channel_id, title, link) VALUES (?, ?, ?)",
                (channel_id, title, link)
            )
        return True
    except sqlite3.IntegrityError:
        return False


def remove_channel(db_path: Path, channel_id: str) -> bool:
    with get_conn(db_path) as conn:
        cur = conn.execute("DELETE FROM channels WHERE channel_id = ?", (channel_id,))
        return cur.rowcount > 0


def list_channels(db_path: Path) -> list:
    with get_conn(db_path) as conn:
        return conn.execute("SELECT * FROM channels ORDER BY added_at ASC").fetchall()


# ─── Broadcast log ───────────────────────────────────────────────────────────

def log_broadcast(db_path: Path, total: int, success: int, failed: int, blocked: int):
    with get_conn(db_path) as conn:
        conn.execute(
            "INSERT INTO broadcast_log (total, success, failed, blocked) VALUES (?, ?, ?, ?)",
            (total, success, failed, blocked)
        )
