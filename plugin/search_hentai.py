import logging

from pyrogram import Client
from pyrogram.types import (
    Message,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from api.hentaiff import HentaiFFScraper

hentaiff_scraper = HentaiFFScraper()
from utils.auth import approved_only
from utils.fsub import force_sub
from utils.logger import log_search
from utils.autodelete import track_message

log = logging.getLogger(__name__)


@approved_only
@force_sub
async def hentaisearch(client: Client, message: Message):
    """Search hentai — triggered by any non-command text message."""
    query = message.text.strip()

    if not query:
        return

    await log_search(client, message.from_user.username, query)

    try:
        results = hentaiff_scraper.search(query)
    except Exception:
        log.exception("Search failed for query=%s", query)
        msg = await message.reply_text("❌ Search API is currently unavailable. Please try again later.")
        await track_message(message.chat.id, msg.id)
        return

    if not results:
        msg = await message.reply_text("No results found. Please check the spelling and try again.")
        await track_message(message.chat.id, msg.id)
        return

    keyboard = []
    for item in results[:20]:
        slug = item.get("slug", "")
        name = item.get("title", "Unknown")
        display_name = name if len(name) <= 60 else name[:57] + "..."
        keyboard.append([InlineKeyboardButton(display_name, callback_data=f"info_{slug}")])

    msg = await message.reply_text(
        f"🔍 Search results for **{query}** ({len(results)} found):",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    await track_message(message.chat.id, msg.id)
    # Also track user's search message
    await track_message(message.chat.id, message.id)
