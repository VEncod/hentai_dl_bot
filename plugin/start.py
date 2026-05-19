"""
/start command handler.

On first run (no admins exist), the user who sends /start becomes the owner.
Sends a welcome photo with bot info.
"""

import logging
from datetime import datetime, timezone

import aiohttp
from pyrogram import Client
from pyrogram.types import (
    Message,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from utils.db import get_db

log = logging.getLogger(__name__)

# Fallback waifu images if API fails
FALLBACK_IMAGES = [
    "https://nekos.best/api/v2/waifu/de0d245b-03d6-4485-bfe7-8e274d29938f.png",
]


async def _get_waifu_image() -> str:
    """Fetch a random waifu image URL from nekos.best API."""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            async with session.get("https://nekos.best/api/v2/waifu") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["results"][0]["url"]
    except Exception:
        log.warning("Failed to fetch waifu image, using fallback")
    return FALLBACK_IMAGES[0]

WELCOME_TEXT = (
    "✨ **Welcome to Hentai DL Bot** ✨\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "🎌 Your ultimate hentai companion — search, stream,\n"
    "and download your favorite titles directly to Telegram.\n\n"
    "**📖 How to use:**\n"
    "• `/search <name>` — Search for hentai\n"
    "• Tap a result → View details → Download or Stream\n"
    "• `/archive <series>` — Browse archived episodes\n"
    "• `/series` — List all archived series\n\n"
    "**🔐 Access:**\n"
    "• `/request` — Request access if you're new\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "⚡ Powered by Hanime.tv API & FFmpeg\n"
    "👨‍💻 **Created by Mr. Aman**\n"
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
    "👨‍💻 **Created by Mr. Aman**\n"
)


async def _send_welcome_photo(client: Client, chat_id: int, text: str, keyboard):
    """Try to send welcome as photo with random waifu, fallback to text."""
    waifu_url = await _get_waifu_image()
    try:
        await client.send_photo(
            chat_id=chat_id,
            photo=waifu_url,
            caption=text,
            reply_markup=keyboard,
        )
    except Exception:
        # Fallback: send as animation (GIF)
        try:
            await client.send_animation(
                chat_id=chat_id,
                animation="https://telegra.ph/file/cdeae50a8a23041b01935.mp4",
                caption=text,
                reply_markup=keyboard,
            )
        except Exception:
            # Final fallback: text only
            await client.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=keyboard,
            )


async def start_command(client: Client, message: Message):
    user = message.from_user
    db = get_db()

    # Check if any admins exist
    admin_count = await db.admins.count_documents({})
    if admin_count == 0:
        # First user becomes owner
        await db.admins.insert_one({
            "user_id": user.id,
            "role": "owner",
            "added_at": datetime.now(timezone.utc),
        })

        # Also auto-approve the owner
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

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Updates", url="https://t.me/metavoid")],
            [InlineKeyboardButton("💬 Support", url="https://t.me/metavoidsupport")],
        ])

        await _send_welcome_photo(client, message.chat.id, OWNER_SETUP_TEXT, keyboard)
        return

    # Regular /start
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Search Hentai", switch_inline_query_current_chat="/search ")],
        [
            InlineKeyboardButton("📢 Updates", url="https://t.me/metavoid"),
            InlineKeyboardButton("💬 Support", url="https://t.me/metavoidsupport"),
        ],
        [InlineKeyboardButton("👨‍💻 Created by Mr. Aman", url="https://t.me/Am_ankhan")],
    ])

    await _send_welcome_photo(client, message.chat.id, WELCOME_TEXT, keyboard)
