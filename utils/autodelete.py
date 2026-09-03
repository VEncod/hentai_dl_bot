"""
Auto-delete system — wipes a user's chat history after 10 minutes of inactivity.

Design:
- Messages are tracked **per user_id** (not just chat_id) in MongoDB.
- Every new message from a user RESETS their 10-minute inactivity timer.
- When the timer fires (no new messages for 10 minutes), ALL tracked messages
  for that user are bulk-deleted, then the DB records are purged.
- Multi-user safe: each user has their own independent timer.
"""

import asyncio
import logging
from datetime import datetime, timezone
from wzgram import Client
from utils.db import get_db

log = logging.getLogger(__name__)

WIPE_AFTER_MINUTES = 10

_bot: Client | None = None

# Per-user timers: {user_id: asyncio.Task}
_user_timers: dict[int, asyncio.Task] = {}


def set_bot(client: Client):
    global _bot
    _bot = client
    log.info("Bot client registered for auto-delete")


async def _track_db(user_id: int, chat_id: int, message_id: int):
    """Store a single message record keyed by user_id."""
    try:
        db = get_db()
        await db.autodelete.update_one(
            {"user_id": user_id},
            {
                "$addToSet": {"msg_ids": message_id},
                "$set": {
                    "chat_id": chat_id,
                    "last_activity": datetime.now(timezone.utc),
                },
                "$setOnInsert": {"created_at": datetime.now(timezone.utc)},
            },
            upsert=True,
        )
    except Exception as e:
        log.warning("Failed to save tracked message to MongoDB: %s", e)


async def _wipe_user(user_id: int):
    """Delete all tracked messages for a specific user, then purge DB record."""
    db = get_db()
    doc = await db.autodelete.find_one({"user_id": user_id})
    if not doc:
        return

    chat_id = doc.get("chat_id")
    msg_ids = sorted(doc.get("msg_ids", []))

    if not msg_ids or not chat_id:
        await db.autodelete.delete_one({"user_id": user_id})
        return

    log.info("Wiping %d messages for user %s in chat %s", len(msg_ids), user_id, chat_id)
    deleted = 0

    # Batch delete in groups of 100 (Telegram API limit)
    for i in range(0, len(msg_ids), 100):
        batch = msg_ids[i:i + 100]
        if _bot:
            try:
                count = await _bot.delete_messages(chat_id, batch)
                log.info("Batch delete for user %s: result=%s", user_id, count)
                deleted += len(batch)
            except Exception as e:
                log.warning("Batch delete failed for user %s: %s", user_id, e)
                # Fallback: delete one by one
                for mid in batch:
                    try:
                        await _bot.delete_messages(chat_id, mid)
                        deleted += 1
                    except Exception:
                        pass

    await db.autodelete.delete_one({"user_id": user_id})
    log.info("Wipe complete for user %s: deleted %d/%d messages", user_id, deleted, len(msg_ids))


async def _delayed_wipe(user_id: int, delay_seconds: int):
    """Wait for delay_seconds of inactivity, then wipe the user's messages."""
    try:
        log.info("Inactivity timer started for user %s: %d seconds", user_id, delay_seconds)
        await asyncio.sleep(delay_seconds)
        log.info("Inactivity timer fired for user %s — wiping now", user_id)
        await _wipe_user(user_id)
    except asyncio.CancelledError:
        log.info("Inactivity timer cancelled for user %s (new activity detected)", user_id)
    except Exception:
        log.exception("Error in delayed wipe for user %s", user_id)
    finally:
        _user_timers.pop(user_id, None)


def _reset_timer(user_id: int, delay_seconds: int = WIPE_AFTER_MINUTES * 60):
    """Cancel any existing timer for this user and start a fresh one.
    
    This ensures the wipe only happens after `delay_seconds` of complete silence.
    """
    # Cancel existing timer if running
    existing = _user_timers.get(user_id)
    if existing and not existing.done():
        existing.cancel()

    # Start a fresh timer
    task = asyncio.create_task(_delayed_wipe(user_id, delay_seconds))
    _user_timers[user_id] = task


