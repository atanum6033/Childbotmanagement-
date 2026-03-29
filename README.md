# Telegram Bot Management System

A Python-based system for managing multiple Telegram child bots from a single Admin Bot.

---

## 📁 Project Structure

```
telegram-bot-system/
├── admin_bot/
│   └── main.py           # Admin Bot — manages child bots, admins, backups
├── child_bot/
│   └── main.py           # Child Bot template — user-facing bot
├── shared/
│   ├── database.py       # SQLite database layer (shared by all bots)
│   ├── keyboards.py      # Telegram keyboard builders
│   └── utils.py          # Utility helpers
├── data/                 # Auto-created at runtime (database files live here)
├── requirements.txt      # Python dependencies
├── railway.toml          # Railway deployment config
├── nixpacks.toml         # Railway build config
├── Procfile              # Process definition
├── .env.example          # Environment variable template
└── .gitignore
```

---

## 🚀 Quick Start

### 1. Prerequisites

- Python 3.11+
- A Telegram Bot token (create via [@BotFather](https://t.me/BotFather))
- Your Telegram User ID (get via [@userinfobot](https://t.me/userinfobot))

### 2. Clone and Install

```bash
git clone <your-repo>
cd telegram-bot-system
pip install -r requirements.txt
```

### 3. Configure Environment

```bash
cp .env.example .env
```

Edit `.env`:
```
ADMIN_BOT_TOKEN=123456789:AABBCCDDEEFFaabbccddeeff
OWNER_ID=123456789
```

### 4. Run Locally

```bash
python admin_bot/main.py
```

---

## ☁️ Deploy to Railway (Free)

1. Push this folder to a GitHub repository
2. Go to [Railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Select your repository
4. Add environment variables in Railway dashboard:
   - `ADMIN_BOT_TOKEN` = your admin bot token
   - `OWNER_ID` = your Telegram user ID
5. Railway auto-deploys. Done!

> The Admin Bot runs on Railway and launches child bots as subprocess workers within the same deployment.

---

## 🤖 Admin Bot Features

| Button | Description |
|--------|-------------|
| ➕ Add Child Bot | Add a child bot by token. Auto-detects bot name & username. |
| ➖ Remove Child Bot | Paginated list (10/page) with confirmation before deleting. |
| 📋 List Child Bots | View all bots: name, username, joined date, status, page by 10. |
| ▶️ Stop/Run Bot | Start or stop any child bot with one click. |
| 💾 Backup Database | Downloads a ZIP of all database files. |
| ♻️ Restore Database | Send/forward a backup ZIP to restore all data. |
| 🎛 Use Child Bot Admin | Open the selected child bot's admin panel directly. |
| 👥 Add/Remove Admin | Owner can add admins by user ID or remove existing ones. |

---

## 🎛 Child Bot Admin Features

| Button | Description |
|--------|-------------|
| 📝 Set Start Message | Set start message (any type: text, photo, video, doc, sticker, forward). |
| 📢 Broadcast | Send any message to all users. Preview first, then confirm. Set delay (seconds). Choose to include inactive users. |
| 👥 Total Users | See active, inactive, blocked user counts. |
| 🚫 Block/Unblock User | Toggle user block status by User ID. |
| 🔗 Channel Links | Add/remove mandatory join channels for users. |

---

## 👤 User Features

| Button | Description |
|--------|-------------|
| 📨 Message Admin | Send a message to the child bot admin. Admin can reply back. |
| 🔗 Join Channel | View and join configured channels. |

---

## 💬 Message Routing

When a user sends a message to a child bot:
- The message is forwarded to all admins with the user's full info (name, ID, username, join date)
- An inline "↩️ Reply" button lets admin respond to that specific user
- Admin replies go directly to the user

---

## 🗃 Database Design

- **SQLite** with WAL mode for fast concurrent access
- Admin Bot uses: `data/botmanager.db`
- Each Child Bot uses: `data/child_<bot_username>.db`
- **No duplicates**: All tables use `UNIQUE` constraints + `INSERT OR IGNORE`
- **Indexed**: User IDs and usernames are indexed for fast lookups

### Key Tables

**`botmanager.db`:**
- `admins` — Admin users with owner flag
- `child_bots` — Registered child bots with tokens and status

**`child_<username>.db`:**
- `bot_users` — User records (ID, name, username, join date, active/blocked)
- `bot_settings` — Key-value settings (start message, etc.)
- `channels` — Mandatory join channels
- `broadcast_log` — History of broadcasts

---

## 🔐 Security Notes

- Only users in the `admins` table can use the Admin Bot
- The owner (OWNER_ID) is auto-registered and cannot be removed
- Child bot admin access is shared with Admin Bot admins
- Bot tokens are stored in the local SQLite database (encrypted at rest via OS)

---

## 🔄 Upgrade to MongoDB (Future)

The `shared/database.py` module is designed with clean function boundaries. To upgrade to MongoDB:
1. Replace the SQLite connection functions with PyMongo equivalents
2. The function signatures remain the same — no changes needed in bot files

---

## 📝 Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ADMIN_BOT_TOKEN` | ✅ Yes | Admin Bot token from @BotFather |
| `OWNER_ID` | ✅ Yes | Your Telegram user ID |

Child bots use these (set automatically when started by Admin Bot):

| Variable | Description |
|----------|-------------|
| `CHILD_BOT_TOKEN` | Token of the child bot |
| `CHILD_BOT_USERNAME` | Username of the child bot |
| `ADMIN_BOT_TOKEN` | Admin bot token (for reverse notifications) |
| `ADMIN_BOT_OWNER_ID` | Owner's user ID |
