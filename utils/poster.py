"""
Poster image helper.

hentaiff.com poster image download helper.
Telegram can't fetch these URLs directly, so we download first then upload.
Also converts webp to jpg since Telegram rejects webp in send_photo.
"""

import logging
import os
import subprocess
import tempfile

import aiohttp

log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://hentaiff.com/",
    "Origin": "https://hentaiff.com",
}


async def download_poster(url: str) -> str | None:
    """
    Download a poster image to a temp file.
    Returns the temp file path, or None on failure.
    Caller is responsible for deleting the file.
    """
    if not url:
        return None

    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(headers=_HEADERS, timeout=timeout) as session:
            async with session.get(url) as resp:
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

                tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
                try:
                    async for chunk in resp.content.iter_chunked(64 * 1024):
                        tmp.write(chunk)
                    tmp.close()

                    # Verify file has content
                    if os.path.getsize(tmp.name) < 1000:
                        os.unlink(tmp.name)
                        return None

                    # Convert webp to jpg (Telegram rejects webp in send_photo)
                    if tmp.name.endswith(".webp"):
                        jpg_path = tmp.name.rsplit(".", 1)[0] + ".jpg"
                        try:
                            subprocess.run(
                                ["ffmpeg", "-y", "-i", tmp.name, jpg_path],
                                capture_output=True, timeout=10,
                            )
                            os.unlink(tmp.name)
                            if os.path.exists(jpg_path) and os.path.getsize(jpg_path) > 1000:
                                return jpg_path
                        except Exception:
                            log.warning("webp->jpg conversion failed")
                            if os.path.exists(jpg_path):
                                os.unlink(jpg_path)
                        # If conversion fails, try sending webp anyway
                        return None

                    return tmp.name
                except Exception:
                    tmp.close()
                    os.unlink(tmp.name)
                    raise

    except Exception:
        log.warning("Failed to download poster from %s", url)
        return None
