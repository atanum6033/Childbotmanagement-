"""
Keyboard builders for Admin Bot and Child Bots.
"""

from telebot import types


# ─── Admin Bot ───────────────────────────────────────────────────────────────

def admin_main_menu() -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton("➕ Add Child Bot"),
        types.KeyboardButton("➖ Remove Child Bot"),
        types.KeyboardButton("📋 List Child Bots"),
        types.KeyboardButton("▶️ Stop/Run Bot"),
        types.KeyboardButton("📊 Total Users"),
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


def build_bot_list_inline(bots: list, page: int, total_pages: int, action: str) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=1)
    for bot in bots:
        status_icon = "🟢" if bot["is_running"] else "🔴"
        kb.add(types.InlineKeyboardButton(
            text=f"{status_icon} {bot['bot_name']} (@{bot['bot_username']})",
            callback_data=f"{action}:{bot['id']}"
        ))
    nav = []
    if page > 1:
        nav.append(types.InlineKeyboardButton("◀️", callback_data=f"page:{action}:{page-1}"))
    nav.append(types.InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav.append(types.InlineKeyboardButton("▶️", callback_data=f"page:{action}:{page+1}"))
    if nav:
        kb.row(*nav)
    return kb


def confirm_delete_inline(bot_id: int) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ Yes, Delete", callback_data=f"confirm_delete:{bot_id}"),
        types.InlineKeyboardButton("❌ Cancel", callback_data="cancel_delete"),
    )
    return kb


def admin_manage_inline(admins: list) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=1)
    for a in admins:
        if not a["is_owner"]:
            kb.add(types.InlineKeyboardButton(
                f"❌ Remove {a['full_name']}",
                callback_data=f"remove_admin:{a['user_id']}"
            ))
    kb.add(types.InlineKeyboardButton("➕ Add Admin by ID", callback_data="add_admin_prompt"))
    return kb


# ─── Child Bot ───────────────────────────────────────────────────────────────

def child_main_menu() -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton("📨 Message Admin"),
        types.KeyboardButton("🔗 Join Channel"),
        types.KeyboardButton("🙋 Request Admin"),
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
        types.KeyboardButton("👮 Manage Admins"),
        types.KeyboardButton("📬 Admin Requests"),
        types.KeyboardButton("🔙 Back to User Menu"),
    )
    return kb


def join_channels_inline(channels: list) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=1)
    for ch in channels:
        title = ch["title"] or ch["channel_id"]
        icon = "📢" if ch["is_mandatory"] else "🔔"
        kb.add(types.InlineKeyboardButton(f"{icon} {title}", url=ch["link"]))
    kb.add(types.InlineKeyboardButton("✅ Verify Membership", callback_data="check_join"))
    return kb


def channel_manage_inline(channels: list) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=1)
    for ch in channels:
        title = ch["title"] or ch["channel_id"]
        mandatory_icon = "🔴 Mandatory" if ch["is_mandatory"] else "🟢 Optional"
        kb.add(types.InlineKeyboardButton(
            f"📢 {title} — {mandatory_icon}",
            callback_data=f"ch_toggle:{ch['channel_id']}"
        ))
        kb.add(types.InlineKeyboardButton(
            f"🗑 Remove {title}",
            callback_data=f"ch_remove:{ch['channel_id']}"
        ))
    kb.add(types.InlineKeyboardButton("➕ Add New Channel", callback_data="add_channel"))
    return kb


def reply_to_user_inline(user_id: int) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton(
        f"↩️ Reply to {user_id}",
        callback_data=f"reply_user:{user_id}"
    ))
    return kb


def admin_request_inline(user_id: int) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ Approve", callback_data=f"approve_req:{user_id}"),
        types.InlineKeyboardButton("❌ Deny", callback_data=f"deny_req:{user_id}"),
    )
    return kb


def child_admins_manage_inline(admins: list) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=1)
    for a in admins:
        uname = f"@{a['username']}" if a["username"] else a["full_name"]
        kb.add(types.InlineKeyboardButton(
            f"❌ Remove {uname}",
            callback_data=f"rm_cadmin:{a['user_id']}"
        ))
    kb.add(types.InlineKeyboardButton("➕ Add Admin by ID", callback_data="add_cadmin_prompt"))
    return kb
