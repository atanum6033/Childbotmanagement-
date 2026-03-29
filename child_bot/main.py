"""
Child Bot — User-facing bot with full admin panel.
Uses active_db which auto-selects SQLite or MongoDB based on config.
"""

import os
import sys
import logging
import time
from pathlib import Path
from typing import Optional

import telebot
from telebot import types

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from shared import active_db as db
from shared.keyboards import (
    child_main_menu, child_admin_menu, cancel_keyboard,
    join_channels_inline, channel_manage_inline,
    reply_to_user_inline, admin_request_inline,
    child_admins_manage_inline,
)
from shared.utils import ts_to_human, paginate

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

CHILD_TOKEN = os.environ.get("CHILD_BOT_TOKEN", "")
CHILD_USERNAME = os.environ.get("CHILD_BOT_USERNAME", "")
ADMIN_OWNER_ID = int(os.environ.get("ADMIN_BOT_OWNER_ID", "0"))

if not CHILD_TOKEN:
    raise RuntimeError("CHILD_BOT_TOKEN is required")

bot = telebot.TeleBot(CHILD_TOKEN, parse_mode="HTML")
DB_PATH = db.get_child_db_path(CHILD_USERNAME or "child")

user_states: dict[int, dict] = {}


def gs(uid): return user_states.get(uid, {})
def ss(uid, **kw): user_states[uid] = kw
def cs(uid): user_states.pop(uid, None)


def _full_name(u) -> str:
    fn = u.first_name or ""
    ln = u.last_name or ""
    return (fn + " " + ln).strip() or "Unknown"


def _is_admin(uid: int) -> bool:
    return db.is_child_admin(DB_PATH, uid)


def require_admin(func):
    def wrapper(message, *a, **kw):
        if not _is_admin(message.from_user.id):
            bot.reply_to(message, "⛔ Admin only.")
            return
        return func(message, *a, **kw)
    return wrapper


def require_admin_cb(func):
    def wrapper(call, *a, **kw):
        if not _is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "⛔ Admin only.")
            return
        return func(call, *a, **kw)
    return wrapper


def get_menu(uid: int) -> types.ReplyKeyboardMarkup:
    return child_admin_menu() if _is_admin(uid) else child_main_menu()


def register(message: types.Message) -> bool:
    return db.upsert_user(DB_PATH, message.from_user.id, message.from_user.username, _full_name(message.from_user))


# ─── /start ──────────────────────────────────────────────────────────────────

@bot.message_handler(commands=["start"])
def cmd_start(message: types.Message):
    uid = message.from_user.id
    register(message)
    user_row = db.get_user(DB_PATH, uid)

    if user_row and user_row["is_blocked"]:
        bot.send_message(message.chat.id, "🚫 You are blocked from this bot.")
        return

    # Admin deep link
    args = message.text.split()
    if len(args) > 1 and args[1] == "adminpanel" and _is_admin(uid):
        bot.send_message(message.chat.id, "🎛 <b>Admin Panel</b>\n\nWelcome back!", reply_markup=child_admin_menu())
        return

    # Always send start message first
    _deliver_start_message(message.chat.id, uid)

    # Then — if mandatory channels exist — also send the join prompt
    mandatory = db.get_mandatory_channels(DB_PATH)
    if mandatory:
        all_channels = db.list_channels(DB_PATH)
        bot.send_message(
            message.chat.id,
            "📢 <b>Please also join our channel(s)!</b>\n\n"
            "Click ✅ Verify after joining to unlock all features.",
            reply_markup=join_channels_inline(all_channels),
        )


def _deliver_start_message(chat_id: int, uid: int):
    """Send the configured start message. Always called on /start."""
    start_data = db.get_setting(DB_PATH, "start_message")
    if start_data and isinstance(start_data, dict):
        src_chat = start_data.get("source_chat_id")
        src_msg = start_data.get("source_message_id")
        if src_chat and src_msg:
            try:
                # copy_message preserves ALL formatting, links, inline keyboards, entities
                bot.copy_message(chat_id, src_chat, src_msg, reply_markup=get_menu(uid))
                return
            except Exception as e:
                logger.warning(f"copy_message failed ({e}), using fallback.")
    bot.send_message(
        chat_id,
        "👋 <b>Welcome!</b>\n\nThis bot is ready to use.",
        reply_markup=get_menu(uid),
    )


