"""
Async wrapper around hanime.tv APIs.

Functions:
    search(query, page=0)   -> list of hit dicts
    details(slug)           -> dict with video metadata + episodes
    get_streams(slug)       -> dict with 'streams' list and 'dl_url'
"""

import json
import logging
import re
from datetime import datetime, timezone

import aiohttp

log = logging.getLogger(__name__)

SEARCH_URL = "https://search.htv-services.com/"
VIDEO_URL = "https://hanime.tv/api/v8/video"

_TIMEOUT = aiohttp.ClientTimeout(total=20)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://hanime.tv/",
    "Origin": "https://hanime.tv",
}


def _unix_to_date(ts: int | float | None) -> str:
    if not ts:
        return "N/A"
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, OSError):
        return "N/A"


def _format_duration(ms: int | None) -> str:
    if not ms:
        return "N/A"
    total_secs = int(ms) // 1000
    mins, secs = divmod(total_secs, 60)
    return f"{mins}:{secs:02d}"


def _fix_pixeldrain_url(url: str) -> str:
    """
    Convert pixeldrain page URLs to direct download API URLs.
    https://pixeldrain.com/d/XXXX  -> https://pixeldrain.com/api/file/XXXX
    https://pixeldrain.com/u/XXXX  -> https://pixeldrain.com/api/file/XXXX
    """
    if not url:
        return url
    m = re.match(r"https?://pixeldrain\.com/[du]/([A-Za-z0-9]+)", url)
    if m:
        file_id = m.group(1)
        return f"https://pixeldrain.com/api/file/{file_id}"
    return url


# ── Search ──────────────────────────────────────────────────────────────

async def search(query: str, page: int = 0) -> list[dict]:
    """Search for hentai videos. Returns list of hit dicts."""
    payload = {
        "search_text": query,
        "tags": [],
        "tags_mode": "AND",
        "brands": [],
        "blacklist": [],
        "order_by": "likes",
        "ordering": "desc",
        "page": page,
    }

    try:
        async with aiohttp.ClientSession(headers=_HEADERS, timeout=_TIMEOUT) as session:
            async with session.post(SEARCH_URL, json=payload) as resp:
                resp.raise_for_status()
                data = await resp.json()
    except aiohttp.ClientError as e:
        log.error("Search failed for query=%r: %s", query, e)
        raise
    except Exception:
        log.exception("Unexpected error during search for query=%r", query)
        raise

    raw_hits = data.get("hits", "[]")
    if isinstance(raw_hits, str):
        try:
            hits = json.loads(raw_hits)
        except json.JSONDecodeError:
            log.error("Failed to parse hits JSON for query=%r", query)
            return []
    else:
        hits = raw_hits

    return hits if isinstance(hits, list) else []


# ── Video Details ───────────────────────────────────────────────────────

async def _fetch_video_data(slug: str) -> dict:
    """Fetch raw video data from the API."""
    try:
        async with aiohttp.ClientSession(headers=_HEADERS, timeout=_TIMEOUT) as session:
            async with session.get(VIDEO_URL, params={"id": slug}) as resp:
                resp.raise_for_status()
                return await resp.json()
    except aiohttp.ClientError as e:
        log.error("Video API failed for slug=%r: %s", slug, e)
        raise
    except Exception:
        log.exception("Unexpected error for slug=%r", slug)
        raise


async def details(slug: str) -> dict:
    """
    Get detailed info for a video by slug.

    Returns dict with: name, slug, views, poster_url, cover_url, description,
    released_date, likes, dislikes, duration, duration_ms, brand, tags, titles,
    episodes (list of {name, slug} for the franchise)
    """
    data = await _fetch_video_data(slug)
    hv = data.get("hentai_video", {})

    # Extract tag names
    tags_raw = hv.get("hentai_tags", [])
    tag_names = []
    for t in tags_raw:
        if isinstance(t, dict):
            tag_names.append(t.get("text", t.get("name", "unknown")))
        elif isinstance(t, str):
            tag_names.append(t)

    # Extract franchise episodes
    episodes = []
    for ep in data.get("hentai_franchise_hentai_videos", []):
        if isinstance(ep, dict):
            episodes.append({
                "name": ep.get("name", "Unknown"),
                "slug": ep.get("slug", ""),
                "poster_url": ep.get("poster_url", ""),
            })

    return {
        "name": hv.get("name", "Unknown"),
        "slug": hv.get("slug", slug),
        "views": hv.get("views", 0),
        "poster_url": hv.get("poster_url", ""),
        "cover_url": hv.get("cover_url", ""),
        "description": hv.get("description", ""),
        "released_date": _unix_to_date(hv.get("released_at_unix")),
        "likes": hv.get("likes", 0),
        "dislikes": hv.get("dislikes", 0),
        "duration": _format_duration(hv.get("duration_in_ms")),
        "duration_ms": hv.get("duration_in_ms", 0),
        "brand": hv.get("brand", "N/A"),
        "tags": tag_names,
        "titles": hv.get("titles", []),
        "episodes": episodes,
    }


# ── Streams ─────────────────────────────────────────────────────────────

async def get_streams(slug: str) -> dict:
    """
    Get stream URLs and download link for a video.

    Returns dict with:
        streams: list of {url, height, width, kind, ...}
        dl_url: direct download URL (pixeldrain fixed to API format)
    """
    data = await _fetch_video_data(slug)

    streams = []
    manifest = data.get("videos_manifest", {})
    for server in manifest.get("servers", []):
        for stream in server.get("streams", []):
            url = stream.get("url", "")
            if not url:
                continue
            streams.append({
                "url": url,
                "height": stream.get("height", 0),
                "width": stream.get("width", 0),
                "kind": stream.get("kind", "unknown"),
                "filename": stream.get("filename", ""),
                "filesize_mbs": stream.get("filesize_mbs", 0),
                "is_downloadable": stream.get("is_downloadable", False),
            })

    streams.sort(key=lambda s: int(s.get("height", 0) or 0), reverse=True)

    # Fix pixeldrain URL to use direct download API
    dl_url = _fix_pixeldrain_url(data.get("dl_url", "") or "")

    return {
        "streams": streams,
        "dl_url": dl_url,
    }
