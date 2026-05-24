import logging
import os
import traceback

from pyrogram import Client
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from api.hanime_api import HanimeAPI, BASE_URL

hanime_api = HanimeAPI()
from utils.auth import approved_only
from utils.fsub import force_sub
from utils.poster import download_poster
from utils.autodelete import track_message, clear_chat_history

log = logging.getLogger(__name__)


async def _send_with_poster(client, chat_id, poster_url, text, keyboard):
    """Download poster and send as photo. Returns True on success."""
    poster_path = None
    try:
        poster_path = await download_poster(poster_url)
        if not poster_path:
            log.warning("Poster download returned None for %s", poster_url)
            return False
        log.info("Poster downloaded to %s, size=%d", poster_path, os.path.getsize(poster_path))
        msg = await client.send_photo(
            chat_id=chat_id,
            photo=poster_path,
            caption=text,
            reply_markup=keyboard,
        )
        await track_message(chat_id, msg.id)
        return True
    except Exception:
        log.exception("Failed to send poster")
        return False
    finally:
        if poster_path:
            try:
                os.unlink(poster_path)
            except Exception:
                pass


@approved_only
@force_sub
async def infohentai(client: Client, callback_query: CallbackQuery):
    """Show details for a selected hentai (info_<slug> callback)."""
    slug = callback_query.data.split("_", 1)[1]
    chat_id = callback_query.from_user.id
    log.info("=== INFO HANDLER CALLED for slug=%s ===", slug)

    # Clear old messages before showing new info
    await clear_chat_history(client, chat_id, preserve_message_ids=[callback_query.message.id])

    try:
        await callback_query.answer("Loading details...")
    except Exception:
        pass

    try:
        log.info("Fetching details for %s...", slug)
        info = hanime_api.details(slug)
        if not info:
            raise ValueError(f"No details found for slug={slug}")
        log.info("Got details: name=%s, episodes=%d, poster=%s",
                 info.get("name"), len(info.get("episodes", [])), bool(info.get("poster_url")))
    except Exception:
        log.exception("Details fetch FAILED for slug=%s", slug)
        try:
            await callback_query.answer("❌ API unavailable, try again later.", show_alert=True)
        except Exception:
            pass
        return

    name = info["title"]
    poster = info["poster_url"]
    summary = info["description"]
    tags = info["tags"]
    episodes = info.get("episodes", [])

    tags_str = ", ".join(tags[:10]) if tags else "N/A"
    if len(tags) > 10:
        tags_str += f" (+{len(tags) - 10} more)"

    text = (
        f"**{name}**\n\n"
        f"📝 **Summary:** {summary}\n"
        f"🔖 **Tags:** {tags_str}"
    )

    buttons = []

    buttons.append([InlineKeyboardButton("⬇️ Download Now", callback_data=f"dlt_{slug}")])
    
    # Add batch download button if there are multiple episodes
    if len(episodes) > 1:
        buttons.append([InlineKeyboardButton("📥 Download All Episodes", callback_data=f"ball_{slug}")])
    
    buttons.append([InlineKeyboardButton("🇮🇳 Hindi Dub", callback_data=f"hindi_{slug}")])
    buttons.append([InlineKeyboardButton("🔗 Stream Links", callback_data=f"link_{slug}")])

    keyboard = InlineKeyboardMarkup(buttons)

    log.info("Attempting to send info for %s (poster=%s, episodes=%d)", slug, bool(poster), len(episodes))

    # Try with poster
    sent_photo = False
    if poster:
        sent_photo = await _send_with_poster(
            client, callback_query.from_user.id, poster, text, keyboard
        )
        if sent_photo:
            log.info("Poster sent successfully for %s", slug)
            try:
                await callback_query.message.delete()
            except Exception:
                pass

    # Fallback to text
    if not sent_photo:
        log.info("Falling back to text for %s", slug)
        try:
            await callback_query.edit_message_text(text, reply_markup=keyboard)
            log.info("Text edit successful for %s", slug)
        except Exception as e:
            log.warning("edit_message_text failed for %s: %s", slug, e)
            try:
                msg = await client.send_message(
                    chat_id=callback_query.from_user.id,
                    text=text,
                    reply_markup=keyboard,
                )
                await track_message(callback_query.from_user.id, msg.id)
                log.info("Sent as new message for %s", slug)
            except Exception:
                log.exception("ALL methods failed for info_%s", slug)


@approved_only
@force_sub
async def episode_info(client: Client, callback_query: CallbackQuery):
    """Show download/stream options for a specific episode (eps_<slug>)."""
    slug = callback_query.data.split("_", 1)[1]
    log.info("=== EPISODE INFO CALLED for slug=%s ===", slug)

    try:
        await callback_query.answer("Loading episode...")
    except Exception:
        pass

    try:
        info = hanime_api.details(slug)
        if not info:
            raise ValueError(f"No details found for slug={slug}")
    except Exception:
        log.exception("Details fetch failed for episode slug=%s", slug)
        try:
            await callback_query.answer("❌ API unavailable", show_alert=True)
        except Exception:
            pass
        return

    name = info["title"]
    poster = info["poster_url"]

    text = (
        f"📺 **{name}**\n\n"
        "Choose an option:"
    )

    # Get episode info to check if series has multiple episodes
    series_info = None
    try:
        series_info = hanime_api.details(slug)
    except Exception:
        pass
    
    has_multiple_eps = series_info and len(series_info.get("episodes", [])) > 1
    
    buttons = [
        [InlineKeyboardButton("⬇️ Download", callback_data=f"dlt_{slug}")],
    ]
    
    if has_multiple_eps:
        buttons.append([InlineKeyboardButton("📥 Download All Episodes", callback_data=f"ball_{slug}")])
    
    buttons.extend([
        [InlineKeyboardButton("🔗 Stream Links", callback_data=f"link_{slug}")],
        [InlineKeyboardButton("⬅️ Back to Info", callback_data=f"info_{slug}")],
    ])
    
    keyboard = InlineKeyboardMarkup(buttons)

    sent_photo = False
    if poster:
        sent_photo = await _send_with_poster(
            client, callback_query.from_user.id, poster, text, keyboard
        )
        if sent_photo:
            try:
                await callback_query.message.delete()
            except Exception:
                pass

    if not sent_photo:
        try:
            await callback_query.edit_message_text(text, reply_markup=keyboard)
        except Exception:
            try:
                msg = await client.send_message(
                    chat_id=callback_query.from_user.id,
                    text=text,
                    reply_markup=keyboard,
                )
                await track_message(callback_query.from_user.id, msg.id)
            except Exception:
                log.exception("All methods failed for eps_%s", slug)