@bot.callback_query_handler(func=lambda c: c.data == "check_join")
def cb_check_join(call: types.CallbackQuery):
    uid = call.from_user.id
    mandatory = db.get_mandatory_channels(DB_PATH)

    if not mandatory:
        # No mandatory channels — always pass verify
        bot.answer_callback_query(call.id, "✅ Verified!")
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        return

    # Check join status — bot must be channel admin for this to work
    not_joined = []
    for ch in mandatory:
        cid = ch["channel_id"]
        try:
            member = bot.get_chat_member(cid, uid)
            if member.status in ("left", "kicked", "banned"):
                not_joined.append(ch["title"] or cid)
        except Exception as e:
            # If bot is not admin, can't check — let through (log warning)
            logger.warning(f"Cannot check membership for {cid}: {e}")

    if not_joined:
        titles = ", ".join(not_joined)
        bot.answer_callback_query(
            call.id,
            f"⚠️ Please join first: {titles}",
            show_alert=True,
        )
        return

    bot.answer_callback_query(call.id, "✅ All channels joined!")
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass


# ─── User: Message Admin ──────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "📨 Message Admin")
def menu_message_admin(message: types.Message):
    uid = message.from_user.id
    u = db.get_user(DB_PATH, uid)
    if u and u["is_blocked"]:
        bot.reply_to(message, "🚫 You are blocked.")
        return
    ss(uid, action="send_to_admin")
    bot.send_message(
        message.chat.id,
        "💬 <b>Send a message to Admin</b>\n\nType or send anything:",
        reply_markup=cancel_keyboard(),
    )


@bot.message_handler(
    content_types=["text", "photo", "video", "document", "audio", "sticker", "voice", "animation"],
    func=lambda m: gs(m.from_user.id).get("action") == "send_to_admin",
)
def handle_send_to_admin(message: types.Message):
    uid = message.from_user.id
    if message.content_type == "text" and message.text == "❌ Cancel":
        cs(uid)
        bot.send_message(message.chat.id, "❌ Cancelled.", reply_markup=get_menu(uid))
        return

    u = db.get_user(DB_PATH, uid)
    uname = f"@{message.from_user.username}" if message.from_user.username else "No username"
    joined = ts_to_human(u["joined_at"]) if u else "Unknown"

    header = (
        f"📨 <b>User Message</b>\n"
        f"👤 {_full_name(message.from_user)} ({uname})\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"📅 Joined: {joined}\n"
        f"─────────────────"
    )
    for aid in db.get_admin_ids():
        try:
            bot.send_message(aid, header, reply_markup=reply_to_user_inline(uid))
            bot.forward_message(aid, message.chat.id, message.message_id)
        except Exception as e:
            logger.warning(f"Forward to admin {aid}: {e}")

    cs(uid)
    bot.send_message(message.chat.id, "✅ <b>Message sent!</b>\n\nWait for admin reply.", reply_markup=get_menu(uid))


# ─── Admin: Reply to User ─────────────────────────────────────────────────────

@bot.callback_query_handler(func=lambda c: c.data.startswith("reply_user:"))
@require_admin_cb
def cb_reply_user(call: types.CallbackQuery):
    target = int(call.data.split(":")[1])
    ss(call.from_user.id, action="reply_user", target_uid=target)
    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        f"↩️ <b>Reply to user <code>{target}</code></b>\n\nSend your reply:",
        reply_markup=cancel_keyboard(),
    )


@bot.message_handler(
    content_types=["text", "photo", "video", "document", "audio", "sticker", "voice", "animation"],
    func=lambda m: gs(m.from_user.id).get("action") == "reply_user",
)
@require_admin
def handle_reply_user(message: types.Message):
    uid = message.from_user.id
    state = gs(uid)
    if message.content_type == "text" and message.text == "❌ Cancel":
        cs(uid)
        bot.send_message(message.chat.id, "❌ Cancelled.", reply_markup=child_admin_menu())
        return
    target = state.get("target_uid")
    if not target:
        cs(uid)
        return
    try:
        bot.copy_message(target, message.chat.id, message.message_id)
        bot.send_message(message.chat.id, f"✅ Reply sent to <code>{target}</code>.", reply_markup=child_admin_menu())
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Failed: {e}", reply_markup=child_admin_menu())
    cs(uid)


