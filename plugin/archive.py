"""
Archive and series browsing commands — powered by the catalog collection.

Commands:
    /archive <series_name>  — list all episodes of a series (approved users)
    /series                 — list all cataloged series (approved users)
"""

import logging

from wzgram import Client
from wzgram.types import Message

from utils.db import get_db
from utils.auth import approved_only
from utils.fsub import force_sub

log = logging.getLogger(__name__)


@approved_only
@force_sub
async def archive_command(client: Client, message: Message):
    """List all episodes of a series from the catalog."""
    parts = message.text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip():
        await message.reply_text("**Usage:** `/archive <series name>`")
        return

    series_name = parts[1].strip()
    db = get_db()

    # Search catalog with case-insensitive regex on series or series_name
    docs = await db.catalog.find({
        "$or": [
            {"series": {"$regex": series_name, "$options": "i"}},
            {"series_name": {"$regex": series_name, "$options": "i"}},
        ]
    }).to_list(length=50)

    if not docs:
        await message.reply_text(f"No series found matching **{series_name}**.")
        return

    lines = []
    for doc in docs:
        display_name = doc.get("series_name", doc.get("series", "Unknown"))
        episodes = doc.get("episodes", {})
        channel_id = doc.get("channel_id")
        channel_message_id = doc.get("channel_message_id")

        lines.append(f"📺 **{display_name}** — {len(episodes)} episode(s)")

        if channel_id and channel_message_id:
            chan_id_str = str(channel_id)
            if chan_id_str.startswith("-100"):
                chan_id_str = chan_id_str[4:]
            link = f"https://t.me/c/{chan_id_str}/{channel_message_id}"
            lines.append(f"  📌 [View in channel]({link})")

        # List individual episodes
        for ep_slug in sorted(episodes.keys()):
            ep = episodes[ep_slug]
            ep_name = ep.get("name", ep_slug)
            file_size = ep.get("file_size", 0)
            size_str = f" ({file_size / 1024 / 1024:.1f} MB)" if file_size else ""
            lines.append(f"  • {ep_name}{size_str}")

        lines.append("")  # blank separator

    text = "\n".join(lines).strip()
    if len(text) > 4000:
        text = text[:4000] + "\n\n... (truncated)"

    await message.reply_text(text, disable_web_page_preview=True)


@approved_only
@force_sub
async def series_command(client: Client, message: Message):
    """List all cataloged series."""
    db = get_db()

    # Get all catalog entries
    docs = await db.catalog.find({}).sort("series_name", 1).to_list(length=500)

    if not docs:
        await message.reply_text("No series in the catalog yet.")
        return

    lines = [f"📚 **Series Catalog** ({len(docs)}):\n"]
    for doc in docs:
        display_name = doc.get("series_name", doc.get("series", "Unknown"))
        episodes = doc.get("episodes", {})
        tags = doc.get("tags", [])
        tags_str = f" — {', '.join(tags[:3])}" if tags else ""
        lines.append(f"• **{display_name}** — {len(episodes)} ep(s){tags_str}")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n\n... (truncated)"

    await message.reply_text(text)
