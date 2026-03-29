"""
Child Bot — Runs independently under Admin Bot supervision.
Handles users with start messages, broadcasts, blocking, and channels.
"""

import os
import sys
import logging
import time
from pathlib import Path
from typing import Optional

import telebot
from telebot import types

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared import database as db
from shared.keyboards import (
    child_main_menu, child_admin_menu, cancel_keyboard, confirm_keyboard,
    join_channels_inline, channel_manage_inline, broadcast_confirm_inline,
    reply_to_user_inline,
)
from shared.utils import ts_to_human, format_user_info, paginate

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

CHILD_TOKEN = os.environ.get("CHILD_BOT_TOKEN", "")
CHILD_USERNAME = os.environ.get("CHILD_BOT_USERNAME", "")
ADMIN_BOT_TOKEN = os.environ.get("ADMIN_BOT_TOKEN", "")
ADMIN_OWNER_ID = int(os.environ.get("ADMIN_BOT_OWNER_ID", "0"))

if not CHILD_TOKEN:
    raise RuntimeError("CHILD_BOT_TOKEN is required")

bot = telebot.TeleBot(CHILD_TOKEN, parse_mode="HTML")

DB_PATH = db.get_child_db_path(CHILD_USERNAME or "child")

# State tracking
user_states: dict[int, dict] = {}


def get_state(user_id: int) -> dict:
    return user_states.get(user_id, {})


def set_state(user_id: int, **kwargs):
    user_states[user_id] = kwargs


def clear_state(user_id: int):
    user_states.pop(user_id, None)


def get_admins_for_child() -> list[int]:
    """Return list of admin user IDs (from admin bot DB + owner)."""
    try:
        admin_rows = db.list_admins()
        return [row["user_id"] for row in admin_rows]
    except Exception:
        return [ADMIN_OWNER_ID] if ADMIN_OWNER_ID else []


def is_child_admin(user_id: int) -> bool:
    return user_id in get_admins_for_child()


def require_child_admin(func):
    def wrapper(message, *args, **kwargs):
        if not is_child_admin(message.from_user.id):
            bot.reply_to(message, "⛔ Access denied.")
            return
        return func(message, *args, **kwargs)
    return wrapper


def require_child_admin_cb(func):
    def wrapper(call, *args, **kwargs):
        if not is_child_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "⛔ Access denied.")
            return
        return func(call, *args, **kwargs)
    return wrapper


def check_and_register_user(message: types.Message) -> bool:
    """Register user and return True if new."""
    uid = message.from_user.id
    username = message.from_user.username
    full_name = (message.from_user.first_name or "") + (" " + message.from_user.last_name if message.from_user.last_name else "")
    return db.upsert_user(DB_PATH, uid, username, full_name.strip() or "Unknown")


def get_effective_keyboard(user_id: int) -> types.ReplyKeyboardMarkup:
    if is_child_admin(user_id):
        return child_admin_menu()
    return child_main_menu()


# ─── /start ──────────────────────────────────────────────────────────────────

