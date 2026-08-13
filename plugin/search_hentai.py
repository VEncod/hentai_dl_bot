import asyncio
import logging

from pyrogram import Client
from pyrogram.types import (
    Message,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from api.hanime_api import HanimeAPI

hanime_api = HanimeAPI()
from utils.auth import approved_only
from utils.fsub import force_sub
from utils.logger import log_search
from utils.slug_map import get_short_slug
from utils.autodelete import track_message, clear_chat_history

log = logging.getLogger(__name__)


@approved_only
@force_sub
async def hentaisearch(client: Client, message: Message):
    """Search hentai — triggered by any non-command text message."""
    query = message.text.strip()

    if not query:
        return

    # Clear old bot messages from this chat
    await clear_chat_history(client, message.chat.id)
    
    # Track user's search message for auto-delete (10 min)
    await track_message(message.chat.id, message.id, sender_type="user")

    await log_search(client, message.from_user.username, query)

    # 1. Send instant UI progress indicator
    status_msg = await message.reply_text(
        f"🔎 **Searching for:** `{query}`...\n"
        f"⏳ *Fetching titles from multi-provider engine...*"
    )
    await track_message(message.chat.id, status_msg.id)

    try:
        results = await asyncio.to_thread(hanime_api.search, query)
    except Exception:
        log.exception("Search failed for query=%s", query)
        await status_msg.edit_text("❌ Search API is currently unavailable. Please try again later.")
        return

    if not results:
        await status_msg.edit_text(
            f"🔍 **Search Query:** `{query}`\n\n"
            f"❌ **No results found.**\n"
            f"💡 *Tip: Try searching with a shorter title keyword (e.g. 'Overflow' or 'Liliana').*"
        )
        return

    keyboard = []
    for item in results[:20]:
        slug = item.get("slug", "")
        short_key = await get_short_slug(slug)
        name = item.get("title", "Unknown")
        display_name = name if len(name) <= 55 else name[:52] + "..."
        keyboard.append([InlineKeyboardButton(f"🎬 {display_name}", callback_data=f"info_{short_key}")])

    await status_msg.edit_text(
        f"🔍 **Search Results for:** **{query}** ({len(results)} found)\n\n"
        f"👇 *Tap a title below to view details & download options:*",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
