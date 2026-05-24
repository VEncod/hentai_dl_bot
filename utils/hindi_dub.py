"""
Hindi Dubbed Hentai Search.

Uses the userbot to search through joined Telegram channels for
Hindi-dubbed versions of hentai. Flow:

1. Check MongoDB cache first (instant hit)
2. Search pre-configured Hindi dub channels (fast, targeted)
3. Broader Telegram global search (slower, 5-min timeout)

MongoDB collections:
  - MangaDb.hindi_channels  → list of channel IDs to search
  - MangaDb.hindi_cache     → cached search results {slug, file_id, channel, ...}
"""

import asyncio
import logging
import re
import time
from datetime import datetime, timezone

from pyrogram import Client
from pyrogram.enums import MessagesFilter

from utils.db import get_hindi_db

log = logging.getLogger(__name__)

# Max search time for broad Telegram search
BROAD_SEARCH_TIMEOUT = 300  # 5 minutes

# Global reference to userbot — set by app.py
_userbot: Client | None = None


def set_userbot(client: Client):
    global _userbot
    _userbot = client


def get_userbot() -> Client | None:
    return _userbot


# ── Channel management ───────────────────────────────────────────────────

async def add_hindi_channel(channel_id: int, title: str = "") -> bool:
    """Add a channel to the Hindi dub search list."""
    db = get_hindi_db()
    await db.hindi_channels.update_one(
        {"channel_id": channel_id},
        {"$set": {
            "channel_id": channel_id,
            "title": title,
            "added_at": datetime.now(timezone.utc),
        }},
        upsert=True,
    )
    log.info("Added Hindi dub channel: %s (%s)", channel_id, title)
    return True


async def remove_hindi_channel(channel_id: int) -> bool:
    """Remove a channel from the Hindi dub search list."""
    db = get_hindi_db()
    result = await db.hindi_channels.delete_one({"channel_id": channel_id})
    return result.deleted_count > 0


async def list_hindi_channels() -> list[dict]:
    """List all configured Hindi dub channels."""
    db = get_hindi_db()
    channels = []
    async for doc in db.hindi_channels.find():
        channels.append(doc)
    return channels


# ── Search helpers ───────────────────────────────────────────────────────

def _normalize_title(title: str) -> str:
    """Normalize a title for matching — lowercase, strip punctuation."""
    title = title.lower().strip()
    title = re.sub(r'[^a-z0-9\s]', '', title)
    title = re.sub(r'\s+', ' ', title)
    return title


def _build_search_queries(slug: str, name: str = "") -> list[str]:
    """
    Build search queries from slug and name.
    E.g. "ane-yome-quartet-1" → ["ane yome quartet 1", "ane yome quartet", "ane yome quartet hindi"]
    """
    queries = []

    # From slug
    slug_text = slug.replace("-", " ").strip()
    queries.append(slug_text)

    # Without episode number
    parts = slug.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit():
        series = parts[0].replace("-", " ")
        ep_num = parts[1]
        queries.append(f"{series} {ep_num}")
        queries.append(f"{series} episode {ep_num}")
        queries.append(series)

    # From display name
    if name:
        norm = _normalize_title(name)
        if norm and norm not in queries:
            queries.append(norm)

    # Add "hindi" variants
    hindi_queries = []
    for q in queries[:3]:  # Limit to avoid too many searches
        hindi_queries.append(f"{q} hindi")
        hindi_queries.append(f"{q} hindi dub")
    queries.extend(hindi_queries)

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for q in queries:
        if q not in seen:
            seen.add(q)
            unique.append(q)

    return unique


def _is_video_message(msg) -> bool:
    """Check if a message contains a video or document (video file)."""
    if msg.video:
        return True
    if msg.document:
        mime = msg.document.mime_type or ""
        if "video" in mime:
            return True
        # Check file name
        fname = msg.document.file_name or ""
        if any(fname.lower().endswith(ext) for ext in ['.mp4', '.mkv', '.avi', '.webm']):
            return True
    if msg.animation:
        return True
    return False


