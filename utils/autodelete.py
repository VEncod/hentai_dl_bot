"""
Auto-delete — tracks bot messages in user DMs and deletes them after a set time.

Messages are stored in MongoDB and a background task cleans them up periodically.
Also supports immediate cleanup of all messages for a user.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta

from pyrogram import Client

from utils.db import get_db

log = logging.getLogger(__name__)

# Delete messages after 10 minutes
DELETE_AFTER_MINUTES = 10
# Check for expired messages every 5 minutes
CHECK_INTERVAL_SECONDS = 300

# Optional userbot client — set by app.py if SESSION_STRING is configured.
# When set, it handles deleting user-sent messages (bots can't do this in DMs).
_userbot: Client | None = None


def set_userbot(client: Client):
    """Register the userbot client for deleting user messages."""
    global _userbot
    _userbot = client
    log.info("Userbot registered for auto-delete")


async def delete_user_message(chat_id: int, message_id: int):
    """
    Try to delete a user-sent message immediately.
    Uses userbot if available, otherwise tries the bot (fails silently in DMs).
    """
    client = _userbot
    if client is None:
        log.debug("No userbot configured, skipping user message deletion")
        return
    try:
        await client.delete_messages(chat_id, message_id)
        log.info("Userbot deleted user message %s in chat %s", message_id, chat_id)
    except Exception as e:
        log.warning("Userbot failed to delete user message %s in chat %s: %s", message_id, chat_id, e)


async def track_message(chat_id: int, message_id: int, extra_data: dict = None, sender_type: str = "bot"):
    """Track a message for auto-deletion.
    
    sender_type: "bot" for bot messages, "user" for user messages.
    """
    db = get_db()
    doc = {
        "chat_id": chat_id,
        "message_id": message_id,
        "sender_type": sender_type,
        "created_at": datetime.now(timezone.utc),
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=DELETE_AFTER_MINUTES),
    }
    if extra_data:
        doc.update(extra_data)
    await db.auto_delete.insert_one(doc)


async def track_messages(chat_id: int, message_ids: list[int], extra_data: dict = None, sender_type: str = "bot"):
    """Track multiple messages for auto-deletion."""
    if not message_ids:
        return
    db = get_db()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=DELETE_AFTER_MINUTES)
    docs = []
    for mid in message_ids:
        doc = {"chat_id": chat_id, "message_id": mid, "sender_type": sender_type, "created_at": now, "expires_at": expires}
        if extra_data:
            doc.update(extra_data)
        docs.append(doc)
    await db.auto_delete.insert_many(docs)


async def delete_all_user_messages(client: Client, chat_id: int):
    """Delete all tracked messages for a user immediately."""
    db = get_db()
    cursor = db.auto_delete.find({"chat_id": chat_id})
    deleted_count = 0
    message_ids = []
    
    async for doc in cursor:
        message_ids.append(doc["message_id"])
    
    if message_ids:
        try:
            await client.delete_messages(chat_id, message_ids)
            deleted_count = len(message_ids)
            log.info("Auto-delete: immediately deleted %d messages for chat %s", deleted_count, chat_id)
        except Exception as e:
            log.warning("Failed to bulk delete messages for chat %s: %s", chat_id, e)
        
        # Remove from DB regardless of success
        await db.auto_delete.delete_many({"chat_id": chat_id, "message_id": {"$in": message_ids}})
    
    return deleted_count


async def clear_chat_history(client: Client, chat_id: int, preserve_message_ids: list = None, delete_user_messages: bool = False):
    """
    Clear tracked bot messages for a user.
    
    By default only deletes bot messages (sender_type="bot").
    Set delete_user_messages=True to also delete user messages immediately.
    
    The auto-delete loop will still clean up user messages after 10 minutes.
    """
    preserve_set = set(preserve_message_ids or [])
    db = get_db()
    
    # Only delete bot messages by default (preserve user messages for 10-min auto-delete)
    query = {"chat_id": chat_id, "sender_type": "bot"}
    if delete_user_messages:
        query = {"chat_id": chat_id}
    
    cursor = db.auto_delete.find(query)
    deleted_count = 0
    message_ids = []

    async for doc in cursor:
        mid = doc["message_id"]
        if mid not in preserve_set:
            message_ids.append(mid)

    if message_ids:
        # Try userbot first (can delete any message)
        if _userbot:
            try:
                await _userbot.delete_messages(chat_id, message_ids)
                deleted_count = len(message_ids)
                log.info("Userbot: cleared %d tracked messages for chat %s", deleted_count, chat_id)
            except Exception as e:
                log.warning("Userbot failed to clear tracked messages for chat %s: %s", chat_id, e)
        
        # Fallback to bot client
        if deleted_count == 0:
            try:
                await client.delete_messages(chat_id, message_ids)
                deleted_count = len(message_ids)
                log.info("Auto-delete: cleared %d tracked messages for chat %s", deleted_count, chat_id)
            except Exception as e:
                log.warning("Failed to clear tracked messages for chat %s: %s", chat_id, e)

        await db.auto_delete.delete_many({"chat_id": chat_id, "message_id": {"$in": message_ids}})

    return deleted_count


async def _cleanup_expired(client: Client):
    """Delete expired messages and remove them from DB.
    
    Uses userbot first (can delete both user and bot messages),
    falls back to bot client (can only delete bot's own messages).
    """
    db = get_db()
    now = datetime.now(timezone.utc)

    cursor = db.auto_delete.find({"expires_at": {"$lte": now}})
    deleted_count = 0
    failed_ids = []

    async for doc in cursor:
        chat_id = doc["chat_id"]
        message_id = doc["message_id"]
        deleted = False

        # Try userbot first (can delete any message in private chats)
        if _userbot:
            try:
                await _userbot.delete_messages(chat_id, message_id)
                deleted = True
                deleted_count += 1
            except Exception:
                pass

        # Fallback to bot client (can only delete its own messages)
        if not deleted:
            try:
                await client.delete_messages(chat_id, message_id)
                deleted = True
                deleted_count += 1
            except Exception:
                # Message might already be deleted or chat unavailable
                pass

        failed_ids.append(doc["_id"])

    # Remove all processed entries from DB
    if failed_ids:
        await db.auto_delete.delete_many({"_id": {"$in": failed_ids}})

    if deleted_count:
        log.info("Auto-delete: cleaned up %d expired messages", deleted_count)


async def start_autodelete_loop(client: Client):
    """Background loop that deletes expired messages every CHECK_INTERVAL_SECONDS."""
    log.info("Auto-delete loop started (delete after %dm, check every %ds)",
             DELETE_AFTER_MINUTES, CHECK_INTERVAL_SECONDS)

    # Create TTL index on expires_at for safety (MongoDB auto-cleanup backup)
    db = get_db()
    try:
        await db.auto_delete.create_index("expires_at", expireAfterSeconds=3600)
    except Exception:
        pass  # Index might already exist

    while True:
        try:
            await _cleanup_expired(client)
        except Exception:
            log.exception("Auto-delete cleanup error")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
