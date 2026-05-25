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
from utils.link_resolver import (
    needs_link_resolution, get_message_links, resolve_all_links,
    is_shortened_url, is_telegram_link, set_search_context,
)

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
    Build search queries from slug and name — SHORT and FLEXIBLE.
    Channels often use abbreviated or different names, so we generate
    multiple short variations to maximize match chances.

    E.g. "showtime-uta-no-onee-san-datte-shitai-season-1" →
      ["showtime uta no onee san", "showtime", "uta no onee san",
       "showtime hindi", "uta no onee san hindi", ...]
    """
    queries = []

    # From slug — full text
    slug_text = slug.replace("-", " ").strip()

    # Strip trailing episode/season numbers
    # "showtime-uta-no-onee-san-datte-shitai-season-1" → series + ep
    ep_num = ""
    series_text = slug_text
    parts = slug.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit():
        ep_num = parts[1]
        series_text = parts[0].replace("-", " ")

    # Also strip "season X" from series text
    series_clean = re.sub(r'\s+season\s*\d*', '', series_text, flags=re.IGNORECASE).strip()

    # From display name
    name_clean = ""
    if name:
        name_clean = _normalize_title(name)
        # Strip episode numbers from name too
        name_clean = re.sub(r'\s+\d+$', '', name_clean).strip()
        name_clean = re.sub(r'\s+season\s*\d*', '', name_clean, flags=re.IGNORECASE).strip()
        name_clean = re.sub(r'\s+episode\s*\d*', '', name_clean, flags=re.IGNORECASE).strip()

    # Build query list — SHORT queries first (more likely to match)
    # 1. First 3-4 significant words of the title (most distinctive part)
    words = series_clean.split()
    if len(words) > 4:
        queries.append(" ".join(words[:4]))  # First 4 words
        queries.append(" ".join(words[:3]))  # First 3 words
    if len(words) > 2:
        queries.append(" ".join(words[:3]))

    # 2. Full series name (without season/ep)
    queries.append(series_clean)

    # 3. Name from API (often cleaner)
    if name_clean and name_clean != series_clean:
        name_words = name_clean.split()
        if len(name_words) > 4:
            queries.append(" ".join(name_words[:4]))
        queries.append(name_clean)

    # 4. With episode number
    if ep_num:
        queries.append(f"{series_clean} {ep_num}")
        if name_clean:
            queries.append(f"{name_clean} {ep_num}")

    # 5. Hindi variants of the best queries
    hindi_queries = []
    for q in queries[:4]:
        hindi_queries.append(f"{q} hindi")
    queries.extend(hindi_queries)

    # 6. Just the first 2 words (very broad fallback)
    if len(words) >= 2:
        queries.append(" ".join(words[:2]))

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for q in queries:
        q = q.strip()
        if q and q not in seen:
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


async def clear_hindi_cache(slug: str = None):
    """Clear Hindi dub cache. If slug given, clear just that entry. Otherwise clear all."""
    db = get_hindi_db()
    if slug:
        await db.hindi_cache.delete_one({"slug": slug})
    else:
        await db.hindi_cache.delete_many({})
    return True


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

    # Set search context so invite link resolver knows what to look for
    set_search_context(slug)

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

            result = await _search_channel(ub, ch_id, ch_title, slug, queries, progress_cb)
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
    progress_cb=None,
) -> dict | None:
    """
    Search a specific channel for matching Hindi dub content.
    Handles both direct video files AND shortened links.
    """
    for query in queries[:5]:
        # Search with VIDEO filter first, then DOCUMENT, then no filter (text posts with links)
        for msg_filter in [MessagesFilter.VIDEO, MessagesFilter.DOCUMENT, MessagesFilter.EMPTY]:
            try:
                async for msg in ub.search_messages(
                    chat_id=channel_id,
                    query=query,
                    limit=20,
                    filter=msg_filter,
                ):
                    # Case 1: Direct video file
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

                    # Case 2: Message has links — try ALL of them
                    if needs_link_resolution(msg):
                        links = get_message_links(msg)
                        if links:
                            log.info("Found %d links in %s — resolving all",
                                     len(links), channel_title)
                            if progress_cb:
                                await progress_cb(
                                    f"🔗 Found {len(links)} links in **{channel_title}**\n"
                                    f"🔓 Trying all..."
                                )
                            resolved = await resolve_all_links(ub, links, progress_cb)
                            if resolved and resolved.get("file_id"):
                                await _save_to_cache(
                                    slug,
                                    resolved["file_id"],
                                    resolved.get("file_name", ""),
                                    channel_id, channel_title,
                                    msg.id,
                                    resolved.get("file_size", 0),
                                )
                                resolved["channel_id"] = channel_id
                                resolved["channel_title"] = channel_title
                                resolved["message_id"] = msg.id
                                return resolved

            except Exception as e:
                log.debug("Search failed in %s for '%s' (filter=%s): %s",
                          channel_title, query, msg_filter, e)
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
                ch_id = msg.chat.id if msg.chat else None
                ch_title = msg.chat.title if msg.chat else "Unknown"

                if ch_id and ch_id not in checked_channels:
                    checked_channels.add(ch_id)

                # Basic relevance check — at least 2 key words must match
                caption_text = (msg.caption or "").lower() + " " + (msg.text or "").lower()
                slug_words = slug.replace("-", " ").lower().split()
                # Filter out common stop words
                stop_words = {"no", "wa", "to", "de", "ni", "the", "a", "in", "of", "and", "or", "season", "episode"}
                key_words = [w for w in slug_words if w not in stop_words and len(w) > 2]
                matches = sum(1 for w in key_words if w in caption_text)
                if matches < min(2, len(key_words)):
                    continue

                # Case 1: Direct video file
                if _is_video_message(msg):
                    file_id, file_name, file_size = _extract_file_info(msg)
                    if file_id:
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

                # Case 2: Message has links — try ALL of them
                if needs_link_resolution(msg):
                    links = get_message_links(msg)
                    if links:
                        if progress_cb:
                            await progress_cb(
                                f"🔗 Found {len(links)} links in **{ch_title}**\n"
                                f"🔓 Trying all..."
                            )
                        resolved = await resolve_all_links(ub, links, progress_cb)
                        if resolved and resolved.get("file_id"):
                            await _save_to_cache(
                                slug,
                                resolved["file_id"],
                                resolved.get("file_name", ""),
                                ch_id or 0, ch_title,
                                msg.id,
                                resolved.get("file_size", 0),
                            )
                            if progress_cb:
                                await progress_cb(f"✅ Found in **{ch_title}**!")
                            resolved["channel_id"] = ch_id
                            resolved["channel_title"] = ch_title
                            resolved["message_id"] = msg.id
                            return resolved

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
