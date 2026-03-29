"""
Keyboard builders for Admin Bot and Child Bots.
"""

from telebot import types


# ─── Admin Bot keyboards ────────────────────────────────────────────────────

def admin_main_menu() -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton("➕ Add Child Bot"),
        types.KeyboardButton("➖ Remove Child Bot"),
        types.KeyboardButton("📋 List Child Bots"),
        types.KeyboardButton("▶️ Stop/Run Bot"),
        types.KeyboardButton("💾 Backup Database"),
        types.KeyboardButton("♻️ Restore Database"),
        types.KeyboardButton("🎛 Use Child Bot Admin"),
        types.KeyboardButton("👥 Add/Remove Admin"),
    )
    return kb


def cancel_keyboard() -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton("❌ Cancel"))
    return kb


def confirm_keyboard() -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton("✅ Confirm"),
        types.KeyboardButton("❌ Cancel"),
    )
    return kb


def build_bot_list_inline(bots: list, page: int, total_pages: int, action: str) -> types.InlineKeyboardMarkup:
    """Build paginated bot list inline keyboard."""
    kb = types.InlineKeyboardMarkup(row_width=1)
    for bot in bots:
        status_icon = "🟢" if bot["is_running"] else "🔴"
        kb.add(types.InlineKeyboardButton(
            text=f"{status_icon} {bot['bot_name']} (@{bot['bot_username']})",
            callback_data=f"{action}:{bot['id']}"
        ))
    nav_buttons = []
    if page > 1:
        nav_buttons.append(types.InlineKeyboardButton("◀️ Prev", callback_data=f"page:{action}:{page-1}"))
    nav_buttons.append(types.InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav_buttons.append(types.InlineKeyboardButton("Next ▶️", callback_data=f"page:{action}:{page+1}"))
    if nav_buttons:
        kb.row(*nav_buttons)
    return kb


def confirm_delete_inline(bot_id: int) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ Yes, Delete", callback_data=f"confirm_delete:{bot_id}"),
        types.InlineKeyboardButton("❌ Cancel", callback_data="cancel_delete"),
    )
    return kb


def toggle_bot_inline(bot_id: int, is_running: bool) -> types.InlineKeyboardMarkup:
    label = "⏹ Stop" if is_running else "▶️ Start"
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton(label, callback_data=f"toggle_bot:{bot_id}"))
    return kb


def child_bot_select_inline(bots: list, page: int, total_pages: int, action: str) -> types.InlineKeyboardMarkup:
    return build_bot_list_inline(bots, page, total_pages, action)


def admin_manage_inline(admins: list) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=1)
    for admin in admins:
        if not admin["is_owner"]:
            name = admin["full_name"]
            kb.add(types.InlineKeyboardButton(
                f"❌ Remove {name}",
                callback_data=f"remove_admin:{admin['user_id']}"
            ))
    return kb


# ─── Child Bot keyboards ─────────────────────────────────────────────────────

def child_main_menu() -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton("📨 Message Admin"),
        types.KeyboardButton("🔗 Join Channel"),
    )
    return kb


def child_admin_menu() -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton("📝 Set Start Message"),
        types.KeyboardButton("📢 Broadcast"),
        types.KeyboardButton("👥 Total Users"),
        types.KeyboardButton("🚫 Block/Unblock User"),
        types.KeyboardButton("🔗 Channel Links"),
        types.KeyboardButton("🔙 Back to Main"),
    )
    return kb


def join_channels_inline(channels: list) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=1)
    for ch in channels:
        title = ch["title"] or ch["channel_id"]
        kb.add(types.InlineKeyboardButton(f"📢 {title}", url=ch["link"]))
    kb.add(types.InlineKeyboardButton("✅ I've Joined", callback_data="check_join"))
    return kb


def channel_manage_inline(channels: list) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=1)
    for ch in channels:
        title = ch["title"] or ch["channel_id"]
        kb.add(types.InlineKeyboardButton(
            f"❌ Remove {title}",
            callback_data=f"remove_channel:{ch['channel_id']}"
        ))
    kb.add(types.InlineKeyboardButton("➕ Add New Channel", callback_data="add_channel"))
    return kb


def broadcast_confirm_inline() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ Send Broadcast", callback_data="confirm_broadcast"),
        types.InlineKeyboardButton("❌ Cancel", callback_data="cancel_broadcast"),
    )
    return kb


def reply_to_user_inline(user_id: int) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton(
        f"↩️ Reply to user {user_id}",
        callback_data=f"reply_user:{user_id}"
    ))
    return kb
