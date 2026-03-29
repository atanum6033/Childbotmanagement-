"""
MongoDB implementation — mirrors all functions in database.py exactly.
All admin_bot and child_bot code can use this drop-in replacement.
"""

import json
import time
import logging
from pathlib import Path
from typing import Optional, Any

logger = logging.getLogger(__name__)

try:
    from pymongo import MongoClient, ASCENDING
    from pymongo.errors import DuplicateKeyError
    PYMONGO_AVAILABLE = True
except ImportError:
    PYMONGO_AVAILABLE = False
    logger.warning("pymongo not installed. MongoDB features unavailable.")

_PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = _PROJECT_ROOT / "data"

_client = None
_uri = None


def _get_client():
    global _client, _uri
    from shared.db_config import get_mongo_uri
    current_uri = get_mongo_uri()
    if _client is None or _uri != current_uri:
        _uri = current_uri
        if not current_uri:
            raise RuntimeError("MongoDB URI is not configured.")
        _client = MongoClient(current_uri, serverSelectionTimeoutMS=5000)
    return _client


def _admin_db():
    return _get_client()["botmanager"]


def _child_db(db_path: Path) -> Any:
    name = db_path.stem  # e.g. "child_mybotusername"
    return _get_client()[name]


# ─── Utility — fake Row dict ─────────────────────────────────────────────────

class _Row(dict):
    """Dict that also supports attribute-style access like sqlite3.Row."""
    def __getitem__(self, key):
        return super().__getitem__(key)
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)


def _to_row(doc: Optional[dict]) -> Optional[_Row]:
    if doc is None:
        return None
    d = {k: v for k, v in doc.items() if k != "_id"}
    return _Row(d)


def _to_rows(docs) -> list:
    return [_to_row(d) for d in docs if d]


def _now() -> int:
    return int(time.time())


# ─── Init ────────────────────────────────────────────────────────────────────

def get_db_path() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / "botmanager.db"


def get_child_db_path(bot_username: str) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / f"child_{bot_username}.db"


def init_admin_db():
    adb = _admin_db()
    adb["admins"].create_index("user_id", unique=True)
    adb["child_bots"].create_index("bot_token", unique=True)
    adb["child_bots"].create_index("bot_username", unique=True)
    logger.info("MongoDB admin DB initialized.")


def init_child_db(db_path: Path):
    cdb = _child_db(db_path)
    cdb["bot_users"].create_index("user_id", unique=True)
    cdb["bot_settings"].create_index("key", unique=True)
    cdb["channels"].create_index("channel_id", unique=True)
    cdb["child_admins"].create_index("user_id", unique=True)
    cdb["admin_requests"].create_index("user_id", unique=True)
    logger.info(f"MongoDB child DB initialized: {db_path.stem}")


# ─── Admin helpers ────────────────────────────────────────────────────────────

def is_admin(user_id: int) -> bool:
    return _admin_db()["admins"].find_one({"user_id": user_id}) is not None


def is_owner(user_id: int) -> bool:
    return _admin_db()["admins"].find_one({"user_id": user_id, "is_owner": 1}) is not None


def add_admin(user_id: int, username: Optional[str], full_name: str, is_owner_flag: bool = False):
    try:
        _admin_db()["admins"].insert_one({
            "user_id": user_id,
            "username": username,
            "full_name": full_name,
            "is_owner": 1 if is_owner_flag else 0,
            "added_at": _now(),
        })
    except DuplicateKeyError:
        pass


def remove_admin(user_id: int) -> bool:
    result = _admin_db()["admins"].delete_one({"user_id": user_id, "is_owner": 0})
    return result.deleted_count > 0


def list_admins() -> list:
    return _to_rows(_admin_db()["admins"].find({}).sort("added_at", ASCENDING))


def get_admin_ids() -> list:
    return [r["user_id"] for r in _admin_db()["admins"].find({}, {"user_id": 1})]


# ─── Child bot registry ───────────────────────────────────────────────────────

def add_child_bot(token: str, bot_username: str, bot_name: str, added_by: int) -> bool:
    try:
        _admin_db()["child_bots"].insert_one({
            "bot_token": token,
            "bot_username": bot_username,
            "bot_name": bot_name,
            "is_running": 0,
            "added_by": added_by,
            "added_at": _now(),
        })
        return True
    except DuplicateKeyError:
        return False


def remove_child_bot(bot_id: int) -> bool:
    result = _admin_db()["child_bots"].delete_one({"id": bot_id})
    return result.deleted_count > 0


def get_child_bot(bot_id: int) -> Optional[_Row]:
    return _to_row(_admin_db()["child_bots"].find_one({"id": bot_id}))


def get_child_bot_by_token(token: str) -> Optional[_Row]:
    doc = _admin_db()["child_bots"].find_one({"bot_token": token})
    if doc and "id" not in doc:
        # Auto-assign integer id using the counter approach
        doc["id"] = _admin_db()["child_bots"].count_documents({"added_at": {"$lte": doc["added_at"]}})
    return _to_row(doc)


