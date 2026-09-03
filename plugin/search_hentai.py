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
    BUTTON_HENTAICITY,
    BUTTON_HENTAI_TV,
    BUTTON_OPPAI_STREAM,
    BUTTON_BOTH_SOURCES,
    BUTTON_SERIES_ARCHIVE,
)
from plugin.archive import series_command

log = logging.getLogger(__name__)
hanime_api = HanimeAPI()


async def _safe_edit_or_send(client: Client, chat_id: int, status_msg: Message | None, text: str, reply_markup=None):
    """Safely edit existing status message or send a new message if edit fails (e.g. MESSAGE_ID_INVALID)."""
    if status_msg and getattr(status_msg, "id", None):
        try:
            return await client.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg.id,
                text=text,
                reply_markup=reply_markup,
            )
        except Exception as e:
            log.warning("edit_message_text failed (%s), sending fresh message to chat %s", e, chat_id)

    try:
        sent = await client.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
        )
        await track_message(chat_id, sent.id)
        return sent
    except Exception:
        log.exception("Fallback send_message failed for chat %s", chat_id)
        return None


@approved_only
@force_sub
async def hentaisearch(client: Client, message: Message):
    """Search hentai or handle custom keyboard source selections."""
    try:
        query = (message.text or "").strip()
        if not query:
            return

        chat_id = message.chat.id
        user_id = message.from_user.id
        reply_kb = get_main_reply_keyboard()

        # ── Handle Custom Reply Keyboard Selection Buttons ──────────────────────
        if BUTTON_HENTAICITY in query or BUTTON_HENTAI_TV in query or "hentaicity" in query.lower() or "hentai.tv" in query.lower():
            await set_user_search_source(user_id, "hcity")
            msg = await client.send_message(
                chat_id=chat_id,
                text=(
                    "🏙️ **Search Mode Selected: HentaiCity (Default)**\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    "✅ Your searches will now query **HentaiCity** in Full HD (1080p).\n\n"
                    "💬 **Type any hentai or anime name to search!**"
                ),
                reply_markup=reply_kb,
            )
            await track_message(chat_id, msg.id)
            return

        if BUTTON_OPPAI_STREAM in query or "oppai.stream" in query.lower():
            await set_user_search_source(user_id, "oppai")
            msg = await client.send_message(
                chat_id=chat_id,
                text=(
                    "✨ **Search Mode Selected: Oppai.stream (4K)**\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    "✅ Your searches will now query **Oppai.stream** for 4K Ultra HD & HD releases.\n\n"
                    "💬 **Type any hentai or anime name to search!**"
                ),
                reply_markup=reply_kb,
            )
            await track_message(chat_id, msg.id)
            return

        if BUTTON_BOTH_SOURCES in query or "both sources" in query.lower() or "search both" in query.lower():
            await set_user_search_source(user_id, "both")
            msg = await client.send_message(
                chat_id=chat_id,
                text=(
                    "🔍 **Search Mode Selected: Both Sources**\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    "✅ Your searches will now query both **HentaiCity (1080p)** and **Oppai.stream (4K)**.\n\n"
                    "💬 **Type any hentai or anime name to search!**"
                ),
                reply_markup=reply_kb,
            )
            await track_message(chat_id, msg.id)
            return

        if BUTTON_SERIES_ARCHIVE in query or "series archive" in query.lower():
            await series_command(client, message)
            return

        # ── Regular Text Search ────────────────────────────────────────────────
        await clear_chat_history(client, chat_id)
        await track_message(chat_id, message.id, sender_type="user")
        await log_search(client, message.from_user.username, query)

        # Determine user's selected search source
        source_pref = await get_user_search_source(user_id)
        if source_pref in ("hcity", "htv"):
            source_label = "🏙️ HentaiCity (Default)"
            status_text = f"🔎 **Searching HentaiCity for:** `{query}`...\n⏳ *Fetching 1080p catalog...*"
        elif source_pref == "oppai":
            source_label = "✨ Oppai.stream (4K)"
            status_text = f"🔎 **Searching Oppai.stream for:** `{query}`...\n⏳ *Fetching 4K catalog...*"
        else:
            source_label = "🔍 Both Sources (HentaiCity + Oppai 4K)"
            status_text = f"🔎 **Searching for:** `{query}`...\n⏳ *Searching HentaiCity and Oppai.stream...*"

        # 1. Send instant UI progress indicator directly to chat
        status_msg = None
        try:
            status_msg = await client.send_message(
                chat_id=chat_id,
                text=status_text,
                reply_markup=reply_kb,
            )
            if status_msg:
                await track_message(chat_id, status_msg.id)
        except Exception as e:
            log.warning("Failed to send initial search status: %s", e)

        # 2. Run search in thread
        results = []
        try:
            results = await asyncio.to_thread(hanime_api.search, query, 0, source_pref)
        except Exception:
            log.exception("Search failed for query=%s source=%s", query, source_pref)
            await _safe_edit_or_send(
                client=client,
                chat_id=chat_id,
                status_msg=status_msg,
                text="❌ Search API is currently unavailable. Please try again in a moment.",
                reply_markup=reply_kb,
            )
            return

        # 3. Handle no results found
        if not results:
            no_res_text = (
                f"🔍 **Search Query:** `{query}`\n"
                f"🌐 **Source:** {source_label}\n\n"
                f"❌ **No results found.**\n"
                f"💡 *Tip: Try searching with a shorter keyword (e.g. 'Uncle' or 'Landlady'), or switch search source using the keyboard below.*"
            )
            await _safe_edit_or_send(
                client=client,
                chat_id=chat_id,
                status_msg=status_msg,
                text=no_res_text,
                reply_markup=reply_kb,
            )
            return

        # 4. Build results keyboard
        keyboard = []
        for item in results[:20]:
            slug = item.get("slug", "")
            short_key = await get_short_slug(slug)
            name = item.get("title", "Unknown")
            display_name = name if len(name) <= 55 else name[:52] + "..."
            keyboard.append([InlineKeyboardButton(f"🎬 {display_name}", callback_data=f"info_{short_key}")])

        res_text = (
            f"🔍 **Search Results for:** `{query}` ({len(results)} found)\n"
            f"🌐 **Source:** {source_label}\n\n"
            f"👇 *Tap a title below to view details & download options:*"
        )
        markup = InlineKeyboardMarkup(keyboard)

        # 5. Deliver search results safely
        await _safe_edit_or_send(
            client=client,
            chat_id=chat_id,
            status_msg=status_msg,
            text=res_text,
            reply_markup=markup,
        )

    except Exception:
        log.exception("Unhandled error in hentaisearch")
        try:
            await client.send_message(
                chat_id=message.chat.id,
                text="⚠️ An unexpected error occurred while searching. Please try again.",
                reply_markup=get_main_reply_keyboard(),
            )
        except Exception:
            pass
