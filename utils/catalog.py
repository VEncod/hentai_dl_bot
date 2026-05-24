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
from datetime import datetime, timezone

from pyrogram import Client
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from utils.db import get_db
from utils.logger import get_main_channel
from utils.poster import download_poster

log = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_series_slug(episode_slug: str) -> str:
    """
    Strip trailing episode number from a slug.
    "ane-yome-quartet-2" -> "ane-yome-quartet"
    "some-title"         -> "some-title"
    """
    parts = episode_slug.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0]
    return episode_slug


def _slug_to_display_name(slug: str) -> str:
    return slug.replace("-", " ").title()


def _build_caption(series_name: str, tags: list[str], episode_count: int) -> str:
    tags_str = ", ".join(tags[:6]) if tags else "—"
    return (
        f"📺 **{series_name}**\n"
        f"🏷 {tags_str}\n"
        f"📂 Episodes: {episode_count}"
    )


def _build_keyboard(series_slug: str) -> InlineKeyboardMarkup:
    cb_data = f"cat_{series_slug}"
    if len(cb_data.encode("utf-8")) > 64:
        cb_data = cb_data[:64]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📁 Get Episodes", callback_data=cb_data)]
    ])


async def _resolve_poster_url(slug: str, provided_url: str) -> str:
    """
    Return the best available poster URL.
    Falls back to API fetch if nothing was provided.
    """
    if provided_url:
        return provided_url

    try:
        from api.hanime_api import HanimeAPI
        api = HanimeAPI()
        info = api.details(slug)
        url = (
            info.get("poster_url")
            or info.get("cover_url")
            or info.get("cover")
            or ""
        )
        if url:
            log.info("Resolved poster URL from API for %s: %s", slug, url[:80])
        return url
    except Exception as e:
        log.warning("Could not resolve poster URL for %s: %s", slug, e)
        return ""


# ── Main entry point ──────────────────────────────────────────────────────────

async def update_catalog(
    client: Client,
    slug: str,
    file_id: str,
    file_size: int,
    series_name: str,
    poster_url: str,
    tags: list[str],
) -> dict | None:
    """
    Update the series catalog after a successful episode download.

    - Extracts the series slug from the episode slug.
    - Upserts the episode into the catalog collection.
    - Creates or updates the catalog message in the main channel.

    Returns the catalog document, or None on failure.
    """
    db = get_db()
    series_slug = _extract_series_slug(slug)
    display_name = series_name or _slug_to_display_name(series_slug)

    # Resolve poster URL if not provided
    poster_url = await _resolve_poster_url(slug, poster_url)
    log.info("update_catalog: series=%s poster=%s", series_slug, poster_url[:80] if poster_url else "NONE")

    # Upsert episode into catalog doc
    episode_data = {
        "file_id":   file_id,
        "name":      _slug_to_display_name(slug),
        "file_size": file_size,
    }

    update_fields: dict = {
        f"episodes.{slug}": episode_data,
        "series_name":      display_name,
        "tags":             tags,
        "updated_at":       datetime.now(timezone.utc),
    }
    # Only overwrite stored poster_url if we have a non-empty one
    if poster_url:
        update_fields["poster_url"] = poster_url

    await db.catalog.update_one(
        {"series": series_slug},
        {
            "$set": update_fields,
            "$setOnInsert": {"series": series_slug},
        },
        upsert=True,
    )

    catalog_doc = await db.catalog.find_one({"series": series_slug})
    if not catalog_doc:
        log.error("Catalog doc missing after upsert for series=%s", series_slug)
        return None

    # Use stored poster_url if current call didn't provide one
    if not poster_url:
        poster_url = catalog_doc.get("poster_url", "")

    episode_count    = len(catalog_doc.get("episodes", {}))
    caption          = _build_caption(display_name, tags, episode_count)
    keyboard         = _build_keyboard(series_slug)

    main_channel = await get_main_channel()
    if not main_channel:
        log.warning("No main channel set — skipping catalog update for %s", series_slug)
        return catalog_doc

    channel_message_id = catalog_doc.get("channel_message_id")

    # ── Try to update existing message ───────────────────────────────────────
    if channel_message_id:
        try:
            await client.edit_message_caption(
                chat_id=main_channel,
                message_id=channel_message_id,
                caption=caption,
                reply_markup=keyboard,
            )
            log.info("Updated catalog message %d for series=%s (ep=%d)",
                     channel_message_id, series_slug, episode_count)
            return catalog_doc
        except Exception as e:
            log.warning(
                "Could not edit catalog message %d for %s (%s) — will create new one",
                channel_message_id, series_slug, e,
            )
            # Clear stale message ID so we create a fresh one
            channel_message_id = None
            await db.catalog.update_one(
                {"series": series_slug},
                {"$unset": {"channel_message_id": "", "channel_id": ""}},
            )

    # ── Create new catalog message ────────────────────────────────────────────
    poster_path = None
    try:
        if poster_url:
            poster_path = await download_poster(poster_url, for_thumbnail=False)

        msg = None

        if poster_path:
            try:
                msg = await client.send_photo(
                    chat_id=main_channel,
                    photo=poster_path,
                    caption=caption,
                    reply_markup=keyboard,
                )
                log.info("Created catalog poster message for %s", series_slug)
            except Exception as e:
                log.warning("send_photo failed for %s: %s — trying text fallback", series_slug, e)

        if not msg:
            # Text-only fallback (no poster or photo send failed)
            msg = await client.send_message(
                chat_id=main_channel,
                text=caption,
                reply_markup=keyboard,
            )
            log.info("Created catalog text message for %s (no poster)", series_slug)

        # Persist the new message ID
        await db.catalog.update_one(
            {"series": series_slug},
            {"$set": {
                "channel_message_id": msg.id,
                "channel_id":         main_channel,
            }},
        )
        log.info("Saved catalog message_id=%d for series=%s", msg.id, series_slug)

    except Exception:
        log.exception("Failed to create catalog message for series=%s", series_slug)
    finally:
        if poster_path and os.path.exists(poster_path):
            try:
                os.unlink(poster_path)
            except OSError:
                pass

    return catalog_doc


# ── Episode retrieval ─────────────────────────────────────────────────────────

async def get_catalog_episodes(series_slug: str) -> dict:
    """
    Retrieve all episodes for a series from the catalog.
    Returns {episode_slug: {file_id, name, file_size}} or {}.
    """
    db = get_db()
    doc = await db.catalog.find_one({"series": series_slug})
    if not doc:
        return {}
    return doc.get("episodes", {})
