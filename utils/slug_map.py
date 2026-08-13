"""
Slug Mapping Utility.

Ensures Telegram inline button callback_data is always <= 64 bytes by mapping
long video/series slugs to short 16-character MD5 hashes with MongoDB + memory caching.
"""

import hashlib
import logging
from utils.db import get_db

log = logging.getLogger(__name__)

_memory_slug_cache: dict[str, str] = {}


async def get_short_slug(slug: str) -> str:
    """Return a short slug key if the original slug is longer than 45 bytes."""
    if not slug:
        return ""

    if len(slug.encode("utf-8")) <= 45:
        return slug

    short_hash = hashlib.md5(slug.encode("utf-8")).hexdigest()[:16]
    _memory_slug_cache[short_hash] = slug

    try:
        db = get_db()
        await db.slug_map.update_one(
            {"short": short_hash},
            {"$set": {"short": short_hash, "full": slug}},
            upsert=True,
        )
    except Exception as e:
        log.warning("Could not persist slug map to DB: %s", e)

    return short_hash


async def resolve_slug(key: str) -> str:
    """Resolve a short key back to full slug, or return key if already full."""
    if not key:
        return ""

    if key in _memory_slug_cache:
        return _memory_slug_cache[key]

    try:
        db = get_db()
        doc = await db.slug_map.find_one({"short": key})
        if doc and "full" in doc:
            _memory_slug_cache[key] = doc["full"]
            return doc["full"]
    except Exception as e:
        log.warning("Could not resolve slug from DB: %s", e)

    return key
