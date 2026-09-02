"""
Log channel helper.

Sends log messages to the configured log channel (if set).
Supports creating a message and returning an updater function for progress edits.
"""

import logging
from wzgram import Client

from utils.db import get_db

log = logging.getLogger(__name__)


import os

async def get_log_channel() -> int | None:
    """Get the log channel ID from config or env, or None if not set."""
    db = get_db()
    doc = await db.config.find_one({"key": "log_channel"})
    if doc and doc.get("value"):
        return int(doc["value"])
    env_ch = os.environ.get("LOG_CHANNEL")
    if env_ch:
        try:
            return int(env_ch)
        except ValueError:
            pass
    return None


async def get_main_channel() -> int | None:
    """Get the main channel ID from config or env, or None if not set."""
    db = get_db()
    doc = await db.config.find_one({"key": "main_channel"})
    if doc and doc.get("value"):
        return int(doc["value"])
    env_ch = os.environ.get("MAIN_CHANNEL")
    if env_ch:
        try:
            return int(env_ch)
        except ValueError:
            pass
    return None


async def log_to_channel(client: Client, text: str) -> int | None:
    """
    Send a log message to the log channel.
    Returns the message_id of the sent message, or None if no log channel is set.
    """
    channel_id = await get_log_channel()
    if not channel_id:
        return None
    try:
        msg = await client.send_message(chat_id=channel_id, text=text)
        return msg.id
    except Exception:
        log.warning("Failed to send log message to channel %s", channel_id)
        return None


async def edit_log_message(client: Client, message_id: int, text: str):
    """Edit an existing log message in the log channel."""
    channel_id = await get_log_channel()
    if not channel_id or not message_id:
        return
    try:
        await client.edit_message_text(
            chat_id=channel_id,
            message_id=message_id,
            text=text,
        )
    except Exception:
        log.warning("Failed to edit log message %s in channel %s", message_id, channel_id)


async def log_search(client: Client, username: str, query: str):
    """Log a search query."""
    username_str = f"@{username}" if username else "unknown"
    await log_to_channel(client, f"🔍 User {username_str} searched: {query}")


async def log_download_start(client: Client, username: str, slug: str) -> int | None:
    """Log download start. Returns message_id for progress updates."""
    username_str = f"@{username}" if username else "unknown"
    return await log_to_channel(client, f"⬇️ User {username_str} downloading: {slug}")


def _progress_bar(pct: float, length: int = 10) -> str:
    filled = int(length * pct / 100)
    empty = length - filled
    bar = "█" * filled + "░" * empty
    return f"[{bar}] {pct:.1f}%"


async def log_download_progress(client: Client, message_id: int, username: str, slug: str, progress: float):
    """Update download progress on the log message."""
    username_str = f"@{username}" if username else "unknown"
    bar = _progress_bar(progress)
    await edit_log_message(
        client, message_id,
        f"⬇️ User {username_str} downloading: {slug}\n{bar}"
    )


async def log_upload_complete(client: Client, message_id: int | None, slug: str, file_id: str):
    """Log upload completion. If message_id is provided, edits that message."""
    if message_id:
        await edit_log_message(
            client, message_id,
            f"✅ Uploaded: {slug} (file_id: {file_id[:20]}...)"
        )
    else:
        await log_to_channel(client, f"✅ Uploaded: {slug} (file_id: {file_id[:20]}...)")


async def log_error(client: Client, username: str, description: str):
    """Log an error."""
    username_str = f"@{username}" if username else "unknown"
    await log_to_channel(client, f"❌ Error for {username_str}: {description}")


async def log_user_action(client: Client, action: str, user_id: int, admin_username: str):
    """Log user approval/rejection/revoke actions."""
    admin_str = f"@{admin_username}" if admin_username else "unknown"
    await log_to_channel(client, f"👤 {action}: user {user_id} by {admin_str}")


async def log_admin_action(client: Client, action: str, user_id: int, admin_username: str):
    """Log admin add/remove actions."""
    admin_str = f"@{admin_username}" if admin_username else "unknown"
    await log_to_channel(client, f"🛡 {action}: user {user_id} by {admin_str}")
