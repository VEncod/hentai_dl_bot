# Hentai DL Bot

A Telegram bot to search and download hentai videos.

## Features

- `/start` — Welcome message with usage info
- `/search <name>` — Search for hentai by name
- Inline buttons for details, streaming links, and direct download
- MongoDB caching for previously downloaded files
- ffmpeg-based video downloading

## Requirements

- Python 3.10+
- ffmpeg (system package)
- MongoDB instance
- Telegram API credentials

## Environment Variables

| Variable | Description |
|---|---|
| `API_ID` | Telegram API ID from [my.telegram.org](https://my.telegram.org) |
| `API_HASH` | Telegram API Hash |
| `BOT_TOKEN` | Bot token from [@BotFather](https://t.me/BotFather) |
| `MONGO_URL` | MongoDB connection string |
| `CACHE_CHANNEL` | Telegram channel/supergroup ID for file caching |

Copy `.env.example` to `.env` and fill in your values.

## Local Setup

```bash
pip install -r requirements.txt
# Make sure ffmpeg is installed: apt install ffmpeg
python app.py
```

## Deploy to Railway

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/template/new)

1. Click the button above or create a new project on [Railway](https://railway.app)
2. Connect your GitHub repo
3. Add the environment variables listed above
4. Railway will auto-detect the `nixpacks.toml` and install ffmpeg
5. Deploy!

Railway config files included:
- `railway.toml` — build & deploy settings
- `nixpacks.toml` — ensures ffmpeg is available
- `Procfile` — worker process definition

## Deploy to Heroku (Legacy)

The `app.json` is kept for Heroku compatibility:

[![Deploy to Heroku](https://img.shields.io/badge/Deploy%20To%20Heroku-black?style=for-the-badge&logo=heroku)](https://heroku.com/deploy)

## Tech Stack

- [Pyrofork](https://github.com/Mayuri-Chan/pyrofork) — Modern Pyrogram fork (async)
- [Motor](https://motor.readthedocs.io/) — Async MongoDB driver
- [aiohttp](https://aiohttp.readthedocs.io/) — Async HTTP client
- ffmpeg — Video processing

## License

See [LICENSE](LICENSE).
