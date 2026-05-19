"""
Admin management commands.

Commands:
    /addadmin <user_id>    — add another admin
    /removeadmin <user_id> — remove admin (owner can't be removed)
    /admins                — list all admins
"""

import logging
from datetime import datetime, timezone

from pyrogram import Client
from pyrogram.types import Message

from utils.db import get_db
from utils.auth import admin_only, is_owner
from utils.logger import log_admin_action

log = logging.getLogger(__name__)


@admin_only
async def addadmin_command(client: Client, message: Message):
    """Add a new admin by user_id."""
    parts = message.text.split()
    if len(parts) < 2:
        await message.reply_text("**Usage:** `/addadmin <user_id>`")
        return

    try:
        target_id = int(parts[1])
    except ValueError:
        await message.reply_text("❌ Invalid user ID. Must be a number.")
        return

    db = get_db()

    # Check if already an admin
    existing = await db.admins.find_one({"user_id": target_id})
    if existing:
        await message.reply_text(f"ℹ️ User `{target_id}` is already an admin.")
        return

    await db.admins.insert_one({
        "user_id": target_id,
        "added_by": message.from_user.id,
        "added_at": datetime.now(timezone.utc),
    })

    await message.reply_text(f"✅ User `{target_id}` has been added as admin.")
    await log_admin_action(client, "Admin added", target_id, message.from_user.username)


@admin_only
async def removeadmin_command(client: Client, message: Message):
    """Remove an admin by user_id. Owner cannot be removed."""
    parts = message.text.split()
    if len(parts) < 2:
        await message.reply_text("**Usage:** `/removeadmin <user_id>`")
        return

    try:
        target_id = int(parts[1])
    except ValueError:
        await message.reply_text("❌ Invalid user ID. Must be a number.")
        return

    db = get_db()

    # Check if target is owner
    target_doc = await db.admins.find_one({"user_id": target_id})
    if not target_doc:
        await message.reply_text(f"ℹ️ User `{target_id}` is not an admin.")
        return

    if target_doc.get("role") == "owner":
        await message.reply_text("⛔ Cannot remove the owner.")
        return

    # Only owner can remove other admins
    if not await is_owner(message.from_user.id):
        await message.reply_text("⛔ Only the owner can remove admins.")
        return

    await db.admins.delete_one({"user_id": target_id})
    await message.reply_text(f"✅ User `{target_id}` has been removed as admin.")
    await log_admin_action(client, "Admin removed", target_id, message.from_user.username)


@admin_only
async def admins_command(client: Client, message: Message):
    """List all admins."""
    db = get_db()
    admins = await db.admins.find().to_list(length=100)

    if not admins:
        await message.reply_text("No admins found.")
        return

    lines = ["🛡 **Admins:**\n"]
    for admin in admins:
        uid = admin["user_id"]
        role = admin.get("role", "admin")
        emoji = "👑" if role == "owner" else "🛡"
        lines.append(f"{emoji} `{uid}` — {role}")

    await message.reply_text("\n".join(lines))
