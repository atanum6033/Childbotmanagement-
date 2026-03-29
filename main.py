"""
Admin Bot — Main entry point.
Manages multiple child bots, admins, backups, and routing.
"""

import os
import sys
import logging
import asyncio
import subprocess
import zipfile
import shutil
import tempfile
from pathlib import Path

import telebot
from telebot import types

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared import database as db
from shared.keyboards import (
    admin_main_menu, cancel_keyboard, confirm_keyboard,
    build_bot_list_inline, confirm_delete_inline, toggle_bot_inline,
    child_bot_select_inline, admin_manage_inline,
)
from shared.utils import paginate, ts_to_human, human_status, format_bot_info

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

ADMIN_TOKEN = os.environ.get("ADMIN_BOT_TOKEN", "")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))

if not ADMIN_TOKEN:
    raise RuntimeError("ADMIN_BOT_TOKEN env var is required")
if not OWNER_ID:
    raise RuntimeError("OWNER_ID env var is required")

bot = telebot.TeleBot(ADMIN_TOKEN, parse_mode="HTML")

# In-memory process registry for child bots
child_processes: dict[int, subprocess.Popen] = {}

# State tracking for conversations
user_states: dict[int, dict] = {}


def get_state(user_id: int) -> dict:
    return user_states.get(user_id, {})


def set_state(user_id: int, **kwargs):
    user_states[user_id] = kwargs


def clear_state(user_id: int):
    user_states.pop(user_id, None)


def require_admin(func):
    def wrapper(message, *args, **kwargs):
        uid = message.from_user.id
        if not db.is_admin(uid):
            bot.reply_to(message, "⛔ Access denied. You are not an admin.")
            return
        return func(message, *args, **kwargs)
    return wrapper


def require_admin_callback(func):
    def wrapper(call, *args, **kwargs):
        uid = call.from_user.id
        if not db.is_admin(uid):
            bot.answer_callback_query(call.id, "⛔ Access denied.")
            return
        return func(call, *args, **kwargs)
    return wrapper


# ─── Start ───────────────────────────────────────────────────────────────────

@bot.message_handler(commands=["start"])
def cmd_start(message: types.Message):
    uid = message.from_user.id
    if not db.is_admin(uid):
        bot.reply_to(message, "⛔ You are not authorized to use this bot.")
        return
    clear_state(uid)
    bot.send_message(
        message.chat.id,
        f"👋 Welcome to <b>Admin Bot</b>, {message.from_user.first_name}!\n\n"
        "Use the menu buttons below to manage your child bots.",
        reply_markup=admin_main_menu(),
    )


# ─── Add Child Bot ────────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "➕ Add Child Bot")
@require_admin
def menu_add_bot(message: types.Message):
    set_state(message.from_user.id, action="add_bot")
    bot.send_message(
        message.chat.id,
        "🤖 <b>Add a New Child Bot</b>\n\n"
        "Please send the bot token from @BotFather.\n\n"
        "<i>Example: <code>123456789:AABBCCaabbcc...</code></i>",
        reply_markup=cancel_keyboard(),
    )


@bot.message_handler(func=lambda m: get_state(m.from_user.id).get("action") == "add_bot")
@require_admin
def handle_add_bot_token(message: types.Message):
    if message.text == "❌ Cancel":
        clear_state(message.from_user.id)
        bot.send_message(message.chat.id, "❌ Cancelled.", reply_markup=admin_main_menu())
        return

    token = message.text.strip()
    if ":" not in token or len(token.split(":")[0]) < 5:
        bot.reply_to(message, "⚠️ Invalid token format. Please try again.")
        return

    # Validate token via Telegram API
    try:
        import requests
        resp = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
        data = resp.json()
        if not data.get("ok"):
            bot.reply_to(message, f"❌ Invalid token: {data.get('description', 'Unknown error')}")
            return
        bot_info = data["result"]
        bot_username = bot_info["username"]
        bot_name = bot_info.get("first_name", bot_username)
    except Exception as e:
        bot.reply_to(message, f"❌ Error validating token: {e}")
        return

    uid = message.from_user.id
    if db.add_child_bot(token, bot_username, bot_name, uid):
        bot_row = db.get_child_bot_by_token(token)
        # Initialize the child bot's database
        child_db_path = db.get_child_db_path(bot_username)
        db.init_child_db(child_db_path)
        clear_state(uid)
        bot.send_message(
            message.chat.id,
            f"✅ <b>Bot Added Successfully!</b>\n\n"
            f"🤖 <b>Name:</b> {bot_name}\n"
            f"🔗 <b>Username:</b> @{bot_username}\n"
            f"🆔 <b>Bot ID:</b> <code>{bot_row['id']}</code>\n\n"
            "You can now start it using <b>▶️ Stop/Run Bot</b>.",
            reply_markup=admin_main_menu(),
        )
    else:
        bot.send_message(
            message.chat.id,
            f"⚠️ Bot @{bot_username} is already added.",
            reply_markup=admin_main_menu(),
        )
        clear_state(uid)


