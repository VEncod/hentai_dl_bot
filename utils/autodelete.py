"""
Auto-delete — wipes entire chat history after a set delay using userbot + DeleteHistory.

Pure in-memory approach: each private chat interaction spawns an asyncio timer.
No MongoDB polling, no background loops — just fire-and-forget tasks.
"""

import asyncio
import logging

from pyrogram import Client
from pyrogram.raw.functions.messages import DeleteHistory

log = logging.getLogger(__name__)

# Wipe chat after 10 minutes
WIPE_AFTER_MINUTES = 10

# Optional userbot client — set by app.py if SESSION_STRING is configured.
_userbot: Client | None = None

# In-memory timers: chat_id → asyncio.Task
# Only ONE timer per chat — first interaction sets it, subsequent ones don't reset it.
_active_timers: dict[int, asyncio.Task] = {}


def set_userbot(client: Client):
    """Register the userbot client for chat history wipe."""
    global _userbot
    _userbot = client
    log.info("Userbot registered for auto-delete")


async def _wipe_chat_history(chat_id: int):
    """Wipe entire chat history using userbot's DeleteHistory."""
    if _userbot is None:
        log.warning("No userbot configured, cannot wipe chat %s", chat_id)
        return False

    try:
        peer = await _userbot.resolve_peer(chat_id)

        # Strategy 1: Loop DeleteHistory until pts_count == 0
        for attempt in range(20):
            result = await _userbot.invoke(
                DeleteHistory(
                    peer=peer,
                    max_id=2147483647,
                    revoke=True,
                )
            )
            pts = getattr(result, "pts_count", "?")
            log.info("DeleteHistory attempt %d for chat %s — pts_count=%s",
                     attempt + 1, chat_id, pts)

            if hasattr(result, "pts_count") and result.pts_count == 0:
                break
            await asyncio.sleep(0.5)

        # Strategy 2: Bulk-delete any survivors
        msg_ids = []
        async for msg in _userbot.get_chat_history(chat_id, limit=1000):
            msg_ids.append(msg.id)

        if msg_ids:
            log.warning("Chat %s still has %d messages after DeleteHistory, bulk deleting",
                        chat_id, len(msg_ids))
            for i in range(0, len(msg_ids), 100):
                batch = msg_ids[i:i + 100]
                try:
                    await _userbot.delete_messages(chat_id, batch, revoke=True)
                except Exception as e:
                    log.warning("Bulk delete batch failed for chat %s: %s", chat_id, e)
                await asyncio.sleep(0.3)

        # Strategy 3: Final sweep
        remaining = []
        async for msg in _userbot.get_chat_history(chat_id, limit=5):
            remaining.append(msg.id)
        if remaining:
            log.warning("Chat %s STILL has messages, final DeleteHistory", chat_id)
            await _userbot.invoke(
                DeleteHistory(peer=peer, max_id=2147483647, revoke=True)
            )

        log.info("Chat wipe complete for %s", chat_id)
        return True
    except Exception as e:
        log.warning("Failed to wipe chat history for %s: %s", chat_id, e)
        return False


async def _delayed_wipe(chat_id: int, delay_seconds: int):
    """Wait for delay, then wipe the chat. Runs as an independent asyncio task."""
    try:
        log.info("Timer started: chat %s will be wiped in %d seconds", chat_id, delay_seconds)
        await asyncio.sleep(delay_seconds)
        log.info("Timer fired: wiping chat %s now", chat_id)
        await _wipe_chat_history(chat_id)
    except asyncio.CancelledError:
        log.info("Timer cancelled for chat %s", chat_id)
    except Exception:
        log.exception("Error in delayed wipe for chat %s", chat_id)
    finally:
        _active_timers.pop(chat_id, None)


def _ensure_timer(chat_id: int):
    """Ensure a wipe timer exists for this chat. Does NOT reset existing timers.
    First message sets the timer, subsequent messages are ignored.
    The chat gets wiped X minutes after the FIRST interaction.
    """
    if chat_id in _active_timers:
        task = _active_timers[chat_id]
        if not task.done():
            return  # Timer already running, don't reset
        # Old task finished/failed, clean up
        del _active_timers[chat_id]

    delay = WIPE_AFTER_MINUTES * 60
    task = asyncio.create_task(_delayed_wipe(chat_id, delay))
    _active_timers[chat_id] = task
    log.info("New wipe timer created for chat %s (%d min)", chat_id, WIPE_AFTER_MINUTES)


# ── Middleware: fires on EVERY private message/callback ───────────────────

async def autodelete_message_middleware(client: Client, message):
    """Called on EVERY private message. Sets a wipe timer if none exists."""
    try:
        from pyrogram.enums import ChatType
        if message.chat and message.chat.type == ChatType.PRIVATE:
            _ensure_timer(message.chat.id)
    except Exception as e:
        log.warning("Autodelete middleware error: %s", e)
    await message.continue_propagation()


async def autodelete_callback_middleware(client: Client, callback_query):
    """Called on EVERY callback query in private chats. Sets a wipe timer if none exists."""
    try:
        from pyrogram.enums import ChatType
        if callback_query.message and callback_query.message.chat.type == ChatType.PRIVATE:
            _ensure_timer(callback_query.message.chat.id)
    except Exception as e:
        log.warning("Autodelete callback middleware error: %s", e)
    await callback_query.continue_propagation()


# ── Legacy API (kept for compatibility with existing plugin imports) ──────

async def schedule_chat_wipe(chat_id: int, delay_minutes: int = None):
    """Legacy: just ensures a timer exists."""
    _ensure_timer(chat_id)


async def cancel_chat_wipe(chat_id: int):
    """Cancel any pending wipe timer for a chat."""
    task = _active_timers.pop(chat_id, None)
    if task and not task.done():
        task.cancel()
        log.info("Cancelled wipe timer for chat %s", chat_id)


async def clear_chat_history(client: Client, chat_id: int, preserve_message_ids: list = None, delete_user_messages: bool = False):
    """Legacy: ensures a timer is running (does not reset)."""
    _ensure_timer(chat_id)


async def track_message(chat_id: int, message_id: int, extra_data: dict = None, sender_type: str = "bot"):
    pass

async def track_messages(chat_id: int, message_ids: list[int], extra_data: dict = None, sender_type: str = "bot"):
    pass


async def delete_user_message(chat_id: int, message_id: int):
    if _userbot is None:
        return
    try:
        await _userbot.delete_messages(chat_id, message_id)
    except Exception:
        pass


async def delete_all_user_messages(client: Client, chat_id: int):
    await _wipe_chat_history(chat_id)


async def start_autodelete_loop(client: Client):
    """Legacy: no-op. Timers are now per-chat asyncio tasks."""
    log.info("Auto-delete system ready (in-memory timers, wipe after %dm)", WIPE_AFTER_MINUTES)
