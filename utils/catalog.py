"""
Series Catalog system.

Maintains a browsable catalog in the main channel. Each series gets one
message with a poster, tag info, episode count, and an inline "Get Episodes"
button. When a new episode is downloaded, the catalog entry is created or
updated.

MongoDB collection: MangaDb.catalog
"""

import asyncio
import logging
import os
import re
from datetime import datetime, timezone

from pyrogram import Client
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from utils.db import get_db
from utils.logger import get_main_channel
from utils.poster import download_poster

log = logging.getLogger(__name__)


def _extract_series_slug(episode_slug: str) -> str:
    """
    Extract the series slug from an episode slug by stripping the trailing
    episode number.

    Examples:
        "ane-yome-quartet-1"  -> "ane-yome-quartet"
        "ane-yome-quartet-2"  -> "ane-yome-quartet"
        "some-title"          -> "some-title"
    """
    parts = episode_slug.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0]
    return episode_slug


def _slug_to_display_name(slug: str) -> str:
    """Convert a slug to a human-readable title-case name."""
    return slug.replace("-", " ").title()


def _build_caption(series_name: str, tags: list[str], episode_count: int) -> str:
    """Build the caption text for the catalog channel message."""
    tags_str = ", ".join(tags[:6]) if tags else "—"
    return (
        f"📺 **{series_name}**\n"
        f"🏷 {tags_str}\n"
        f"📂 Episodes: {episode_count}"
    )


def _build_keyboard(series_slug: str) -> InlineKeyboardMarkup:
    """Build the inline keyboard with a 'Get Episodes' button."""
    # Callback data limit is 64 bytes. "cat_" = 4 bytes, so slug can be up to 60.
    cb_data = f"cat_{series_slug}"
    if len(cb_data.encode("utf-8")) > 64:
        # Truncate slug to fit
        cb_data = cb_data[:64]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📁 Get Episodes", callback_data=cb_data)]
    ])


async def update_catalog(
    client: Client,
    slug: str,
    file_id: str,
    file_size: int,
    series_name: str,
    poster_url: str,
    tags: list[str],
) -> dict | None:
    log.info("update_catalog called for %s with poster_url=%s", slug, poster_url[:80] if poster_url else "NONE")
    
    # If no poster_url provided, try to fetch from API
    if not poster_url:
        try:
            from api.hanime_api import HanimeAPI
            api = HanimeAPI()
            info = api.details(slug)
            if info:
                poster_url = (info.get('poster_url') or 
                             info.get('cover_url') or 
                             info.get('poster') or 
                             '')
                log.info("Fetched poster from API for %s: %s", slug, poster_url[:80] if poster_url else "NONE")
        except Exception as e:
            log.warning("Failed to fetch poster from API for %s: %s", slug, e)
    """
    Update the series catalog after a successful episode download.

    - Extracts the series slug from the episode slug.
    - Upserts the episode into the catalog collection.
    - Creates or updates the catalog message in the main channel.

    Returns the catalog document, or None on failure.
    """
    db = get_db()
    series_slug = _extract_series_slug(slug)

    # Use provided series_name or derive from slug
    display_name = series_name or _slug_to_display_name(series_slug)

    # Episode entry
    episode_data = {
        "file_id": file_id,
        "name": _slug_to_display_name(slug),
        "file_size": file_size,
    }

    # Upsert the episode into the catalog doc
    await db.catalog.update_one(
        {"series": series_slug},
        {
            "$set": {
                f"episodes.{slug}": episode_data,
                "series_name": display_name,
                "poster_url": poster_url,
                "tags": tags,
                "updated_at": datetime.now(timezone.utc),
            },
            "$setOnInsert": {
                "series": series_slug,
            },
        },
        upsert=True,
    )

    # Reload the doc to get current state
    catalog_doc = await db.catalog.find_one({"series": series_slug})
    if not catalog_doc:
        log.error("Catalog doc missing after upsert for series=%s", series_slug)
        return None

    episodes = catalog_doc.get("episodes", {})
    episode_count = len(episodes)

    caption = _build_caption(display_name, tags, episode_count)
    keyboard = _build_keyboard(series_slug)

    main_channel = await get_main_channel()
    if not main_channel:
        log.warning("No main channel set — skipping catalog channel message for %s", series_slug)
        return catalog_doc

    channel_message_id = catalog_doc.get("channel_message_id")

    if channel_message_id:
        # ── Update existing message ─────────────────────────────────
        try:
            await client.edit_message_caption(
                chat_id=main_channel,
                message_id=channel_message_id,
                caption=caption,
                reply_markup=keyboard,
            )
            log.info("Updated catalog message %d for series=%s (episodes=%d)",
                     channel_message_id, series_slug, episode_count)
        except Exception:
            log.exception("Failed to edit catalog message %d for %s — sending new one",
                          channel_message_id, series_slug)
            # Message might have been deleted; fall through to create new
            channel_message_id = None

    if not channel_message_id:
        # ── Create new catalog message ──────────────────────────────
        poster_path = None
        try:
            # Try to download poster with fallback
            log.info("Attempting to download poster for %s from: %s", series_slug, poster_url[:80] if poster_url else "NONE")
            poster_path = await download_poster(poster_url)
            
            if not poster_path and poster_url:
                log.warning("Failed to download poster for %s from %s", series_slug, poster_url[:80])

            if poster_path:
                log.info("Poster downloaded successfully: %s (%d bytes)", poster_path, os.path.getsize(poster_path))
                try:
                    msg = await client.send_photo(
                        chat_id=main_channel,
                        photo=poster_path,
                        caption=caption,
                        reply_markup=keyboard,
                    )
                    log.info("Sent catalog message with poster for %s", series_slug)
                except Exception as e:
                    log.error("Failed to send_photo for %s: %s", series_slug, e)
                    # Fallback to text message
                    msg = await client.send_message(
                        chat_id=main_channel,
                        text=caption,
                        reply_markup=keyboard,
                    )
                    log.info("Sent catalog text message as fallback for %s", series_slug)
            else:
                # No poster available — send text-only message
                log.info("No poster available, sending text-only catalog for %s", series_slug)
                msg = await client.send_message(
                    chat_id=main_channel,
                    text=caption,
                    reply_markup=keyboard,
                )
                log.info("Sent catalog message without poster for %s", series_slug)

            channel_message_id = msg.id
            log.info("Created catalog message %d for series=%s", channel_message_id, series_slug)

            # Save the message ID back to the catalog doc
            await db.catalog.update_one(
                {"series": series_slug},
                {"$set": {
                    "channel_message_id": channel_message_id,
                    "channel_id": main_channel,
                }},
            )

        except Exception:
            log.exception("Failed to create catalog message for series=%s", series_slug)
        finally:
            if poster_path and os.path.exists(poster_path):
                try:
                    os.unlink(poster_path)
                except OSError:
                    pass

    return catalog_doc


async def get_catalog_episodes(series_slug: str) -> dict:
    """
    Retrieve all episodes for a series from the catalog.

    Returns a dict of {episode_slug: {"file_id": ..., "name": ..., "file_size": ...}}
    or an empty dict if nothing found.
    """
    db = get_db()
    doc = await db.catalog.find_one({"series": series_slug})
    if not doc:
        return {}
    return doc.get("episodes", {})
