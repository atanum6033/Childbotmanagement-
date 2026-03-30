"""
Admin Bot — Manages child bots, admins, backups, database switching, CSV export.
"""

import csv
import io
import os
import sys
import logging
import subprocess
import zipfile
import tempfile
import datetime
from pathlib import Path

import telebot
from telebot import types

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from shared import active_db as db
from shared import db_config
from shared.keyboards import (
    admin_main_menu, cancel_keyboard, build_bot_list_inline,
    confirm_delete_inline, admin_manage_inline, db_switch_inline,
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
    raise RuntimeError("ADMIN_BOT_TOKEN is required")
if not OWNER_ID:
    raise RuntimeError("OWNER_ID is required")

bot = telebot.TeleBot(ADMIN_TOKEN, parse_mode="HTML")

child_processes: dict[int, subprocess.Popen] = {}
user_states: dict[int, dict] = {}


def gs(uid): return user_states.get(uid, {})
def ss(uid, **kw): user_states[uid] = kw
def cs(uid): user_states.pop(uid, None)


def require_admin(func):
    def wrapper(message, *a, **kw):
        if not db.is_admin(message.from_user.id):
            bot.reply_to(message, "⛔ Access denied.")
            return
        return func(message, *a, **kw)
    return wrapper


def require_owner(func):
    def wrapper(message, *a, **kw):
        if not db.is_owner(message.from_user.id):
            bot.reply_to(message, "⛔ Owner only.")
            return
        return func(message, *a, **kw)
    return wrapper


def require_admin_cb(func):
    def wrapper(call, *a, **kw):
        if not db.is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "⛔ Access denied.")
            return
        return func(call, *a, **kw)
    return wrapper


def require_owner_cb(func):
    def wrapper(call, *a, **kw):
        if not db.is_owner(call.from_user.id):
            bot.answer_callback_query(call.id, "⛔ Owner only.")
            return
        return func(call, *a, **kw)
    return wrapper


def _safe_count(bot_username: str) -> dict:
    """Safely get user counts for a child bot — init DB if needed."""
    cdb = db.get_child_db_path(bot_username)
    try:
        db.init_child_db(cdb)
        return db.count_users(cdb)
    except Exception:
        return {"total": 0, "active": 0, "inactive": 0, "blocked": 0}


# ─── /start ──────────────────────────────────────────────────────────────────

@bot.message_handler(commands=["start"])
def cmd_start(message: types.Message):
    if not db.is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ You are not authorized.")
        return
    cs(message.from_user.id)
    bot.send_message(
        message.chat.id,
        f"👋 Welcome to <b>Admin Bot</b>, {message.from_user.first_name}!\n\n"
        "Use the menu buttons below to manage your bots.",
        reply_markup=admin_main_menu(),
    )


# ─── Add Child Bot ────────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "➕ Add Child Bot")
@require_admin
def menu_add_bot(message: types.Message):
    ss(message.from_user.id, action="add_bot")
    bot.send_message(
        message.chat.id,
        "🤖 <b>Add a New Child Bot</b>\n\n"
        "Send the bot token from @BotFather.\n"
        "Example: <code>123456789:AABBCCaabbcc...</code>",
        reply_markup=cancel_keyboard(),
    )


