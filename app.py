import os
import sys
import asyncio
import logging

from pyrogram import Client, filters, idle
from pyrogram.types import BotCommand
from pyrogram.handlers import MessageHandler, CallbackQueryHandler

from utils.db import init_db

from plugin.start import start_command, checksub_callback
from plugin.search_hentai import hentaisearch
from plugin.info_hentai import infohentai, episode_info
from plugin.video_hentai import hentailink, hentaidl, batch_download
from plugin.admin import addadmin_command, removeadmin_command, admins_command, clearcache_command
from plugin.users import (
    request_command, approve_command, reject_command, revoke_command,
    users_command, pending_command, adduser_command, removeuser_command,
    approve_callback, reject_callback,
)
from plugin.channels import (
    setlog_command, removelog_command, setchannel_command, removechannel_command,
)
from plugin.archive import archive_command, series_command
from plugin.catalog import catalog_episodes_callback
from plugin.broadcast import broadcast_command
from plugin.hindi_dub import hindi_dub_handler, addhindi_command, removehindi_command, hindichannels_command
from utils.autodelete import start_autodelete_loop, set_userbot
from utils.hindi_dub import set_userbot as set_hindi_userbot
from utils.session_store import load_session_string, save_session_string

# ── Logging ─────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("hentai_dl_bot")

# ── Environment variables ───────────────────────────────────────────────
API_ID         = os.environ.get("API_ID")
API_HASH       = os.environ.get("API_HASH")
BOT_TOKEN      = os.environ.get("BOT_TOKEN")
MONGO_URL      = os.environ.get("MONGO_URL")
SESSION_STRING = os.environ.get("SESSION_STRING", "")

_missing = [k for k, v in {
    "API_ID": API_ID,
    "API_HASH": API_HASH,
    "BOT_TOKEN": BOT_TOKEN,
    "MONGO_URL": MONGO_URL,
}.items() if not v]

if _missing:
    log.critical("Missing required env vars: %s", ", ".join(_missing))
    sys.exit(1)

API_ID = int(API_ID)

# ── Bot client ──────────────────────────────────────────────────────────
bot = Client(
    "hentai_dl_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    plugins=None,
)
bot.mongo_url = MONGO_URL

# ── Userbot client (optional) ────────────────────────────────────────────
# Used solely to delete user messages / wipe chat history.
# Set SESSION_STRING env var to enable, or it will be loaded from DB.
# Generate it with: python gen_session.py
userbot = None


