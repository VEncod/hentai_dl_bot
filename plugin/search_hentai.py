import asyncio
import logging

from wzgram import Client
from wzgram.types import (
    Message,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from api.hanime_api import HanimeAPI
from utils.auth import approved_only
from utils.fsub import force_sub
from utils.logger import log_search
from utils.slug_map import get_short_slug
from utils.autodelete import track_message, clear_chat_history
from utils.keyboard import (
    get_main_reply_keyboard,
    get_user_search_source,
    set_user_search_source,
    BUTTON_HENTAI_TV,
    BUTTON_OPPAI_STREAM,
    BUTTON_BOTH_SOURCES,
    BUTTON_SERIES_ARCHIVE,
)
from plugin.archive import series_command

log = logging.getLogger(__name__)
hanime_api = HanimeAPI()


@approved_only
@force_sub
async def hentaisearch(client: Client, message: Message):
    """Search hentai or handle custom keyboard source selections."""
    query = (message.text or "").strip()
    if not query:
        return

    chat_id = message.chat.id
    user_id = message.from_user.id
    reply_kb = get_main_reply_keyboard()

    # ── Handle Custom Reply Keyboard Selection Buttons ──────────────────────
    if BUTTON_HENTAI_TV in query or "hentai.tv" in query.lower():
        await set_user_search_source(user_id, "htv")
        msg = await message.reply_text(
            "🌐 **Search Mode Selected: Hentai.tv (Default)**\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "✅ Your searches will now query **Hentai.tv** in Full HD (1080p).\n\n"
            "💬 **Type any hentai or anime name to search!**",
            reply_markup=reply_kb,
        )
        await track_message(chat_id, msg.id)
        return

    if BUTTON_OPPAI_STREAM in query or "oppai.stream" in query.lower():
        await set_user_search_source(user_id, "oppai")
        msg = await message.reply_text(
            "✨ **Search Mode Selected: Oppai.stream (4K)**\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "✅ Your searches will now query **Oppai.stream** for 4K Ultra HD & HD releases.\n\n"
            "💬 **Type any hentai or anime name to search!**",
            reply_markup=reply_kb,
        )
        await track_message(chat_id, msg.id)
        return

    if BUTTON_BOTH_SOURCES in query or "both sources" in query.lower() or "search both" in query.lower():
        await set_user_search_source(user_id, "both")
        msg = await message.reply_text(
            "🔍 **Search Mode Selected: Both Sources**\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "✅ Your searches will now query both **Hentai.tv (1080p)** and **Oppai.stream (4K)**.\n\n"
            "💬 **Type any hentai or anime name to search!**",
            reply_markup=reply_kb,
        )
        await track_message(chat_id, msg.id)
        return

    if BUTTON_SERIES_ARCHIVE in query or "series archive" in query.lower():
        await series_command(client, message)
        return

    # ── Regular Text Search ────────────────────────────────────────────────
    # Clear old bot messages from this chat
    await clear_chat_history(client, chat_id)
    
    # Track user's search message for auto-delete (10 min)
    await track_message(chat_id, message.id, sender_type="user")
    await log_search(client, message.from_user.username, query)

    # Determine user's selected search source
    source_pref = await get_user_search_source(user_id)
    if source_pref == "htv":
        source_label = "🌐 Hentai.tv (Default)"
        status_text = f"🔎 **Searching Hentai.tv for:** `{query}`...\n⏳ *Fetching 1080p catalog...*"
    elif source_pref == "oppai":
        source_label = "✨ Oppai.stream (4K)"
        status_text = f"🔎 **Searching Oppai.stream for:** `{query}`...\n⏳ *Fetching 4K catalog...*"
    else:
        source_label = "🔍 Both Sources (Hentai.tv + Oppai 4K)"
        status_text = f"🔎 **Searching for:** `{query}`...\n⏳ *Searching Hentai.tv and Oppai.stream...*"

    # 1. Send instant UI progress indicator
    status_msg = await message.reply_text(
        status_text,
        reply_markup=reply_kb,
    )
    await track_message(chat_id, status_msg.id)

    try:
        results = await asyncio.to_thread(hanime_api.search, query, 0, source_pref)
    except Exception:
        log.exception("Search failed for query=%s source=%s", query, source_pref)
        await status_msg.edit_text("❌ Search API is currently unavailable. Please try again later.")
        return

    if not results:
        await status_msg.edit_text(
            f"🔍 **Search Query:** `{query}`\n"
            f"🌐 **Source:** {source_label}\n\n"
            f"❌ **No results found.**\n"
            f"💡 *Tip: Try searching with a shorter keyword (e.g. 'Overflow' or 'Liliana'), or switch search source using the keyboard below.*",
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
        f"🔍 **Search Results for:** **{query}** ({len(results)} found)\n"
        f"🌐 **Source:** {source_label}\n\n"
        f"👇 *Tap a title below to view details & download options:*",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