@bot.message_handler(func=lambda m: gs(m.from_user.id).get("action") == "add_bot")
@require_admin
def handle_add_bot_token(message: types.Message):
    uid = message.from_user.id
    if message.text == "❌ Cancel":
        cs(uid)
        bot.send_message(message.chat.id, "❌ Cancelled.", reply_markup=admin_main_menu())
        return
    token = message.text.strip()
    if ":" not in token or len(token.split(":")[0]) < 5:
        bot.reply_to(message, "⚠️ Invalid token format.")
        return
    try:
        import requests as req
        r = req.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
        data = r.json()
        if not data.get("ok"):
            bot.reply_to(message, f"❌ {data.get('description', 'Invalid token')}")
            return
        info = data["result"]
        uname = info["username"]
        name = info.get("first_name", uname)
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")
        return

    if db.add_child_bot(token, uname, name, uid):
        cdb = db.get_child_db_path(uname)
        db.init_child_db(cdb)
        bot_row = db.get_child_bot_by_token(token)
        cs(uid)
        bot.send_message(
            message.chat.id,
            f"✅ <b>Bot Added!</b>\n\n"
            f"🤖 Name: <b>{name}</b>\n"
            f"🔗 Username: @{uname}\n"
            f"🆔 ID: <code>{bot_row['id'] if bot_row else '?'}</code>\n\n"
            "Use <b>▶️ Stop/Run Bot</b> to start it.",
            reply_markup=admin_main_menu(),
        )
    else:
        bot.send_message(message.chat.id, f"⚠️ @{uname} is already added.", reply_markup=admin_main_menu())
    cs(uid)


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
        message.chat.id, "🗑 <b>Remove Child Bot</b>\n\nSelect bot:",
        reply_markup=build_bot_list_inline(page_bots, 1, total_pages, "remove_select"),
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("remove_select:"))
@require_admin_cb
def cb_remove_select(call: types.CallbackQuery):
    bot_id = int(call.data.split(":")[1])
    row = db.get_child_bot(bot_id)
    if not row:
        bot.answer_callback_query(call.id, "Not found.")
        return
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"⚠️ Delete <b>{row['bot_name']}</b> (@{row['bot_username']})?\n<b>This cannot be undone.</b>",
        reply_markup=confirm_delete_inline(bot_id),
        parse_mode="HTML",
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("confirm_delete:"))
@require_admin_cb
def cb_confirm_delete(call: types.CallbackQuery):
    bot_id = int(call.data.split(":")[1])
    row = db.get_child_bot(bot_id)
    if not row:
        bot.answer_callback_query(call.id, "Not found.")
        return
    _stop_child(bot_id)
    db.remove_child_bot(bot_id)
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"✅ <b>{row['bot_name']}</b> deleted.",
        parse_mode="HTML",
    )


@bot.callback_query_handler(func=lambda c: c.data == "cancel_delete")
def cb_cancel_delete(call: types.CallbackQuery):
    bot.edit_message_text(call.message.chat.id, call.message.message_id, text="❌ Cancelled.")


# ─── List Child Bots ──────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "📋 List Child Bots")
@require_admin
def menu_list_bots(message: types.Message):
    bots = db.list_child_bots()
    if not bots:
        bot.send_message(message.chat.id, "📭 No child bots.", reply_markup=admin_main_menu())
        return
    _send_bot_list(message.chat.id, bots, 1)


