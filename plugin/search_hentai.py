import logging

from pyrogram import Client
from pyrogram.types import (
    Message,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from api.hanime import search
from utils.auth import approved_only
from utils.logger import log_search

log = logging.getLogger(__name__)


@approved_only
async def hentaisearch(client: Client, message: Message):
    """Handle /search <query> command."""
    parts = message.text.split(None, 1)
    query = parts[1].strip() if len(parts) > 1 else ""

    if not query:
        await message.reply_animation(
            animation="https://telegra.ph/file/cdeae50a8a23041b01935.mp4",
            caption="**Usage:** `/search <hentai name>`",
        )
        return

    # Log the search
    await log_search(client, message.from_user.username, query)

    try:
        results = await search(query)
    except Exception:
        log.exception("Search failed for query=%s", query)
        await message.reply_text("❌ Search API is currently unavailable. Please try again later.")
        return

    if not results:
        await message.reply_text("No results found. Please check the spelling and try again.")
        return

    keyboard = []
    for item in results[:20]:  # Limit to 20 results for Telegram button limits
        slug = item.get("slug", "")
        name = item.get("name", "Unknown")
        # Truncate name if too long for callback button text
        display_name = name if len(name) <= 60 else name[:57] + "..."
        keyboard.append([InlineKeyboardButton(display_name, callback_data=f"info_{slug}")])

    await message.reply_text(
        f"🔍 Search results for **{query}** ({len(results)} found):",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