# ─── Remove Child Bot ─────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "➖ Remove Child Bot")
@require_admin
def menu_remove_bot(message: types.Message):
    bots = db.list_child_bots()
    if not bots:
        bot.send_message(message.chat.id, "📭 No child bots added yet.", reply_markup=admin_main_menu())
        return
    set_state(message.from_user.id, action="remove_bot", page=1)
    page_bots, total_pages = paginate(bots, 1)
    kb = build_bot_list_inline(page_bots, 1, total_pages, "remove_select")
    bot.send_message(
        message.chat.id,
        "🗑 <b>Remove Child Bot</b>\n\nSelect the bot to remove:",
        reply_markup=kb,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("remove_select:"))
@require_admin_callback
def cb_remove_select(call: types.CallbackQuery):
    bot_id = int(call.data.split(":")[1])
    bot_row = db.get_child_bot(bot_id)
    if not bot_row:
        bot.answer_callback_query(call.id, "Bot not found.")
        return
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"⚠️ Are you sure you want to delete <b>{bot_row['bot_name']}</b> (@{bot_row['bot_username']})?\n\n"
             "This will remove it from the system. <b>This cannot be undone.</b>",
        reply_markup=confirm_delete_inline(bot_id),
        parse_mode="HTML",
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("confirm_delete:"))
@require_admin_callback
def cb_confirm_delete(call: types.CallbackQuery):
    bot_id = int(call.data.split(":")[1])
    bot_row = db.get_child_bot(bot_id)
    if not bot_row:
        bot.answer_callback_query(call.id, "Bot not found.")
        return
    # Stop running process
    if bot_id in child_processes:
        try:
            child_processes[bot_id].terminate()
            del child_processes[bot_id]
        except Exception:
            pass
    db.remove_child_bot(bot_id)
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"✅ Bot <b>{bot_row['bot_name']}</b> (@{bot_row['bot_username']}) has been deleted.",
        parse_mode="HTML",
    )


@bot.callback_query_handler(func=lambda c: c.data == "cancel_delete")
def cb_cancel_delete(call: types.CallbackQuery):
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text="❌ Deletion cancelled.",
    )


# ─── List Child Bots ──────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "📋 List Child Bots")
@require_admin
def menu_list_bots(message: types.Message):
    bots = db.list_child_bots()
    if not bots:
        bot.send_message(message.chat.id, "📭 No child bots added yet.", reply_markup=admin_main_menu())
        return
    page = 1
    page_bots, total_pages = paginate(bots, page)
    text = _build_bot_list_text(page_bots, page, total_pages, len(bots))
    kb = _build_list_nav_inline(page, total_pages)
    bot.send_message(message.chat.id, text, reply_markup=kb)


def _build_bot_list_text(bots_page, page, total_pages, total_count) -> str:
    lines = [f"📋 <b>Child Bots</b> — Page {page}/{total_pages} (Total: {total_count})\n"]
    for i, b in enumerate(bots_page, 1):
        status = "🟢" if b["is_running"] else "🔴"
        lines.append(
            f"{i}. {status} <b>{b['bot_name']}</b>\n"
            f"   🔗 @{b['bot_username']}\n"
            f"   📅 Added: {ts_to_human(b['added_at'])}\n"
        )
    return "\n".join(lines)


