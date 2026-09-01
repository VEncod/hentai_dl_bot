"""
Auto-delete system — wipes chat history after a set delay.

Features:
- Stores tracked message IDs in MongoDB (db.autodelete) so tracked messages survive bot restarts.
- Auto-wipes private chat messages via Bot Client (and Userbot if configured).
"""

import asyncio
import logging
from datetime import datetime, timezone
from wzgram import Client
from utils.db import get_db

log = logging.getLogger(__name__)

WIPE_AFTER_MINUTES = 10

_bot: Client | None = None
_userbot: Client | None = None
_active_timers: dict[int, asyncio.Task] = {}


def set_bot(client: Client):
    global _bot
    _bot = client
    log.info("Bot client registered for auto-delete")


def set_userbot(client: Client | None):
    """Register or unregister the userbot client for user message deletion."""
    global _userbot
    _userbot = client
    if client:
        log.info("Userbot registered for auto-delete")
    else:
        log.info("Userbot unregistered / disabled for auto-delete")


async def _handle_userbot_error(e: Exception):
    """Check if exception was caused by session revocation / termination."""
    global _userbot
    error_name = type(e).__name__
    is_auth_error = any(
        kw in error_name for kw in ("AuthKey", "SessionRevoked", "SessionExpired", "Unauthorized", "UserDeactivated")
    )
    if is_auth_error:
        log.warning(
            "Userbot session was terminated or revoked by Telegram (%s: %s). Falling back to normal bot mode.",
            error_name, e
        )
        _userbot = None
        try:
            from utils.session_store import delete_session_string
            await delete_session_string()
        except Exception:
            pass



async def _track_db(chat_id: int, message_id: int):
    try:
        db = get_db()
        await db.autodelete.update_one(
            {"chat_id": chat_id},
            {
                "$addToSet": {"msg_ids": message_id},
                "$setOnInsert": {"created_at": datetime.now(timezone.utc)}
            },
            upsert=True
        )
    except Exception as e:
        log.warning("Failed to save tracked message to MongoDB: %s", e)


async def _wipe_chat(chat_id: int):
    db = get_db()
    doc = await db.autodelete.find_one({"chat_id": chat_id})
    msg_ids = sorted(doc.get("msg_ids", [])) if doc else []

    if not msg_ids:
        log.info("No tracked messages for chat %s", chat_id)
        await db.autodelete.delete_one({"chat_id": chat_id})
        return True

    log.info("Wiping chat %s: %d tracked messages: %s", chat_id, len(msg_ids), msg_ids)
    deleted = 0

    for i in range(0, len(msg_ids), 100):
        batch = msg_ids[i:i + 100]
        batch_deleted = False

        if _bot and not batch_deleted:
            try:
                count = await _bot.delete_messages(chat_id, batch)
                log.info("Bot delete_messages for chat %s: result=%s", chat_id, count)
                deleted += len(batch)
                batch_deleted = True
            except Exception as e:
                log.warning("Bot delete_messages failed for chat %s: %s", chat_id, e)

        if _userbot and not batch_deleted:
            try:
                count = await _userbot.delete_messages(chat_id, batch, revoke=True)
                log.info("Userbot delete_messages for chat %s: result=%s", chat_id, count)
                deleted += len(batch)
                batch_deleted = True
            except Exception as e:
                await _handle_userbot_error(e)
                log.warning("Userbot delete_messages failed for chat %s: %s", chat_id, e)

        # Last resort: delete one by one via bot
        if _bot and not batch_deleted:
            for mid in batch:
                try:
                    await _bot.delete_messages(chat_id, mid)
                    deleted += 1
                except Exception as e:
                    log.warning("Bot single delete failed for chat %s msg %s: %s", chat_id, mid, e)

    # Also try to get and delete any untracked messages via userbot
    if _userbot:
        try:
            remaining = []
            async for msg in _userbot.get_chat_history(chat_id, limit=200):
                remaining.append(msg.id)
            if remaining:
                log.info("Found %d untracked messages in chat %s, deleting", len(remaining), chat_id)
                for i in range(0, len(remaining), 100):
                    batch = remaining[i:i + 100]
                    try:
                        await _userbot.delete_messages(chat_id, batch, revoke=True)
                    except Exception as e:
                        await _handle_userbot_error(e)
        except Exception as e:
            await _handle_userbot_error(e)
            log.debug("Userbot cleanup for chat %s: %s", chat_id, e)

    await db.autodelete.delete_one({"chat_id": chat_id})
    log.info("Chat wipe complete for %s: deleted %d messages (tracked %d)", chat_id, deleted, len(msg_ids))
    return True


