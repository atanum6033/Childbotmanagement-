"""
Admin Bot — Manages multiple child bots, admins, backups.
"""

import os
import sys
import logging
import subprocess
import zipfile
import tempfile
from pathlib import Path

import telebot
from telebot import types

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from shared import database as db
from shared.keyboards import (
    admin_main_menu, cancel_keyboard, build_bot_list_inline,
    confirm_delete_inline, admin_manage_inline,
)
from shared.utils import paginate, ts_to_human

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

# In-memory process registry: bot_id -> Popen
child_processes: dict[int, subprocess.Popen] = {}

# Conversation states
user_states: dict[int, dict] = {}


def get_state(uid: int) -> dict:
    return user_states.get(uid, {})


def set_state(uid: int, **kwargs):
    user_states[uid] = kwargs


def clear_state(uid: int):
    user_states.pop(uid, None)


def require_admin(func):
    def wrapper(message, *args, **kwargs):
        if not db.is_admin(message.from_user.id):
            bot.reply_to(message, "⛔ Access denied.")
            return
        return func(message, *args, **kwargs)
    return wrapper


def require_admin_cb(func):
    def wrapper(call, *args, **kwargs):
        if not db.is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "⛔ Access denied.")
            return
        return func(call, *args, **kwargs)
    return wrapper


# ─── /start ──────────────────────────────────────────────────────────────────

@bot.message_handler(commands=["start"])
def cmd_start(message: types.Message):
    if not db.is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ You are not authorized.")
        return
    clear_state(message.from_user.id)
    bot.send_message(
        message.chat.id,
        f"👋 Welcome to <b>Admin Bot</b>, {message.from_user.first_name}!\n\nUse the menu below.",
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
        "Send the bot token from @BotFather.\n\n"
        "Example: <code>123456789:AABBCCaabbcc...</code>",
        reply_markup=cancel_keyboard(),
    )


@bot.message_handler(func=lambda m: get_state(m.from_user.id).get("action") == "add_bot")
@require_admin
def handle_add_bot_token(message: types.Message):
    uid = message.from_user.id
    if message.text == "❌ Cancel":
        clear_state(uid)
        bot.send_message(message.chat.id, "❌ Cancelled.", reply_markup=admin_main_menu())
        return

    token = message.text.strip()
    if ":" not in token or len(token.split(":")[0]) < 5:
        bot.reply_to(message, "⚠️ Invalid token format. Try again.")
        return

    try:
        import requests as req
        resp = req.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
        data = resp.json()
        if not data.get("ok"):
            bot.reply_to(message, f"❌ Invalid token: {data.get('description', 'Unknown error')}")
            return
        info = data["result"]
        bot_username = info["username"]
        bot_name = info.get("first_name", bot_username)
    except Exception as e:
        bot.reply_to(message, f"❌ Error validating token: {e}")
        return

    if db.add_child_bot(token, bot_username, bot_name, uid):
        child_db_path = db.get_child_db_path(bot_username)
        db.init_child_db(child_db_path)
        bot_row = db.get_child_bot_by_token(token)
        clear_state(uid)
        bot.send_message(
            message.chat.id,
            f"✅ <b>Bot Added!</b>\n\n"
            f"🤖 <b>Name:</b> {bot_name}\n"
            f"🔗 <b>Username:</b> @{bot_username}\n"
            f"🆔 <b>ID:</b> <code>{bot_row['id']}</code>\n\n"
            "Use <b>▶️ Stop/Run Bot</b> to start it.",
            reply_markup=admin_main_menu(),
        )
    else:
        clear_state(uid)
        bot.send_message(
            message.chat.id, f"⚠️ Bot @{bot_username} is already added.",
            reply_markup=admin_main_menu(),
        )


