"""
Broadcast command for admins.

Commands:
    /broadcast <message>       — send a text message to all approved users
    /broadcast (reply)         — forward the replied message to all approved users
"""

import asyncio
import logging

from wzgram import Client
from wzgram.types import Message

from utils.db import get_db
from utils.auth import admin_only
from utils.logger import log_to_channel

log = logging.getLogger(__name__)


@admin_only
async def broadcast_command(client: Client, message: Message):
    """Broadcast a message to all approved users."""
    # Determine what to broadcast
    reply = message.reply_to_message
    text = message.text.split(None, 1)[1].strip() if len(message.text.split(None, 1)) > 1 else ""

    if not reply and not text:
        await message.reply_text(
            "**Usage:**\n"
            "• `/broadcast <message>` — send text to all users\n"
            "• Reply to a message with `/broadcast` — forward it to all users"
        )
        return

    db = get_db()

    # Get all approved users
    approved = await db.approved_users.find().to_list(length=10000)
    # Also include admins who might not be in approved_users
    admins = await db.admins.find().to_list(length=100)

    # Deduplicate user IDs
    user_ids = set()
    for u in approved:
        user_ids.add(u["user_id"])
    for a in admins:
        user_ids.add(a["user_id"])

    total = len(user_ids)
    if total == 0:
        await message.reply_text("No users to broadcast to.")
        return

    # Progress message
    progress_msg = await message.reply_text(f"📢 Broadcasting... 0/{total}")

    sent = 0
    failed = 0

    for i, user_id in enumerate(user_ids, 1):
        try:
            if reply:
                await reply.forward(chat_id=user_id)
            else:
                await client.send_message(chat_id=user_id, text=text)
            sent += 1
        except Exception:
            failed += 1

        # Update progress every 10 users
        if i % 10 == 0 or i == total:
            try:
                await progress_msg.edit_text(f"📢 Broadcasting... {i}/{total}")
            except Exception:
                pass

        # Small delay to avoid flood limits
        await asyncio.sleep(0.1)

    # Final result
    result_text = f"✅ Broadcast complete!\n📨 Sent: {sent} | ❌ Failed: {failed}"
    try:
        await progress_msg.edit_text(result_text)
    except Exception:
        await message.reply_text(result_text)

    # Log to log channel
    admin_name = f"@{message.from_user.username}" if message.from_user.username else str(message.from_user.id)
    await log_to_channel(
        client,
        f"📢 **Broadcast** by {admin_name}\n"
        f"📨 Sent: {sent} | ❌ Failed: {failed} | 👥 Total: {total}"
    )
