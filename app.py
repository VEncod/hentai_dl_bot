import os
import sys
import logging

from pyrogram import Client, filters, idle
from pyrogram.handlers import MessageHandler, CallbackQueryHandler

from utils.db import init_db

from plugin.start import start_command
from plugin.search_hentai import hentaisearch
from plugin.info_hentai import infohentai, episode_info
from plugin.video_hentai import hentailink, hentaidl
from plugin.admin import addadmin_command, removeadmin_command, admins_command
from plugin.users import (
    request_command, approve_command, reject_command, revoke_command,
    users_command, pending_command, adduser_command, removeuser_command,
    approve_callback, reject_callback,
)
from plugin.channels import (
    setlog_command, removelog_command, setchannel_command, removechannel_command,
)
from plugin.archive import archive_command, series_command
from plugin.broadcast import broadcast_command

# ── Logging ─────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("hentai_dl_bot")

# ── Environment variables ───────────────────────────────────────────────
API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URL = os.environ.get("MONGO_URL")

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

# Store mongo_url for legacy compat (video_hentai uses it)
bot.mongo_url = MONGO_URL


async def main():
    # Initialize centralized DB
    await init_db(MONGO_URL)

    # ── Command handlers ────────────────────────────────────────────────
    bot.add_handler(MessageHandler(start_command, filters.command("start")))
    bot.add_handler(MessageHandler(hentaisearch, filters.command("search")))

    # Admin commands
    bot.add_handler(MessageHandler(addadmin_command, filters.command("addadmin")))
    bot.add_handler(MessageHandler(removeadmin_command, filters.command("removeadmin")))
    bot.add_handler(MessageHandler(admins_command, filters.command("admins")))

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

    # Broadcast command
    bot.add_handler(MessageHandler(broadcast_command, filters.command("broadcast")))

    # ── Callback query handlers ─────────────────────────────────────────
    bot.add_handler(CallbackQueryHandler(infohentai, filters.regex(r"^info_")))
    bot.add_handler(CallbackQueryHandler(episode_info, filters.regex(r"^eps_")))
    bot.add_handler(CallbackQueryHandler(hentailink, filters.regex(r"^link_")))
    bot.add_handler(CallbackQueryHandler(hentaidl, filters.regex(r"^dlt_")))
    bot.add_handler(CallbackQueryHandler(approve_callback, filters.regex(r"^apr_")))
    bot.add_handler(CallbackQueryHandler(reject_callback, filters.regex(r"^rej_")))

    # Debug: catch-all callback handler (last priority)
    async def debug_callback(client, callback_query):
        log.warning("UNHANDLED CALLBACK: data=%s user=%s", callback_query.data, callback_query.from_user.id)
    bot.add_handler(CallbackQueryHandler(debug_callback))

    await bot.start()
    log.info("Bot started successfully!")
    await idle()
    await bot.stop()
    log.info("Bot stopped.")


if __name__ == "__main__":
    bot.run(main())