# ─── Remove Child Bot ─────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "➖ Remove Child Bot")
@require_admin
def menu_remove_bot(message: types.Message):
    bots = db.list_child_bots()
    if not bots:
        bot.send_message(message.chat.id, "📭 No child bots added yet.", reply_markup=admin_main_menu())
        return
    page_bots, total_pages = paginate(bots, 1)
    bot.send_message(
        message.chat.id, "🗑 <b>Remove Child Bot</b>\n\nSelect the bot to remove:",
        reply_markup=build_bot_list_inline(page_bots, 1, total_pages, "remove_select"),
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("remove_select:"))
@require_admin_cb
def cb_remove_select(call: types.CallbackQuery):
    bot_id = int(call.data.split(":")[1])
    bot_row = db.get_child_bot(bot_id)
    if not bot_row:
        bot.answer_callback_query(call.id, "Not found.")
        return
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"⚠️ Delete <b>{bot_row['bot_name']}</b> (@{bot_row['bot_username']})?\n\n<b>This cannot be undone.</b>",
        reply_markup=confirm_delete_inline(bot_id),
        parse_mode="HTML",
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("confirm_delete:"))
@require_admin_cb
def cb_confirm_delete(call: types.CallbackQuery):
    bot_id = int(call.data.split(":")[1])
    bot_row = db.get_child_bot(bot_id)
    if not bot_row:
        bot.answer_callback_query(call.id, "Not found.")
        return
    _stop_child(bot_id)
    db.remove_child_bot(bot_id)
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"✅ Bot <b>{bot_row['bot_name']}</b> deleted.",
        parse_mode="HTML",
    )


@bot.callback_query_handler(func=lambda c: c.data == "cancel_delete")
def cb_cancel_delete(call: types.CallbackQuery):
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text="❌ Cancelled.",
    )


# ─── List Child Bots ──────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "📋 List Child Bots")
@require_admin
def menu_list_bots(message: types.Message):
    bots = db.list_child_bots()
    if not bots:
        bot.send_message(message.chat.id, "📭 No child bots.", reply_markup=admin_main_menu())
        return
    _send_bot_list_page(message.chat.id, bots, 1)


def _send_bot_list_page(chat_id: int, bots: list, page: int, msg_id: int = None):
    page_bots, total_pages = paginate(bots, page)
    lines = [f"📋 <b>Child Bots</b> — Page {page}/{total_pages} (Total: {len(bots)})\n"]
    for i, b in enumerate(page_bots, 1):
        status = "🟢" if b["is_running"] else "🔴"
        child_db = db.get_child_db_path(b["bot_username"])
        try:
            counts = db.count_users(child_db)
            users = counts["total"]
        except Exception:
            users = 0
        lines.append(
            f"{i}. {status} <b>{b['bot_name']}</b>\n"
            f"   🔗 @{b['bot_username']}\n"
            f"   👥 Users: {users}\n"
            f"   📅 Added: {ts_to_human(b['added_at'])}\n"
        )
    text = "\n".join(lines)
    kb = types.InlineKeyboardMarkup(row_width=3)
    nav = []
    if page > 1:
        nav.append(types.InlineKeyboardButton("◀️", callback_data=f"list_page:{page-1}"))
    nav.append(types.InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav.append(types.InlineKeyboardButton("▶️", callback_data=f"list_page:{page+1}"))
    if nav:
        kb.row(*nav)
    if msg_id:
        bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text, reply_markup=kb, parse_mode="HTML")
    else:
        bot.send_message(chat_id, text, reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith("list_page:"))
@require_admin_cb
def cb_list_page(call: types.CallbackQuery):
    page = int(call.data.split(":")[1])
    bots = db.list_child_bots()
    _send_bot_list_page(call.message.chat.id, bots, page, call.message.message_id)