# ─── User: Join Channel ───────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "🔗 Join Channel")
def menu_join(message: types.Message):
    channels = db.list_channels(DB_PATH)
    if not channels:
        bot.send_message(message.chat.id, "ℹ️ No channels configured yet.")
        return
    bot.send_message(message.chat.id, "📢 <b>Our Channels:</b>", reply_markup=join_channels_inline(channels))


# ─── User: Request Admin ──────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "🙋 Request Admin")
def menu_request_admin(message: types.Message):
    uid = message.from_user.id
    if _is_admin(uid):
        bot.send_message(message.chat.id, "ℹ️ You are already an admin.", reply_markup=child_admin_menu())
        return
    ok = db.request_admin_access(DB_PATH, uid, message.from_user.username, _full_name(message.from_user))
    if not ok:
        bot.send_message(message.chat.id, "ℹ️ Your request is already pending. Please wait.", reply_markup=child_main_menu())
        return
    uname = f"@{message.from_user.username}" if message.from_user.username else "No username"
    for aid in db.get_admin_ids():
        try:
            bot.send_message(
                aid,
                f"🙋 <b>Admin Access Request</b>\n\n"
                f"👤 {_full_name(message.from_user)} ({uname})\n"
                f"🆔 <code>{uid}</code>",
                reply_markup=admin_request_inline(uid),
            )
        except Exception:
            pass
    bot.send_message(message.chat.id, "✅ <b>Request sent!</b>\n\nWait for admin approval.", reply_markup=child_main_menu())


# ─── Admin: Set Start Message ─────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "📝 Set Start Message")
@require_admin
def menu_set_start(message: types.Message):
    ss(message.from_user.id, action="set_start_msg")
    bot.send_message(
        message.chat.id,
        "📝 <b>Set Start Message</b>\n\n"
        "Send any message — text, photo, video, sticker, or <b>forward from anywhere</b>.\n\n"
        "✅ All formatting, inline links, and buttons are <b>fully preserved</b>.\n"
        "The exact message will be copied to each user.",
        reply_markup=cancel_keyboard(),
    )


@bot.message_handler(
    content_types=["text", "photo", "video", "document", "audio", "sticker", "voice", "animation"],
    func=lambda m: gs(m.from_user.id).get("action") == "set_start_msg",
)
@require_admin
def handle_set_start_msg(message: types.Message):
    uid = message.from_user.id
    if message.content_type == "text" and message.text == "❌ Cancel":
        cs(uid)
        bot.send_message(message.chat.id, "❌ Cancelled.", reply_markup=child_admin_menu())
        return

    # Store ONLY source_chat_id + source_message_id
    # bot.copy_message() will reproduce the exact message with all links/formatting
    msg_data = {
        "source_chat_id": message.chat.id,
        "source_message_id": message.message_id,
        "content_type": message.content_type,
    }
    db.set_setting(DB_PATH, "start_message", msg_data)
    cs(uid)
    bot.send_message(
        message.chat.id,
        "✅ <b>Start message saved!</b>\n\n"
        "All links, formatting, and media are preserved exactly.",
        reply_markup=child_admin_menu(),
    )


# ─── Admin: Broadcast ─────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "📢 Broadcast")
@require_admin
def menu_broadcast(message: types.Message):
    ss(message.from_user.id, action="broadcast_msg")
    bot.send_message(
        message.chat.id,
        "📢 <b>Broadcast Message</b>\n\n"
        "Send the message to broadcast (any type — forward works too):",
        reply_markup=cancel_keyboard(),
    )


