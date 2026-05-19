import logging

from pyrogram import Client
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from api.hanime import details
from utils.auth import approved_only

log = logging.getLogger(__name__)


@approved_only
async def infohentai(client: Client, callback_query: CallbackQuery):
    """Show details for a selected hentai (info_<slug> callback)."""
    slug = callback_query.data.split("_", 1)[1]

    try:
        info = await details(slug)
    except Exception:
        log.exception("Details fetch failed for slug=%s", slug)
        await callback_query.answer("❌ API unavailable, try again later.", show_alert=True)
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

    # Format tags (show up to 10)
    tags_str = ", ".join(tags[:10]) if tags else "N/A"
    if len(tags) > 10:
        tags_str += f" (+{len(tags) - 10} more)"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬇️ Download Now", callback_data=f"dlt_{slug}")],
        [InlineKeyboardButton("🔗 Stream Links", callback_data=f"link_{slug}")],
    ])

    text = (
        f"**{name}**\n\n"
        f"👁 **Views:** {views}\n"
        f"👍 **Likes:** {likes}  |  👎 **Dislikes:** {dislikes}\n"
        f"⏱ **Duration:** {duration}\n"
        f"📅 **Released:** {released}\n"
        f"🏷 **Brand:** {brand}\n"
        f"🔖 **Tags:** {tags_str}"
    )

    try:
        # Try sending with poster image
        if poster:
            await callback_query.message.delete()
            await client.send_photo(
                chat_id=callback_query.from_user.id,
                photo=poster,
                caption=text,
                reply_markup=keyboard,
            )
        else:
            await callback_query.edit_message_text(text, reply_markup=keyboard)
    except Exception:
        # Fallback to text-only if image fails
        try:
            await callback_query.edit_message_text(text, reply_markup=keyboard)
        except Exception:
            log.exception("Failed to edit message for info_%s", slug)
            await callback_query.answer("Something went wrong.", show_alert=True)
