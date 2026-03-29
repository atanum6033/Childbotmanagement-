"""
Shared utility functions for Admin Bot and Child Bots.
"""

import time
import datetime
from typing import Optional


def ts_to_human(ts: int) -> str:
    """Convert Unix timestamp to readable date string."""
    if not ts:
        return "Unknown"
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def human_status(is_running: bool) -> str:
    return "🟢 Running" if is_running else "🔴 Stopped"


def paginate(items: list, page: int, per_page: int = 10) -> tuple:
    """Return (page_items, total_pages)."""
    total = len(items)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    return items[start:start + per_page], total_pages


def escape_md(text: str) -> str:
    """Escape Telegram MarkdownV2 special characters."""
    if not text:
        return ""
    special = r"\_*[]()~`>#+-=|{}.!"
    for ch in special:
        text = text.replace(ch, f"\\{ch}")
    return text


def format_user_info(user_id: int, username: Optional[str], full_name: str, joined_ts: int, is_active: bool, is_blocked: bool) -> str:
    status = "🚫 Blocked" if is_blocked else ("✅ Active" if is_active else "⚪ Inactive")
    uname = f"@{username}" if username else "No username"
    return (
        f"👤 <b>{full_name}</b>\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"🔗 {uname}\n"
        f"📅 Joined: {ts_to_human(joined_ts)}\n"
        f"📊 Status: {status}"
    )


def format_bot_info(bot_id: int, bot_username: str, bot_name: str, is_running: bool, added_at: int) -> str:
    return (
        f"🤖 <b>{bot_name}</b>\n"
        f"🔗 @{bot_username}\n"
        f"🆔 Bot ID: <code>{bot_id}</code>\n"
        f"📅 Added: {ts_to_human(added_at)}\n"
        f"📊 Status: {human_status(is_running)}"
    )