async def main():
    await init_db(MONGO_URL)

    # ── Load or save session string ─────────────────────────────────────
    global SESSION_STRING
    db_session = await load_session_string()
    if SESSION_STRING:
        # Env var takes priority — save to DB for next time
        if db_session != SESSION_STRING:
            await save_session_string(SESSION_STRING)
    elif db_session:
        # Load from DB since env var is empty
        SESSION_STRING = db_session
        log.info("Session string loaded from database")

    # Initialize userbot if session string is available
    global userbot
    if SESSION_STRING:
        userbot = Client(
            "hentai_userbot",
            api_id=API_ID,
            api_hash=API_HASH,
            session_string=SESSION_STRING,
            plugins=None,
        )
        log.info("Userbot session configured — full chat wipe enabled")

    # ── Command handlers (registered FIRST — commands take priority) ────
    bot.add_handler(MessageHandler(start_command, filters.command("start")))

    # Admin commands
    bot.add_handler(MessageHandler(addadmin_command, filters.command("addadmin")))
    bot.add_handler(MessageHandler(removeadmin_command, filters.command("removeadmin")))
    bot.add_handler(MessageHandler(admins_command, filters.command("admins")))
    bot.add_handler(MessageHandler(clearcache_command, filters.command("clearcache")))

    # User management commands
    bot.add_handler(MessageHandler(request_command, filters.command("request")))
    bot.add_handler(MessageHandler(approve_command, filters.command("approve")))
    bot.add_handler(MessageHandler(reject_command, filters.command("reject")))
    bot.add_handler(MessageHandler(revoke_command, filters.command("revoke")))
    bot.add_handler(MessageHandler(users_command, filters.command("users")))
    bot.add_handler(MessageHandler(pending_command, filters.command("pending")))
    bot.add_handler(MessageHandler(adduser_command, filters.command("adduser")))
    bot.add_handler(MessageHandler(removeuser_command, filters.command("removeuser")))

    # Channel management commands
    bot.add_handler(MessageHandler(setlog_command, filters.command("setlog")))
    bot.add_handler(MessageHandler(removelog_command, filters.command("removelog")))
    bot.add_handler(MessageHandler(setchannel_command, filters.command("setchannel")))
    bot.add_handler(MessageHandler(removechannel_command, filters.command("removechannel")))

    # Archive commands
    bot.add_handler(MessageHandler(archive_command, filters.command("archive")))
    bot.add_handler(MessageHandler(series_command, filters.command("series")))

    # Broadcast
    bot.add_handler(MessageHandler(broadcast_command, filters.command("broadcast")))

    # Hindi dub admin commands
    bot.add_handler(MessageHandler(addhindi_command, filters.command("addhindi")))
    bot.add_handler(MessageHandler(removehindi_command, filters.command("removehindi")))
    bot.add_handler(MessageHandler(hindichannels_command, filters.command("hindichannels")))

    # Search — LAST message handler (catches any non-command text)
    bot.add_handler(MessageHandler(hentaisearch, filters.text & ~filters.regex(r"^/") & filters.private))

    # ── Callback query handlers ─────────────────────────────────────────
    bot.add_handler(CallbackQueryHandler(infohentai, filters.regex(r"^info_")))
    bot.add_handler(CallbackQueryHandler(episode_info, filters.regex(r"^eps_")))
    bot.add_handler(CallbackQueryHandler(hentailink, filters.regex(r"^link_")))
    bot.add_handler(CallbackQueryHandler(hentaidl, filters.regex(r"^dlt_")))

    bot.add_handler(CallbackQueryHandler(batch_download, filters.regex(r"^ball_")))
    bot.add_handler(CallbackQueryHandler(hindi_dub_handler, filters.regex(r"^hindi_")))
    bot.add_handler(CallbackQueryHandler(catalog_episodes_callback, filters.regex(r"^cat_")))
    bot.add_handler(CallbackQueryHandler(approve_callback, filters.regex(r"^apr_")))
    bot.add_handler(CallbackQueryHandler(reject_callback, filters.regex(r"^rej_")))
    bot.add_handler(CallbackQueryHandler(checksub_callback, filters.regex(r"^checksub$")))

    # Set bot commands visible in Telegram menu
    await bot.start()

    # Start userbot if configured
    if userbot:
        await userbot.start()
        set_userbot(userbot)
        set_hindi_userbot(userbot)
        log.info("Userbot started — user message deletion + Hindi dub search enabled")

    await bot.set_bot_commands([
        BotCommand("start", "Start the bot"),
        BotCommand("request", "Request access to use the bot"),
        BotCommand("archive", "Browse archived episodes"),
        BotCommand("series", "List all archived series"),
        BotCommand("broadcast", "Broadcast message to all users"),
        BotCommand("clearcache", "Clear download cache"),
        BotCommand("admins", "List all admins"),
        BotCommand("users", "List approved users"),
        BotCommand("pending", "View pending access requests"),
        BotCommand("approve", "Approve a user"),
        BotCommand("reject", "Reject a user"),
        BotCommand("adduser", "Directly add a user"),
        BotCommand("removeuser", "Remove a user"),
        BotCommand("setlog", "Set log channel"),
        BotCommand("setchannel", "Set main archive channel"),
    ])

    # Start auto-delete background task
    asyncio.create_task(start_autodelete_loop(bot))

    log.info("Bot started successfully! Commands registered.")
    await idle()
    await bot.stop()
    if userbot:
        await userbot.stop()
    log.info("Bot stopped.")


if __name__ == "__main__":
    bot.run(main())
