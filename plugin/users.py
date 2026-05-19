"""
User approval system commands.

Commands:
    /request               — user requests access
    /approve <user_id>     — admin approves user
    /reject <user_id>      — admin rejects user
    /revoke <user_id>      — admin revokes approved user
    /users                 — admin lists all approved users
    /pending               — admin lists pending requests with inline buttons
    /adduser <user_id>     — admin directly adds user
    /removeuser <user_id>  — admin removes user

Callback queries:
    apr_<user_id>          — approve from inline button
    rej_<user_id>          — reject from inline button
"""

import logging
from datetime import datetime, timezone

from pyrogram import Client
from pyrogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from utils.db import get_db
from utils.auth import admin_only, is_admin
from utils.logger import log_user_action

log = logging.getLogger(__name__)


async def request_command(client: Client, message: Message):
    """User requests access to the bot."""
    user = message.from_user
    db = get_db()

    # Check if already approved
    if await db.approved_users.find_one({"user_id": user.id}):
        await message.reply_text("✅ You already have access!")
        return

    # Check if already admin
    if await db.admins.find_one({"user_id": user.id}):
        await message.reply_text("✅ You're an admin, you already have access!")
        return

    # Check if already has a pending request
    pending = await db.user_requests.find_one({"user_id": user.id, "status": "pending"})
    if pending:
        await message.reply_text("⏳ You already have a pending request. Please wait for admin approval.")
        return

    # Create request
    await db.user_requests.insert_one({
        "user_id": user.id,
        "username": user.username or "",
        "first_name": user.first_name or "",
        "requested_at": datetime.now(timezone.utc),
        "status": "pending",
    })

    await message.reply_text(
        "✅ Access request submitted! An admin will review it shortly."
    )

    # Notify all admins
    admins = await db.admins.find().to_list(length=100)
    username_str = f"@{user.username}" if user.username else user.first_name or str(user.id)
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"apr_{user.id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"rej_{user.id}"),
        ]
    ])

    for admin in admins:
        try:
            await client.send_message(
                chat_id=admin["user_id"],
                text=(
                    f"🔔 **New Access Request**\n\n"
                    f"User: {username_str}\n"
                    f"ID: `{user.id}`\n"
                    f"Name: {user.first_name or 'N/A'}"
                ),
                reply_markup=keyboard,
            )
        except Exception:
            log.warning("Failed to notify admin %s about access request", admin["user_id"])


@admin_only
async def approve_command(client: Client, message: Message):
    """Admin approves a user by ID."""
    parts = message.text.split()
    if len(parts) < 2:
        await message.reply_text("**Usage:** `/approve <user_id>`")
        return

    try:
        target_id = int(parts[1])
    except ValueError:
        await message.reply_text("❌ Invalid user ID.")
        return

    await _approve_user(client, target_id, message.from_user.id, message.from_user.username)
    await message.reply_text(f"✅ User `{target_id}` has been approved.")


@admin_only
async def reject_command(client: Client, message: Message):
    """Admin rejects a user's request."""
    parts = message.text.split()
    if len(parts) < 2:
        await message.reply_text("**Usage:** `/reject <user_id>`")
        return

    try:
        target_id = int(parts[1])
    except ValueError:
        await message.reply_text("❌ Invalid user ID.")
        return

    await _reject_user(client, target_id, message.from_user.username)
    await message.reply_text(f"❌ User `{target_id}` has been rejected.")


@admin_only
async def revoke_command(client: Client, message: Message):
    """Admin revokes an approved user."""
    parts = message.text.split()
    if len(parts) < 2:
        await message.reply_text("**Usage:** `/revoke <user_id>`")
        return

    try:
        target_id = int(parts[1])
    except ValueError:
        await message.reply_text("❌ Invalid user ID.")
        return

    db = get_db()
    result = await db.approved_users.delete_one({"user_id": target_id})
    if result.deleted_count == 0:
        await message.reply_text(f"ℹ️ User `{target_id}` was not in the approved list.")
        return

    await message.reply_text(f"✅ User `{target_id}` access has been revoked.")
    await log_user_action(client, "User revoked", target_id, message.from_user.username)

    # Try to notify the user
    try:
        await client.send_message(target_id, "⛔ Your access has been revoked by an admin.")
    except Exception:
        pass


@admin_only
async def users_command(client: Client, message: Message):
    """List all approved users."""
    db = get_db()
    users = await db.approved_users.find().to_list(length=500)

    if not users:
        await message.reply_text("No approved users yet.")
        return

    lines = [f"👥 **Approved Users** ({len(users)}):\n"]
    for u in users:
        uid = u["user_id"]
        uname = u.get("username", "")
        uname_str = f"@{uname}" if uname else str(uid)
        lines.append(f"• {uname_str} (`{uid}`)")

    # Split if too long
    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n\n... (truncated)"
    await message.reply_text(text)