@bot.message_handler(
    content_types=["text", "photo", "video", "document", "audio", "sticker", "voice", "animation"],
    func=lambda m: gs(m.from_user.id).get("action") == "broadcast_msg",
)
@require_admin
def handle_broadcast_preview(message: types.Message):
    uid = message.from_user.id
    if message.content_type == "text" and message.text == "❌ Cancel":
        cs(uid)
        bot.send_message(message.chat.id, "❌ Cancelled.", reply_markup=child_admin_menu())
        return

    ss(uid, action="broadcast_delay", bc_chat_id=message.chat.id, bc_msg_id=message.message_id)
    bot.send_message(message.chat.id, "👀 <b>Broadcast Preview:</b>")
    try:
        bot.copy_message(message.chat.id, message.chat.id, message.message_id)
    except Exception:
        pass
    counts = db.count_users(DB_PATH)
    bot.send_message(
        message.chat.id,
        f"📊 Will send to <b>{counts['active']}</b> active users.\n\n"
        "⏱ Delay between sends (seconds)? Send <code>0</code> for none, or e.g. <code>1</code>:",
        reply_markup=cancel_keyboard(),
    )


@bot.message_handler(
    content_types=["text"],
    func=lambda m: gs(m.from_user.id).get("action") == "broadcast_delay",
)
@require_admin
def handle_broadcast_delay(message: types.Message):
    uid = message.from_user.id
    state = gs(uid)
    if message.text == "❌ Cancel":
        cs(uid)
        bot.send_message(message.chat.id, "❌ Cancelled.", reply_markup=child_admin_menu())
        return
    txt = message.text.strip().replace(",", ".")
    try:
        delay = float(txt) if txt.replace(".", "", 1).isdigit() else 0
    except Exception:
        delay = 0
    ss(uid, action="broadcast_dead", bc_chat_id=state["bc_chat_id"], bc_msg_id=state["bc_msg_id"], bc_delay=delay)
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ Include inactive", callback_data="bc_dead:yes"),
        types.InlineKeyboardButton("❌ Active only", callback_data="bc_dead:no"),
    )
    bot.send_message(
        message.chat.id,
        f"⏱ Delay: <b>{delay}s</b>\n\n🧟 Also send to inactive/dead users?",
        reply_markup=kb,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("bc_dead:"))
@require_admin_cb
def cb_broadcast_dead(call: types.CallbackQuery):
    uid = call.from_user.id
    state = gs(uid)
    include_dead = call.data.split(":")[1] == "yes"
    bot.answer_callback_query(call.id, "⏳ Starting broadcast...")
    _run_broadcast(
        call.message.chat.id,
        state["bc_msg_id"],
        state["bc_chat_id"],
        state.get("bc_delay", 0),
        include_dead,
    )
    cs(uid)


def _run_broadcast(chat_id: int, src_msg_id: int, src_chat_id: int, delay: float, include_dead: bool):
    if include_dead:
        users = db.get_non_blocked_users(DB_PATH)  # All users not blocked (includes inactive)
    else:
        users = db.get_active_users(DB_PATH)

    total = len(users)
    success = failed = blocked_count = 0
    status_msg = bot.send_message(chat_id, f"📤 Sending to {total} users...")

    for i, row in enumerate(users):
        try:
            bot.copy_message(row["user_id"], src_chat_id, src_msg_id)
            success += 1
        except Exception as e:
            err = str(e).lower()
            if any(x in err for x in ("blocked", "deactivated", "kicked", "not found", "forbidden")):
                db.set_user_inactive(DB_PATH, row["user_id"])
                blocked_count += 1
            else:
                failed += 1
        if delay > 0:
            time.sleep(delay)
        if (i + 1) % 50 == 0:
            try:
                bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text=f"📤 {i+1}/{total}...")
            except Exception:
                pass

    db.log_broadcast(DB_PATH, total, success, failed, blocked_count)
    try:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_msg.message_id,
            text=(
                f"✅ <b>Broadcast Complete!</b>\n\n"
                f"📊 Total: <b>{total}</b>\n"
                f"✅ Sent: <b>{success}</b>\n"
                f"❌ Failed: <b>{failed}</b>\n"
                f"🚫 Blocked/Inactive: <b>{blocked_count}</b>"
            ),
        )
    except Exception:
        pass


# ─── Admin: Total Users ───────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "👥 Total Users")
@require_admin
def menu_total_users(message: types.Message):
    c = db.count_users(DB_PATH)
    bot.send_message(
        message.chat.id,
        f"👥 <b>User Statistics</b>\n\n"
        f"📊 Total: <b>{c['total']}</b>\n"
        f"✅ Active: <b>{c['active']}</b>\n"
        f"⚪ Inactive: <b>{c['inactive']}</b>\n"
        f"🚫 Blocked: <b>{c['blocked']}</b>",
        reply_markup=child_admin_menu(),
    )


