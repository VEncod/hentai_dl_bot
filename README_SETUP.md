# HentaiDL Bot - Telegram Downloader

A Telegram bot to search and download hentai from HentaiHaven using the [hentai-api](https://github.com/sulvii/hentai-api) service.

## Setup

### 1. Deploy hentai-api Service

First, deploy the hentai-api Node.js service to get streams:

```bash
# Option A: Local (for testing)
git clone https://github.com/sulvii/hentai-api.git
cd hentai-api
bun install
bun run dev
# Service runs on http://localhost:3000
```

Or deploy to production (Railway, Vercel, Heroku, etc.)

### 2. Configure Bot

**Clone and setup:**
```bash
git clone https://github.com/VEncod/hentai_dl_bot.git
cd hentai_dl_bot
pip install -r requirements.txt
```

**Create `.env` file:**
```env
BOT_TOKEN=your_telegram_bot_token
MONGODB_URI=mongodb+srv://user:pass@cluster.mongodb.net/hentai_dl_bot
HENTAI_API_URL=http://localhost:3000  # Change to your deployed URL
```

**Update `api/hentaiff.py`:**
```python
HENTAI_API_BASE = "http://localhost:3000"  # Your hentai-api URL
```

### 3. Run Bot

```bash
python bot.py
```

## Architecture

```
Telegram Bot
    ↓
Python API Layer (api/hentaiff.py)
    ↓
External hentai-api Service (Node.js)
    ↓
HentaiHaven Website
```

**Benefits of using hentai-api:**
- ✅ No Playwright/Chromium needed in bot container
- ✅ Handles Cloudflare bypass centrally
- ✅ Better performance & resource usage
- ✅ Scalable (separate service)

## Commands

- `/start` - Show help
- `/search <query>` - Search for content
- `/download <slug>` - Download episode

## Files

- `bot.py` - Main Telegram bot
- `api/hentaiff.py` - API wrapper (calls hentai-api)
- `plugin/` - Command handlers
- `utils/` - Utilities (auth, db)

## License

For personal use only. Respect copyright laws.