@bot.message_handler(commands=["start"])
def cmd_start(message: types.Message):
    uid = message.from_user.id
    is_new = check_and_register_user(message)
    user_row = db.get_user(DB_PATH, uid)

    if user_row and user_row["is_blocked"]:
        bot.send_message(message.chat.id, "🚫 You have been blocked from using this bot.")
        return

    # Check for adminpanel deep link
    args = message.text.split()
    if len(args) > 1 and args[1] == "adminpanel" and is_child_admin(uid):
        bot.send_message(
            message.chat.id,
            "🎛 <b>Child Bot Admin Panel</b>\n\nWelcome back, Admin!",
            reply_markup=child_admin_menu(),
        )
        return

    # Check mandatory channel joins
    channels = db.list_channels(DB_PATH)
    if channels:
        bot.send_message(
            message.chat.id,
            "📢 <b>Please join our channels first!</b>\n\nClick the buttons below to join, then press ✅",
            reply_markup=join_channels_inline(channels),
        )
        return

    # Send start message
    start_data = db.get_setting(DB_PATH, "start_message")
    if start_data and isinstance(start_data, dict):
        msg_type = start_data.get("type")
        file_id = start_data.get("file_id")
        text = start_data.get("text", "👋 Welcome!")
        try:
            if msg_type == "text":
                bot.send_message(message.chat.id, text, reply_markup=get_effective_keyboard(uid))
            elif msg_type == "photo":
                bot.send_photo(message.chat.id, file_id, caption=text, reply_markup=get_effective_keyboard(uid))
            elif msg_type == "video":
                bot.send_video(message.chat.id, file_id, caption=text, reply_markup=get_effective_keyboard(uid))
            elif msg_type == "document":
                bot.send_document(message.chat.id, file_id, caption=text, reply_markup=get_effective_keyboard(uid))
            elif msg_type == "audio":
                bot.send_audio(message.chat.id, file_id, caption=text, reply_markup=get_effective_keyboard(uid))
            elif msg_type == "sticker":
                bot.send_sticker(message.chat.id, file_id)
                bot.send_message(message.chat.id, text or "👋", reply_markup=get_effective_keyboard(uid))
            else:
                bot.send_message(message.chat.id, text, reply_markup=get_effective_keyboard(uid))
        except Exception:
            bot.send_message(message.chat.id, "👋 Welcome!", reply_markup=get_effective_keyboard(uid))
    else:
        bot.send_message(
            message.chat.id,
            "👋 <b>Welcome!</b>\n\nThis bot is ready to use.",
            reply_markup=get_effective_keyboard(uid),
        )


@bot.callback_query_handler(func=lambda c: c.data == "check_join")
def cb_check_join(call: types.CallbackQuery):
    uid = call.from_user.id
    bot.answer_callback_query(call.id, "✅ Thanks!")
    bot.delete_message(call.message.chat.id, call.message.message_id)
    # Resend start
    cmd_start(call.message)


# ─── User: Message Admin ──────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "📨 Message Admin")
def menu_message_admin(message: types.Message):
    uid = message.from_user.id
    user_row = db.get_user(DB_PATH, uid)
    if user_row and user_row["is_blocked"]:
        bot.reply_to(message, "🚫 You are blocked.")
        return
    set_state(uid, action="send_to_admin")
    bot.send_message(
        message.chat.id,
        "💬 <b>Send a message to admin</b>\n\nType your message below:",
        reply_markup=cancel_keyboard(),
    )


@bot.message_handler(func=lambda m: get_state(m.from_user.id).get("action") == "send_to_admin")
def handle_send_to_admin(message: types.Message):
    uid = message.from_user.id
    if message.text == "❌ Cancel":
        clear_state(uid)
        bot.send_message(message.chat.id, "❌ Cancelled.", reply_markup=get_effective_keyboard(uid))
        return

    user_row = db.get_user(DB_PATH, uid)
    username = message.from_user.username
    full_name = (message.from_user.first_name or "") + (" " + message.from_user.last_name if message.from_user.last_name else "")
    uname_display = f"@{username}" if username else "No username"

    header = (
        f"📨 <b>Message from User</b>\n"
        f"👤 {full_name.strip()} ({uname_display})\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"📅 Joined: {ts_to_human(user_row['joined_at'] if user_row else 0)}\n"
        f"─────────────────\n"
    )

    admins = get_admins_for_child()
    for admin_id in admins:
        try:
            # Forward the original message with header
            bot.send_message(admin_id, header, reply_markup=reply_to_user_inline(uid))
            bot.forward_message(admin_id, message.chat.id, message.message_id)
        except Exception as e:
            logger.warning(f"Could not forward to admin {admin_id}: {e}")

    clear_state(uid)
    bot.send_message(
        message.chat.id,
        "✅ <b>Your message has been sent to admin.</b>\n\nWait for a reply.",
        reply_markup=get_effective_keyboard(uid),
    )


# ─── Admin: Reply to User ─────────────────────────────────────────────────────