# ─── Admin: Block/Unblock ─────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "🚫 Block/Unblock User")
@require_admin
def menu_block(message: types.Message):
    ss(message.from_user.id, action="block_user")
    bot.send_message(message.chat.id, "🚫 <b>Block / Unblock User</b>\n\nSend the <b>User ID</b>:", reply_markup=cancel_keyboard())


@bot.message_handler(func=lambda m: gs(m.from_user.id).get("action") == "block_user")
@require_admin
def handle_block(message: types.Message):
    uid = message.from_user.id
    if message.text == "❌ Cancel":
        cs(uid)
        bot.send_message(message.chat.id, "❌ Cancelled.", reply_markup=child_admin_menu())
        return
    try:
        tid = int(message.text.strip())
    except ValueError:
        bot.reply_to(message, "⚠️ Send a valid numeric User ID.")
        return
    row = db.get_user(DB_PATH, tid)
    if not row:
        cs(uid)
        bot.send_message(message.chat.id, f"❌ User <code>{tid}</code> not found.", reply_markup=child_admin_menu())
        return
    new_blocked = not bool(row["is_blocked"])
    db.set_user_blocked(DB_PATH, tid, new_blocked)
    cs(uid)
    action_text = "🚫 Blocked" if new_blocked else "✅ Unblocked"
    bot.send_message(
        message.chat.id,
        f"{action_text} user <code>{tid}</code> (<b>{row['full_name']}</b>).",
        reply_markup=child_admin_menu(),
    )


# ─── Admin: Channel Links ─────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "🔗 Channel Links")
@require_admin
def menu_channels(message: types.Message):
    _show_channel_manage(message.chat.id)


def _show_channel_manage(chat_id: int, msg_id: int = None):
    channels = db.list_channels(DB_PATH)
    text = (
        "🔗 <b>Channel Management</b>\n\n"
        "📢 <b>🔴 Mandatory</b> — bot checks user has joined before granting access.\n"
        "   (Bot must be an <b>admin</b> in the channel to verify membership.)\n\n"
        "🔔 <b>🟢 Optional</b> — shown but not required. Verify always passes.\n\n"
        "Click a channel row to toggle Mandatory/Optional.\n"
        "Click 🗑 Remove to delete it."
    )
    kb = channel_manage_inline(channels)
    if msg_id:
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            bot.send_message(chat_id, text, reply_markup=kb)
    else:
        bot.send_message(chat_id, text, reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data == "add_channel")
@require_admin_cb
def cb_add_channel(call: types.CallbackQuery):
    ss(call.from_user.id, action="add_channel")
    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        "➕ <b>Add Channel</b>\n\n"
        "Format: <code>Title | https://t.me/username</code>\n\n"
        "Example: <code>My Channel | https://t.me/mychannel</code>\n\n"
        "💡 To verify membership, add this bot as <b>Admin</b> in the channel.",
        reply_markup=cancel_keyboard(),
    )


@bot.message_handler(func=lambda m: gs(m.from_user.id).get("action") == "add_channel")
@require_admin
def handle_add_channel(message: types.Message):
    uid = message.from_user.id
    if message.text == "❌ Cancel":
        cs(uid)
        bot.send_message(message.chat.id, "❌ Cancelled.", reply_markup=child_admin_menu())
        return
    if "|" not in message.text:
        bot.reply_to(message, "⚠️ Format: <code>Title | https://t.me/username</code>")
        return
    parts = message.text.split("|", 1)
    title = parts[0].strip()
    link = parts[1].strip()
    if not (link.startswith("https://t.me/") or link.startswith("http")):
        bot.reply_to(message, "⚠️ Use a full https:// link.")
        return
    channel_id = "@" + link.rstrip("/").split("/")[-1].split("?")[0]
    if db.add_channel(DB_PATH, channel_id, title, link, is_mandatory=True):
        cs(uid)
        bot.send_message(
            message.chat.id,
            f"✅ Channel <b>{title}</b> added!\n\n"
            "Default: <b>Mandatory</b>. Toggle in <b>🔗 Channel Links</b>.\n"
            "⚠️ Make this bot an admin in the channel to verify memberships.",
            reply_markup=child_admin_menu(),
        )
    else:
        cs(uid)
        bot.send_message(message.chat.id, "⚠️ Channel already exists.", reply_markup=child_admin_menu())