def _is_hindi_content(msg) -> bool:
    """Check if a message is likely Hindi dubbed content."""
    text = ""
    if msg.caption:
        text += msg.caption.lower()
    if msg.text:
        text += msg.text.lower()

    hindi_keywords = [
        "hindi", "हिंदी", "हिन्दी", "dubbed", "dub", "डब",
        "fan dub", "fandub", "hindi dub",
    ]
    return any(kw in text for kw in hindi_keywords)


# ── Cache ────────────────────────────────────────────────────────────────

async def _check_cache(slug: str) -> dict | None:
    """Check if we have a cached Hindi dub for this slug."""
    db = get_hindi_db()
    return await db.hindi_cache.find_one({"slug": slug})


async def _save_to_cache(slug: str, file_id: str, file_name: str,
                          channel_id: int, channel_title: str,
                          message_id: int, file_size: int = 0):
    db = get_hindi_db()
    await db.hindi_cache.update_one(
        {"slug": slug},
        {"$set": {
            "slug": slug,
            "file_id": file_id,
            "file_name": file_name,
            "channel_id": channel_id,
            "channel_title": channel_title,
            "message_id": message_id,
            "file_size": file_size,
            "found_at": datetime.now(timezone.utc),
        }},
        upsert=True,
    )


# ── Main search ─────────────────────────────────────────────────────────

async def search_hindi_dub(
    slug: str,
    name: str = "",
    progress_cb=None,
) -> dict | None:
    """
    Search for a Hindi dubbed version of a hentai.

    Args:
        slug:        Episode slug (e.g. "ane-yome-quartet-1")
        name:        Display name (optional, for better search)
        progress_cb: async callback(status_text) for UI updates

    Returns:
        {file_id, file_name, channel_title, ...} or None if not found.
    """
    ub = get_userbot()
    if not ub:
        log.warning("Hindi dub search: userbot not available")
        return None

    # Step 0: Check cache
    cached = await _check_cache(slug)
    if cached:
        log.info("Hindi dub cache hit for %s", slug)
        if progress_cb:
            await progress_cb("📦 Found in cache!")
        return cached

    queries = _build_search_queries(slug, name)
    log.info("Hindi dub search for %s — queries: %s", slug, queries[:5])

    # Step 1: Search pre-configured channels
    channels = await list_hindi_channels()
    if channels:
        if progress_cb:
            await progress_cb(f"🔍 Searching {len(channels)} Hindi dub channels...")

        for i, ch in enumerate(channels):
            ch_id = ch["channel_id"]
            ch_title = ch.get("title", str(ch_id))

            if progress_cb and i > 0 and i % 3 == 0:
                await progress_cb(
                    f"🔍 Searching channels... ({i}/{len(channels)})"
                )

            result = await _search_channel(ub, ch_id, ch_title, slug, queries)
            if result:
                log.info("Hindi dub found in channel %s for %s", ch_title, slug)
                if progress_cb:
                    await progress_cb(f"✅ Found in **{ch_title}**!")
                return result

    # Step 2: Broader Telegram global search (5 min timeout)
    if progress_cb:
        await progress_cb("🌐 Searching all of Telegram... (up to 5 min)")

    result = await _broad_search(ub, slug, queries, progress_cb)
    if result:
        return result

    log.info("Hindi dub not found for %s after full search", slug)
    return None


