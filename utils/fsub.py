"""
Force Subscribe — strict channel membership check.

Users MUST join the main channel to use ANY feature of the bot.
Applied to /start, search, info, download — everything.
"""

import logging
from functools import wraps

from pyrogram import Client
from pyrogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import UserNotParticipant, ChatAdminRequired, ChannelPrivate

from utils.db import get_db

log = logging.getLogger(__name__)

# In-memory cache
_channel_link_cache: dict[int, str] = {}


async def get_main_channel_id() -> int | None:
    """Get the main channel ID from config."""
    db = get_db()
    doc = await db.config.find_one({"key": "main_channel"})
    if doc:
        return int(doc["value"])
    return None


async def _get_saved_invite_link(channel_id: int) -> str | None:
    """Get saved invite link from DB."""
    db = get_db()
    doc = await db.config.find_one({"key": f"invite_link_{channel_id}"})
    if doc:
        return doc["value"]
    return None


async def _save_invite_link(channel_id: int, link: str):
    """Save invite link to DB for persistence."""
    db = get_db()
    await db.config.update_one(
        {"key": f"invite_link_{channel_id}"},
        {"$set": {"key": f"invite_link_{channel_id}", "value": link}},
        upsert=True,
    )


async def _is_member(client: Client, channel_id: int, user_id: int) -> bool:
    """Check if user is a member of the channel. Strict — defaults to False on errors."""
    try:
        member = await client.get_chat_member(channel_id, user_id)
        return member.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        )
    except UserNotParticipant:
        return False
    except ChatAdminRequired:
        log.error("Bot is not admin in channel %s — can't check membership!", channel_id)
        # Can't verify, but don't block users if bot isn't set up right
        return True
    except ChannelPrivate:
        log.error("Channel %s is private and bot isn't a member!", channel_id)
        return True
    except Exception as e:
        log.warning("Membership check failed for user %s in %s: %s", user_id, channel_id, e)
        # Strict: if we can't check, assume NOT a member
        return False


async def _get_channel_link(client: Client, channel_id: int) -> str:
    """Get the channel invite/public link."""
    if channel_id in _channel_link_cache:
        return _channel_link_cache[channel_id]

    # Check DB for saved permanent link
    saved = await _get_saved_invite_link(channel_id)
    if saved:
        _channel_link_cache[channel_id] = saved
        return saved

    link = None
    try:
        chat = await client.get_chat(channel_id)
        log.info("Channel %s: username=%s invite_link=%s", channel_id, chat.username, chat.invite_link)

        if chat.username:
            link = f"https://t.me/{chat.username}"
        elif chat.invite_link:
            link = chat.invite_link
    except Exception:
        log.warning("get_chat failed for %s", channel_id)

    # If no public link, create a permanent invite link
    if not link:
        try:
            # create_chat_invite_link with no expiry = permanent
            invite = await client.create_chat_invite_link(
                channel_id,
                name="Force Sub Bot Link",
                creates_join_request=False,
            )
            link = invite.invite_link
            log.info("Created permanent invite link for %s: %s", channel_id, link)
        except Exception:
            log.warning("create_chat_invite_link failed for %s, trying export", channel_id)
            try:
                link = await client.export_chat_invite_link(channel_id)
                log.info("Exported invite link for %s: %s", channel_id, link)
            except Exception:
                log.warning("export_chat_invite_link also failed for %s", channel_id)

    if not link:
        log.error("Could not get ANY join link for channel %s! Make sure bot is admin.", channel_id)
        link = "https://t.me/+placeholder"
    else:
        # Save to DB so it persists across restarts
        await _save_invite_link(channel_id, link)

    _channel_link_cache[channel_id] = link
    return link


NOT_JOINED_TEXT = (
    "⚠️ **You must join our channel to use this bot!**\n\n"
    "👇 Join the channel below, then come back and try again."
)


async def check_force_sub(client: Client, user_id: int) -> tuple[bool, int | None]:
    """
    Check if force-sub is required and if user passes.
    Returns (passed, channel_id).
    """
    channel_id = await get_main_channel_id()
    if not channel_id:
        return True, None
    is_mem = await _is_member(client, channel_id, user_id)
    return is_mem, channel_id


async def send_force_sub_message(client: Client, chat_id: int, channel_id: int):
    """Send the 'join channel' message with button."""
    link = await _get_channel_link(client, channel_id)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel", url=link)],
        [InlineKeyboardButton("🔄 I've Joined", callback_data="checksub")],
    ])
    await client.send_message(
        chat_id=chat_id,
        text=NOT_JOINED_TEXT,
        reply_markup=keyboard,
    )


def force_sub(func):
    """Decorator: strictly check if user has joined the main channel."""

    @wraps(func)
    async def wrapper(client: Client, update, *args, **kwargs):
        if isinstance(update, CallbackQuery):
            user_id = update.from_user.id
        elif isinstance(update, Message):
            user_id = update.from_user.id
        else:
            return

        passed, channel_id = await check_force_sub(client, user_id)

        if not passed and channel_id:
            if isinstance(update, CallbackQuery):
                await update.answer("⚠️ Join our channel first!", show_alert=True)
                try:
                    await send_force_sub_message(client, update.from_user.id, channel_id)
                except Exception:
                    pass
            else:
                try:
                    await send_force_sub_message(client, update.chat.id, channel_id)
                except Exception:
                    pass
            return

        return await func(client, update, *args, **kwargs)

    return wrapper