def list_child_bots() -> list:
    docs = list(_admin_db()["child_bots"].find({}).sort("added_at", ASCENDING))
    # Assign sequential IDs if missing
    for i, doc in enumerate(docs, 1):
        if "id" not in doc:
            doc["id"] = i
    return _to_rows(docs)


def set_child_bot_running(bot_id: int, running: bool):
    _admin_db()["child_bots"].update_one({"id": bot_id}, {"$set": {"is_running": 1 if running else 0}})


def set_all_bots_stopped():
    _admin_db()["child_bots"].update_many({}, {"$set": {"is_running": 0}})


# ─── Child bot user helpers ───────────────────────────────────────────────────

def upsert_user(db_path: Path, user_id: int, username: Optional[str], full_name: str) -> bool:
    cdb = _child_db(db_path)
    existing = cdb["bot_users"].find_one({"user_id": user_id})
    if existing:
        cdb["bot_users"].update_one(
            {"user_id": user_id},
            {"$set": {"username": username, "full_name": full_name, "last_seen": _now(), "is_active": 1}},
        )
        return False
    cdb["bot_users"].insert_one({
        "user_id": user_id,
        "username": username,
        "full_name": full_name,
        "is_active": 1,
        "is_blocked": 0,
        "joined_at": _now(),
        "last_seen": _now(),
    })
    return True


def get_user(db_path: Path, user_id: int) -> Optional[_Row]:
    return _to_row(_child_db(db_path)["bot_users"].find_one({"user_id": user_id}))


def set_user_blocked(db_path: Path, user_id: int, blocked: bool):
    _child_db(db_path)["bot_users"].update_one(
        {"user_id": user_id}, {"$set": {"is_blocked": 1 if blocked else 0}}
    )


def set_user_inactive(db_path: Path, user_id: int):
    _child_db(db_path)["bot_users"].update_one(
        {"user_id": user_id}, {"$set": {"is_active": 0}}
    )


def count_users(db_path: Path) -> dict:
    coll = _child_db(db_path)["bot_users"]
    total = coll.count_documents({})
    active = coll.count_documents({"is_active": 1, "is_blocked": 0})
    inactive = coll.count_documents({"is_active": 0})
    blocked = coll.count_documents({"is_blocked": 1})
    return {"total": total, "active": active, "inactive": inactive, "blocked": blocked}


def get_active_users(db_path: Path) -> list:
    return _to_rows(_child_db(db_path)["bot_users"].find(
        {"is_active": 1, "is_blocked": 0}, {"user_id": 1}
    ))


def get_non_blocked_users(db_path: Path) -> list:
    """Return all users who are not blocked (includes inactive). For broad broadcasts."""
    return _to_rows(_child_db(db_path)["bot_users"].find(
        {"is_blocked": 0}, {"user_id": 1}
    ))


def get_all_users_paginated(db_path: Path, page: int = 1, per_page: int = 10) -> tuple:
    coll = _child_db(db_path)["bot_users"]
    total = coll.count_documents({})
    rows = _to_rows(coll.find({}).sort("joined_at", -1).skip((page - 1) * per_page).limit(per_page))
    return rows, total


# ─── Settings ────────────────────────────────────────────────────────────────

def get_setting(db_path: Path, key: str, default: Any = None) -> Any:
    doc = _child_db(db_path)["bot_settings"].find_one({"key": key})
    if doc:
        try:
            return json.loads(doc["value"])
        except Exception:
            return doc["value"]
    return default


def set_setting(db_path: Path, key: str, value: Any):
    serialized = json.dumps(value)
    _child_db(db_path)["bot_settings"].update_one(
        {"key": key}, {"$set": {"value": serialized}}, upsert=True
    )


# ─── Channels ────────────────────────────────────────────────────────────────

def add_channel(db_path: Path, channel_id: str, title: str, link: str, is_mandatory: bool = True) -> bool:
    try:
        _child_db(db_path)["channels"].insert_one({
            "channel_id": channel_id,
            "title": title,
            "link": link,
            "is_mandatory": 1 if is_mandatory else 0,
            "added_at": _now(),
        })
        return True
    except DuplicateKeyError:
        return False


def remove_channel(db_path: Path, channel_id: str) -> bool:
    result = _child_db(db_path)["channels"].delete_one({"channel_id": channel_id})
    return result.deleted_count > 0


def toggle_channel_mandatory(db_path: Path, channel_id: str) -> Optional[bool]:
    doc = _child_db(db_path)["channels"].find_one({"channel_id": channel_id})
    if not doc:
        return None
    new_val = 0 if doc.get("is_mandatory", 1) else 1
    _child_db(db_path)["channels"].update_one(
        {"channel_id": channel_id}, {"$set": {"is_mandatory": new_val}}
    )
    return bool(new_val)


def list_channels(db_path: Path) -> list:
    return _to_rows(_child_db(db_path)["channels"].find({}).sort("added_at", ASCENDING))


def get_mandatory_channels(db_path: Path) -> list:
    return _to_rows(_child_db(db_path)["channels"].find({"is_mandatory": 1}).sort("added_at", ASCENDING))


# ─── Broadcast log ───────────────────────────────────────────────────────────

