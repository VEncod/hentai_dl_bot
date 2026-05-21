"""
Log channel and main channel management commands.

Commands:
    /setlog <channel_id>        — set log channel
    /removelog                  — remove log channel
    /setchannel <channel_id>    — set main channel (file archive)
    /removechannel              — remove main channel
"""

import logging

from pyrogram import Client
from pyrogram.types import Message

from utils.db import get_db
from utils.auth import admin_only
from utils.fsub import clear_fsub_cache

log = logging.getLogger(__name__)


@admin_only
async def setlog_command(client: Client, message: Message):
    """Set the log channel ID."""
    parts = message.text.split()

    channel_id = None

    # Check if replying to a forwarded message from a channel
    if message.reply_to_message and message.reply_to_message.forward_from_chat:
        channel_id = message.reply_to_message.forward_from_chat.id
    elif len(parts) >= 2:
        try:
            channel_id = int(parts[1])
        except ValueError:
            await message.reply_text("❌ Invalid channel ID. Must be a number (e.g., `-1001234567890`).")
            return
    else:
        await message.reply_text(
            "**Usage:** `/setlog <channel_id>`\n"
            "Or reply to a forwarded message from the channel."
        )
        return

    db = get_db()
    await db.config.update_one(
        {"key": "log_channel"},
        {"$set": {"key": "log_channel", "value": channel_id}},
        upsert=True,
    )

    # Test sending
    try:
        msg = await client.send_message(channel_id, "✅ Log channel configured successfully!")
        await client.delete_messages(channel_id, msg.id)
    except Exception:
        await message.reply_text(
            f"⚠️ Log channel set to `{channel_id}`, but I couldn't send a test message. "
            "Make sure the bot is an admin in the channel."
        )
        return

    await message.reply_text(f"✅ Log channel set to `{channel_id}`.")


@admin_only
async def removelog_command(client: Client, message: Message):
    """Remove the log channel."""
    db = get_db()
    result = await db.config.delete_one({"key": "log_channel"})
    if result.deleted_count:
        await message.reply_text("✅ Log channel removed.")
    else:
        await message.reply_text("ℹ️ No log channel was set.")


@admin_only
async def setchannel_command(client: Client, message: Message):
    """Set the main channel (file archive)."""
    parts = message.text.split()

    channel_id = None

    if message.reply_to_message and message.reply_to_message.forward_from_chat:
        channel_id = message.reply_to_message.forward_from_chat.id
    elif len(parts) >= 2:
        try:
            channel_id = int(parts[1])
        except ValueError:
            await message.reply_text("❌ Invalid channel ID. Must be a number.")
            return
    else:
        await message.reply_text(
            "**Usage:** `/setchannel <channel_id>`\n"
            "Or reply to a forwarded message from the channel."
        )
        return

    db = get_db()
    await db.config.update_one(
        {"key": "main_channel"},
        {"$set": {"key": "main_channel", "value": channel_id}},
        upsert=True,
    )

    # Test sending
    try:
        msg = await client.send_message(channel_id, "✅ Main channel configured successfully!")
        await client.delete_messages(channel_id, msg.id)
    except Exception:
        await message.reply_text(
            f"⚠️ Main channel set to `{channel_id}`, but I couldn't send a test message. "
            "Make sure the bot is an admin in the channel."
        )
        return

    clear_fsub_cache()
    await message.reply_text(f"✅ Main channel set to `{channel_id}`.")


@admin_only
async def removechannel_command(client: Client, message: Message):
    """Remove the main channel."""
    db = get_db()
    result = await db.config.delete_one({"key": "main_channel"})
    clear_fsub_cache()
    if result.deleted_count:
        await message.reply_text("✅ Main channel removed.")
    else:
        await message.reply_text("ℹ️ No main channel was set.")
