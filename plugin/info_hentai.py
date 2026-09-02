"""Show Series Album details, episode grids, and quality download buttons."""

import asyncio
import logging
import os

from wzgram import Client
from wzgram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from api.hanime_api import HanimeAPI
from utils.auth import approved_only
from utils.fsub import force_sub
from utils.poster import download_poster
from utils.autodelete import track_message, clear_chat_history
from utils.slug_map import get_short_slug, resolve_slug

log = logging.getLogger(__name__)
hanime_api = HanimeAPI()


async def _send_with_poster(client, chat_id, poster_url, text, keyboard):
    """Download poster and send as photo. Returns True on success."""
    poster_path = None
    try:
        poster_path = await download_poster(poster_url)
        if not poster_path:
            return False
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
        if poster_path and os.path.exists(poster_path):
            try:
                os.unlink(poster_path)
            except OSError:
                pass


@approved_only
@force_sub
async def infohentai(client: Client, callback_query: CallbackQuery):
    """Show Album/Series Overview with episode selector grid."""
    raw_data = callback_query.data.split("_", 1)[1]
    slug = await resolve_slug(raw_data)
    chat_id = callback_query.from_user.id
    log.info("=== INFO HANDLER for slug=%s ===", slug)

    try:
        await callback_query.answer("⚡ Loading series...")
    except Exception:
        pass

    try:
        info = await asyncio.to_thread(hanime_api.details, slug)
        if not info:
            raise ValueError(f"No details found for slug={slug}")
    except Exception:
        log.exception("Details fetch failed for slug=%s", slug)
        try:
            await callback_query.answer("❌ Title unavailable, please try again later.", show_alert=True)
        except Exception:
            pass
        return

    name = info.get("title") or info.get("name") or slug
    poster = info.get("poster_url") or info.get("cover_url") or ""
    summary = info.get("description") or "No description available."
    tags = info.get("tags", [])
    episodes = info.get("episodes", [])
    brand = info.get("brand") or "hentaicity.com"

    tags_str = ", ".join(tags[:8]) if tags else "N/A"
    if len(tags) > 8:
        tags_str += f" (+{len(tags) - 8} more)"

    total_eps = len(episodes)
    text = (
        f"🎬 **{name}**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌐 **Source:** {brand}\n"
        f"📺 **Total Episodes:** {total_eps}\n"
        f"🔖 **Tags:** {tags_str}\n\n"
        f"📝 **Summary:**\n{summary[:350]}{'...' if len(summary) > 350 else ''}"
    )

    short_key = await get_short_slug(slug)
    buttons = []

    # If series has multiple episodes -> Show Episode Selection Grid (3 buttons per row)
    if total_eps > 1:
        row = []
        for i, ep in enumerate(episodes):
            ep_slug = ep.get("slug", "")
            ep_num = ep.get("ep", i + 1)
            ep_short = await get_short_slug(ep_slug)
            row.append(InlineKeyboardButton(f"📺 Ep {ep_num}", callback_data=f"eps_{ep_short}"))
            if len(row) == 3:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        buttons.append([InlineKeyboardButton("📥 Download All Episodes (Batch)", callback_data=f"ball_{short_key}")])
        buttons.append([InlineKeyboardButton("🔗 Web Stream Links", callback_data=f"link_{short_key}")])
    else:
        # Single episode series -> Show direct Quality Download Buttons
        streams_data = await asyncio.to_thread(hanime_api.get_streams, slug)
        streams = streams_data.get("streams", [])

        has_4k = any(s.get("height", 0) >= 2160 or "4k" in s.get("label", "").lower() for s in streams)
        has_1080 = any(s.get("height", 0) == 1080 or "1080" in s.get("label", "").lower() for s in streams)
        has_720 = any(s.get("height", 0) == 720 or "720" in s.get("label", "").lower() for s in streams)
        has_480 = any(s.get("height", 0) == 480 or "480" in s.get("label", "").lower() for s in streams)

        if has_4k:
            buttons.append([InlineKeyboardButton("✨ Download 4K (2160p)", callback_data=f"dlt_{short_key}_4k")])
        if has_1080:
            buttons.append([InlineKeyboardButton("📺 Download 1080p (Full HD)", callback_data=f"dlt_{short_key}_1080")])
        if has_720:
            buttons.append([InlineKeyboardButton("📱 Download 720p (HD)", callback_data=f"dlt_{short_key}_720")])
        if has_480:
            buttons.append([InlineKeyboardButton("📼 Download 480p (SD)", callback_data=f"dlt_{short_key}_480")])
        if not (has_4k or has_1080 or has_720 or has_480):
            buttons.append([InlineKeyboardButton("⬇️ Download Video", callback_data=f"dlt_{short_key}_best")])

        buttons.append([InlineKeyboardButton("🔗 Web Stream Links", callback_data=f"link_{short_key}")])

    keyboard = InlineKeyboardMarkup(buttons)

    sent_photo = False
    if poster:
        sent_photo = await _send_with_poster(client, chat_id, poster, text, keyboard)
        if sent_photo:
            try:
                await callback_query.message.delete()
            except Exception:
                pass

    if not sent_photo:
        try:
            await callback_query.edit_message_text(text, reply_markup=keyboard)
        except Exception:
            msg = await client.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)
            await track_message(chat_id, msg.id)