# ─── Stop/Run Child Bot ───────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "▶️ Stop/Run Bot")
@require_admin
def menu_toggle_bot(message: types.Message):
    bots = db.list_child_bots()
    if not bots:
        bot.send_message(message.chat.id, "📭 No child bots.", reply_markup=admin_main_menu())
        return
    page_bots, total_pages = paginate(bots, 1)
    bot.send_message(
        message.chat.id, "▶️ <b>Stop / Run Bot</b>\n\nSelect a bot:",
        reply_markup=build_bot_list_inline(page_bots, 1, total_pages, "toggle_select"),
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("toggle_select:"))
@require_admin_cb
def cb_toggle_select(call: types.CallbackQuery):
    bot_id = int(call.data.split(":")[1])
    bot_row = db.get_child_bot(bot_id)
    if not bot_row:
        bot.answer_callback_query(call.id, "Not found.")
        return
    _do_toggle(call, bot_id, bot_row)


def _do_toggle(call: types.CallbackQuery, bot_id: int, bot_row):
    if bot_row["is_running"]:
        _stop_child(bot_id)
        db.set_child_bot_running(bot_id, False)
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"⏹ <b>{bot_row['bot_name']}</b> stopped.",
            parse_mode="HTML",
        )
    else:
        ok, err = _start_child(bot_id, bot_row)
        if ok:
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=f"▶️ <b>{bot_row['bot_name']}</b> is now running.",
                parse_mode="HTML",
            )
        else:
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=f"❌ Failed to start <b>{bot_row['bot_name']}</b>: {err}",
                parse_mode="HTML",
            )


def _start_child(bot_id: int, bot_row) -> tuple[bool, str]:
    child_script = ROOT / "child_bot" / "main.py"
    env = os.environ.copy()
    env["CHILD_BOT_TOKEN"] = bot_row["bot_token"]
    env["CHILD_BOT_USERNAME"] = bot_row["bot_username"]
    env["ADMIN_BOT_TOKEN"] = ADMIN_TOKEN
    env["ADMIN_BOT_OWNER_ID"] = str(OWNER_ID)
    try:
        proc = subprocess.Popen(
            [sys.executable, str(child_script)],
            env=env,
            cwd=str(ROOT),
        )
        child_processes[bot_id] = proc
        db.set_child_bot_running(bot_id, True)
        return True, ""
    except Exception as e:
        return False, str(e)


def _stop_child(bot_id: int):
    proc = child_processes.pop(bot_id, None)
    if proc:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    db.set_child_bot_running(bot_id, False)


# ─── Total Users ──────────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "📊 Total Users")
@require_admin
def menu_total_users(message: types.Message):
    bots = db.list_child_bots()
    if not bots:
        bot.send_message(message.chat.id, "📭 No child bots added yet.", reply_markup=admin_main_menu())
        return

    lines = ["📊 <b>Total Users Across All Bots</b>\n"]
    grand_total = 0
    grand_active = 0

    for b in bots:
        child_db = db.get_child_db_path(b["bot_username"])
        try:
            db.init_child_db(child_db)
            counts = db.count_users(child_db)
        except Exception:
            counts = {"total": 0, "active": 0, "inactive": 0, "blocked": 0}

        status = "🟢" if b["is_running"] else "🔴"
        lines.append(
            f"{status} <b>{b['bot_name']}</b> (@{b['bot_username']})\n"
            f"   👥 Total: {counts['total']} | ✅ Active: {counts['active']} "
            f"| ⚪ Inactive: {counts['inactive']} | 🚫 Blocked: {counts['blocked']}\n"
        )
        grand_total += counts["total"]
        grand_active += counts["active"]

    lines.append(f"\n📈 <b>Grand Total: {grand_total} users | Active: {grand_active}</b>")
    bot.send_message(message.chat.id, "\n".join(lines), reply_markup=admin_main_menu())