def _build_list_nav_inline(page, total_pages) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=3)
    nav = []
    if page > 1:
        nav.append(types.InlineKeyboardButton("◀️", callback_data=f"list_page:{page-1}"))
    nav.append(types.InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav.append(types.InlineKeyboardButton("▶️", callback_data=f"list_page:{page+1}"))
    if nav:
        kb.row(*nav)
    return kb


@bot.callback_query_handler(func=lambda c: c.data.startswith("list_page:"))
@require_admin_callback
def cb_list_page(call: types.CallbackQuery):
    page = int(call.data.split(":")[1])
    bots = db.list_child_bots()
    page_bots, total_pages = paginate(bots, page)
    text = _build_bot_list_text(page_bots, page, total_pages, len(bots))
    kb = _build_list_nav_inline(page, total_pages)
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=text,
        reply_markup=kb,
        parse_mode="HTML",
    )


# ─── Stop/Run Child Bot ───────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "▶️ Stop/Run Bot")
@require_admin
def menu_toggle_bot(message: types.Message):
    bots = db.list_child_bots()
    if not bots:
        bot.send_message(message.chat.id, "📭 No child bots added yet.", reply_markup=admin_main_menu())
        return
    page_bots, total_pages = paginate(bots, 1)
    kb = build_bot_list_inline(page_bots, 1, total_pages, "toggle_select")
    bot.send_message(
        message.chat.id,
        "▶️ <b>Stop / Run Bot</b>\n\nSelect a bot to toggle:",
        reply_markup=kb,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("toggle_select:"))
@require_admin_callback
def cb_toggle_select(call: types.CallbackQuery):
    bot_id = int(call.data.split(":")[1])
    bot_row = db.get_child_bot(bot_id)
    if not bot_row:
        bot.answer_callback_query(call.id, "Bot not found.")
        return
    _toggle_bot(call, bot_id, bot_row)


@bot.callback_query_handler(func=lambda c: c.data.startswith("toggle_bot:"))
@require_admin_callback
def cb_toggle_bot(call: types.CallbackQuery):
    bot_id = int(call.data.split(":")[1])
    bot_row = db.get_child_bot(bot_id)
    if not bot_row:
        bot.answer_callback_query(call.id, "Bot not found.")
        return
    _toggle_bot(call, bot_id, bot_row)


def _toggle_bot(call: types.CallbackQuery, bot_id: int, bot_row):
    is_running = bool(bot_row["is_running"])
    if is_running:
        # Stop
        proc = child_processes.get(bot_id)
        if proc:
            proc.terminate()
            proc.wait(timeout=10)
            del child_processes[bot_id]
        db.set_child_bot_running(bot_id, False)
        bot.answer_callback_query(call.id, f"⏹ Stopped {bot_row['bot_name']}")
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"⏹ <b>{bot_row['bot_name']}</b> (@{bot_row['bot_username']}) has been <b>stopped</b>.",
            parse_mode="HTML",
        )
    else:
        # Start
        child_dir = Path(__file__).parent.parent / "child_bot"
        env = os.environ.copy()
        env["CHILD_BOT_TOKEN"] = bot_row["bot_token"]
        env["CHILD_BOT_USERNAME"] = bot_row["bot_username"]
        env["ADMIN_BOT_TOKEN"] = ADMIN_TOKEN
        env["ADMIN_BOT_OWNER_ID"] = str(OWNER_ID)
        try:
            proc = subprocess.Popen(
                [sys.executable, str(child_dir / "main.py")],
                env=env,
                cwd=str(child_dir),
            )
            child_processes[bot_id] = proc
            db.set_child_bot_running(bot_id, True)
            bot.answer_callback_query(call.id, f"▶️ Started {bot_row['bot_name']}")
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=f"▶️ <b>{bot_row['bot_name']}</b> (@{bot_row['bot_username']}) is now <b>running</b>.",
                parse_mode="HTML",
            )
        except Exception as e:
            bot.answer_callback_query(call.id, f"❌ Failed to start: {e}")
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=f"❌ Failed to start <b>{bot_row['bot_name']}</b>: {e}",
                parse_mode="HTML",
            )


