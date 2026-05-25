"""
Hindi Dubbed Hentai — Telegram search + channel forward.

Handlers:
  - hindi_<slug>  callback  → search for Hindi dub and send to user
  - /addhindi <channel_id>  → add a channel to the search list (admin)
  - /removehindi <channel_id> → remove a channel (admin)
  - /hindichannels           → list configured channels (admin)
"""

import logging

from pyrogram import Client
from pyrogram.types import (
    CallbackQuery,
    Message,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from api.hanime_api import HanimeAPI

hanime_api = HanimeAPI()

from utils.auth import approved_only
from utils.fsub import force_sub
from utils.autodelete import track_message
from utils.hindi_dub import (
    search_hindi_dub,
    add_hindi_channel,
    remove_hindi_channel,
    list_hindi_channels,
    get_userbot,
    clear_hindi_cache,
)

log = logging.getLogger(__name__)


# ── Hindi dub search callback ────────────────────────────────────────────

@approved_only
@force_sub
async def hindi_dub_handler(client: Client, callback_query: CallbackQuery):
    """
    Search for Hindi dubbed version when user clicks 🇮🇳 Hindi Dub button.
    Callback data: hindi_<slug>
    """
    slug = callback_query.data.split("_", 1)[1]
    chat_id = callback_query.from_user.id

    log.info("=== HINDI DUB SEARCH === slug=%s user=%s", slug, chat_id)

    # Check if userbot is available
    if not get_userbot():
        await callback_query.answer(
            "❌ Hindi dub search is not available right now.",
            show_alert=True,
        )
        return

    try:
        await callback_query.answer("🔍 Searching for Hindi dub...")
    except Exception:
        pass

    # Get video name for better search
    name = ""
    try:
        info = hanime_api.details(slug)
        name = info.get("name", "")
    except Exception:
        pass

    # Show searching message
    status_msg = None
    try:
        status_msg = await client.send_message(
            chat_id=chat_id,
            text=(
                "🇮🇳 **Searching for Hindi Dub...**\n\n"
                f"📺 **{name or slug}**\n\n"
                "🔍 Checking Hindi dub channels...\n"
                "⏳ This may take a few minutes."
            ),
        )
        await track_message(chat_id, status_msg.id)
    except Exception:
        pass

    # Progress callback
    async def on_progress(status_text: str):
        if status_msg:
            try:
                await status_msg.edit_text(
                    f"🇮🇳 **Hindi Dub Search**\n\n"
                    f"📺 **{name or slug}**\n\n"
                    f"{status_text}"
                )
            except Exception:
                pass

    # Search!
    result = await search_hindi_dub(slug, name, progress_cb=on_progress)

    if result:
        # Found! Send the file to user
        file_id = result["file_id"]
        file_name = result.get("file_name", "")
        channel_title = result.get("channel_title", "Unknown")
        file_size = result.get("file_size", 0)
        size_mb = file_size / (1024 * 1024) if file_size else 0

        caption = (
            f"🇮🇳 **{name or slug}** — Hindi Dubbed\n\n"
            f"📁 {file_name}\n"
        )
        if size_mb > 0:
            caption += f"📦 Size: {size_mb:.1f} MB\n"
        caption += f"📺 Source: {channel_title}\n\nDownloaded via @hanime_dl_bot"

        try:
            sent = await client.send_document(
                chat_id=chat_id,
                document=file_id,
                caption=caption,
            )
            await track_message(chat_id, sent.id)

            # Update status message
            if status_msg:
                try:
                    await status_msg.edit_text(
                        f"✅ **Hindi Dub Found!**\n\n"
                        f"📺 **{name or slug}**\n"
                        f"📁 Source: {channel_title}\n\n"
                        f"💾 Auto-deletes in 30 min. Save it!"
                    )
                except Exception:
                    pass

        except Exception:
            log.exception("Failed to send Hindi dub file for %s", slug)
            if status_msg:
                try:
                    await status_msg.edit_text(
                        f"❌ Found the file but couldn't send it.\n"
                        f"Source: {channel_title}\n\n"
                        f"The file might be too large or restricted."
                    )
                except Exception:
                    pass
    else:
        # Not found
        if status_msg:
            try:
                await status_msg.edit_text(
                    f"❌ **Hindi Dub Not Available**\n\n"
                    f"📺 **{name or slug}**\n\n"
                    "No Hindi dubbed version was found in any channel.\n"
                    "Try again later — new dubs are added regularly!",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton(
                            "⬅️ Back",
                            callback_data=f"info_{slug}",
                        )]
                    ]),
                )
            except Exception:
                pass


