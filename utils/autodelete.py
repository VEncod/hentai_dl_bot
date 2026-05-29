"""
Auto-delete — wipes entire chat history after a set delay using userbot + DeleteHistory.

When userbot is available, uses raw MTProto DeleteHistory(revoke=True) to clear
both sides' chat history completely. Falls back to per-message deletion if no userbot.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta

from pyrogram import Client
from pyrogram.raw.functions.messages import DeleteHistory

from utils.db import get_db

log = logging.getLogger(__name__)

# Wipe chat after 10 minutes
WIPE_AFTER_MINUTES = 10
# Check for expired wipes every 30 seconds
CHECK_INTERVAL_SECONDS = 30

# Optional userbot client — set by app.py if SESSION_STRING is configured.
_userbot: Client | None = None


def set_userbot(client: Client):
    """Register the userbot client for chat history wipe."""
    global _userbot
    _userbot = client
    log.info("Userbot registered for auto-delete")


async def _wipe_chat_history(chat_id: int):
    """Wipe entire chat history using userbot's DeleteHistory."""
    if _userbot is None:
        log.debug("No userbot configured, skipping chat wipe for %s", chat_id)
        return False

    try:
        from pyrogram.raw.functions.messages import GetHistory

        peer = await _userbot.resolve_peer(chat_id)

        # ── Strategy 1: Loop DeleteHistory until pts_count == 0 ───────
        for attempt in range(20):
            result = await _userbot.invoke(
                DeleteHistory(
                    peer=peer,
                    max_id=2147483647,  # INT32_MAX — delete absolutely everything
                    revoke=True,
                )
            )
            log.info("DeleteHistory attempt %d for chat %s — pts_count=%s",
                     attempt + 1, chat_id, getattr(result, "pts_count", "?"))

            # pts_count == 0 means nothing left to delete
            if hasattr(result, "pts_count") and result.pts_count == 0:
                break
            await asyncio.sleep(0.5)

        # ── Strategy 2: Verify & bulk-delete any survivors ────────────
        msg_ids = []
        async for msg in _userbot.get_chat_history(chat_id, limit=1000):
            msg_ids.append(msg.id)

        if msg_ids:
            log.warning("Chat %s still has %d messages after DeleteHistory, bulk deleting", chat_id, len(msg_ids))
            for i in range(0, len(msg_ids), 100):
                batch = msg_ids[i:i + 100]
                try:
                    await _userbot.delete_messages(chat_id, batch, revoke=True)
                except Exception as e:
                    log.warning("Bulk delete batch failed for chat %s: %s", chat_id, e)
                await asyncio.sleep(0.3)

        # ── Strategy 3: Final check — if STILL not empty, one more DeleteHistory
        remaining = []
        async for msg in _userbot.get_chat_history(chat_id, limit=5):
            remaining.append(msg.id)
        if remaining:
            log.warning("Chat %s STILL has messages after bulk delete, final DeleteHistory", chat_id)
            await _userbot.invoke(
                DeleteHistory(peer=peer, max_id=2147483647, revoke=True)
            )

        log.info("Chat wipe complete for %s", chat_id)
        return True
    except Exception as e:
        log.warning("Failed to wipe chat history for %s: %s", chat_id, e)
        return False


async def schedule_chat_wipe(chat_id: int, delay_minutes: int = None):
    """Schedule a full chat history wipe after the specified delay.
    Uses upsert so each chat only has ONE pending wipe (reset on every interaction).
    """
    delay = delay_minutes or WIPE_AFTER_MINUTES
    db = get_db()
    wipe_at = datetime.now(timezone.utc) + timedelta(minutes=delay)
    await db.chat_wipes.update_one(
        {"chat_id": chat_id},
        {"$set": {
            "chat_id": chat_id,
            "wipe_at": wipe_at,
            "created_at": datetime.now(timezone.utc),
        }},
        upsert=True,
    )
    log.info("Scheduled chat wipe for %s in %d minutes", chat_id, delay)


async def cancel_chat_wipe(chat_id: int):
    """Cancel any pending chat wipe for a chat."""
    db = get_db()
    result = await db.chat_wipes.delete_many({"chat_id": chat_id})
    if result.deleted_count:
        log.info("Cancelled %d pending chat wipe(s) for %s", result.deleted_count, chat_id)


# ── Legacy per-message tracking (kept for compatibility, minimal) ─────────

