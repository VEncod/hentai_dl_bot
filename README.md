# 🎌 Hentai DL Bot

A powerful Telegram bot to search, stream, and download hentai videos directly to Telegram — with user management, channel archiving, force-subscribe, and broadcast features.

**Created by [Mr. Aman](https://t.me/Am_ankhan)**

---

## ✨ Features

- 🔍 **Search** — Find hentai by name via Hanime.tv API
- 📺 **Stream Links** — Get direct streaming URLs in multiple qualities
- ⬇️ **Download** — Download videos and receive them as Telegram documents
- 💾 **Smart Caching** — Previously downloaded files are served instantly from cache
- 📂 **Archive System** — Browse downloaded episodes by series
- 📢 **Channel Archiving** — Automatically sends downloads to your main channel
- 🔐 **User Approval System** — Request-based access with admin approve/reject
- 🛡 **Admin Management** — Multi-admin support with owner privileges
- 📋 **Force Subscribe** — Require users to join your channel before using the bot
- 📣 **Broadcast** — Send announcements to all approved users
- 📝 **Log Channel** — Track searches, downloads, and admin actions
- 🖼 **Waifu Welcome** — Random waifu images on /start

---

## 📖 Bot Commands

### 👤 User Commands

| Command | Description |
|---|---|
| `/start` | Welcome message with bot info |
| `/search <name>` | Search for hentai by name |
| `/request` | Request access to use the bot |
| `/archive <series>` | Browse archived episodes of a series |
| `/series` | List all archived series |

### 🛡 Admin Commands

| Command | Description |
|---|---|
| `/addadmin <user_id>` | Add a new admin |
| `/removeadmin <user_id>` | Remove an admin (owner only) |
| `/admins` | List all admins |
| `/approve <user_id>` | Approve a user's access request |
| `/reject <user_id>` | Reject a user's access request |
| `/revoke <user_id>` | Revoke an approved user's access |
| `/adduser <user_id>` | Directly approve a user without request |
| `/removeuser <user_id>` | Remove an approved user |
| `/users` | List all approved users |
| `/pending` | View pending access requests with inline buttons |
| `/broadcast <message>` | Send a message to all approved users |

### ⚙️ Settings Commands

| Command | Description |
|---|---|
| `/setlog <channel_id>` | Set the log channel for bot activity |
| `/removelog` | Remove the log channel |
| `/setchannel <channel_id>` | Set the main channel (archive + force-sub) |
| `/removechannel` | Remove the main channel |

---

## 🚀 Deploy to Railway

### Step 1: Fork the Repository

Fork this repo to your GitHub account.

### Step 2: Create a Railway Project

1. Go to [railway.app](https://railway.app)
2. Click **"New Project"**
3. Select **"Deploy from GitHub Repo"**
4. Connect your GitHub account and select the forked repo

### Step 3: Add Environment Variables

Go to your service → **Variables** tab and add:

| Variable | Description | Example |
|---|---|---|
| `API_ID` | Telegram API ID from [my.telegram.org](https://my.telegram.org) | `12345678` |
| `API_HASH` | Telegram API Hash from [my.telegram.org](https://my.telegram.org) | `abcdef1234567890abcdef1234567890` |
| `BOT_TOKEN` | Bot token from [@BotFather](https://t.me/BotFather) | `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11` |
| `MONGO_URL` | MongoDB connection string | `mongodb+srv://user:pass@cluster.mongodb.net/` |

> 💡 **Get a free MongoDB:** Sign up at [MongoDB Atlas](https://www.mongodb.com/atlas) and create a free M0 cluster.

### Step 4: Deploy

Railway will automatically detect the `nixpacks.toml` configuration and install all dependencies including ffmpeg. Click **Deploy** and wait for the build to complete.

### Step 5: First Start

Send `/start` to your bot on Telegram. The **first user** to send `/start` automatically becomes the **owner** (super admin).

---

## 🛠 Post-Deploy Setup

1. **Start the bot** — Send `/start` to become the owner
2. **Create a Telegram channel** — This will be your archive/force-sub channel
3. **Add the bot as admin** to that channel (needs permission to post and check members)
4. **Set the main channel** — `/setchannel <channel_id>` (e.g., `/setchannel -1001234567890`)
   - This enables **channel archiving** (downloads are forwarded here)
   - This enables **force-subscribe** (users must join to use the bot)
5. **Set the log channel** (optional) — `/setlog <channel_id>` to track bot activity
6. **Add users** — Either `/adduser <user_id>` directly, or wait for users to `/request` access

> 💡 **Finding channel IDs:** Forward a message from the channel to [@userinfobot](https://t.me/userinfobot) or use the `-100` prefix format.

---

## 🧰 Tech Stack

- **[Pyrofork](https://github.com/Mayuri-Chan/pyrofork)** — Modern async Pyrogram fork for Telegram Bot API
- **[Motor](https://motor.readthedocs.io/)** — Async MongoDB driver
- **[aiohttp](https://aiohttp.readthedocs.io/)** — Async HTTP client
- **[FFmpeg](https://ffmpeg.org/)** — Video processing and HLS stream downloading
- **[MongoDB](https://www.mongodb.com/)** — Database for users, cache, config, and archives
- **[HentaiFF](https://hentaiff.com/)** — Video search and streaming data

---

## 📄 License

See [LICENSE](LICENSE) for details.

---

**⚡ **Powered by HentaiFF.com & FFmpeg | 👨‍💻 Created by [Mr. Aman](https://t.me/Am_ankhan)**
