"""
Force Subscribe — strict channel membership check with caching.
"""

import logging
import time as _time
from functools import wraps

from pyrogram import Client
from pyrogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import UserNotParticipant, ChatAdminRequired, ChannelPrivate

from utils.db import get_db

log = logging.getLogger(__name__)

# Caches to avoid DB/API spam
_channel_link_cache: dict[int, str] = {}
_channel_id_cache: int | None | str = "unset"
_member_cache: dict[tuple[int, int], float] = {}  # (channel_id, user_id) -> timestamp
_MEMBER_CACHE_TTL = 300  # 5 min


async def get_main_channel_id() -> int | None:
    global _channel_id_cache
    if _channel_id_cache != "unset":
        return _channel_id_cache
    db = get_db()
    doc = await db.config.find_one({"key": "main_channel"})
    _channel_id_cache = int(doc["value"]) if doc else None
    return _channel_id_cache


def clear_fsub_cache():
    global _channel_id_cache
    _channel_id_cache = "unset"
    _channel_link_cache.clear()
    _member_cache.clear()


async def _get_saved_invite_link(channel_id: int) -> str | None:
    db = get_db()
    doc = await db.config.find_one({"key": f"invite_link_{channel_id}"})
    return doc["value"] if doc else None


async def _save_invite_link(channel_id: int, link: str):
    db = get_db()
    await db.config.update_one(
        {"key": f"invite_link_{channel_id}"},
        {"$set": {"key": f"invite_link_{channel_id}", "value": link}},
        upsert=True,
    )


async def _is_member(client: Client, channel_id: int, user_id: int) -> bool:
    cache_key = (channel_id, user_id)
    cached_time = _member_cache.get(cache_key)
    if cached_time and (_time.time() - cached_time) < _MEMBER_CACHE_TTL:
        return True

    try:
        member = await client.get_chat_member(channel_id, user_id)
        is_mem = member.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        )
        if is_mem:
            _member_cache[cache_key] = _time.time()
        return is_mem
    except UserNotParticipant:
        _member_cache.pop(cache_key, None)
        return False
    except ChatAdminRequired:
        log.error("Bot is not admin in channel %s!", channel_id)
        return True
    except ChannelPrivate:
        return True
    except Exception as e:
        log.warning("Membership check error: %s", e)
        return False


async def _get_channel_link(client: Client, channel_id: int) -> str:
    if channel_id in _channel_link_cache:
        return _channel_link_cache[channel_id]

    saved = await _get_saved_invite_link(channel_id)
    if saved and saved.startswith("https://t.me/"):
        _channel_link_cache[channel_id] = saved
        return saved

    link = None
    try:
        chat = await client.get_chat(channel_id)
        if chat.username:
            link = f"https://t.me/{chat.username}"
        elif chat.invite_link:
            link = chat.invite_link
    except Exception:
        pass

    if not link:
        try:
            invite = await client.create_chat_invite_link(
                channel_id, name="Bot Force Sub", creates_join_request=False,
            )
            link = invite.invite_link
        except Exception:
            try:
                link = await client.export_chat_invite_link(channel_id)
            except Exception:
                pass

    if not link:
        link = "https://t.me/+placeholder"
    else:
        await _save_invite_link(channel_id, link)

    _channel_link_cache[channel_id] = link
    return link


NOT_JOINED_TEXT = (
    "⚠️ **You must join our channel to use this bot!**\n\n"
    "👇 Join the channel below, then tap **I've Joined**."
)


async def check_force_sub(client: Client, user_id: int) -> tuple[bool, int | None]:
    channel_id = await get_main_channel_id()
    if not channel_id:
        return True, None
    return await _is_member(client, channel_id, user_id), channel_id


async def send_force_sub_message(client: Client, chat_id: int, channel_id: int):
    from utils.autodelete import track_message
    link = await _get_channel_link(client, channel_id)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel", url=link)],
        [InlineKeyboardButton("🔄 I've Joined", callback_data="checksub")],
    ])
    msg = await client.send_message(chat_id=chat_id, text=NOT_JOINED_TEXT, reply_markup=keyboard)
    await track_message(chat_id, msg.id)


def force_sub(func):
    @wraps(func)
    async def wrapper(client: Client, update, *args, **kwargs):
        if isinstance(update, (CallbackQuery, Message)):
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
