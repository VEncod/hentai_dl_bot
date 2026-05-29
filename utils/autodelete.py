"""
Auto-delete — wipes entire chat history after a set delay.

Tracks all message IDs (bot + user) per chat, then deletes them via the bot client.
Userbot is used to delete user messages the bot can't delete.
Pure in-memory asyncio timers — no MongoDB polling.
"""

import asyncio
import logging

from pyrogram import Client

log = logging.getLogger(__name__)

# Wipe chat after 10 minutes
WIPE_AFTER_MINUTES = 10

# The bot client — set during startup
_bot: Client | None = None

# Optional userbot client
_userbot: Client | None = None

# In-memory: chat_id → set of message IDs to delete
_tracked_messages: dict[int, set[int]] = {}

# In-memory timers: chat_id → asyncio.Task
_active_timers: dict[int, asyncio.Task] = {}


def set_bot(client: Client):
    """Register the bot client."""
    global _bot
    _bot = client
    log.info("Bot client registered for auto-delete")


def set_userbot(client: Client):
    """Register the userbot client for user message deletion."""
    global _userbot
    _userbot = client
    log.info("Userbot registered for auto-delete")


def _track(chat_id: int, message_id: int):
    """Track a message ID for later deletion."""
    if chat_id not in _tracked_messages:
        _tracked_messages[chat_id] = set()
    _tracked_messages[chat_id].add(message_id)


async def _wipe_chat(chat_id: int):
    """Delete all tracked messages for a chat."""
    msg_ids = sorted(_tracked_messages.pop(chat_id, set()))
    if not msg_ids:
        log.info("No tracked messages for chat %s, nothing to delete", chat_id)
        return True

    log.info("Wiping chat %s: %d tracked messages", chat_id, len(msg_ids))
    deleted = 0

    # Delete in batches of 100 (Telegram limit)
    for i in range(0, len(msg_ids), 100):
        batch = msg_ids[i:i + 100]

        # Try bot first (can delete its own messages + user messages in private chats)
        if _bot:
            try:
                await _bot.delete_messages(chat_id, batch)
                deleted += len(batch)
                continue
            except Exception as e:
                log.debug("Bot delete failed for chat %s: %s", chat_id, e)

        # Fallback to userbot
        if _userbot:
            try:
                await _userbot.delete_messages(chat_id, batch)
                deleted += len(batch)
                continue
            except Exception as e:
                log.debug("Userbot delete failed for chat %s: %s", chat_id, e)

        log.warning("Could not delete batch for chat %s", chat_id)

    log.info("Chat wipe done for %s: deleted %d/%d messages", chat_id, deleted, len(msg_ids))

    # If userbot available, also try DeleteHistory as final cleanup
    if _userbot:
        try:
            from pyrogram.raw.functions.messages import DeleteHistory
            peer = await _userbot.resolve_peer(chat_id)
            await _userbot.invoke(
                DeleteHistory(peer=peer, max_id=2147483647, revoke=True)
            )
        except Exception:
            pass

    return True


async def _delayed_wipe(chat_id: int, delay_seconds: int):
    """Wait for delay, then wipe the chat."""
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


def _ensure_timer(chat_id: int):
    """Ensure a wipe timer exists for this chat. Does NOT reset existing timers."""
    if chat_id in _active_timers:
        task = _active_timers[chat_id]
        if not task.done():
            return
        del _active_timers[chat_id]

    delay = WIPE_AFTER_MINUTES * 60
    task = asyncio.create_task(_delayed_wipe(chat_id, delay))
    _active_timers[chat_id] = task
    log.info("New wipe timer created for chat %s (%d min)", chat_id, WIPE_AFTER_MINUTES)


# ── Middleware ────────────────────────────────────────────────────────────

async def autodelete_message_middleware(client: Client, message):
    """Fires on EVERY private message. Tracks it and ensures a timer is running."""
    try:
        from pyrogram.enums import ChatType
        if message.chat and message.chat.type == ChatType.PRIVATE:
            chat_id = message.chat.id
            _track(chat_id, message.id)
            _ensure_timer(chat_id)
    except Exception as e:
        log.warning("Autodelete middleware error: %s", e)
    await message.continue_propagation()


async def autodelete_callback_middleware(client: Client, callback_query):
    """Fires on EVERY callback query in private chats."""
    try:
        from pyrogram.enums import ChatType
        if callback_query.message and callback_query.message.chat.type == ChatType.PRIVATE:
            chat_id = callback_query.message.chat.id
            _track(chat_id, callback_query.message.id)
            _ensure_timer(chat_id)
    except Exception as e:
        log.warning("Autodelete callback middleware error: %s", e)
    await callback_query.continue_propagation()


# ── Public API (used by plugins to track bot-sent messages) ──────────────

async def track_message(chat_id: int, message_id: int, extra_data: dict = None, sender_type: str = "bot"):
    """Track a message for auto-deletion."""
    _track(chat_id, message_id)


async def track_messages(chat_id: int, message_ids: list[int], extra_data: dict = None, sender_type: str = "bot"):
    """Track multiple messages for auto-deletion."""
    for mid in message_ids:
        _track(chat_id, mid)


async def schedule_chat_wipe(chat_id: int, delay_minutes: int = None):
    """Ensure a timer is running for this chat."""
    _ensure_timer(chat_id)


async def cancel_chat_wipe(chat_id: int):
    """Cancel any pending wipe timer."""
    task = _active_timers.pop(chat_id, None)
    if task and not task.done():
        task.cancel()
        log.info("Cancelled wipe timer for chat %s", chat_id)


async def clear_chat_history(client: Client, chat_id: int, preserve_message_ids: list = None, delete_user_messages: bool = False):
    """Ensure a timer is running (does not reset)."""
    _ensure_timer(chat_id)


async def delete_user_message(chat_id: int, message_id: int):
    """Delete a single user message immediately."""
    if _userbot:
        try:
            await _userbot.delete_messages(chat_id, message_id)
        except Exception:
            pass
    elif _bot:
        try:
            await _bot.delete_messages(chat_id, message_id)
        except Exception:
            pass


async def delete_all_user_messages(client: Client, chat_id: int):
    """Immediately wipe all tracked messages."""
    await _wipe_chat(chat_id)


async def start_autodelete_loop(client: Client):
    """No background loop needed — just register the bot client."""
    set_bot(client)
    log.info("Auto-delete system ready (in-memory timers + message tracking, wipe after %dm)",
             WIPE_AFTER_MINUTES)
