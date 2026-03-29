"""
Shared database module for Admin Bot and Child Bots.
SQLite with WAL mode — fast, concurrent, duplicate-free.
"""

import sqlite3
import json
import logging
from pathlib import Path
from contextlib import contextmanager
from typing import Optional, Any

logger = logging.getLogger(__name__)

# All paths are absolute based on project root
_PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = _PROJECT_ROOT / "data"


def get_db_path() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / "botmanager.db"


def get_child_db_path(bot_username: str) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / f"child_{bot_username}.db"


@contextmanager
def get_conn(db_path: Optional[Path] = None):
    path = db_path or get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
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
                id           INTEGER PRIMARY KEY,
                bot_token    TEXT NOT NULL UNIQUE,
                bot_username TEXT NOT NULL UNIQUE,
                bot_name     TEXT NOT NULL,
                is_running   INTEGER NOT NULL DEFAULT 0,
                added_by     INTEGER NOT NULL,
                added_at     INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            );

            CREATE INDEX IF NOT EXISTS idx_child_bots_username ON child_bots(bot_username);
            CREATE INDEX IF NOT EXISTS idx_admins_user_id ON admins(user_id);
        """)
    logger.info("Admin DB initialized.")


def init_child_db(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with get_conn(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS bot_users (
                id          INTEGER PRIMARY KEY,
                user_id     INTEGER NOT NULL UNIQUE,
                username    TEXT,
                full_name   TEXT NOT NULL,
                is_active   INTEGER NOT NULL DEFAULT 1,
                is_blocked  INTEGER NOT NULL DEFAULT 0,
                joined_at   INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                last_seen   INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            );

            CREATE TABLE IF NOT EXISTS bot_settings (
                key     TEXT PRIMARY KEY,
                value   TEXT
            );

            CREATE TABLE IF NOT EXISTS channels (
                id           INTEGER PRIMARY KEY,
                channel_id   TEXT NOT NULL UNIQUE,
                title        TEXT,
                link         TEXT NOT NULL,
                is_mandatory INTEGER NOT NULL DEFAULT 1,
                added_at     INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            );

            CREATE TABLE IF NOT EXISTS broadcast_log (
                id       INTEGER PRIMARY KEY,
                sent_at  INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                total    INTEGER NOT NULL DEFAULT 0,
                success  INTEGER NOT NULL DEFAULT 0,
                failed   INTEGER NOT NULL DEFAULT 0,
                blocked  INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS child_admins (
                id       INTEGER PRIMARY KEY,
                user_id  INTEGER NOT NULL UNIQUE,
                username TEXT,
                full_name TEXT NOT NULL,
                added_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            );

            CREATE TABLE IF NOT EXISTS admin_requests (
                id          INTEGER PRIMARY KEY,
                user_id     INTEGER NOT NULL UNIQUE,
                username    TEXT,
                full_name   TEXT NOT NULL,
                requested_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                status      TEXT NOT NULL DEFAULT 'pending'
            );

            CREATE INDEX IF NOT EXISTS idx_bot_users_uid ON bot_users(user_id);
            CREATE INDEX IF NOT EXISTS idx_bot_users_active ON bot_users(is_active, is_blocked);
            CREATE INDEX IF NOT EXISTS idx_child_admins_uid ON child_admins(user_id);
        """)
    logger.info(f"Child DB initialized at {db_path}.")


# ─── Admin DB helpers ────────────────────────────────────────────────────────

def is_admin(user_id: int) -> bool:
    with get_conn() as conn:
        return conn.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,)).fetchone() is not None


def is_owner(user_id: int) -> bool:
    with get_conn() as conn:
        return conn.execute("SELECT 1 FROM admins WHERE user_id=? AND is_owner=1", (user_id,)).fetchone() is not None


