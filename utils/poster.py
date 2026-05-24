"""
Poster image helper.

hanime.tv poster image download helper.
Telegram can't fetch these URLs directly, so we download first then upload.
Also converts webp to jpg since Telegram rejects webp in send_photo.
"""

import logging
import os
import subprocess
import tempfile
from io import BytesIO

import aiohttp

log = logging.getLogger(__name__)

# Use a dedicated temp directory that we control
POSTER_TEMP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tmp_posters")
os.makedirs(POSTER_TEMP_DIR, exist_ok=True)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "Referer": "https://hanime.tv/",
    "Origin": "https://hanime.tv",
}


async def download_poster(url: str) -> str | None:
    """
    Download a poster image to a temp file.
    Returns the temp file path, or None on failure.
    Caller is responsible for deleting the file.
    """
    if not url:
        log.warning("download_poster called with empty URL")
        return None

    log.info("Downloading poster from: %s", url[:80])

    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(headers=_HEADERS, timeout=timeout) as session:
            async with session.get(url) as resp:
                log.info("Poster download response: HTTP %d, content-type=%s", resp.status, resp.content_type)
                if resp.status != 200:
                    log.warning("Poster download failed: HTTP %d for %s", resp.status, url)
                    return None

                # Determine extension
                ct = resp.content_type or ""
                ext = ".jpg"
                if "png" in ct:
                    ext = ".png"
                elif "webp" in ct:
                    ext = ".webp"

                # Download to memory first, then write to file
                log.info("Reading poster data into memory...")
                data = await resp.read()
                log.info("Poster data read: %d bytes", len(data))
                
                if len(data) < 1000:
                    log.warning("Poster data too small (%d bytes)", len(data))
                    return None
                
                # Write to our dedicated temp directory
                tmp_path = os.path.join(POSTER_TEMP_DIR, f"poster_{os.urandom(8).hex()}{ext}")
                with open(tmp_path, "wb") as f:
                    f.write(data)
                log.info("Poster saved to %s (%d bytes)", tmp_path, len(data))

                # Convert webp to jpg (Telegram rejects webp in send_photo)
                if tmp_path.endswith(".webp"):
                    jpg_path = tmp_path.rsplit(".", 1)[0] + ".jpg"
                    try:
                        subprocess.run(
                            ["ffmpeg", "-y", "-i", tmp_path, jpg_path],
                            capture_output=True, timeout=10,
                        )
                        os.unlink(tmp_path)
                        if os.path.exists(jpg_path) and os.path.getsize(jpg_path) > 1000:
                            return jpg_path
                    except Exception:
                        log.warning("webp->jpg conversion failed")
                        if os.path.exists(jpg_path):
                            os.unlink(jpg_path)
                    # If conversion fails, return None
                    return None

                return tmp_path

    except Exception as e:
        log.error("Failed to download poster from %s: %s", url, e, exc_info=True)
        return None