@bot.callback_query_handler(func=lambda c: c.data.startswith("reply_user:"))
@require_child_admin_cb
def cb_reply_user(call: types.CallbackQuery):
    target_uid = int(call.data.split(":")[1])
    set_state(call.from_user.id, action="reply_user", target_uid=target_uid)
    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        f"↩️ <b>Reply to User <code>{target_uid}</code></b>\n\nSend your reply message:",
        reply_markup=cancel_keyboard(),
    )


@bot.message_handler(func=lambda m: get_state(m.from_user.id).get("action") == "reply_user")
@require_child_admin
def handle_reply_to_user(message: types.Message):
    uid = message.from_user.id
    state = get_state(uid)
    if message.text == "❌ Cancel":
        clear_state(uid)
        bot.send_message(message.chat.id, "❌ Cancelled.", reply_markup=child_admin_menu())
        return

    target_uid = state.get("target_uid")
    if not target_uid:
        clear_state(uid)
        return

    try:
        bot.send_message(
            target_uid,
            f"💬 <b>Reply from Admin:</b>\n\n{message.text}" if message.text else None,
        )
        if message.text is None:
            bot.copy_message(target_uid, message.chat.id, message.message_id)
        bot.send_message(message.chat.id, f"✅ Reply sent to user <code>{target_uid}</code>.", reply_markup=child_admin_menu())
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Failed to send: {e}", reply_markup=child_admin_menu())
    clear_state(uid)


# ─── User: Join Channel ───────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "🔗 Join Channel")
def menu_join_channel(message: types.Message):
    channels = db.list_channels(DB_PATH)
    if not channels:
        bot.send_message(message.chat.id, "ℹ️ No channels configured yet.")
        return
    bot.send_message(
        message.chat.id,
        "📢 <b>Our Channels</b>\n\nJoin us:",
        reply_markup=join_channels_inline(channels),
    )


# ─── Admin: Set Start Message ─────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "📝 Set Start Message")
@require_child_admin
def menu_set_start_msg(message: types.Message):
    set_state(message.from_user.id, action="set_start_msg")
    bot.send_message(
        message.chat.id,
        "📝 <b>Set Start Message</b>\n\n"
        "Send any message (text, photo, video, document, audio, sticker).\n"
        "You can also forward a message from anywhere.\n\n"
        "💡 <i>Tip: You can use a File ID to copy messages.</i>",
        reply_markup=cancel_keyboard(),
    )


@bot.message_handler(
    content_types=["text", "photo", "video", "document", "audio", "sticker", "voice", "animation"],
    func=lambda m: get_state(m.from_user.id).get("action") == "set_start_msg",
)
@require_child_admin
def handle_set_start_msg(message: types.Message):
    uid = message.from_user.id
    if message.text == "❌ Cancel":
        clear_state(uid)
        bot.send_message(message.chat.id, "❌ Cancelled.", reply_markup=child_admin_menu())
        return

    msg_data = _extract_message_data(message)
    db.set_setting(DB_PATH, "start_message", msg_data)
    clear_state(uid)
    bot.send_message(
        message.chat.id,
        "✅ <b>Start message updated!</b>\n\nUsers will see this when they start the bot.",
        reply_markup=child_admin_menu(),
    )


def _extract_message_data(message: types.Message) -> dict:
    if message.content_type == "text":
        return {"type": "text", "text": message.text, "file_id": None}
    elif message.content_type == "photo":
        file_id = message.photo[-1].file_id
        return {"type": "photo", "file_id": file_id, "text": message.caption or ""}
    elif message.content_type == "video":
        return {"type": "video", "file_id": message.video.file_id, "text": message.caption or ""}
    elif message.content_type == "document":
        return {"type": "document", "file_id": message.document.file_id, "text": message.caption or ""}
    elif message.content_type == "audio":
        return {"type": "audio", "file_id": message.audio.file_id, "text": message.caption or ""}
    elif message.content_type == "sticker":
        return {"type": "sticker", "file_id": message.sticker.file_id, "text": ""}
    elif message.content_type == "voice":
        return {"type": "voice", "file_id": message.voice.file_id, "text": message.caption or ""}
    elif message.content_type == "animation":
        return {"type": "animation", "file_id": message.animation.file_id, "text": message.caption or ""}
    return {"type": "text", "text": "👋 Welcome!", "file_id": None}