def add_admin(user_id: int, username: Optional[str], full_name: str, is_owner_flag: bool = False):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO admins (user_id, username, full_name, is_owner) VALUES (?,?,?,?)",
            (user_id, username, full_name, 1 if is_owner_flag else 0)
        )


def remove_admin(user_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM admins WHERE user_id=? AND is_owner=0", (user_id,))
        return cur.rowcount > 0


def list_admins() -> list:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM admins ORDER BY added_at ASC").fetchall()


def get_admin_ids() -> list[int]:
    with get_conn() as conn:
        rows = conn.execute("SELECT user_id FROM admins").fetchall()
        return [r["user_id"] for r in rows]


# ─── Child Bot registry helpers ──────────────────────────────────────────────

def add_child_bot(token: str, bot_username: str, bot_name: str, added_by: int) -> bool:
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO child_bots (bot_token, bot_username, bot_name, added_by) VALUES (?,?,?,?)",
                (token, bot_username, bot_name, added_by)
            )
        return True
    except sqlite3.IntegrityError:
        return False


def remove_child_bot(bot_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM child_bots WHERE id=?", (bot_id,))
        return cur.rowcount > 0


def get_child_bot(bot_id: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM child_bots WHERE id=?", (bot_id,)).fetchone()


def get_child_bot_by_token(token: str) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM child_bots WHERE bot_token=?", (token,)).fetchone()


def list_child_bots() -> list:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM child_bots ORDER BY added_at ASC").fetchall()


def set_child_bot_running(bot_id: int, running: bool):
    with get_conn() as conn:
        conn.execute("UPDATE child_bots SET is_running=? WHERE id=?", (1 if running else 0, bot_id))


def set_all_bots_stopped():
    with get_conn() as conn:
        conn.execute("UPDATE child_bots SET is_running=0")


# ─── Child bot user helpers ──────────────────────────────────────────────────

def upsert_user(db_path: Path, user_id: int, username: Optional[str], full_name: str) -> bool:
    with get_conn(db_path) as conn:
        existing = conn.execute("SELECT id FROM bot_users WHERE user_id=?", (user_id,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE bot_users SET username=?, full_name=?, last_seen=strftime('%s','now'), is_active=1 WHERE user_id=?",
                (username, full_name, user_id)
            )
            return False
        conn.execute(
            "INSERT INTO bot_users (user_id, username, full_name) VALUES (?,?,?)",
            (user_id, username, full_name)
        )
        return True


def get_user(db_path: Path, user_id: int) -> Optional[sqlite3.Row]:
    with get_conn(db_path) as conn:
        return conn.execute("SELECT * FROM bot_users WHERE user_id=?", (user_id,)).fetchone()


def set_user_blocked(db_path: Path, user_id: int, blocked: bool):
    with get_conn(db_path) as conn:
        conn.execute("UPDATE bot_users SET is_blocked=? WHERE user_id=?", (1 if blocked else 0, user_id))


def set_user_inactive(db_path: Path, user_id: int):
    with get_conn(db_path) as conn:
        conn.execute("UPDATE bot_users SET is_active=0 WHERE user_id=?", (user_id,))


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


# ─── Settings helpers ────────────────────────────────────────────────────────

def get_setting(db_path: Path, key: str, default: Any = None) -> Any:
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT value FROM bot_settings WHERE key=?", (key,)).fetchone()
    if row:
        try:
            return json.loads(row["value"])
        except Exception:
            return row["value"]
    return default


def set_setting(db_path: Path, key: str, value: Any):
    serialized = json.dumps(value)
    with get_conn(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?,?)",
            (key, serialized)
        )


# ─── Channel helpers ─────────────────────────────────────────────────────────

def add_channel(db_path: Path, channel_id: str, title: str, link: str, is_mandatory: bool = True) -> bool:
    try:
        with get_conn(db_path) as conn:
            conn.execute(
                "INSERT INTO channels (channel_id, title, link, is_mandatory) VALUES (?,?,?,?)",
                (channel_id, title, link, 1 if is_mandatory else 0)
            )
        return True
    except sqlite3.IntegrityError:
        return False


def remove_channel(db_path: Path, channel_id: str) -> bool:
    with get_conn(db_path) as conn:
        cur = conn.execute("DELETE FROM channels WHERE channel_id=?", (channel_id,))
        return cur.rowcount > 0


def toggle_channel_mandatory(db_path: Path, channel_id: str) -> Optional[bool]:
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT is_mandatory FROM channels WHERE channel_id=?", (channel_id,)).fetchone()
        if not row:
            return None
        new_val = 0 if row["is_mandatory"] else 1
        conn.execute("UPDATE channels SET is_mandatory=? WHERE channel_id=?", (new_val, channel_id))
        return bool(new_val)


def list_channels(db_path: Path) -> list:
    with get_conn(db_path) as conn:
        return conn.execute("SELECT * FROM channels ORDER BY added_at ASC").fetchall()


def get_mandatory_channels(db_path: Path) -> list:
    with get_conn(db_path) as conn:
        return conn.execute("SELECT * FROM channels WHERE is_mandatory=1 ORDER BY added_at ASC").fetchall()


# ─── Broadcast log ───────────────────────────────────────────────────────────

def log_broadcast(db_path: Path, total: int, success: int, failed: int, blocked: int):
    with get_conn(db_path) as conn:
        conn.execute(
            "INSERT INTO broadcast_log (total, success, failed, blocked) VALUES (?,?,?,?)",
            (total, success, failed, blocked)
        )


# ─── Child bot admins ────────────────────────────────────────────────────────

def is_child_admin(db_path: Path, user_id: int) -> bool:
    # Check both global admin bot admins AND child-specific admins
    if is_admin(user_id):
        return True
    with get_conn(db_path) as conn:
        return conn.execute("SELECT 1 FROM child_admins WHERE user_id=?", (user_id,)).fetchone() is not None


def add_child_admin(db_path: Path, user_id: int, username: Optional[str], full_name: str) -> bool:
    try:
        with get_conn(db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO child_admins (user_id, username, full_name) VALUES (?,?,?)",
                (user_id, username, full_name)
            )
        return True
    except Exception:
        return False


def remove_child_admin(db_path: Path, user_id: int) -> bool:
    with get_conn(db_path) as conn:
        cur = conn.execute("DELETE FROM child_admins WHERE user_id=?", (user_id,))
        return cur.rowcount > 0


def list_child_admins(db_path: Path) -> list:
    with get_conn(db_path) as conn:
        return conn.execute("SELECT * FROM child_admins ORDER BY added_at ASC").fetchall()


# ─── Admin access requests ───────────────────────────────────────────────────

def request_admin_access(db_path: Path, user_id: int, username: Optional[str], full_name: str) -> bool:
    try:
        with get_conn(db_path) as conn:
            existing = conn.execute(
                "SELECT status FROM admin_requests WHERE user_id=?", (user_id,)
            ).fetchone()
            if existing:
                if existing["status"] == "pending":
                    return False  # already pending
                conn.execute(
                    "UPDATE admin_requests SET status='pending', requested_at=strftime('%s','now') WHERE user_id=?",
                    (user_id,)
                )
            else:
                conn.execute(
                    "INSERT INTO admin_requests (user_id, username, full_name) VALUES (?,?,?)",
                    (user_id, username, full_name)
                )
        return True
    except Exception:
        return False


def get_pending_requests(db_path: Path) -> list:
    with get_conn(db_path) as conn:
        return conn.execute(
            "SELECT * FROM admin_requests WHERE status='pending' ORDER BY requested_at ASC"
        ).fetchall()


def resolve_request(db_path: Path, user_id: int, status: str):
    with get_conn(db_path) as conn:
        conn.execute("UPDATE admin_requests SET status=? WHERE user_id=?", (status, user_id))