@bot.callback_query_handler(func=lambda c: c.data.startswith("ch_toggle:"))
@require_admin_cb
def cb_toggle_mandatory(call: types.CallbackQuery):
    channel_id = call.data.split(":", 1)[1]
    new_val = db.toggle_channel_mandatory(DB_PATH, channel_id)
    if new_val is None:
        bot.answer_callback_query(call.id, "Not found.")
        return
    status = "🔴 Mandatory" if new_val else "🟢 Optional"
    bot.answer_callback_query(call.id, f"✅ Toggled to {status}")
    _show_channel_manage(call.message.chat.id, call.message.message_id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("ch_remove:"))
@require_admin_cb
def cb_remove_channel(call: types.CallbackQuery):
    channel_id = call.data.split(":", 1)[1]
    if db.remove_channel(DB_PATH, channel_id):
        bot.answer_callback_query(call.id, "✅ Removed.")
        _show_channel_manage(call.message.chat.id, call.message.message_id)
    else:
        bot.answer_callback_query(call.id, "Not found.")


# ─── Admin: Manage Admins ─────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "👮 Manage Admins")
@require_admin
def menu_manage_admins(message: types.Message):
    _show_admins(message.chat.id)


def _show_admins(chat_id: int, msg_id: int = None):
    admins = db.list_child_admins(DB_PATH)
    lines = ["👮 <b>Child Bot Admins</b>\n"]
    if admins:
        for a in admins:
            uname = f"@{a['username']}" if a["username"] else "No username"
            lines.append(f"• {a['full_name']} ({uname}) — <code>{a['user_id']}</code>")
    else:
        lines.append("No extra admins added yet.")
    lines.append("\n<i>Admin Bot admins are also admins here automatically.</i>")
    text = "\n".join(lines)
    kb = child_admins_manage_inline(admins)
    if msg_id:
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            bot.send_message(chat_id, text, reply_markup=kb)
    else:
        bot.send_message(chat_id, text, reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data == "add_cadmin_prompt")
@require_admin_cb
def cb_add_cadmin_prompt(call: types.CallbackQuery):
    ss(call.from_user.id, action="add_cadmin")
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "➕ Send the <b>User ID</b> to make admin:", reply_markup=cancel_keyboard())


@bot.message_handler(func=lambda m: gs(m.from_user.id).get("action") == "add_cadmin")
@require_admin
def handle_add_cadmin(message: types.Message):
    uid = message.from_user.id
    if message.text == "❌ Cancel":
        cs(uid)
        bot.send_message(message.chat.id, "❌ Cancelled.", reply_markup=child_admin_menu())
        return
    try:
        new_id = int(message.text.strip())
    except ValueError:
        bot.reply_to(message, "⚠️ Send a numeric User ID.")
        return
    db.add_child_admin(DB_PATH, new_id, None, f"Admin {new_id}")
    cs(uid)
    bot.send_message(message.chat.id, f"✅ User <code>{new_id}</code> is now an admin.", reply_markup=child_admin_menu())


@bot.callback_query_handler(func=lambda c: c.data.startswith("rm_cadmin:"))
@require_admin_cb
def cb_remove_cadmin(call: types.CallbackQuery):
    target = int(call.data.split(":")[1])
    if db.remove_child_admin(DB_PATH, target):
        bot.answer_callback_query(call.id, "✅ Removed.")
        _show_admins(call.message.chat.id, call.message.message_id)
    else:
        bot.answer_callback_query(call.id, "Not found.")