@admin_only
async def pending_command(client: Client, message: Message):
    """List pending access requests with approve/reject buttons."""
    db = get_db()
    pending = await db.user_requests.find({"status": "pending"}).to_list(length=100)

    if not pending:
        await message.reply_text("No pending requests.")
        return

    for req in pending:
        uid = req["user_id"]
        uname = req.get("username", "")
        fname = req.get("first_name", "")
        uname_str = f"@{uname}" if uname else fname or str(uid)
        requested_at = req.get("requested_at", "N/A")

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"apr_{uid}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"rej_{uid}"),
            ]
        ])

        await message.reply_text(
            f"🔔 **Pending Request**\n\n"
            f"User: {uname_str}\n"
            f"ID: `{uid}`\n"
            f"Requested: {requested_at}",
            reply_markup=keyboard,
        )


@admin_only
async def adduser_command(client: Client, message: Message):
    """Admin directly adds a user without a request."""
    parts = message.text.split()
    if len(parts) < 2:
        await message.reply_text("**Usage:** `/adduser <user_id>`")
        return

    try:
        target_id = int(parts[1])
    except ValueError:
        await message.reply_text("❌ Invalid user ID.")
        return

    db = get_db()
    existing = await db.approved_users.find_one({"user_id": target_id})
    if existing:
        await message.reply_text(f"ℹ️ User `{target_id}` is already approved.")
        return

    await db.approved_users.insert_one({
        "user_id": target_id,
        "username": "",
        "approved_by": message.from_user.id,
        "approved_at": datetime.now(timezone.utc),
    })

    await message.reply_text(f"✅ User `{target_id}` has been directly approved.")
    await log_user_action(client, "User directly added", target_id, message.from_user.username)


@admin_only
async def removeuser_command(client: Client, message: Message):
    """Admin removes an approved user."""
    parts = message.text.split()
    if len(parts) < 2:
        await message.reply_text("**Usage:** `/removeuser <user_id>`")
        return

    try:
        target_id = int(parts[1])
    except ValueError:
        await message.reply_text("❌ Invalid user ID.")
        return

    db = get_db()
    result = await db.approved_users.delete_one({"user_id": target_id})
    if result.deleted_count == 0:
        await message.reply_text(f"ℹ️ User `{target_id}` was not in the approved list.")
        return

    await message.reply_text(f"✅ User `{target_id}` has been removed.")
    await log_user_action(client, "User removed", target_id, message.from_user.username)


# ── Inline button callbacks ─────────────────────────────────────────────

async def approve_callback(client: Client, callback_query: CallbackQuery):
    """Handle apr_<user_id> callback from inline buttons."""
    if not await is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Admin only.", show_alert=True)
        return

    target_id = int(callback_query.data.split("_", 1)[1])
    await _approve_user(client, target_id, callback_query.from_user.id, callback_query.from_user.username)
    await callback_query.edit_message_text(
        f"✅ User `{target_id}` has been **approved** by @{callback_query.from_user.username or 'admin'}."
    )


async def reject_callback(client: Client, callback_query: CallbackQuery):
    """Handle rej_<user_id> callback from inline buttons."""
    if not await is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Admin only.", show_alert=True)
        return

    target_id = int(callback_query.data.split("_", 1)[1])
    await _reject_user(client, target_id, callback_query.from_user.username)
    await callback_query.edit_message_text(
        f"❌ User `{target_id}` has been **rejected** by @{callback_query.from_user.username or 'admin'}."
    )


# ── Helpers ──────────────────────────────────────────────────────────────

async def _approve_user(client: Client, target_id: int, admin_id: int, admin_username: str | None):
    """Approve a user: add to approved_users, update request status."""
    db = get_db()

    # Add to approved users (upsert to avoid duplicates)
    request_doc = await db.user_requests.find_one({"user_id": target_id, "status": "pending"})
    username = request_doc.get("username", "") if request_doc else ""

    await db.approved_users.update_one(
        {"user_id": target_id},
        {"$set": {
            "user_id": target_id,
            "username": username,
            "approved_by": admin_id,
            "approved_at": datetime.now(timezone.utc),
        }},
        upsert=True,
    )

    # Update request status
    await db.user_requests.update_many(
        {"user_id": target_id, "status": "pending"},
        {"$set": {"status": "approved"}},
    )

    await log_user_action(client, "User approved", target_id, admin_username)

    # Try to notify the user
    try:
        await client.send_message(target_id, "🎉 Your access request has been approved! You can now use /search.")
    except Exception:
        pass


async def _reject_user(client: Client, target_id: int, admin_username: str | None):
    """Reject a user's request."""
    db = get_db()

    await db.user_requests.update_many(
        {"user_id": target_id, "status": "pending"},
        {"$set": {"status": "rejected"}},
    )

    await log_user_action(client, "User rejected", target_id, admin_username)

    # Try to notify the user
    try:
        await client.send_message(target_id, "❌ Your access request has been rejected.")
    except Exception:
        pass
