"""
Auto-delete — tracks bot messages in user DMs and deletes them after a set time.

Messages are stored in MongoDB and a background task cleans them up periodically.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta

from pyrogram import Client

from utils.db import get_db

log = logging.getLogger(__name__)

# Delete messages after 4 hours
DELETE_AFTER_HOURS = 4
# Check for expired messages every 5 minutes
CHECK_INTERVAL_SECONDS = 300


async def track_message(chat_id: int, message_id: int):
    """Track a bot message for auto-deletion."""
    db = get_db()
    await db.auto_delete.insert_one({
        "chat_id": chat_id,
        "message_id": message_id,
        "created_at": datetime.now(timezone.utc),
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=DELETE_AFTER_HOURS),
    })


async def track_messages(chat_id: int, message_ids: list[int]):
    """Track multiple bot messages for auto-deletion."""
    if not message_ids:
        return
    db = get_db()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=DELETE_AFTER_HOURS)
    docs = [
        {"chat_id": chat_id, "message_id": mid, "created_at": now, "expires_at": expires}
        for mid in message_ids
    ]
    await db.auto_delete.insert_many(docs)


async def _cleanup_expired(client: Client):
    """Delete expired messages and remove them from DB."""
    db = get_db()
    now = datetime.now(timezone.utc)

    cursor = db.auto_delete.find({"expires_at": {"$lte": now}})
    deleted_count = 0
    failed_ids = []

    async for doc in cursor:
        try:
            await client.delete_messages(doc["chat_id"], doc["message_id"])
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
    log.info("Auto-delete loop started (delete after %dh, check every %ds)",
             DELETE_AFTER_HOURS, CHECK_INTERVAL_SECONDS)

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