# ─── Backup Database ──────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "💾 Backup Database")
@require_admin
def menu_backup(message: types.Message):
    data_dir = db.DATA_DIR
    db_files = list(data_dir.glob("*.db")) if data_dir.exists() else []

    if not db_files:
        bot.send_message(message.chat.id, "⚠️ No database files found.", reply_markup=admin_main_menu())
        return

    import time
    ts = time.strftime("%Y%m%d_%H%M%S")
    zip_path = Path(tempfile.gettempdir()) / f"backup_{ts}.zip"

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for db_file in db_files:
            zf.write(db_file, db_file.name)

    with open(zip_path, "rb") as f:
        bot.send_document(
            message.chat.id, f,
            caption=(
                f"💾 <b>Full Database Backup</b>\n"
                f"📅 {ts}\n"
                f"📦 Files: {len(db_files)} database(s)\n\n"
                "All users, settings, channels, and bot data are included.\n"
                "Restore with the <b>♻️ Restore Database</b> button."
            ),
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
        "Send or forward the backup ZIP file.\n\n"
        "⚠️ All currently running bots will be stopped, data restored, then <b>automatically restarted</b>.",
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

    status_msg = bot.send_message(message.chat.id, "⏳ Restoring database...")

    # Step 1: Stop all running bots
    running_bots = [b for b in db.list_child_bots() if b["is_running"]]
    for b in running_bots:
        _stop_child(b["id"])

    # Step 2: Extract zip
    file_info = bot.get_file(doc.file_id)
    downloaded = bot.download_file(file_info.file_path)
    tmp_zip = Path(tempfile.gettempdir()) / doc.file_name
    tmp_zip.write_bytes(downloaded)

    data_dir = db.DATA_DIR
    data_dir.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(tmp_zip, "r") as zf:
            names = zf.namelist()
            zf.extractall(data_dir)
        tmp_zip.unlink(missing_ok=True)
    except Exception as e:
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=status_msg.message_id,
            text=f"❌ Restore failed: {e}",
        )
        clear_state(uid)
        return

    # Step 3: Re-initialize admin DB
    db.init_admin_db()
    if not db.is_admin(OWNER_ID):
        db.add_admin(OWNER_ID, None, "Owner", is_owner_flag=True)

    # Step 4: Re-initialize all child DBs
    restored_bots = db.list_child_bots()
    for b in restored_bots:
        child_db_path = db.get_child_db_path(b["bot_username"])
        try:
            db.init_child_db(child_db_path)
        except Exception:
            pass

    # Step 5: Auto-restart all bots that were previously marked running
    restarted = []
    db.set_all_bots_stopped()
    for b in restored_bots:
        ok, err = _start_child(b["id"], b)
        if ok:
            restarted.append(f"▶️ @{b['bot_username']}")

    clear_state(uid)
    restarted_text = "\n".join(restarted) if restarted else "None"
    bot.edit_message_text(
        chat_id=message.chat.id,
        message_id=status_msg.message_id,
        text=(
            f"✅ <b>Database Restored!</b>\n\n"
            f"📦 Files restored: {', '.join(names)}\n\n"
            f"🤖 <b>Auto-restarted bots:</b>\n{restarted_text}\n\n"
            "All settings, users, and channels are back."
        ),
        parse_mode="HTML",
    )
    bot.send_message(message.chat.id, "Ready.", reply_markup=admin_main_menu())


# ─── Use Child Bot Admin ──────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "🎛 Use Child Bot Admin")
@require_admin
def menu_use_child_admin(message: types.Message):
    bots = db.list_child_bots()
    if not bots:
        bot.send_message(message.chat.id, "📭 No child bots.", reply_markup=admin_main_menu())
        return
    page_bots, total_pages = paginate(bots, 1)
    bot.send_message(
        message.chat.id, "🎛 <b>Open Child Bot Admin Panel</b>\n\nSelect a bot:",
        reply_markup=build_bot_list_inline(page_bots, 1, total_pages, "open_child_admin"),
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("open_child_admin:"))
@require_admin_cb
def cb_open_child_admin(call: types.CallbackQuery):
    bot_id = int(call.data.split(":")[1])
    bot_row = db.get_child_bot(bot_id)
    if not bot_row:
        bot.answer_callback_query(call.id, "Not found.")
        return
    link = f"https://t.me/{bot_row['bot_username']}?start=adminpanel"
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=(
            f"🎛 <b>{bot_row['bot_name']}</b> Admin Panel\n\n"
            f"👉 <a href='{link}'>Open @{bot_row['bot_username']}</a>\n\n"
            "Your ID is pre-authorized as admin in this bot."
        ),
        parse_mode="HTML",
    )