# ─── Backup Database ──────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "💾 Backup Database")
@require_admin
def menu_backup(message: types.Message):
    data_dir = Path("data")
    if not data_dir.exists():
        bot.send_message(message.chat.id, "⚠️ No database files found.", reply_markup=admin_main_menu())
        return

    ts = __import__("time").strftime("%Y%m%d_%H%M%S")
    zip_path = Path(tempfile.gettempdir()) / f"backup_{ts}.zip"

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for db_file in data_dir.glob("*.db"):
            zf.write(db_file, db_file.name)

    with open(zip_path, "rb") as f:
        bot.send_document(
            message.chat.id,
            f,
            caption=f"💾 <b>Database Backup</b>\n📅 {ts}\n\n"
                    "Save this file. You can restore it with the <b>♻️ Restore Database</b> button.",
            visible_file_name=f"backup_{ts}.zip",
        )
    zip_path.unlink(missing_ok=True)


# ─── Restore Database ─────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "♻️ Restore Database")
@require_admin
def menu_restore(message: types.Message):
    set_state(message.from_user.id, action="restore_db")
    bot.send_message(
        message.chat.id,
        "♻️ <b>Restore Database</b>\n\n"
        "Please send or forward the backup ZIP file to restore.",
        reply_markup=cancel_keyboard(),
    )


@bot.message_handler(
    content_types=["document"],
    func=lambda m: get_state(m.from_user.id).get("action") == "restore_db",
)
@require_admin
def handle_restore_file(message: types.Message):
    uid = message.from_user.id
    doc = message.document
    if not doc.file_name.endswith(".zip"):
        bot.reply_to(message, "⚠️ Please send a .zip backup file.")
        return

    file_info = bot.get_file(doc.file_id)
    downloaded = bot.download_file(file_info.file_path)

    tmp_zip = Path(tempfile.gettempdir()) / doc.file_name
    tmp_zip.write_bytes(downloaded)

    data_dir = Path("data")
    data_dir.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(tmp_zip, "r") as zf:
            names = zf.namelist()
            zf.extractall(data_dir)
        tmp_zip.unlink(missing_ok=True)
        clear_state(uid)
        bot.send_message(
            message.chat.id,
            f"✅ <b>Database Restored!</b>\n\n"
            f"Files restored: {', '.join(names)}\n\n"
            "All child bot data has been restored. Please restart the bots.",
            reply_markup=admin_main_menu(),
        )
        # Re-init admin db
        db.init_admin_db()
    except Exception as e:
        bot.send_message(
            message.chat.id,
            f"❌ Restore failed: {e}",
            reply_markup=admin_main_menu(),
        )
        clear_state(uid)


# ─── Use Child Bot as Admin ───────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "🎛 Use Child Bot Admin")
@require_admin
def menu_use_child_admin(message: types.Message):
    bots = db.list_child_bots()
    if not bots:
        bot.send_message(message.chat.id, "📭 No child bots added yet.", reply_markup=admin_main_menu())
        return
    page_bots, total_pages = paginate(bots, 1)
    kb = build_bot_list_inline(page_bots, 1, total_pages, "open_child_admin")
    bot.send_message(
        message.chat.id,
        "🎛 <b>Use Child Bot Admin Panel</b>\n\nSelect a child bot:",
        reply_markup=kb,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("open_child_admin:"))
@require_admin_callback
def cb_open_child_admin(call: types.CallbackQuery):
    bot_id = int(call.data.split(":")[1])
    bot_row = db.get_child_bot(bot_id)
    if not bot_row:
        bot.answer_callback_query(call.id, "Bot not found.")
        return
    # Send instructions to use the child bot directly
    child_link = f"https://t.me/{bot_row['bot_username']}?start=adminpanel"
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"🎛 <b>{bot_row['bot_name']}</b> Admin Panel\n\n"
             f"Open the child bot and use its admin menu:\n"
             f"👉 <a href='{child_link}'>Open @{bot_row['bot_username']}</a>\n\n"
             "Your Telegram ID is pre-authorized as admin in this child bot.",
        parse_mode="HTML",
    )


