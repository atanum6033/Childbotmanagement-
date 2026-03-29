"""
Database configuration manager.
Stores which database engine to use (sqlite or mongodb) and connection details.
"""

import json
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_FILE = _PROJECT_ROOT / "data" / "db_config.json"

_DEFAULT = {"type": "sqlite", "mongo_uri": ""}


def _load() -> dict:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return dict(_DEFAULT)


def _save(cfg: dict):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


def get_db_type() -> str:
    return _load().get("type", "sqlite")


def get_mongo_uri() -> str:
    return _load().get("mongo_uri", "")


def switch_to_mongodb(uri: str):
    _save({"type": "mongodb", "mongo_uri": uri})


def switch_to_sqlite():
    cfg = _load()
    cfg["type"] = "sqlite"
    _save(cfg)


def get_status_info() -> dict:
    cfg = _load()
    return {
        "type": cfg.get("type", "sqlite"),
        "mongo_uri": cfg.get("mongo_uri", ""),
    }