# ─── Add/Remove Admin ─────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "👥 Add/Remove Admin")
@require_admin
def menu_manage_admins(message: types.Message):
    if not db.is_owner(message.from_user.id):
        bot.send_message(message.chat.id, "⛔ Only the owner can manage admins.", reply_markup=admin_main_menu())
        return
    admins = db.list_admins()
    lines = ["👥 <b>Current Admins:</b>\n"]
    for a in admins:
        role = "👑 Owner" if a["is_owner"] else "🛡 Admin"
        uname = f"@{a['username']}" if a["username"] else "No username"
        lines.append(f"• {role} — {a['full_name']} ({uname}) [<code>{a['user_id']}</code>]")
    bot.send_message(
        message.chat.id, "\n".join(lines),
        reply_markup=admin_manage_inline(admins),
    )


@bot.callback_query_handler(func=lambda c: c.data == "add_admin_prompt")
@require_admin_cb
def cb_add_admin_prompt(call: types.CallbackQuery):
    if not db.is_owner(call.from_user.id):
        bot.answer_callback_query(call.id, "⛔ Only the owner.")
        return
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
        new_id = int(message.text.strip())
    except ValueError:
        bot.reply_to(message, "⚠️ Send a numeric User ID.")
        return
    if db.is_admin(new_id):
        bot.send_message(message.chat.id, "ℹ️ Already an admin.", reply_markup=admin_main_menu())
    else:
        db.add_admin(new_id, None, f"Admin {new_id}")
        bot.send_message(
            message.chat.id,
            f"✅ User <code>{new_id}</code> added as admin.",
            reply_markup=admin_main_menu(),
        )
    clear_state(uid)


@bot.callback_query_handler(func=lambda c: c.data.startswith("remove_admin:"))
@require_admin_cb
def cb_remove_admin(call: types.CallbackQuery):
    if not db.is_owner(call.from_user.id):
        bot.answer_callback_query(call.id, "⛔ Only the owner.")
        return
    target_id = int(call.data.split(":")[1])
    if db.remove_admin(target_id):
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"✅ Admin <code>{target_id}</code> removed.",
            parse_mode="HTML",
        )
    else:
        bot.answer_callback_query(call.id, "Cannot remove owner or not found.")


# ─── Pagination ───────────────────────────────────────────────────────────────

@bot.callback_query_handler(func=lambda c: c.data.startswith("page:"))
@require_admin_cb
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
def cb_noop(call): bot.answer_callback_query(call.id)


# ─── Cancel ───────────────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "❌ Cancel")
def handle_cancel(message: types.Message):
    clear_state(message.from_user.id)
    if db.is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "❌ Cancelled.", reply_markup=admin_main_menu())
    else:
        bot.send_message(message.chat.id, "❌ Cancelled.")


# ─── Startup ──────────────────────────────────────────────────────────────────

def startup():
    db.init_admin_db()
    if not db.is_admin(OWNER_ID):
        db.add_admin(OWNER_ID, None, "Owner", is_owner_flag=True)

    # Auto-start bots that were running before last shutdown
    bots = db.list_child_bots()
    db.set_all_bots_stopped()  # reset all flags first
    for b in bots:
        # Initialize child DB if missing
        child_db_path = db.get_child_db_path(b["bot_username"])
        try:
            db.init_child_db(child_db_path)
        except Exception:
            pass
    logger.info("Admin Bot ready. Owner: %s", OWNER_ID)


def main():
    startup()
    logger.info("Admin Bot polling...")
    bot.infinity_polling(skip_pending=True, logger_level=logging.WARNING)


if __name__ == "__main__":
    main()
