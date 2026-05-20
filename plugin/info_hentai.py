import logging

from pyrogram import Client
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from api.hanime import details
from utils.auth import approved_only
from utils.fsub import force_sub

log = logging.getLogger(__name__)


@approved_only
@force_sub
async def infohentai(client: Client, callback_query: CallbackQuery):
    """Show details for a selected hentai (info_<slug> callback)."""
    slug = callback_query.data.split("_", 1)[1]

    try:
        await callback_query.answer("Loading details...")
    except Exception:
        pass

    try:
        info = await details(slug)
    except Exception:
        log.exception("Details fetch failed for slug=%s", slug)
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

    # Build keyboard
    buttons = []

    # Episode buttons (if multiple episodes in franchise)
    if len(episodes) > 1:
        text += f"\n\n📂 **Episodes ({len(episodes)}):**"
        for ep in episodes:
            ep_slug = ep.get("slug", "")
            ep_name = ep.get("name", "Unknown")
            if not ep_slug:
                continue
            # Highlight current episode
            prefix = "▶️ " if ep_slug == slug else "📺 "
            display = ep_name if len(ep_name) <= 55 else ep_name[:52] + "..."
            buttons.append([InlineKeyboardButton(
                f"{prefix}{display}",
                callback_data=f"eps_{ep_slug}"
            )])
    else:
        # Single episode — show download/stream for this one
        buttons.append([InlineKeyboardButton("⬇️ Download Now", callback_data=f"dlt_{slug}")])
        buttons.append([InlineKeyboardButton("🔗 Stream Links", callback_data=f"link_{slug}")])

    keyboard = InlineKeyboardMarkup(buttons)

    # Send with poster photo
    sent_photo = False
    if poster:
        try:
            await client.send_photo(
                chat_id=callback_query.from_user.id,
                photo=poster,
                caption=text,
                reply_markup=keyboard,
            )
            sent_photo = True
            try:
                await callback_query.message.delete()
            except Exception:
                pass
        except Exception:
            log.warning("Failed to send poster for %s, falling back to text", slug)

    if not sent_photo:
        try:
            await callback_query.edit_message_text(text, reply_markup=keyboard)
        except Exception:
            try:
                await client.send_message(
                    chat_id=callback_query.from_user.id,
                    text=text,
                    reply_markup=keyboard,
                )
            except Exception:
                log.exception("All methods failed for info_%s", slug)


@approved_only
@force_sub
async def episode_info(client: Client, callback_query: CallbackQuery):
    """Show download/stream options for a specific episode (eps_<slug>)."""
    slug = callback_query.data.split("_", 1)[1]

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

    # Extract series slug for back button
    series_slug = slug
    if episodes:
        series_slug = episodes[0].get("slug", slug)

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
        try:
            await client.send_photo(
                chat_id=callback_query.from_user.id,
                photo=poster,
                caption=text,
                reply_markup=keyboard,
            )
            sent_photo = True
            try:
                await callback_query.message.delete()
            except Exception:
                pass
        except Exception:
            log.warning("Poster failed for episode %s", slug)

    if not sent_photo:
        try:
            await callback_query.edit_message_text(text, reply_markup=keyboard)
        except Exception:
            try:
                await client.send_message(
                    chat_id=callback_query.from_user.id,
                    text=text,
                    reply_markup=keyboard,
                )
            except Exception:
                log.exception("All methods failed for eps_%s", slug)
