"""
Catalog callback handler.

When a user taps "📁 Get Episodes" on a catalog message in the main channel,
this handler sends them all cached episode file_ids privately.

Callback data format: cat_<series_slug>
"""

import asyncio
import logging

from pyrogram import Client
from pyrogram.types import CallbackQuery

from utils.catalog import get_catalog_episodes
from utils.db import get_db
from utils.autodelete import track_message

log = logging.getLogger(__name__)


async def catalog_episodes_callback(client: Client, callback_query: CallbackQuery):
    """Handle cat_<series_slug> callbacks — send episodes to user privately."""
    series_slug = callback_query.data.split("_", 1)[1]
    user_id = callback_query.from_user.id

    log.info("Catalog request: series=%s user=%s", series_slug, user_id)

    episodes = await get_catalog_episodes(series_slug)

    if not episodes:
        await callback_query.answer("❌ No episodes downloaded yet.", show_alert=True)
        return

    await callback_query.answer(f"📤 Sending {len(episodes)} episode(s)...")

    # Sort episodes by slug (natural episode ordering)
    sorted_episodes = sorted(episodes.items(), key=lambda x: x[0])

    sent_count = 0
    for ep_slug, ep_data in sorted_episodes:
        file_id = ep_data.get("file_id")
        ep_name = ep_data.get("name", ep_slug)
        file_size = ep_data.get("file_size", 0)

        if not file_id:
            continue

        size_str = f" ({file_size / 1024 / 1024:.1f} MB)" if file_size else ""
        caption = f"📺 **{ep_name}**{size_str}\nDownloaded via @hanime_dl_bot"

        try:
            sent = await client.send_document(
                chat_id=user_id,
                document=file_id,
                caption=caption,
            )
            await track_message(user_id, sent.id)
            sent_count += 1
        except Exception:
            log.warning("Failed to send episode %s to user %s", ep_slug, user_id)
            # User might not have started the bot — try to notify
            try:
                await callback_query.answer(
                    "❌ I can't send you messages. Please /start the bot first.",
                    show_alert=True,
                )
            except Exception:
                pass
            return

        # Small delay between sends to avoid Telegram flood limits
        if sent_count < len(sorted_episodes):
            await asyncio.sleep(0.5)

    if sent_count == 0:
        await callback_query.answer("❌ No episodes available to send.", show_alert=True)
    else:
        try:
            msg = await client.send_message(
                chat_id=user_id,
                text=f"✅ Sent **{sent_count}** episode(s) from this series.\n⏳ Files auto-delete in 30 minutes. Save what you need!",
            )
            await track_message(user_id, msg.id)
        except Exception:
            pass