# ── Admin commands ───────────────────────────────────────────────────────

async def addhindi_command(client: Client, message: Message):
    """Add a Hindi dub channel. Usage: /addhindi <channel_id_or_username>"""
    from utils.auth import is_admin

    if not await is_admin(message.from_user.id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply_text(
            "**Usage:** `/addhindi <channel_id or @username>`\n\n"
            "Example:\n"
            "• `/addhindi -1001234567890`\n"
            "• `/addhindi @hindihentai`"
        )
        return

    target = parts[1].strip()

    # Try to resolve channel info via userbot
    ub = get_userbot()
    if not ub:
        await message.reply_text("❌ Userbot not available. Can't add channels.")
        return

    try:
        chat = await ub.get_chat(target)
        ch_id = chat.id
        ch_title = chat.title or str(ch_id)
    except Exception as e:
        log.warning("Could not resolve channel '%s': %s", target, e)
        # Try as raw integer
        try:
            ch_id = int(target)
            ch_title = target
        except ValueError:
            await message.reply_text(f"❌ Could not find channel: `{target}`")
            return

    await add_hindi_channel(ch_id, ch_title)
    await message.reply_text(
        f"✅ Added Hindi dub channel:\n\n"
        f"**{ch_title}**\n"
        f"ID: `{ch_id}`"
    )


async def removehindi_command(client: Client, message: Message):
    """Remove a Hindi dub channel. Usage: /removehindi <channel_id>"""
    from utils.auth import is_admin

    if not await is_admin(message.from_user.id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply_text("**Usage:** `/removehindi <channel_id>`")
        return

    try:
        ch_id = int(parts[1].strip())
    except ValueError:
        await message.reply_text("❌ Channel ID must be a number.")
        return

    removed = await remove_hindi_channel(ch_id)
    if removed:
        await message.reply_text(f"✅ Removed channel `{ch_id}` from Hindi dub list.")
    else:
        await message.reply_text(f"❌ Channel `{ch_id}` not found in the list.")


async def hindichannels_command(client: Client, message: Message):
    """List all configured Hindi dub channels."""
    from utils.auth import is_admin

    if not await is_admin(message.from_user.id):
        return

    channels = await list_hindi_channels()
    if not channels:
        await message.reply_text(
            "📋 **No Hindi dub channels configured.**\n\n"
            "Add channels with `/addhindi <channel_id>`"
        )
        return

    text = "📋 **Hindi Dub Channels:**\n\n"
    for ch in channels:
        text += f"• **{ch.get('title', 'Unknown')}** — `{ch['channel_id']}`\n"

    text += f"\nTotal: {len(channels)} channels"
    await message.reply_text(text)


async def clearhindi_command(client: Client, message: Message):
    """Clear Hindi dub cache. Usage: /clearhindi [slug]"""
    from utils.auth import is_admin

    if not await is_admin(message.from_user.id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) > 1:
        slug = parts[1].strip()
        await clear_hindi_cache(slug)
        await message.reply_text(f"✅ Cleared Hindi cache for `{slug}`")
    else:
        await clear_hindi_cache()
        await message.reply_text("✅ Cleared entire Hindi dub cache")
