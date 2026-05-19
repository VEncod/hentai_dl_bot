"""
Authentication decorators for bot handlers.

Usage:
    from utils.auth import admin_only, approved_only

    @admin_only
    async def my_admin_handler(client, message):
        ...

    @approved_only
    async def my_user_handler(client, message):
        ...

Works with both Message and CallbackQuery handlers.
"""

import logging
from functools import wraps

from pyrogram.types import Message, CallbackQuery

from utils.db import get_db

log = logging.getLogger(__name__)


async def is_admin(user_id: int) -> bool:
    """Check if a user is an admin (including owner)."""
    db = get_db()
    doc = await db.admins.find_one({"user_id": user_id})
    return doc is not None


async def is_owner(user_id: int) -> bool:
    """Check if a user is the owner (super admin)."""
    db = get_db()
    doc = await db.admins.find_one({"user_id": user_id, "role": "owner"})
    return doc is not None


async def is_approved(user_id: int) -> bool:
    """Check if a user is approved or is an admin."""
    if await is_admin(user_id):
        return True
    db = get_db()
    doc = await db.approved_users.find_one({"user_id": user_id})
    return doc is not None


def admin_only(func):
    """Decorator: only allow admins to use this handler."""

    @wraps(func)
    async def wrapper(client, update, *args, **kwargs):
        if isinstance(update, CallbackQuery):
            user_id = update.from_user.id
            if not await is_admin(user_id):
                await update.answer("⛔ Admin only.", show_alert=True)
                return
        elif isinstance(update, Message):
            user_id = update.from_user.id
            if not await is_admin(user_id):
                await update.reply_text("⛔ This command is for admins only.")
                return
        else:
            return

        return await func(client, update, *args, **kwargs)

    return wrapper


def approved_only(func):
    """Decorator: only allow approved users and admins to use this handler."""

    @wraps(func)
    async def wrapper(client, update, *args, **kwargs):
        if isinstance(update, CallbackQuery):
            user_id = update.from_user.id
            if not await is_approved(user_id):
                await update.answer(
                    "⛔ You don't have access. Use /request to request access.",
                    show_alert=True,
                )
                return
        elif isinstance(update, Message):
            user_id = update.from_user.id
            if not await is_approved(user_id):
                await update.reply_text(
                    "⛔ You don't have access. Use /request to request access."
                )
                return
        else:
            return

        return await func(client, update, *args, **kwargs)

    return wrapper