def log_broadcast(db_path: Path, total: int, success: int, failed: int, blocked: int):
    _child_db(db_path)["broadcast_log"].insert_one({
        "sent_at": _now(),
        "total": total,
        "success": success,
        "failed": failed,
        "blocked": blocked,
    })


# ─── Child admins ─────────────────────────────────────────────────────────────

def is_child_admin(db_path: Path, user_id: int) -> bool:
    if is_admin(user_id):
        return True
    return _child_db(db_path)["child_admins"].find_one({"user_id": user_id}) is not None


def add_child_admin(db_path: Path, user_id: int, username: Optional[str], full_name: str) -> bool:
    try:
        _child_db(db_path)["child_admins"].insert_one({
            "user_id": user_id,
            "username": username,
            "full_name": full_name,
            "added_at": _now(),
        })
        return True
    except DuplicateKeyError:
        return False


def remove_child_admin(db_path: Path, user_id: int) -> bool:
    result = _child_db(db_path)["child_admins"].delete_one({"user_id": user_id})
    return result.deleted_count > 0


def list_child_admins(db_path: Path) -> list:
    return _to_rows(_child_db(db_path)["child_admins"].find({}).sort("added_at", ASCENDING))


# ─── Admin requests ───────────────────────────────────────────────────────────

def request_admin_access(db_path: Path, user_id: int, username: Optional[str], full_name: str) -> bool:
    coll = _child_db(db_path)["admin_requests"]
    existing = coll.find_one({"user_id": user_id})
    if existing:
        if existing.get("status") == "pending":
            return False
        coll.update_one({"user_id": user_id}, {"$set": {"status": "pending", "requested_at": _now()}})
        return True
    try:
        coll.insert_one({
            "user_id": user_id,
            "username": username,
            "full_name": full_name,
            "requested_at": _now(),
            "status": "pending",
        })
        return True
    except DuplicateKeyError:
        return False


def get_pending_requests(db_path: Path) -> list:
    return _to_rows(_child_db(db_path)["admin_requests"].find(
        {"status": "pending"}
    ).sort("requested_at", ASCENDING))


def resolve_request(db_path: Path, user_id: int, status: str):
    _child_db(db_path)["admin_requests"].update_one(
        {"user_id": user_id}, {"$set": {"status": status}}
    )


# ─── Migration from SQLite ────────────────────────────────────────────────────

def migrate_from_sqlite() -> dict:
    """Migrate all SQLite data to MongoDB. Returns count of migrated records."""
    from shared import database as sql
    counts = {}
    try:
        # Migrate admins
        admins = sql.list_admins()
        for a in admins:
            try:
                add_admin(a["user_id"], a["username"], a["full_name"], bool(a["is_owner"]))
            except Exception:
                pass
        counts["admins"] = len(admins)

        # Migrate child bots
        bots = sql.list_child_bots()
        for b in bots:
            try:
                add_child_bot(b["bot_token"], b["bot_username"], b["bot_name"], b["added_by"])
                # Preserve is_running state
                _admin_db()["child_bots"].update_one(
                    {"bot_token": b["bot_token"]},
                    {"$set": {"is_running": b["is_running"], "added_at": b["added_at"]}},
                )
            except Exception:
                pass
        counts["child_bots"] = len(bots)

        # Migrate each child bot's data
        for b in bots:
            sql_path = sql.get_child_db_path(b["bot_username"])
            mongo_path = get_child_db_path(b["bot_username"])
            try:
                init_child_db(mongo_path)
                # Users
                users, _ = sql.get_all_users_paginated(sql_path, 1, 99999)
                for u in users:
                    try:
                        _child_db(mongo_path)["bot_users"].update_one(
                            {"user_id": u["user_id"]},
                            {"$setOnInsert": {
                                "user_id": u["user_id"],
                                "username": u["username"],
                                "full_name": u["full_name"],
                                "is_active": u["is_active"],
                                "is_blocked": u["is_blocked"],
                                "joined_at": u["joined_at"],
                                "last_seen": u["last_seen"],
                            }},
                            upsert=True,
                        )
                    except Exception:
                        pass
                counts[f"users_{b['bot_username']}"] = len(users)

                # Settings
                start_msg = sql.get_setting(sql_path, "start_message")
                if start_msg:
                    set_setting(mongo_path, "start_message", start_msg)

                # Channels
                for ch in sql.list_channels(sql_path):
                    try:
                        add_channel(mongo_path, ch["channel_id"], ch["title"], ch["link"], bool(ch["is_mandatory"]))
                    except Exception:
                        pass

                # Child admins
                for ca in sql.list_child_admins(sql_path):
                    try:
                        add_child_admin(mongo_path, ca["user_id"], ca["username"], ca["full_name"])
                    except Exception:
                        pass
            except Exception as e:
                logger.warning(f"Migration error for bot {b['bot_username']}: {e}")

    except Exception as e:
        logger.error(f"Migration failed: {e}")

    return counts


# ─── Connection check ─────────────────────────────────────────────────────────

def test_connection(uri: str) -> tuple[bool, str]:
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        client.admin.command("ping")
        client.close()
        return True, "OK"
    except Exception as e:
        return False, str(e)