# ─── Admin: Broadcast ─────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "📢 Broadcast")
@require_child_admin
def menu_broadcast(message: types.Message):
    set_state(message.from_user.id, action="broadcast_msg")
    bot.send_message(
        message.chat.id,
        "📢 <b>Broadcast Message</b>\n\n"
        "Send the message you want to broadcast to all users.\n"
        "(Text, photo, video, document, forward — any type works.)",
        reply_markup=cancel_keyboard(),
    )


@bot.message_handler(
    content_types=["text", "photo", "video", "document", "audio", "sticker", "voice", "animation"],
    func=lambda m: get_state(m.from_user.id).get("action") == "broadcast_msg",
)
@require_child_admin
def handle_broadcast_preview(message: types.Message):
    uid = message.from_user.id
    if message.text == "❌ Cancel":
        clear_state(uid)
        bot.send_message(message.chat.id, "❌ Cancelled.", reply_markup=child_admin_menu())
        return

    set_state(uid, action="broadcast_confirm", broadcast_msg_id=message.message_id, broadcast_chat_id=message.chat.id)

    # Show preview
    bot.send_message(message.chat.id, "👀 <b>Preview of your broadcast:</b>")
    bot.copy_message(message.chat.id, message.chat.id, message.message_id)

    counts = db.count_users(DB_PATH)
    bot.send_message(
        message.chat.id,
        f"📊 <b>Broadcast Summary:</b>\n"
        f"📬 Will send to: <b>{counts['active']}</b> active users\n\n"
        "Set delay (seconds) between messages [default: 0]:",
        reply_markup=cancel_keyboard(),
    )


@bot.message_handler(func=lambda m: get_state(m.from_user.id).get("action") == "broadcast_confirm")
@require_child_admin
def handle_broadcast_delay(message: types.Message):
    uid = message.from_user.id
    state = get_state(uid)
    if message.text == "❌ Cancel":
        clear_state(uid)
        bot.send_message(message.chat.id, "❌ Cancelled.", reply_markup=child_admin_menu())
        return

    try:
        delay = float(message.text.strip()) if message.text.strip().replace(".", "", 1).isdigit() else 0
    except Exception:
        delay = 0

    set_state(uid, action="broadcast_dead_user",
              broadcast_msg_id=state["broadcast_msg_id"],
              broadcast_chat_id=state["broadcast_chat_id"],
              broadcast_delay=delay)

    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ Yes, include dead users", callback_data="broadcast_dead:yes"),
        types.InlineKeyboardButton("❌ Skip dead users", callback_data="broadcast_dead:no"),
    )
    bot.send_message(
        message.chat.id,
        f"⏱ Delay set to <b>{delay}s</b>\n\n"
        "🧟 Also broadcast to deactivated (dead) users?",
        reply_markup=kb,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("broadcast_dead:"))
@require_child_admin_cb
def cb_broadcast_dead(call: types.CallbackQuery):
    uid = call.from_user.id
    state = get_state(uid)
    include_dead = call.data.split(":")[1] == "yes"
    bot.answer_callback_query(call.id)
    _run_broadcast(
        call.message.chat.id, uid,
        state["broadcast_msg_id"], state["broadcast_chat_id"],
        state.get("broadcast_delay", 0), include_dead,
    )
    clear_state(uid)