# ─── Add/Remove Admin ─────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "👥 Add/Remove Admin")
@require_admin
def menu_manage_admins(message: types.Message):
    uid = message.from_user.id
    if not db.is_owner(uid):
        bot.send_message(message.chat.id, "⛔ Only the owner can manage admins.", reply_markup=admin_main_menu())
        return
    admins = db.list_admins()
    lines = ["👥 <b>Current Admins:</b>\n"]
    for a in admins:
        role = "👑 Owner" if a["is_owner"] else "🛡 Admin"
        uname = f"@{a['username']}" if a["username"] else "No username"
        lines.append(f"• {role} — {a['full_name']} ({uname}) [<code>{a['user_id']}</code>]")
    lines.append("\n➕ To <b>add</b> an admin, forward their message or send their User ID:")
    text = "\n".join(lines)

    kb = types.InlineKeyboardMarkup(row_width=1)
    for a in admins:
        if not a["is_owner"]:
            kb.add(types.InlineKeyboardButton(
                f"❌ Remove {a['full_name']}",
                callback_data=f"remove_admin:{a['user_id']}"
            ))
    kb.add(types.InlineKeyboardButton("➕ Add Admin by ID", callback_data="add_admin_prompt"))

    bot.send_message(message.chat.id, text, reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data == "add_admin_prompt")
@require_admin_callback
def cb_add_admin_prompt(call: types.CallbackQuery):
    set_state(call.from_user.id, action="add_admin")
    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        "➕ Send the <b>User ID</b> of the new admin:",
        reply_markup=cancel_keyboard(),
    )


@bot.message_handler(func=lambda m: get_state(m.from_user.id).get("action") == "add_admin")
@require_admin
def handle_add_admin(message: types.Message):
    uid = message.from_user.id
    if message.text == "❌ Cancel":
        clear_state(uid)
        bot.send_message(message.chat.id, "❌ Cancelled.", reply_markup=admin_main_menu())
        return
    try:
        new_admin_id = int(message.text.strip())
    except ValueError:
        bot.reply_to(message, "⚠️ Invalid ID. Send a numeric Telegram User ID.")
        return

    if db.is_admin(new_admin_id):
        bot.send_message(message.chat.id, "ℹ️ That user is already an admin.", reply_markup=admin_main_menu())
    else:
        db.add_admin(new_admin_id, None, f"Admin {new_admin_id}")
        bot.send_message(
            message.chat.id,
            f"✅ User <code>{new_admin_id}</code> added as admin.\n"
            "They can now use the Admin Bot.",
            reply_markup=admin_main_menu(),
        )
    clear_state(uid)


@bot.callback_query_handler(func=lambda c: c.data.startswith("remove_admin:"))
@require_admin_callback
def cb_remove_admin(call: types.CallbackQuery):
    if not db.is_owner(call.from_user.id):
        bot.answer_callback_query(call.id, "⛔ Only the owner can remove admins.")
        return
    target_id = int(call.data.split(":")[1])
    if db.remove_admin(target_id):
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"✅ Admin <code>{target_id}</code> has been removed.",
            parse_mode="HTML",
        )
    else:
        bot.answer_callback_query(call.id, "Cannot remove owner or user not found.")


# ─── Pagination handler for bot lists ────────────────────────────────────────

@bot.callback_query_handler(func=lambda c: c.data.startswith("page:"))
@require_admin_callback
def cb_generic_page(call: types.CallbackQuery):
    parts = call.data.split(":")
    action = parts[1]
    page = int(parts[2])
    bots = db.list_child_bots()
    page_bots, total_pages = paginate(bots, page)
    kb = build_bot_list_inline(page_bots, page, total_pages, action)
    bot.edit_message_reply_markup(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=kb,
    )


@bot.callback_query_handler(func=lambda c: c.data == "noop")
def cb_noop(call: types.CallbackQuery):
    bot.answer_callback_query(call.id)


# ─── Cancel handler ───────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "❌ Cancel")
def handle_cancel(message: types.Message):
    clear_state(message.from_user.id)
    bot.send_message(message.chat.id, "❌ Cancelled.", reply_markup=admin_main_menu())


# ─── Startup ──────────────────────────────────────────────────────────────────

def startup():
    db.init_admin_db()
    # Register owner as admin if not already
    if not db.is_admin(OWNER_ID):
        db.add_admin(OWNER_ID, None, "Owner", is_owner_flag=True)
    logger.info("Admin Bot started. Owner ID: %s", OWNER_ID)


def main():
    startup()
    logger.info("Admin Bot polling...")
    bot.infinity_polling(skip_pending=True, logger_level=logging.WARNING)


if __name__ == "__main__":
    main()