def _send_bot_list(chat_id, bots, page, msg_id=None):
    page_bots, total_pages = paginate(bots, page)
    lines = [f"📋 <b>Child Bots</b> — Page {page}/{total_pages} (Total: {len(bots)})\n"]
    for i, b in enumerate(page_bots, 1):
        icon = "🟢" if b["is_running"] else "🔴"
        # Always init child DB before reading user counts to get live data
        c = _safe_count(b["bot_username"])
        lines.append(
            f"{i}. {icon} <b>{b['bot_name']}</b> (@{b['bot_username']})\n"
            f"   👥 Total: {c['total']} | ✅ Active: {c['active']}"
            f" | ⚪ Inactive: {c['inactive']} | 🚫 Blocked: {c['blocked']}\n"
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
    _send_bot_list(call.message.chat.id, db.list_child_bots(), page, call.message.message_id)


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
    row = db.get_child_bot(bot_id)
    if not row:
        bot.answer_callback_query(call.id, "Not found.")
        return
    if row["is_running"]:
        _stop_child(bot_id)
        bot.edit_message_text(
            call.message.chat.id, call.message.message_id,
            text=f"⏹ <b>{row['bot_name']}</b> stopped.", parse_mode="HTML",
        )
    else:
        ok, err = _start_child(bot_id, row)
        text = f"▶️ <b>{row['bot_name']}</b> is now running." if ok else f"❌ Failed: {err}"
        bot.edit_message_text(call.message.chat.id, call.message.message_id, text=text, parse_mode="HTML")


def _start_child(bot_id: int, row) -> tuple[bool, str]:
    script = ROOT / "child_bot" / "main.py"
    env = os.environ.copy()
    env["CHILD_BOT_TOKEN"] = row["bot_token"]
    env["CHILD_BOT_USERNAME"] = row["bot_username"]
    env["ADMIN_BOT_TOKEN"] = ADMIN_TOKEN
    env["ADMIN_BOT_OWNER_ID"] = str(OWNER_ID)
    try:
        proc = subprocess.Popen([sys.executable, str(script)], env=env, cwd=str(ROOT))
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
        bot.send_message(message.chat.id, "📭 No child bots.", reply_markup=admin_main_menu())
        return
    lines = ["📊 <b>User Statistics — All Child Bots</b>\n"]
    grand_total = grand_active = grand_inactive = grand_blocked = 0
    for b in bots:
        c = _safe_count(b["bot_username"])
        icon = "🟢" if b["is_running"] else "🔴"
        lines.append(
            f"{icon} <b>{b['bot_name']}</b> (@{b['bot_username']})\n"
            f"   👥 Total: <b>{c['total']}</b> | ✅ Active: {c['active']}"
            f" | ⚪ Inactive: {c['inactive']} | 🚫 Blocked: {c['blocked']}\n"
        )
        grand_total += c["total"]
        grand_active += c["active"]
        grand_inactive += c["inactive"]
        grand_blocked += c["blocked"]
    lines.append(
        "─────────────────\n"
        f"📈 <b>Grand Total: {grand_total}</b>\n"
        f"   ✅ Active: {grand_active} | ⚪ Inactive: {grand_inactive} | 🚫 Blocked: {grand_blocked}"
    )
    bot.send_message(message.chat.id, "\n".join(lines), reply_markup=admin_main_menu())


# ─── 📥 Download Users CSV ────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "📥 Download Users CSV")
@require_admin
def menu_download_csv(message: types.Message):
    bots = db.list_child_bots()
    if not bots:
        bot.send_message(message.chat.id, "📭 No child bots to export.", reply_markup=admin_main_menu())
        return

    status_msg = bot.send_message(message.chat.id, "⏳ Generating CSV file...")

    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    writer.writerow([f"Bot Data Export — Generated: {now_str}"])
    writer.writerow([])

    summary_rows = []
    grand_total = 0

    def ts_fmt(ts) -> str:
        if not ts:
            return ""
        try:
            return datetime.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return str(ts)

    for b in bots:
        cdb = db.get_child_db_path(b["bot_username"])
        try:
            db.init_child_db(cdb)
            users = db.get_all_users_export(cdb)
            count = len(users)
        except Exception:
            users = []
            count = 0

        writer.writerow([f"=== Bot: {b['bot_name']} (@{b['bot_username']}) ==="])
        writer.writerow([f"Total Users: {count}"])
        writer.writerow([
            "User ID", "Username", "Full Name",
            "Status", "Blocked", "Joined Date", "Last Seen",
        ])

        for u in users:
            status = "Active" if u["is_active"] and not u["is_blocked"] else (
                "Blocked" if u["is_blocked"] else "Inactive"
            )
            writer.writerow([
                u["user_id"],
                u["username"] or "",
                u["full_name"],
                status,
                "Yes" if u["is_blocked"] else "No",
                ts_fmt(u["joined_at"]),
                ts_fmt(u["last_seen"]),
            ])

        writer.writerow([])  # blank separator
        summary_rows.append((b["bot_name"], b["bot_username"], count))
        grand_total += count

    # Summary section at the end
    writer.writerow(["=== SUMMARY ==="])
    writer.writerow(["Bot Name", "Bot Username", "Total Users"])
    for bname, buname, cnt in summary_rows:
        writer.writerow([bname, f"@{buname}", cnt])
    writer.writerow(["GRAND TOTAL", "", grand_total])

    csv_bytes = output.getvalue().encode("utf-8-sig")  # utf-8-sig for Excel compatibility
    ts_file = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"bot_users_{ts_file}.csv"

    bot.delete_message(message.chat.id, status_msg.message_id)
    bot.send_document(
        message.chat.id,
        (filename, io.BytesIO(csv_bytes)),
        caption=(
            f"📥 <b>User Data Export</b>\n"
            f"📅 {now_str}\n"
            f"🤖 Bots: {len(bots)}\n"
            f"👥 Grand Total: <b>{grand_total}</b> users\n\n"
            "Open with Excel or Google Sheets."
        ),
        visible_file_name=filename,
    )


# ─── Backup ───────────────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "💾 Backup Database")
@require_admin
def menu_backup(message: types.Message):
    data_dir = db.DATA_DIR
    db_files = list(data_dir.glob("*.db")) if data_dir.exists() else []
    cfg_file = data_dir / "db_config.json"

    if not db_files and not cfg_file.exists():
        bot.send_message(message.chat.id, "⚠️ No data to backup.", reply_markup=admin_main_menu())
        return

    import time as _t
    ts = _t.strftime("%Y%m%d_%H%M%S")
    zip_path = Path(tempfile.gettempdir()) / f"backup_{ts}.zip"

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in db_files:
            zf.write(f, f.name)
        if cfg_file.exists():
            zf.write(cfg_file, "db_config.json")

    with open(zip_path, "rb") as f:
        bot.send_document(
            message.chat.id, f,
            caption=(
                f"💾 <b>Full Database Backup</b>\n"
                f"📅 {ts}\n"
                f"📦 {len(db_files)} database file(s) + config\n\n"
                "Restore with <b>♻️ Restore Database</b>."
            ),
            visible_file_name=f"backup_{ts}.zip",
        )
    zip_path.unlink(missing_ok=True)


# ─── Restore ──────────────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "♻️ Restore Database")
@require_admin
def menu_restore(message: types.Message):
    ss(message.from_user.id, action="restore_db")
    bot.send_message(
        message.chat.id,
        "♻️ <b>Restore Database</b>\n\n"
        "Send or forward the backup ZIP file.\n\n"
        "⚠️ All running bots will stop, data restored, then <b>auto-restarted</b>.",
        reply_markup=cancel_keyboard(),
    )


@bot.message_handler(
    content_types=["document"],
    func=lambda m: gs(m.from_user.id).get("action") == "restore_db",
)
@require_admin
def handle_restore_file(message: types.Message):
    uid = message.from_user.id
    doc = message.document
    if not doc.file_name.endswith(".zip"):
        bot.reply_to(message, "⚠️ Send a .zip backup file.")
        return

    status_msg = bot.send_message(message.chat.id, "⏳ Stopping bots and restoring...")

    for b in db.list_child_bots():
        if b["is_running"]:
            _stop_child(b["id"])

    raw = bot.download_file(bot.get_file(doc.file_id).file_path)
    tmp = Path(tempfile.gettempdir()) / doc.file_name
    tmp.write_bytes(raw)
    data_dir = db.DATA_DIR
    data_dir.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(tmp, "r") as zf:
            names = zf.namelist()
            zf.extractall(data_dir)
        tmp.unlink(missing_ok=True)
    except Exception as e:
        bot.edit_message_text(message.chat.id, status_msg.message_id, text=f"❌ Failed: {e}")
        cs(uid)
        return

    db.init_admin_db()
    if not db.is_admin(OWNER_ID):
        db.add_admin(OWNER_ID, None, "Owner", is_owner_flag=True)

    restored_bots = db.list_child_bots()
    for b in restored_bots:
        try:
            db.init_child_db(db.get_child_db_path(b["bot_username"]))
        except Exception:
            pass

    db.set_all_bots_stopped()
    restarted = []
    for b in restored_bots:
        ok, _ = _start_child(b["id"], b)
        if ok:
            restarted.append(f"▶️ @{b['bot_username']}")

    cs(uid)
    bot.edit_message_text(
        chat_id=message.chat.id,
        message_id=status_msg.message_id,
        text=(
            f"✅ <b>Restored!</b>\n\n"
            f"📦 Files: {', '.join(names)}\n\n"
            f"🤖 Auto-restarted:\n" + ("\n".join(restarted) if restarted else "None")
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
    row = db.get_child_bot(bot_id)
    if not row:
        bot.answer_callback_query(call.id, "Not found.")
        return
    link = f"https://t.me/{row['bot_username']}?start=adminpanel"
    bot.edit_message_text(
        call.message.chat.id, call.message.message_id,
        text=(
            f"🎛 <b>{row['bot_name']}</b> Admin Panel\n\n"
            f"👉 <a href='{link}'>Open @{row['bot_username']}</a>\n\n"
            "Your ID is pre-authorized as admin."
        ),
        parse_mode="HTML",
    )


# ─── Add/Remove Admin ─────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "👥 Add/Remove Admin")
@require_owner
def menu_manage_admins(message: types.Message):
    admins = db.list_admins()
    lines = ["👥 <b>Current Admins:</b>\n"]
    for a in admins:
        role = "👑 Owner" if a["is_owner"] else "🛡 Admin"
        uname = f"@{a['username']}" if a["username"] else "No username"
        lines.append(f"• {role} — {a['full_name']} ({uname}) [<code>{a['user_id']}</code>]")
    bot.send_message(message.chat.id, "\n".join(lines), reply_markup=admin_manage_inline(admins))


@bot.callback_query_handler(func=lambda c: c.data == "add_admin_prompt")
@require_owner_cb
def cb_add_admin_prompt(call: types.CallbackQuery):
    ss(call.from_user.id, action="add_admin")
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "➕ Send the <b>User ID</b> of the new admin:", reply_markup=cancel_keyboard())


@bot.message_handler(func=lambda m: gs(m.from_user.id).get("action") == "add_admin")
@require_owner
def handle_add_admin(message: types.Message):
    uid = message.from_user.id
    if message.text == "❌ Cancel":
        cs(uid)
        bot.send_message(message.chat.id, "❌ Cancelled.", reply_markup=admin_main_menu())
        return
    try:
        new_id = int(message.text.strip())
    except ValueError:
        bot.reply_to(message, "⚠️ Send a numeric ID.")
        return
    if db.is_admin(new_id):
        bot.send_message(message.chat.id, "ℹ️ Already an admin.", reply_markup=admin_main_menu())
    else:
        db.add_admin(new_id, None, f"Admin {new_id}")
        bot.send_message(message.chat.id, f"✅ <code>{new_id}</code> added as admin.", reply_markup=admin_main_menu())
    cs(uid)


@bot.callback_query_handler(func=lambda c: c.data.startswith("remove_admin:"))
@require_owner_cb
def cb_remove_admin(call: types.CallbackQuery):
    target = int(call.data.split(":")[1])
    if db.remove_admin(target):
        bot.edit_message_text(
            call.message.chat.id, call.message.message_id,
            text=f"✅ Admin <code>{target}</code> removed.", parse_mode="HTML",
        )
    else:
        bot.answer_callback_query(call.id, "Cannot remove owner or not found.")


# ─── 🗄 Switch Database ────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "🗄 Switch Database")
@require_owner
def menu_switch_db(message: types.Message):
    info = db_config.get_status_info()
    db_type = info["type"]
    icon = "🍃 MongoDB" if db_type == "mongodb" else "🗃 SQLite (Local)"
    uri_display = ""
    if db_type == "mongodb" and info["mongo_uri"]:
        masked = info["mongo_uri"]
        if "@" in masked:
            parts = masked.split("@")
            prefix = parts[0]
            if ":" in prefix.split("//")[-1]:
                auth = prefix.split("//")[-1]
                user = auth.split(":")[0]
                masked = prefix.split("//")[0] + "//" + user + ":****@" + "@".join(parts[1:])
        uri_display = f"\n🔗 URI: <code>{masked[:60]}...</code>"

    bot.send_message(
        message.chat.id,
        f"🗄 <b>Database Settings</b>\n\n"
        f"📊 Current: <b>{icon}</b>{uri_display}\n\n"
        "Choose an action below:",
        reply_markup=db_switch_inline(db_type),
    )


@bot.callback_query_handler(func=lambda c: c.data == "switch_to_mongodb")
@require_owner_cb
def cb_switch_to_mongodb(call: types.CallbackQuery):
    ss(call.from_user.id, action="enter_mongo_uri")
    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        "🍃 <b>Switch to MongoDB</b>\n\n"
        "Send your MongoDB connection URI.\n\n"
        "Free MongoDB at: <a href='https://www.mongodb.com/cloud/atlas'>MongoDB Atlas</a>\n\n"
        "Format:\n"
        "<code>mongodb+srv://username:password@cluster.mongodb.net/dbname</code>\n\n"
        "Or local:\n"
        "<code>mongodb://localhost:27017/botmanager</code>",
        reply_markup=cancel_keyboard(),
        disable_web_page_preview=True,
    )


@bot.message_handler(func=lambda m: gs(m.from_user.id).get("action") == "enter_mongo_uri")
@require_owner
def handle_mongo_uri(message: types.Message):
    uid = message.from_user.id
    if message.text == "❌ Cancel":
        cs(uid)
        bot.send_message(message.chat.id, "❌ Cancelled.", reply_markup=admin_main_menu())
        return
    uri = message.text.strip()
    if not uri.startswith("mongodb"):
        bot.reply_to(message, "⚠️ Invalid URI. Must start with <code>mongodb://</code> or <code>mongodb+srv://</code>")
        return
    status_msg = bot.send_message(message.chat.id, "⏳ Testing MongoDB connection...")
    try:
        from shared.mongo_db import test_connection
        ok, err = test_connection(uri)
    except Exception as e:
        ok, err = False, str(e)
    if not ok:
        bot.edit_message_text(
            message.chat.id, status_msg.message_id,
            text=f"❌ <b>Connection failed:</b>\n<code>{err}</code>\n\nCheck your URI and try again.",
            parse_mode="HTML",
        )
        return
    db_config.switch_to_mongodb(uri)
    cs(uid)
    bot.edit_message_text(
        message.chat.id, status_msg.message_id,
        text=(
            "✅ <b>MongoDB connected successfully!</b>\n\n"
            "Config saved. <b>Restart the admin bot</b> for full effect.\n\n"
            "💡 Use <b>🔁 Migrate data now</b> in Switch Database menu to copy data to MongoDB."
        ),
        parse_mode="HTML",
    )
    bot.send_message(message.chat.id, "Database switched.", reply_markup=admin_main_menu())


@bot.callback_query_handler(func=lambda c: c.data == "switch_to_sqlite")
@require_owner_cb
def cb_switch_to_sqlite(call: types.CallbackQuery):
    db_config.switch_to_sqlite()
    bot.edit_message_text(
        call.message.chat.id, call.message.message_id,
        text="✅ <b>Switched to SQLite (local).</b>\n\nRestart the admin bot to apply.",
        parse_mode="HTML",
    )


@bot.callback_query_handler(func=lambda c: c.data == "migrate_data")
@require_owner_cb
def cb_migrate_data(call: types.CallbackQuery):
    if db_config.get_db_type() != "mongodb":
        bot.answer_callback_query(call.id, "⚠️ Switch to MongoDB first.", show_alert=True)
        return
    bot.answer_callback_query(call.id, "⏳ Migrating...")
    status_msg = bot.send_message(call.message.chat.id, "⏳ <b>Migrating SQLite → MongoDB...</b>")
    try:
        from shared.mongo_db import migrate_from_sqlite
        counts = migrate_from_sqlite()
        summary = "\n".join(f"• {k}: {v}" for k, v in counts.items())
        bot.edit_message_text(
            call.message.chat.id, status_msg.message_id,
            text=f"✅ <b>Migration Complete!</b>\n\n{summary}", parse_mode="HTML",
        )
    except Exception as e:
        bot.edit_message_text(
            call.message.chat.id, status_msg.message_id,
            text=f"❌ Migration error: {e}", parse_mode="HTML",
        )


# ─── 📡 Server Status ─────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "📡 Server Status")
@require_admin
def menu_server_status(message: types.Message):
    info = db_config.get_status_info()
    db_type = info["type"]
    lines = ["📡 <b>Server & Database Status</b>\n"]

    if db_type == "sqlite":
        data_dir = db.DATA_DIR
        db_files = list(data_dir.glob("*.db")) if data_dir.exists() else []
        total_size = sum(f.stat().st_size for f in db_files) / 1024
        lines.append(f"🗃 <b>Database:</b> SQLite (Local)")
        lines.append(f"📁 Location: <code>{data_dir}</code>")
        lines.append(f"📦 Files: {len(db_files)} ({total_size:.1f} KB)")
        lines.append(f"✅ Status: Running")
    else:
        uri = info["mongo_uri"]
        lines.append(f"🍃 <b>Database:</b> MongoDB (Cloud)")
        try:
            from shared.mongo_db import test_connection
            ok, err = test_connection(uri)
            lines.append(f"✅ Status: Connected" if ok else f"❌ Status: Error — {err[:80]}")
        except Exception as e:
            lines.append(f"❌ Status: {e}")

    lines.append("")
    bots = db.list_child_bots()
    running = sum(1 for b in bots if b["is_running"])
    lines.append(f"🤖 <b>Child Bots:</b> {len(bots)} total")
    lines.append(f"   🟢 Running: {running}  |  🔴 Stopped: {len(bots) - running}")
    for b in bots:
        icon = "🟢" if b["is_running"] else "🔴"
        lines.append(f"   {icon} @{b['bot_username']}")
    lines.append("")
    alive = [bid for bid, proc in child_processes.items() if proc.poll() is None]
    lines.append(f"⚙️ <b>Active processes:</b> {len(alive)}")

    bot.send_message(message.chat.id, "\n".join(lines), reply_markup=admin_main_menu())


# ─── Generic pagination ───────────────────────────────────────────────────────

@bot.callback_query_handler(func=lambda c: c.data.startswith("page:"))
@require_admin_cb
def cb_generic_page(call: types.CallbackQuery):
    parts = call.data.split(":")
    action, page = parts[1], int(parts[2])
    bots = db.list_child_bots()
    page_bots, total_pages = paginate(bots, page)
    bot.edit_message_reply_markup(
        call.message.chat.id, call.message.message_id,
        reply_markup=build_bot_list_inline(page_bots, page, total_pages, action),
    )


@bot.callback_query_handler(func=lambda c: c.data == "noop")
def cb_noop(call): bot.answer_callback_query(call.id)


# ─── Cancel ───────────────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "❌ Cancel")
def handle_cancel(message: types.Message):
    cs(message.from_user.id)
    if db.is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "❌ Cancelled.", reply_markup=admin_main_menu())
    else:
        bot.send_message(message.chat.id, "❌ Cancelled.")


# ─── Startup ──────────────────────────────────────────────────────────────────

def startup():
    db.init_admin_db()
    if not db.is_admin(OWNER_ID):
        db.add_admin(OWNER_ID, None, "Owner", is_owner_flag=True)
    db.set_all_bots_stopped()
    for b in db.list_child_bots():
        try:
            db.init_child_db(db.get_child_db_path(b["bot_username"]))
        except Exception:
            pass
    logger.info("Admin Bot ready. DB: %s | Owner: %s", db_config.get_db_type(), OWNER_ID)


def main():
    startup()
    logger.info("Admin Bot polling...")
    bot.infinity_polling(skip_pending=True, logger_level=logging.WARNING)


if __name__ == "__main__":
    main()