# ── Public API ──────────────────────────────────────────────────────────

async def autodelete_message_middleware(client: Client, message):
    """Middleware: auto-track every private message and reset the user's inactivity timer."""
    try:
        from wzgram.enums import ChatType
        if message.chat and message.chat.type == ChatType.PRIVATE and message.from_user:
            user_id = message.from_user.id
            chat_id = message.chat.id
            await _track_db(user_id, chat_id, message.id)
            _reset_timer(user_id)
    except Exception as e:
        log.warning("Autodelete middleware error: %s", e)
    await message.continue_propagation()


async def autodelete_callback_middleware(client: Client, callback_query):
    """Middleware: track callback messages and reset the user's inactivity timer."""
    try:
        from wzgram.enums import ChatType
        if (callback_query.message
                and callback_query.message.chat.type == ChatType.PRIVATE
                and callback_query.from_user):
            user_id = callback_query.from_user.id
            chat_id = callback_query.message.chat.id
            await _track_db(user_id, chat_id, callback_query.message.id)
            _reset_timer(user_id)
    except Exception as e:
        log.warning("Autodelete callback middleware error: %s", e)
    await callback_query.continue_propagation()


async def track_message(chat_id: int, message_id: int, extra_data: dict = None, sender_type: str = "bot"):
    """Track a bot-sent message for a user. Uses chat_id as user_id for private chats."""
    # In private chats, chat_id == user_id
    user_id = chat_id
    await _track_db(user_id, chat_id, message_id)
    _reset_timer(user_id)


async def track_messages(chat_id: int, message_ids: list[int], extra_data: dict = None, sender_type: str = "bot"):
    """Track multiple bot-sent messages."""
    user_id = chat_id
    for mid in message_ids:
        await _track_db(user_id, chat_id, mid)
    _reset_timer(user_id)


async def schedule_chat_wipe(chat_id: int, delay_minutes: int = None):
    """Schedule a wipe for a user (chat_id == user_id in private)."""
    delay = (delay_minutes * 60) if delay_minutes else (WIPE_AFTER_MINUTES * 60)
    _reset_timer(chat_id, delay)


async def cancel_chat_wipe(chat_id: int):
    """Cancel a pending wipe timer for a user."""
    task = _user_timers.pop(chat_id, None)
    if task and not task.done():
        task.cancel()


async def clear_chat_history(client: Client, chat_id: int, preserve_message_ids: list = None, delete_user_messages: bool = False):
    """Reset the inactivity timer when a new interaction flow starts."""
    _reset_timer(chat_id)


async def delete_user_message(chat_id: int, message_id: int):
    """Delete a single message immediately."""
    if _bot:
        try:
            await _bot.delete_messages(chat_id, message_id)
        except Exception:
            pass


async def delete_all_user_messages(client: Client, chat_id: int):
    """Immediately wipe all tracked messages for a user."""
    await _wipe_user(chat_id)


async def start_autodelete_loop(client: Client):
    """Initialize the auto-delete system. Restore pending timers from MongoDB."""
    set_bot(client)
    db = get_db()
    try:
        pending = await db.autodelete.find().to_list(length=1000)
        restored = 0
        for doc in pending:
            user_id = doc.get("user_id")
            if not user_id:
                # Legacy record keyed by chat_id — migrate it
                user_id = doc.get("chat_id")
                if user_id:
                    await db.autodelete.update_one(
                        {"_id": doc["_id"]},
                        {"$set": {"user_id": user_id}},
                    )
            if user_id:
                _reset_timer(user_id)
                restored += 1
        log.info("Restored auto-delete timers for %d users from MongoDB", restored)
    except Exception as e:
        log.warning("Failed to restore autodelete timers from MongoDB: %s", e)

    log.info("Auto-delete system ready (per-user inactivity timer, wipe after %dm of silence)", WIPE_AFTER_MINUTES)
