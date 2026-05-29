"""
/start command handler.

On first run (no admins exist), the user who sends /start becomes the owner.
Sends a random welcome image from assets/welcome/.
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from pyrogram import Client
from pyrogram.types import (
    Message,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from pymongo import ReturnDocument
from utils.db import get_db
from utils.autodelete import track_message
from utils.fsub import check_force_sub, send_force_sub_message
from utils.autodelete import schedule_chat_wipe, cancel_chat_wipe

log = logging.getLogger(__name__)

# Path to welcome images
WELCOME_DIR = Path(__file__).parent.parent / "assets" / "welcome"

WELCOME_TEXT = (
    "✨ **Welcome to Hentai DL Bot** ✨\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "🎌 Your ultimate hentai companion — search, stream,\n"
    "and download your favorite titles directly to Telegram.\n\n"
    "💬 **Just type any hentai name to search!**\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "⚡ Powered by Hanime.tv API & FFmpeg\n"
    "👨‍💻 **Created by Mr. Aman**"
)

OWNER_SETUP_TEXT = (
    "👑 **Owner Setup Complete!**\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "You are the **super admin** of this bot.\n\n"
    "**🛡 Admin Commands:**\n"
    "• `/addadmin <user_id>` — Add admins\n"
    "• `/removeadmin <user_id>` — Remove admins\n"
    "• `/admins` — List all admins\n\n"
    "**👥 User Management:**\n"
    "• `/pending` — View access requests\n"
    "• `/approve / /reject <user_id>`\n"
    "• `/adduser / /removeuser <user_id>`\n"
    "• `/users` — List approved users\n\n"
    "**📢 Channel Setup:**\n"
    "• `/setlog <channel_id>` — Set log channel\n"
    "• `/setchannel <channel_id>` — Set archive channel\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "⚡ Powered by Hanime.tv API & FFmpeg\n"
    "👨‍💻 **Created by Mr. Aman**"
)


def _get_sorted_welcome_images() -> list[str]:
    """Get all welcome images sorted numerically."""
    if not WELCOME_DIR.exists():
        return []
    images = list(WELCOME_DIR.glob("*.jpg")) + list(WELCOME_DIR.glob("*.png"))
    if not images:
        return []
    # Sort numerically by filename (1.jpg, 2.jpg, ..., 22.jpg)
    images.sort(key=lambda p: int(p.stem) if p.stem.isdigit() else p.stem)
    return [str(img) for img in images]


async def _get_next_welcome_image() -> str | None:
    """Get the next welcome image in sequence using a global counter in DB."""
    images = _get_sorted_welcome_images()
    if not images:
        return None

    db = get_db()
    # Atomically increment and get the counter
    result = await db.bot_state.find_one_and_update(
        {"_id": "welcome_image_counter"},
        {"$inc": {"count": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    index = (result["count"] - 1) % len(images)
    return images[index]


async def _send_welcome(client: Client, chat_id: int, text: str) -> Message | None:
    """Send welcome message with the next image in sequence. Returns the sent message."""
    img = await _get_next_welcome_image()
    msg = None
    if img:
        try:
            msg = await client.send_photo(
                chat_id=chat_id,
                photo=img,
                caption=text,
            )
        except Exception:
            log.warning("Failed to send welcome image")

    if not msg:
        msg = await client.send_message(chat_id=chat_id, text=text)

    if msg:
        await track_message(chat_id, msg.id)

    return msg


async def checksub_callback(client, callback_query):
    """Handle 'I've Joined' button — re-check membership."""
    user_id = callback_query.from_user.id
    passed, channel_id = await check_force_sub(client, user_id)
    if passed:
        await callback_query.answer("✅ Verified! You can now use the bot.", show_alert=True)
        try:
            await callback_query.message.delete()
        except Exception:
            pass
    else:
        await callback_query.answer("❌ You haven't joined yet! Please join the channel first.", show_alert=True)


async def start_command(client: Client, message: Message):
    user = message.from_user
    db = get_db()
    chat_id = message.chat.id
    user_message_id = message.id

    # Schedule full chat wipe after 10 minutes
    await schedule_chat_wipe(chat_id)

    # Force-sub check FIRST
    passed, channel_id = await check_force_sub(client, user.id)
    if not passed and channel_id:
        await send_force_sub_message(client, message.chat.id, channel_id)
        return

    # Check if any admins exist
    admin_count = await db.admins.count_documents({})
    if admin_count == 0:
        # First user becomes owner
        await db.admins.insert_one({
            "user_id": user.id,
            "role": "owner",
            "added_at": datetime.now(timezone.utc),
        })

        await db.approved_users.update_one(
            {"user_id": user.id},
            {"$set": {
                "user_id": user.id,
                "username": user.username or "",
                "approved_by": user.id,
                "approved_at": datetime.now(timezone.utc),
            }},
            upsert=True,
        )

        log.info("Owner set up: user_id=%s username=%s", user.id, user.username)
        await _send_welcome(client, message.chat.id, OWNER_SETUP_TEXT)
        return

    # Regular /start
    await _send_welcome(client, message.chat.id, WELCOME_TEXT)