async def _delayed_wipe(chat_id: int, delay_seconds: int):
    try:
        log.info("Timer started: chat %s will be wiped in %d seconds", chat_id, delay_seconds)
        await asyncio.sleep(delay_seconds)
        log.info("Timer fired: wiping chat %s now", chat_id)
        await _wipe_chat(chat_id)
    except asyncio.CancelledError:
        log.info("Timer cancelled for chat %s", chat_id)
    except Exception:
        log.exception("Error in delayed wipe for chat %s", chat_id)
    finally:
        _active_timers.pop(chat_id, None)


def _ensure_timer(chat_id: int, delay_seconds: int = WIPE_AFTER_MINUTES * 60):
    if chat_id in _active_timers:
        task = _active_timers[chat_id]
        if not task.done():
            return
        del _active_timers[chat_id]

    task = asyncio.create_task(_delayed_wipe(chat_id, delay_seconds))
    _active_timers[chat_id] = task


async def autodelete_message_middleware(client: Client, message):
    try:
        from wzgram.enums import ChatType
        if message.chat and message.chat.type == ChatType.PRIVATE:
            chat_id = message.chat.id
            await _track_db(chat_id, message.id)
            _ensure_timer(chat_id)
    except Exception as e:
        log.warning("Autodelete middleware error: %s", e)
    await message.continue_propagation()


async def autodelete_callback_middleware(client: Client, callback_query):
    try:
        from wzgram.enums import ChatType
        if callback_query.message and callback_query.message.chat.type == ChatType.PRIVATE:
            chat_id = callback_query.message.chat.id
            await _track_db(chat_id, callback_query.message.id)
            _ensure_timer(chat_id)
    except Exception as e:
        log.warning("Autodelete callback middleware error: %s", e)
    await callback_query.continue_propagation()


async def track_message(chat_id: int, message_id: int, extra_data: dict = None, sender_type: str = "bot"):
    await _track_db(chat_id, message_id)
    _ensure_timer(chat_id)


async def track_messages(chat_id: int, message_ids: list[int], extra_data: dict = None, sender_type: str = "bot"):
    for mid in message_ids:
        await _track_db(chat_id, mid)
    _ensure_timer(chat_id)


async def schedule_chat_wipe(chat_id: int, delay_minutes: int = None):
    delay = (delay_minutes * 60) if delay_minutes else (WIPE_AFTER_MINUTES * 60)
    _ensure_timer(chat_id, delay)


async def cancel_chat_wipe(chat_id: int):
    task = _active_timers.pop(chat_id, None)
    if task and not task.done():
        task.cancel()


async def clear_chat_history(client: Client, chat_id: int, preserve_message_ids: list = None, delete_user_messages: bool = False):
    _ensure_timer(chat_id)


async def delete_user_message(chat_id: int, message_id: int):
    """Delete a single user message immediately."""
    deleted = False
    if _userbot:
        try:
            await _userbot.delete_messages(chat_id, message_id)
            deleted = True
        except Exception as e:
            await _handle_userbot_error(e)
    if not deleted and _bot:
        try:
            await _bot.delete_messages(chat_id, message_id)
        except Exception:
            pass


async def delete_all_user_messages(client: Client, chat_id: int):
    """Immediately wipe all tracked messages."""
    await _wipe_chat(chat_id)
async def start_autodelete_loop(client: Client):
    set_bot(client)
    db = get_db()
    # Restore timers for pending chats from MongoDB
    try:
        pending_chats = await db.autodelete.find().to_list(length=1000)
        for doc in pending_chats:
            chat_id = doc.get("chat_id")
            if chat_id:
                _ensure_timer(chat_id)
        log.info("Restored auto-delete timers for %d chats from MongoDB", len(pending_chats))
    except Exception as e:
        log.warning("Failed to restore autodelete timers from MongoDB: %s", e)

    log.info("Auto-delete system ready (MongoDB persistence + timers, wipe after %dm)", WIPE_AFTER_MINUTES)