# ─── Admin: Requests ─────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "📬 Admin Requests")
@require_admin
def menu_admin_requests(message: types.Message):
    reqs = db.get_pending_requests(DB_PATH)
    if not reqs:
        bot.send_message(message.chat.id, "📭 No pending requests.", reply_markup=child_admin_menu())
        return
    for req in reqs:
        uname = f"@{req['username']}" if req["username"] else "No username"
        bot.send_message(
            message.chat.id,
            f"🙋 <b>Admin Request</b>\n\n"
            f"👤 {req['full_name']} ({uname})\n"
            f"🆔 <code>{req['user_id']}</code>\n"
            f"📅 {ts_to_human(req['requested_at'])}",
            reply_markup=admin_request_inline(req["user_id"]),
        )


@bot.callback_query_handler(func=lambda c: c.data.startswith("approve_req:"))
@require_admin_cb
def cb_approve_req(call: types.CallbackQuery):
    uid = int(call.data.split(":")[1])
    reqs = db.get_pending_requests(DB_PATH)
    req = next((r for r in reqs if r["user_id"] == uid), None)
    if not req:
        bot.answer_callback_query(call.id, "Not found.")
        return
    db.add_child_admin(DB_PATH, uid, req["username"], req["full_name"])
    db.resolve_request(DB_PATH, uid, "approved")
    bot.edit_message_text(
        call.message.chat.id, call.message.message_id,
        text=f"✅ <b>{req['full_name']}</b> approved as admin.", parse_mode="HTML",
    )
    try:
        bot.send_message(uid, "🎉 <b>Admin request approved!</b>\n\nSend /start to access the admin panel.")
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("deny_req:"))
@require_admin_cb
def cb_deny_req(call: types.CallbackQuery):
    uid = int(call.data.split(":")[1])
    db.resolve_request(DB_PATH, uid, "denied")
    bot.edit_message_text(
        call.message.chat.id, call.message.message_id,
        text=f"❌ Request from <code>{uid}</code> denied.", parse_mode="HTML",
    )
    try:
        bot.send_message(uid, "❌ Your admin access request was denied.")
    except Exception:
        pass


# ─── Admin: Back to User Menu ─────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "🔙 Back to User Menu")
def menu_back(message: types.Message):
    register(message)
    cs(message.from_user.id)
    bot.send_message(message.chat.id, "🏠 Main Menu", reply_markup=child_main_menu())


# ─── Cancel ───────────────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "❌ Cancel")
def handle_cancel(message: types.Message):
    uid = message.from_user.id
    cs(uid)
    bot.send_message(message.chat.id, "❌ Cancelled.", reply_markup=get_menu(uid))


# ─── Noop ─────────────────────────────────────────────────────────────────────

@bot.callback_query_handler(func=lambda c: c.data == "noop")
def cb_noop(call): bot.answer_callback_query(call.id)


# ─── Fallback: forward any message to admins ─────────────────────────────────

_skip_texts = {
    "📨 Message Admin", "🔗 Join Channel", "🙋 Request Admin", "❌ Cancel",
    "📝 Set Start Message", "📢 Broadcast", "👥 Total Users",
    "🚫 Block/Unblock User", "🔗 Channel Links", "👮 Manage Admins",
    "📬 Admin Requests", "🔙 Back to User Menu",
}


@bot.message_handler(
    content_types=["text", "photo", "video", "document", "audio", "sticker", "voice", "animation"],
    func=lambda m: (
        not gs(m.from_user.id) and
        not _is_admin(m.from_user.id) and
        (m.content_type != "text" or m.text not in _skip_texts)
    ),
)
def handle_fallback(message: types.Message):
    uid = message.from_user.id
    register(message)
    u = db.get_user(DB_PATH, uid)
    if u and u["is_blocked"]:
        return
    uname = f"@{message.from_user.username}" if message.from_user.username else "No username"
    header = (
        f"📨 <b>User Message</b>\n"
        f"👤 {_full_name(message.from_user)} ({uname})\n"
        f"🆔 <code>{uid}</code>\n"
        f"─────────────────"
    )
    for aid in db.get_admin_ids():
        try:
            bot.send_message(aid, header, reply_markup=reply_to_user_inline(uid))
            bot.forward_message(aid, message.chat.id, message.message_id)
        except Exception:
            pass


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    db.init_child_db(DB_PATH)
    logger.info("Child Bot started. Username: @%s", CHILD_USERNAME)
    bot.infinity_polling(skip_pending=True, logger_level=logging.WARNING)


if __name__ == "__main__":
    main()
