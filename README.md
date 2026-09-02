# 🎌 Hentai DL Bot

A powerful Telegram bot to search, stream, and download hentai videos directly to Telegram — with user management, channel archiving, force-subscribe, and broadcast features.

**Created by [Mr. Aman](https://t.me/Am_ankhan)**

---

## ✨ Features

- 🔍 **Search** — Find hentai by name on hentai.tv and oppai.stream
- 📺 **Stream Links** — Resolve direct streaming URLs with 4K preferred when available
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
| `/oppai_login <username> <password>` | Log the shared bot session into Oppai.stream (private chat) |
| `/oppai_logout` | Remove the saved Oppai.stream session |
| `/oppai_status` | Check the Oppai.stream session status |
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

## 🚀 Deployment

### Option 1: Railway (One-Click Cloud Deploy)

#### Step 1: Fork the Repository

Fork this repo to your GitHub account.

#### Step 2: Create a Railway Project

1. Go to [railway.app](https://railway.app)
2. Click **"New Project"**
3. Select **"Deploy from GitHub Repo"**
4. Connect your GitHub account and select the forked repo

#### Step 3: Add Environment Variables

Go to your service → **Variables** tab and add:

| Variable | Required | Description |
|---|---|---|
| `API_ID` | ✅ | Telegram API ID from [my.telegram.org](https://my.telegram.org) |
| `API_HASH` | ✅ | Telegram API Hash from [my.telegram.org](https://my.telegram.org) |
| `BOT_TOKEN` | ✅ | Bot token from [@BotFather](https://t.me/BotFather) |
| `MONGO_URL` | ✅ | MongoDB connection string |

> 💡 **Get a free MongoDB:** Sign up at [MongoDB Atlas](https://www.mongodb.com/atlas) and create a free M0 cluster.

#### Step 4: Deploy

Railway will automatically detect the Dockerfile and install all dependencies including FFmpeg and N_m3u8DL-RE. Click **Deploy** and wait for the build to complete.

---

### Option 2: VPS / Local Server (Ubuntu/Debian)

#### Quick Setup (One Command)

```bash
git clone https://github.com/VEncod/hentai_dl_bot.git
cd hentai_dl_bot
chmod +x setup.sh
./setup.sh
```

The setup script will:
- Install system dependencies (Python3, FFmpeg, pip)
- Create a Python virtual environment
- Install all Python packages
- Set up N_m3u8DL-RE binary
- Create `.env` from template

Then configure and start:

```bash
nano .env                    # Fill in your credentials
source venv/bin/activate     # Activate virtual environment
python3 app.py               # Start the bot
```

#### Run as a Systemd Service (auto-restart on crash/reboot)

```bash
# Edit paths in the service file if needed (default: /root/hentai_dl_bot)
nano hentai-dl-bot.service

# Install and enable the service
sudo cp hentai-dl-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable hentai-dl-bot
sudo systemctl start hentai-dl-bot

# Check status
sudo systemctl status hentai-dl-bot

# View live logs
sudo journalctl -u hentai-dl-bot -f
```

#### Run with Docker Compose

```bash
git clone https://github.com/VEncod/hentai_dl_bot.git
cd hentai_dl_bot

# Create and edit .env
cp .env.example .env
nano .env

# Build and start
docker compose up -d

# View logs
docker compose logs -f

# Stop
docker compose down
```

---

### Option 4: AWS EC2 (Docker - One Command Setup)

Deploy the bot on AWS EC2 so it runs in the background permanently, survives SSH disconnection, auto-restarts on crash, and starts on reboot.

#### Requirements
- AWS EC2 instance (Ubuntu) with at least **2GB RAM** and **8GB storage**
- SSH access to the instance

#### Step 1: Add Swap Space (Recommended)

Prevents upload failures due to low memory:

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile && echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

#### Step 2: Clone and Run Setup

```bash
cd ~ && git clone https://github.com/VEncod/hentai_dl_bot.git && cd hentai_dl_bot && chmod +x ec2-docker-setup.sh && ./ec2-docker-setup.sh
```

The script will:
- Install Docker and Docker Compose
- Ask for your environment variables (API_ID, API_HASH, BOT_TOKEN, MONGO_URL)
- Build the Docker image and start the bot in the background

#### After Setup

The bot runs in the background. You can safely close SSH — the bot stays running.

| Action | Command |
| :--- | :--- |
| Check status | `sudo docker compose ps` |
| View logs | `sudo docker compose logs -f` |
| Restart bot | `sudo docker compose restart` |
| Stop bot | `sudo docker compose down` |
| Start bot | `sudo docker compose up -d` |

The bot will **auto-restart** on crash and **start automatically** on server reboot.

#### For Amazon Linux EC2 (Cheaper)

Amazon Linux costs ~20% less than Ubuntu for the same specs. Use this instead:

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile && echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

```bash
cd ~ && git clone https://github.com/VEncod/hentai_dl_bot.git && cd hentai_dl_bot && chmod +x ec2-amazon-linux-setup.sh && ./ec2-amazon-linux-setup.sh
```

Same management commands apply after setup.

---

### Option 5: Kaggle / Google Colab

```python
!git clone https://github.com/VEncod/hentai_dl_bot.git
%cd hentai_dl_bot
!pip install -r requirements.txt
!chmod +x binary/N_m3u8DL-RE

import os
os.environ["API_ID"] = "your_api_id"
os.environ["API_HASH"] = "your_api_hash"
os.environ["BOT_TOKEN"] = "your_bot_token"
os.environ["MONGO_URL"] = "your_mongo_url"

!python3 app.py
```

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

## ⚙️ Environment Variables

| Variable | Required | Description |
|---|---|---|
| `API_ID` | ✅ | Telegram API ID from [my.telegram.org](https://my.telegram.org) |
| `API_HASH` | ✅ | Telegram API Hash |
| `BOT_TOKEN` | ✅ | Bot token from [@BotFather](https://t.me/BotFather) |
| `MONGO_URL` | ✅ | MongoDB connection string |

---

## 🔧 Engines

| Engine | Purpose |
|---|---|
| **[N_m3u8DL-RE](https://github.com/nilaoda/N_m3u8DL-RE)** | Primary HLS downloader — fast, handles complex M3U8 playlists |
| **[FFmpeg](https://ffmpeg.org/)** | Fallback downloader + video processing |

Both engines are automatically installed during setup. N_m3u8DL-RE is bundled in the `binary/` directory, and FFmpeg is installed via the system package manager.

---

## 🧰 Tech Stack

- **[WZGram](https://github.com/rjriajul/wzgram)** — High-performance async Telegram MTProto framework (active Pyrogram fork)
- **[Motor](https://motor.readthedocs.io/)** — Async MongoDB driver
- **[aiohttp](https://aiohttp.readthedocs.io/)** — Async HTTP client
- **[N_m3u8DL-RE](https://github.com/nilaoda/N_m3u8DL-RE)** — HLS stream downloader
- **[FFmpeg](https://ffmpeg.org/)** — Video processing and stream downloading
- **[MongoDB](https://www.mongodb.com/)** — Database for users, cache, config, and archives
- **[Hentai.tv](https://hentai.tv/)** — Default video search and streaming source
- **[Oppai.stream](https://oppai.stream/)** — 4K Ultra HD video and streaming source

---

## 📄 License

See [LICENSE](LICENSE) for details.

---

**⚡ Powered by N_m3u8DL-RE & FFmpeg | 👨‍💻 Created by [Mr. Aman](https://t.me/Am_ankhan)**
