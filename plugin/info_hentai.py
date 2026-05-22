import logging
import os
import traceback

from pyrogram import Client
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from api.hanime import details
from utils.auth import approved_only
from utils.fsub import force_sub
from utils.poster import download_poster
from utils.autodelete import track_message

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
    log.info("=== INFO HANDLER CALLED for slug=%s ===", slug)

    try:
        await callback_query.answer("Loading details...")
    except Exception:
        pass

    try:
        log.info("Fetching details for %s...", slug)
        info = await details(slug)
        log.info("Got details: name=%s, episodes=%d, poster=%s",
                 info.get("name"), len(info.get("episodes", [])), bool(info.get("poster_url")))
    except Exception:
        log.exception("Details fetch FAILED for slug=%s", slug)
        try:
            await callback_query.answer("❌ API unavailable, try again later.", show_alert=True)
        except Exception:
            pass
        return

    name = info["name"]
    poster = info["poster_url"]
    views = f'{info["views"]:,}' if isinstance(info["views"], int) else info["views"]
    released = info["released_date"]
    likes = f'{info["likes"]:,}' if isinstance(info["likes"], int) else info["likes"]
    dislikes = f'{info["dislikes"]:,}' if isinstance(info["dislikes"], int) else info["dislikes"]
    duration = info["duration"]
    brand = info["brand"]
    tags = info["tags"]
    episodes = info.get("episodes", [])

    tags_str = ", ".join(tags[:10]) if tags else "N/A"
    if len(tags) > 10:
        tags_str += f" (+{len(tags) - 10} more)"

    text = (
        f"**{name}**\n\n"
        f"👁 **Views:** {views}\n"
        f"👍 **Likes:** {likes}  |  👎 **Dislikes:** {dislikes}\n"
        f"⏱ **Duration:** {duration}\n"
        f"📅 **Released:** {released}\n"
        f"🏷 **Brand:** {brand}\n"
        f"🔖 **Tags:** {tags_str}"
    )

    buttons = []

    if len(episodes) > 1:
        text += f"\n\n📂 **Episodes ({len(episodes)}):**"
        for ep in episodes:
            ep_slug = ep.get("slug", "")
            ep_name = ep.get("name", "Unknown")
            if not ep_slug:
                continue
            prefix = "▶️ " if ep_slug == slug else "📺 "
            display = ep_name if len(ep_name) <= 55 else ep_name[:52] + "..."
            buttons.append([InlineKeyboardButton(
                f"{prefix}{display}",
                callback_data=f"eps_{ep_slug}"
            )])
        # Batch download all episodes
        buttons.append([InlineKeyboardButton(
            f"⬇️ Download All {len(episodes)} Episodes",
            callback_data=f"ball_{slug}"
        )])
    else:
        buttons.append([InlineKeyboardButton("⬇️ Download Now", callback_data=f"dlt_{slug}")])
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
        info = await details(slug)
    except Exception:
        log.exception("Details fetch failed for episode slug=%s", slug)
        try:
            await callback_query.answer("❌ API unavailable", show_alert=True)
        except Exception:
            pass
        return

    name = info["name"]
    poster = info["poster_url"]
    duration = info["duration"]
    episodes = info.get("episodes", [])

    series_slug = episodes[0].get("slug", slug) if episodes else slug

    text = (
        f"📺 **{name}**\n"
        f"⏱ Duration: {duration}\n\n"
        "Choose an option:"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬇️ Download", callback_data=f"dlt_{slug}")],
        [InlineKeyboardButton("🔗 Stream Links", callback_data=f"link_{slug}")],
        [InlineKeyboardButton("⬅️ Back to Episodes", callback_data=f"info_{series_slug}")],
    ])

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