async def _search_channel(
    ub: Client,
    channel_id: int,
    channel_title: str,
    slug: str,
    queries: list[str],
) -> dict | None:
    """Search a specific channel for matching Hindi dub content."""
    for query in queries[:5]:  # Limit queries per channel
        try:
            async for msg in ub.search_messages(
                chat_id=channel_id,
                query=query,
                limit=20,
                filter=MessagesFilter.VIDEO,
            ):
                if _is_video_message(msg):
                    file_id, file_name, file_size = _extract_file_info(msg)
                    if file_id:
                        await _save_to_cache(
                            slug, file_id, file_name,
                            channel_id, channel_title,
                            msg.id, file_size,
                        )
                        return {
                            "file_id": file_id,
                            "file_name": file_name,
                            "file_size": file_size,
                            "channel_id": channel_id,
                            "channel_title": channel_title,
                            "message_id": msg.id,
                        }
        except Exception as e:
            log.debug("Search failed in %s for '%s': %s", channel_title, query, e)
            continue

        # Also try document filter (some channels send as documents)
        try:
            async for msg in ub.search_messages(
                chat_id=channel_id,
                query=query,
                limit=20,
                filter=MessagesFilter.DOCUMENT,
            ):
                if _is_video_message(msg):
                    file_id, file_name, file_size = _extract_file_info(msg)
                    if file_id:
                        await _save_to_cache(
                            slug, file_id, file_name,
                            channel_id, channel_title,
                            msg.id, file_size,
                        )
                        return {
                            "file_id": file_id,
                            "file_name": file_name,
                            "file_size": file_size,
                            "channel_id": channel_id,
                            "channel_title": channel_title,
                            "message_id": msg.id,
                        }
        except Exception as e:
            log.debug("Document search failed in %s: %s", channel_title, e)
            continue

    return None


async def _broad_search(
    ub: Client,
    slug: str,
    queries: list[str],
    progress_cb=None,
) -> dict | None:
    """
    Search across all of Telegram for Hindi dubbed content.
    Has a 5-minute timeout.
    """
    start = time.time()
    checked_channels = set()

    for qi, query in enumerate(queries[:6]):
        if time.time() - start > BROAD_SEARCH_TIMEOUT:
            break

        hindi_query = query if "hindi" in query else f"{query} hindi"

        if progress_cb:
            elapsed = int(time.time() - start)
            await progress_cb(
                f"🌐 Global search... ({elapsed}s / 300s)\n"
                f"Query: \"{hindi_query}\""
            )

        try:
            # search_global returns messages from public channels/groups
            count = 0
            async for msg in ub.search_global(
                query=hindi_query,
                limit=50,
                filter=MessagesFilter.VIDEO,
            ):
                if time.time() - start > BROAD_SEARCH_TIMEOUT:
                    break

                count += 1
                if not _is_video_message(msg):
                    continue

                # Track which channels we found content in
                ch_id = msg.chat.id if msg.chat else None
                ch_title = msg.chat.title if msg.chat else "Unknown"

                if ch_id and ch_id not in checked_channels:
                    checked_channels.add(ch_id)

                file_id, file_name, file_size = _extract_file_info(msg)
                if not file_id:
                    continue

                # Basic relevance check
                caption_text = (msg.caption or "").lower() + " " + (msg.text or "").lower()
                slug_words = slug.replace("-", " ").lower().split()
                # At least half the slug words should appear in caption
                matches = sum(1 for w in slug_words if w in caption_text)
                if matches < len(slug_words) * 0.4:
                    continue

                log.info("Hindi dub found via global search: %s in %s", file_name, ch_title)
                await _save_to_cache(
                    slug, file_id, file_name,
                    ch_id or 0, ch_title,
                    msg.id, file_size,
                )

                if progress_cb:
                    await progress_cb(f"✅ Found in **{ch_title}**!")

                return {
                    "file_id": file_id,
                    "file_name": file_name,
                    "file_size": file_size,
                    "channel_id": ch_id,
                    "channel_title": ch_title,
                    "message_id": msg.id,
                }

            log.debug("Global search query '%s': checked %d results", hindi_query, count)

        except Exception as e:
            log.warning("Global search failed for '%s': %s", hindi_query, e)
            continue

        # Small delay between queries to avoid rate limiting
        await asyncio.sleep(1)

    return None


def _extract_file_info(msg) -> tuple[str, str, int]:
    """Extract file_id, file_name, file_size from a message."""
    if msg.video:
        return (
            msg.video.file_id,
            msg.video.file_name or f"video_{msg.id}.mp4",
            msg.video.file_size or 0,
        )
    if msg.document:
        return (
            msg.document.file_id,
            msg.document.file_name or f"document_{msg.id}",
            msg.document.file_size or 0,
        )
    if msg.animation:
        return (
            msg.animation.file_id,
            msg.animation.file_name or f"animation_{msg.id}.mp4",
            msg.animation.file_size or 0,
        )
    return ("", "", 0)