@approved_only
@force_sub
async def episode_info(client: Client, callback_query: CallbackQuery):
    """Show Episode details with Quality Selection Buttons (eps_<slug>)."""
    raw_data = callback_query.data.split("_", 1)[1]
    slug = await resolve_slug(raw_data)
    chat_id = callback_query.from_user.id
    log.info("=== EPISODE INFO for slug=%s ===", slug)

    try:
        await callback_query.answer("⚡ Loading episode qualities...")
    except Exception:
        pass

    try:
        info = await asyncio.to_thread(hanime_api.details, slug)
        streams_data = await asyncio.to_thread(hanime_api.get_streams, slug)
        streams = streams_data.get("streams", [])
    except Exception:
        log.exception("Details fetch failed for episode %s", slug)
        try:
            await callback_query.answer("❌ Episode unavailable.", show_alert=True)
        except Exception:
            pass
        return

    name = info.get("title") or info.get("name") or slug
    poster = info.get("poster_url") or info.get("cover_url") or ""
    brand = info.get("brand") or "hentaicity.com"

    text = (
        f"📺 **{name}**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌐 **Source:** {brand}\n\n"
        f"👇 **Choose video quality to download:**"
    )

    short_key = await get_short_slug(slug)
    buttons = []

    has_4k = any(s.get("height", 0) >= 2160 or "4k" in s.get("label", "").lower() for s in streams)
    has_1080 = any(s.get("height", 0) == 1080 or "1080" in s.get("label", "").lower() for s in streams)
    has_720 = any(s.get("height", 0) == 720 or "720" in s.get("label", "").lower() for s in streams)
    has_480 = any(s.get("height", 0) == 480 or "480" in s.get("label", "").lower() for s in streams)

    if has_4k:
        buttons.append([InlineKeyboardButton("✨ Download 4K (2160p Ultra HD)", callback_data=f"dlt_{short_key}_4k")])
    if has_1080:
        buttons.append([InlineKeyboardButton("📺 Download 1080p (Full HD)", callback_data=f"dlt_{short_key}_1080")])
    if has_720:
        buttons.append([InlineKeyboardButton("📱 Download 720p (HD)", callback_data=f"dlt_{short_key}_720")])
    if has_480:
        buttons.append([InlineKeyboardButton("📼 Download 480p (SD)", callback_data=f"dlt_{short_key}_480")])
    if not (has_4k or has_1080 or has_720 or has_480):
        buttons.append([InlineKeyboardButton("⬇️ Download Video", callback_data=f"dlt_{short_key}_best")])

    buttons.append([InlineKeyboardButton("🔗 Web Stream Link", callback_data=f"link_{short_key}")])
    buttons.append([InlineKeyboardButton("🔙 Back to Series", callback_data=f"info_{short_key}")])

    keyboard = InlineKeyboardMarkup(buttons)

    sent_photo = False
    if poster:
        sent_photo = await _send_with_poster(client, chat_id, poster, text, keyboard)
        if sent_photo:
            try:
                await callback_query.message.delete()
            except Exception:
                pass

    if not sent_photo:
        try:
            await callback_query.edit_message_text(text, reply_markup=keyboard)
        except Exception:
            msg = await client.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)
            await track_message(chat_id, msg.id)
