"""
Shared utility functions.
"""

import datetime
from typing import Optional


def ts_to_human(ts: int) -> str:
    if not ts:
        return "Unknown"
    try:
        return datetime.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "Unknown"


def human_status(is_running: bool) -> str:
    return "🟢 Running" if is_running else "🔴 Stopped"


def paginate(items: list, page: int, per_page: int = 10) -> tuple:
    total = len(items)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    return items[start:start + per_page], total_pages