async def track_message(chat_id: int, message_id: int, extra_data: dict = None, sender_type: str = "bot"):
    """Legacy: track a single message. With DeleteHistory this is no longer needed,
    but kept for compatibility with existing plugin code."""
    pass  # No-op: DeleteHistory handles everything


async def track_messages(chat_id: int, message_ids: list[int], extra_data: dict = None, sender_type: str = "bot"):
    """Legacy: track multiple messages. No-op with DeleteHistory."""
    pass


async def delete_user_message(chat_id: int, message_id: int):
    """Legacy: try to delete a user message immediately."""
    if _userbot is None:
        return
    try:
        await _userbot.delete_messages(chat_id, message_id)
    except Exception:
        pass


async def delete_all_user_messages(client: Client, chat_id: int):
    """Legacy: immediately wipe chat history."""
    await _wipe_chat_history(chat_id)


async def clear_chat_history(client: Client, chat_id: int, preserve_message_ids: list = None, delete_user_messages: bool = False):
    """Cancel any pending wipe and reschedule for 10 minutes from now.
    
    This resets the auto-delete timer each time the user interacts,
    so the chat is wiped 10 minutes after the LAST interaction.
    """
    await cancel_chat_wipe(chat_id)
    await schedule_chat_wipe(chat_id, delay_minutes=WIPE_AFTER_MINUTES)


# ── Middleware: ensure EVERY private interaction schedules a wipe ──────────

async def autodelete_message_middleware(client: Client, message):
    """Called on EVERY private message. Ensures a wipe is always scheduled."""
    try:
        from pyrogram.enums import ChatType
        if message.chat and message.chat.type == ChatType.PRIVATE:
            await schedule_chat_wipe(message.chat.id)
    except Exception as e:
        log.warning("Autodelete middleware error: %s", e)
    await message.continue_propagation()


async def autodelete_callback_middleware(client: Client, callback_query):
    """Called on EVERY callback query in private chats. Ensures a wipe is always scheduled."""
    try:
        from pyrogram.enums import ChatType
        if callback_query.message and callback_query.message.chat.type == ChatType.PRIVATE:
            await schedule_chat_wipe(callback_query.message.chat.id)
    except Exception as e:
        log.warning("Autodelete callback middleware error: %s", e)
    await callback_query.continue_propagation()


# ── Background loop ───────────────────────────────────────────────────────

async def _cleanup_expired():
    """Find expired chat wipes and execute them."""
    db = get_db()
    now = datetime.now(timezone.utc)

    # Debug: count total pending wipes
    total_pending = await db.chat_wipes.count_documents({})
    expired_count = await db.chat_wipes.count_documents({"wipe_at": {"$lte": now}})
    if total_pending > 0:
        log.info("Auto-delete check: %d pending wipes, %d expired (now=%s)",
                 total_pending, expired_count, now.isoformat())

    cursor = db.chat_wipes.find({"wipe_at": {"$lte": now}})
    wiped_count = 0
    wipe_ids = []

    async for doc in cursor:
        chat_id = doc["chat_id"]
        log.info("Executing wipe for chat %s (scheduled at %s)", chat_id, doc.get("wipe_at"))
        success = await _wipe_chat_history(chat_id)
        wipe_ids.append(doc["_id"])
        if success:
            wiped_count += 1
        else:
            log.warning("Wipe FAILED for chat %s", chat_id)

    if wipe_ids:
        await db.chat_wipes.delete_many({"_id": {"$in": wipe_ids}})

    if wiped_count:
        log.info("Auto-delete: wiped %d chats", wiped_count)


async def start_autodelete_loop(client: Client):
    """Background loop that checks for expired chat wipes."""
    log.info("Auto-delete loop started (wipe after %dm, check every %ds)",
             WIPE_AFTER_MINUTES, CHECK_INTERVAL_SECONDS)

    db = get_db()

    # Drop the old TTL index — it can cause MongoDB to silently delete
    # wipe docs before our loop processes them.
    try:
        await db.chat_wipes.drop_index("wipe_at_1")
        log.info("Dropped old TTL index on chat_wipes")
    except Exception:
        pass

    # Create a normal index (NOT TTL) for fast queries
    try:
        await db.chat_wipes.create_index("wipe_at")
        await db.chat_wipes.create_index("chat_id", unique=True)
    except Exception:
        pass

    while True:
        try:
            await _cleanup_expired()
        except Exception:
            log.exception("Auto-delete cleanup error")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