def _run_broadcast(chat_id: int, admin_uid: int, msg_id: int, src_chat_id: int, delay: float, include_dead: bool):
    if include_dead:
        with db.get_conn(DB_PATH) as conn:
            users = conn.execute("SELECT user_id FROM bot_users WHERE is_blocked=0").fetchall()
    else:
        users = db.get_active_users(DB_PATH)

    total = len(users)
    success = 0
    failed = 0
    blocked = 0

    status_msg = bot.send_message(chat_id, f"📤 Broadcasting to {total} users...")

    for row in users:
        uid_target = row["user_id"]
        try:
            bot.copy_message(uid_target, src_chat_id, msg_id)
            success += 1
        except Exception as e:
            err_str = str(e).lower()
            if "blocked" in err_str or "deactivated" in err_str or "bot was kicked" in err_str:
                db.set_user_inactive(DB_PATH, uid_target)
                blocked += 1
            else:
                failed += 1
        if delay > 0:
            time.sleep(delay)

    db.log_broadcast(DB_PATH, total, success, failed, blocked)

    try:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_msg.message_id,
            text=(
                f"✅ <b>Broadcast Complete!</b>\n\n"
                f"📊 Total: <b>{total}</b>\n"
                f"✅ Sent: <b>{success}</b>\n"
                f"❌ Failed: <b>{failed}</b>\n"
                f"🚫 Blocked/Inactive: <b>{blocked}</b>"
            ),
        )
    except Exception:
        pass


# ─── Admin: Total Users ───────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "👥 Total Users")
@require_child_admin
def menu_total_users(message: types.Message):
    counts = db.count_users(DB_PATH)
    bot.send_message(
        message.chat.id,
        f"👥 <b>User Statistics</b>\n\n"
        f"📊 Total: <b>{counts['total']}</b>\n"
        f"✅ Active: <b>{counts['active']}</b>\n"
        f"⚪ Inactive: <b>{counts['inactive']}</b>\n"
        f"🚫 Blocked: <b>{counts['blocked']}</b>",
        reply_markup=child_admin_menu(),
    )


# ─── Admin: Block/Unblock ─────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "🚫 Block/Unblock User")
@require_child_admin
def menu_block_user(message: types.Message):
    set_state(message.from_user.id, action="block_user")
    bot.send_message(
        message.chat.id,
        "🚫 <b>Block / Unblock User</b>\n\n"
        "Send the <b>User ID</b> to toggle their status:",
        reply_markup=cancel_keyboard(),
    )


@bot.message_handler(func=lambda m: get_state(m.from_user.id).get("action") == "block_user")
@require_child_admin
def handle_block_user(message: types.Message):
    uid = message.from_user.id
    if message.text == "❌ Cancel":
        clear_state(uid)
        bot.send_message(message.chat.id, "❌ Cancelled.", reply_markup=child_admin_menu())
        return

    try:
        target_id = int(message.text.strip())
    except ValueError:
        bot.reply_to(message, "⚠️ Send a valid numeric User ID.")
        return

    user_row = db.get_user(DB_PATH, target_id)
    if not user_row:
        bot.send_message(message.chat.id, f"❌ User <code>{target_id}</code> not found.", reply_markup=child_admin_menu())
        clear_state(uid)
        return

    new_blocked = not bool(user_row["is_blocked"])
    db.set_user_blocked(DB_PATH, target_id, new_blocked)
    action_text = "🚫 Blocked" if new_blocked else "✅ Unblocked"
    clear_state(uid)
    bot.send_message(
        message.chat.id,
        f"{action_text} user <code>{target_id}</code> (<b>{user_row['full_name']}</b>).",
        reply_markup=child_admin_menu(),
    )


# ─── Admin: Channel Links ─────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "🔗 Channel Links")
@require_child_admin
def menu_channel_links(message: types.Message):
    channels = db.list_channels(DB_PATH)
    if not channels:
        bot.send_message(
            message.chat.id,
            "📭 No channels configured.\n\nAdd one with the button below.",
            reply_markup=channel_manage_inline([]),
        )
        return
    bot.send_message(
        message.chat.id,
        "🔗 <b>Channel Links</b>\n\nManage your channels:",
        reply_markup=channel_manage_inline(channels),
    )


@bot.callback_query_handler(func=lambda c: c.data == "add_channel")
@require_child_admin_cb
def cb_add_channel(call: types.CallbackQuery):
    set_state(call.from_user.id, action="add_channel")
    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        "➕ <b>Add Channel</b>\n\n"
        "Send channel info in this format:\n"
        "<code>title | link</code>\n\n"
        "Example: <code>My Channel | https://t.me/mychannel</code>",
        reply_markup=cancel_keyboard(),
    )


