"""
Poster / thumbnail image helper.

Downloads hanime.tv cover/poster images for:
  - Series catalog messages in the main channel (poster_url — tall portrait art)
  - Episode thumbnails attached to sent documents   (cover_url — wide episode art)

Telegram notes:
  - send_photo: requires JPEG/PNG, rejects webp → must convert
  - send_document(thumb=): accepts JPEG/PNG/webp thumbnails fine
  - Max thumbnail size: 200KB (Telegram ignores larger ones silently)
"""

import logging
import os
import subprocess
import time

import aiohttp

log = logging.getLogger(__name__)

POSTER_TEMP_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tmp_posters"
)
os.makedirs(POSTER_TEMP_DIR, exist_ok=True)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "Referer": "https://hanime.tv/",
    "Origin": "https://hanime.tv",
}

_MIN_BYTES  = 2_000   # anything smaller is probably an error page
_MAX_RETRIES = 3


def _tmp_path(suffix: str) -> str:
    token = f"{int(time.time() * 1000)}_{os.urandom(3).hex()}"
    return os.path.join(POSTER_TEMP_DIR, f"poster_{token}{suffix}")


def _webp_to_jpg(webp_path: str) -> str | None:
    """Convert webp → jpg via ffmpeg. Returns jpg path or None."""
    jpg_path = webp_path.rsplit(".", 1)[0] + ".jpg"
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", webp_path, jpg_path],
            capture_output=True, timeout=15,
        )
        if r.returncode == 0 and os.path.exists(jpg_path) and os.path.getsize(jpg_path) > _MIN_BYTES:
            return jpg_path
        log.warning("webp→jpg conversion failed (rc=%d)", r.returncode)
    except Exception as e:
        log.warning("ffmpeg not available for webp conversion: %s", e)
    # Clean up failed output
    if os.path.exists(jpg_path):
        try:
            os.unlink(jpg_path)
        except OSError:
            pass
    return None


async def _fetch_image(url: str) -> tuple[bytes, str] | None:
    """
    Fetch raw image bytes from a URL with retries.
    Returns (data, content_type) or None on failure.
    """
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            timeout = aiohttp.ClientTimeout(total=20, connect=8)
            async with aiohttp.ClientSession(headers=_HEADERS, timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        log.warning("Poster fetch attempt %d: HTTP %d for %s",
                                    attempt, resp.status, url[:80])
                        if resp.status in (404, 403, 410):
                            return None  # no point retrying
                        continue

                    ct = resp.content_type or ""
                    data = await resp.read()

                    if len(data) < _MIN_BYTES:
                        log.warning("Poster too small (%d bytes) on attempt %d", len(data), attempt)
                        continue

                    log.info("Poster fetched: %d bytes, ct=%s (attempt %d)", len(data), ct, attempt)
                    return data, ct

        except Exception as e:
            log.warning("Poster fetch attempt %d failed: %s", attempt, e)
            if attempt < _MAX_RETRIES:
                await _async_sleep(1.5 * attempt)

    return None


async def _async_sleep(seconds: float):
    import asyncio
    await asyncio.sleep(seconds)


async def download_poster(url: str, for_thumbnail: bool = False) -> str | None:
    """
    Download a poster/thumbnail image to a temp file and return its path.

    Args:
        url:           Image URL to fetch.
        for_thumbnail: If True, skip webp→jpg conversion (Telegram accepts
                       webp for document thumbnails). If False (send_photo),
                       always produce a JPEG.

    Returns temp file path, or None on failure.
    Caller must delete the file after use.
    """
    if not url:
        log.warning("download_poster called with empty URL")
        return None

    log.info("Downloading image: %s (thumbnail=%s)", url[:80], for_thumbnail)

    result = await _fetch_image(url)
    if not result:
        log.warning("All retries failed for poster URL: %s", url[:80])
        return None

    data, ct = result

    # Determine file extension from content-type
    if "png" in ct:
        ext = ".png"
    elif "webp" in ct:
        ext = ".webp"
    elif "gif" in ct:
        ext = ".gif"
    else:
        ext = ".jpg"

    tmp_path = _tmp_path(ext)
    with open(tmp_path, "wb") as f:
        f.write(data)
    log.info("Image saved: %s (%d bytes)", tmp_path, len(data))

    # For document thumbnails, webp is fine — return as-is
    if for_thumbnail and ext == ".webp":
        return tmp_path

    # For send_photo (catalog channel), must be JPEG/PNG — convert webp
    if ext == ".webp":
        jpg = _webp_to_jpg(tmp_path)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        if jpg:
            return jpg
        log.warning("webp→jpg failed, poster unavailable for send_photo: %s", url[:80])
        return None

    return tmp_path


async def download_thumbnail(url: str) -> str | None:
    """
    Download an episode thumbnail suitable for send_document(thumb=...).
    Webp is accepted by Telegram here, so we skip conversion for speed.
    Returns temp path or None.
    """
    return await download_poster(url, for_thumbnail=True)
