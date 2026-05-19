"""
Archive and series browsing commands.

Commands:
    /archive <series_name>  — list all episodes of a series (approved users)
    /series                 — list all archived series (approved users)
"""

import logging

from pyrogram import Client
from pyrogram.types import Message

from utils.db import get_db
from utils.auth import approved_only

log = logging.getLogger(__name__)


@approved_only
async def archive_command(client: Client, message: Message):
    """List all episodes of a series from the archive."""
    parts = message.text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip():
        await message.reply_text("**Usage:** `/archive <series name>`")
        return

    series_name = parts[1].strip()
    db = get_db()

    # Search with case-insensitive regex
    episodes = await db.archive.find(
        {"series": {"$regex": series_name, "$options": "i"}}
    ).sort("uploaded_at", 1).to_list(length=200)

    if not episodes:
        await message.reply_text(f"No episodes found for series matching **{series_name}**.")
        return

    lines = [f"📂 **Archive: {series_name}**\n"]
    for ep in episodes:
        slug = ep.get("slug", "unknown")
        channel_id = ep.get("channel_id")
        message_id = ep.get("message_id")

        if channel_id and message_id:
            # Convert channel_id to a link-friendly format
            # Telegram channel links: https://t.me/c/<channel_id_without_-100>/<message_id>
            chan_id_str = str(channel_id)
            if chan_id_str.startswith("-100"):
                chan_id_str = chan_id_str[4:]
            link = f"https://t.me/c/{chan_id_str}/{message_id}"
            lines.append(f"• [{slug}]({link})")
        else:
            lines.append(f"• {slug}")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n\n... (truncated)"

    await message.reply_text(text, disable_web_page_preview=True)


@approved_only
async def series_command(client: Client, message: Message):
    """List all unique archived series."""
    db = get_db()

    # Get distinct series names
    series_list = await db.archive.distinct("series")

    if not series_list:
        await message.reply_text("No series in the archive yet.")
        return

    # Count episodes per series
    lines = [f"📚 **Archived Series** ({len(series_list)}):\n"]
    for name in sorted(series_list):
        count = await db.archive.count_documents({"series": name})
        lines.append(f"• **{name}** — {count} episode(s)")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n\n... (truncated)"

    await message.reply_text(text)