@bot.message_handler(func=lambda m: get_state(m.from_user.id).get("action") == "add_channel")
@require_child_admin
def handle_add_channel(message: types.Message):
    uid = message.from_user.id
    if message.text == "❌ Cancel":
        clear_state(uid)
        bot.send_message(message.chat.id, "❌ Cancelled.", reply_markup=child_admin_menu())
        return

    if "|" not in message.text:
        bot.reply_to(message, "⚠️ Wrong format. Use: <code>title | link</code>")
        return

    parts = message.text.split("|", 1)
    title = parts[0].strip()
    link = parts[1].strip()

    if not link.startswith("https://t.me/") and not link.startswith("http"):
        bot.reply_to(message, "⚠️ Invalid link. Use a full https:// URL.")
        return

    channel_id = link.replace("https://t.me/", "@").split("?")[0]
    if db.add_channel(DB_PATH, channel_id, title, link):
        bot.send_message(message.chat.id, f"✅ Channel <b>{title}</b> added!", reply_markup=child_admin_menu())
    else:
        bot.send_message(message.chat.id, "⚠️ Channel already exists.", reply_markup=child_admin_menu())
    clear_state(uid)


@bot.callback_query_handler(func=lambda c: c.data.startswith("remove_channel:"))
@require_child_admin_cb
def cb_remove_channel(call: types.CallbackQuery):
    channel_id = call.data.split(":", 1)[1]
    if db.remove_channel(DB_PATH, channel_id):
        bot.answer_callback_query(call.id, "✅ Channel removed.")
        channels = db.list_channels(DB_PATH)
        try:
            bot.edit_message_reply_markup(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=channel_manage_inline(channels),
            )
        except Exception:
            pass
    else:
        bot.answer_callback_query(call.id, "❌ Channel not found.")


# ─── Admin: Back to Main ──────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "🔙 Back to Main")
def menu_back_main(message: types.Message):
    check_and_register_user(message)
    clear_state(message.from_user.id)
    bot.send_message(
        message.chat.id,
        "🏠 Main Menu",
        reply_markup=child_main_menu(),
    )


# ─── Fallback: Forward to Admin ──────────────────────────────────────────────

@bot.message_handler(
    content_types=["text", "photo", "video", "document", "audio", "sticker", "voice", "animation"],
    func=lambda m: not get_state(m.from_user.id) and not is_child_admin(m.from_user.id),
)
def handle_user_message(message: types.Message):
    uid = message.from_user.id
    check_and_register_user(message)
    user_row = db.get_user(DB_PATH, uid)
    if user_row and user_row["is_blocked"]:
        return

    full_name = (message.from_user.first_name or "") + (" " + message.from_user.last_name if message.from_user.last_name else "")
    username = message.from_user.username
    uname_display = f"@{username}" if username else "No username"

    header = (
        f"📨 <b>Message from User</b>\n"
        f"👤 {full_name.strip()} ({uname_display})\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"─────────────────\n"
    )

    admins = get_admins_for_child()
    for admin_id in admins:
        try:
            bot.send_message(admin_id, header, reply_markup=reply_to_user_inline(uid))
            bot.forward_message(admin_id, message.chat.id, message.message_id)
        except Exception as e:
            logger.warning(f"Could not forward to admin {admin_id}: {e}")


# ─── Cancel ───────────────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "❌ Cancel")
def handle_cancel(message: types.Message):
    clear_state(message.from_user.id)
    bot.send_message(message.chat.id, "❌ Cancelled.", reply_markup=get_effective_keyboard(message.from_user.id))


# ─── Noop callback ───────────────────────────────────────────────────────────

@bot.callback_query_handler(func=lambda c: c.data == "noop")
def cb_noop(call: types.CallbackQuery):
    bot.answer_callback_query(call.id)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    db.init_child_db(DB_PATH)
    logger.info("Child Bot started. Username: @%s", CHILD_USERNAME)
    bot.infinity_polling(skip_pending=True, logger_level=logging.WARNING)


if __name__ == "__main__":
    main()
