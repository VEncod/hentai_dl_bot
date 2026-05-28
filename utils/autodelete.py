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
        peer = await _userbot.resolve_peer(chat_id)

        # Get the latest message ID — max_id=0 is unreliable on Telegram,
        # so we fetch the actual top message ID for a complete wipe.
        from pyrogram.raw.functions.messages import GetHistory
        history = await _userbot.invoke(
            GetHistory(
                peer=peer,
                offset_id=0,
                offset_date=0,
                add_offset=0,
                limit=1,
                max_id=0,
                min_id=0,
                hash=0,
            )
        )
        top_msg_id = 0
        if history.messages:
            top_msg_id = history.messages[0].id

        if top_msg_id == 0:
            log.info("No messages found in chat %s, nothing to wipe", chat_id)
            return True

        # Delete with the real max_id so Telegram actually clears everything
        await _userbot.invoke(
            DeleteHistory(
                peer=peer,
                max_id=top_msg_id,
                revoke=True,
            )
        )
        log.info("Successfully wiped chat history for %s (max_id=%d)", chat_id, top_msg_id)
        return True
    except Exception as e:
        log.warning("Failed to wipe chat history for %s: %s", chat_id, e)
        return False


async def schedule_chat_wipe(chat_id: int, delay_minutes: int = None):
    """Schedule a full chat history wipe after the specified delay."""
    delay = delay_minutes or WIPE_AFTER_MINUTES
    db = get_db()
    await db.chat_wipes.insert_one({
        "chat_id": chat_id,
        "wipe_at": datetime.now(timezone.utc) + timedelta(minutes=delay),
        "created_at": datetime.now(timezone.utc),
    })
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


# ── Background loop ───────────────────────────────────────────────────────

async def _cleanup_expired():
    """Find expired chat wipes and execute them."""
    db = get_db()
    now = datetime.now(timezone.utc)

    cursor = db.chat_wipes.find({"wipe_at": {"$lte": now}})
    wiped_count = 0
    wipe_ids = []

    async for doc in cursor:
        chat_id = doc["chat_id"]
        success = await _wipe_chat_history(chat_id)
        wipe_ids.append(doc["_id"])
        if success:
            wiped_count += 1

    if wipe_ids:
        await db.chat_wipes.delete_many({"_id": {"$in": wipe_ids}})

    if wiped_count:
        log.info("Auto-delete: wiped %d chats", wiped_count)


async def start_autodelete_loop(client: Client):
    """Background loop that checks for expired chat wipes."""
    log.info("Auto-delete loop started (wipe after %dm, check every %ds)",
             WIPE_AFTER_MINUTES, CHECK_INTERVAL_SECONDS)

    db = get_db()
    try:
        await db.chat_wipes.create_index("wipe_at", expireAfterSeconds=3600)
    except Exception:
        pass

    while True:
        try:
            await _cleanup_expired()
        except Exception:
            log.exception("Auto-delete cleanup error")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
