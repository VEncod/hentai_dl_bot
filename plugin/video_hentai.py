import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from pyrogram import Client
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

import re

from api.hentaiff import HentaiFFScraper, BASE_URL

hentaiff_scraper = HentaiFFScraper()
from utils.auth import approved_only
from utils.fsub import force_sub
from utils.db import get_db
from utils.catalog import update_catalog
from utils.autodelete import track_message
from utils.logger import (
    log_download_start, log_download_progress, log_upload_complete,
    log_error, get_main_channel,
)

log = logging.getLogger(__name__)

# Path to N_m3u8DL-RE binary (bundled in repo)
N_M3U8DL_RE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "binary", "N_m3u8DL-RE")

# Download timeout in seconds
DOWNLOAD_TIMEOUT = 300  # 5 minutes max per download
FFMPEG_TIMEOUT = 240    # 4 minutes for ffmpeg
N_M3U8DL_TIMEOUT = 180  # 3 minutes for N_m3u8DL-RE (faster)


# ── Stream link buttons ─────────────────────────────────────────────────

async def hentailink(client: Client, callback_query: CallbackQuery):
    """Show streaming links (link_<slug> callback)."""
    log.info("=== LINK HANDLER CALLED === data=%s", callback_query.data)
    slug = callback_query.data.split("_", 1)[1]

    try:
        data = hentaiff_scraper.get_streams(slug)
    except Exception:
        log.exception("Failed to fetch streams for slug=%s", slug)
        await callback_query.answer("❌ API unavailable, try again later.", show_alert=True)
        return

    sources = data["sources"]

    if not sources:
        await callback_query.answer("No stream links available.", show_alert=True)
        return

    keyboard = []
    for source in sources:
        label = source["label"]
        url = source["url"]
        s_type = source["type"]

        if s_type == "iframe" or s_type == "iframe_decoded":
            label = f"▶️ Stream ({label})"
        elif s_type == "direct_download":
            label = f"⬇️ Download ({label})"
        
        keyboard.append([InlineKeyboardButton(label, url=url)])

    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data=f"info_{slug}")])

    await callback_query.edit_message_text(
        f"▶️ Streaming **{slug}**\n"
        f"{BASE_URL}/anime/{slug}/\n\n"
        "Please share the bot if you like it ☺️",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ── Download helpers ────────────────────────────────────────────────────

async def _download_direct(url: str, filename: str, progress_cb=None) -> bool:
    """
    Download a file directly via aiohttp with timeout and progress.
    Uses larger chunks and connection pooling for speed.
    Validates that the response is actually a video (not HTML).
    """
    try:
        timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT, connect=10, sock_read=60)
        connector = aiohttp.TCPConnector(limit=5, force_close=False)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "*/*",
        }
        async with aiohttp.ClientSession(timeout=timeout, connector=connector, headers=headers) as session:
            async with session.get(url) as resp:
                resp.raise_for_status()

                # Check content type — reject HTML responses
                ct = resp.content_type or ""
                if "text/html" in ct or "application/json" in ct:
                    log.error("URL returned %s instead of video: %s", ct, url)
                    return False

                total = resp.content_length or 0
                log.info("Downloading %s — size: %s, type: %s",
                         url[:80], f"{total / 1024 / 1024:.1f}MB" if total else "unknown", ct)

                downloaded = 0
                last_progress = 0

                with open(filename, "wb") as f:
                    async for chunk in resp.content.iter_chunked(256 * 1024):
                        f.write(chunk)
                        downloaded += len(chunk)

                        if progress_cb and total > 0:
                            pct = int(downloaded * 100 / total)
                            if pct >= last_progress + 10:
                                last_progress = pct
                                await progress_cb(pct)

        # Final validation — reject tiny files (likely error pages)
        file_size = os.path.getsize(filename)
        if file_size < 50_000:  # Less than 50KB is not a video
            log.error("Downloaded file too small (%d bytes), likely not a video: %s", file_size, url)
            os.remove(filename)
            return False

        log.info("Download complete: %s (%.1f MB)", filename, file_size / 1024 / 1024)
        return True
    except asyncio.TimeoutError:
        log.error("Direct download timed out for url=%s", url)
        return False
    except Exception:
        log.exception("Direct download failed for url=%s", url)
        return False


async def _download_n_m3u8dl(url: str, filename: str) -> bool:
    """
    Download HLS stream using N_m3u8DL-RE (much faster than ffmpeg for HLS).
    Uses multi-threaded downloading.
    """
    if not os.path.exists(N_M3U8DL_RE):
        log.warning("N_m3u8DL-RE binary not found at %s", N_M3U8DL_RE)
        return False

    try:
        # N_m3u8DL-RE with optimized settings:
        # --thread-count 8: parallel segment downloads
        # --download-retry-count 3: retry failed segments
        # --tmp-dir: use /tmp for speed
        process = await asyncio.create_subprocess_exec(
            N_M3U8DL_RE,
            url,
            "--save-name", Path(filename).stem,
            "--save-dir", ".",
            "--thread-count", "8",
            "--download-retry-count", "3",
            "--tmp-dir", "/tmp",
            "--no-log",
            "--auto-select",
            "-H", f"Referer: {BASE_URL}/",
            "-H", f"Origin: {BASE_URL}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            _, stderr = await asyncio.wait_for(
                process.communicate(), timeout=N_M3U8DL_TIMEOUT
            )
        except asyncio.TimeoutError:
            log.error("N_m3u8DL-RE timed out for %s, killing process", url)
            process.kill()
            await process.wait()
            return False

        if process.returncode != 0:
            log.error("N_m3u8DL-RE failed (rc=%d): %s", process.returncode, stderr.decode(errors="replace")[-500:])
            return False

        # N_m3u8DL-RE may output with different extension, find the file
        stem = Path(filename).stem
        for ext in [".mp4", ".mkv", ".ts"]:
            candidate = stem + ext
            if os.path.exists(candidate) and os.path.getsize(candidate) > 0:
                if candidate != filename:
                    os.rename(candidate, filename)
                return True

        # Check if output file exists directly
        if os.path.exists(filename) and os.path.getsize(filename) > 0:
            return True

        log.error("N_m3u8DL-RE completed but output file not found for %s", url)
        return False

    except Exception:
        log.exception("N_m3u8DL-RE download failed for url=%s", url)
        return False


async def _download_hls_ffmpeg(url: str, filename: str) -> bool:
    """Download HLS stream via ffmpeg with proper timeout (fallback)."""
    try:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y",
            "-headers", f"Referer: {BASE_URL}/\r\nOrigin: {BASE_URL}\r\n",
            "-i", url,
            "-c", "copy",
            "-bsf:a", "aac_adtstoasc",
            "-movflags", "+faststart",
            filename,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            _, stderr = await asyncio.wait_for(
                process.communicate(), timeout=FFMPEG_TIMEOUT
            )
        except asyncio.TimeoutError:
            log.error("ffmpeg timed out for %s, killing process", url)
            process.kill()
            await process.wait()
            return False

        if process.returncode != 0:
            log.error("ffmpeg failed: %s", stderr.decode(errors="replace")[-500:])
            return False
        return True

    except Exception:
        log.exception("ffmpeg download failed for url=%s", url)
        return False


def _extract_series_name(slug: str) -> str:
    """Extract series name from slug (remove trailing episode number)."""
    parts = slug.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0]
    return slug


def _progress_bar(pct: int, length: int = 12) -> str:
    """Generate a stylish progress bar."""
    filled = int(length * pct / 100)
    bar = "█" * filled + "░" * (length - filled)
    return f"[{bar}] {pct}%"


async def _safe_edit(callback_query: CallbackQuery, text: str):
    """Edit message, ignoring 'message not modified' errors."""
    try:
        await callback_query.edit_message_text(text)
    except Exception:
        pass


# ── Download handler ────────────────────────────────────────────────────

async def hentaidl(client: Client, callback_query: CallbackQuery):
    """Download the video directly via Pixeldrain (dlt_<slug>)."""
    log.info("=== DOWNLOAD HANDLER === data=%s user=%s",
             callback_query.data, callback_query.from_user.id)

    from utils.auth import is_approved
    user_id = callback_query.from_user.id
    if not await is_approved(user_id):
        await callback_query.answer("⛔ No access.", show_alert=True)
        return

    slug = callback_query.data.split("_", 1)[1]
    chat_id = callback_query.from_user.id
    username = callback_query.from_user.username
    db = get_db()

    start_time = time.time()

    await _safe_edit(callback_query, f"⏳ **Downloading...**\n\n{_progress_bar(0)}")

    log_msg_id = await log_download_start(client, username, slug)



    # Fetch streams
    try:
        data = hentaiff_scraper.get_streams(slug)
    except Exception:
        log.exception("Failed to fetch streams for slug=%s", slug)
        await _safe_edit(callback_query, "❌ API unavailable. Please try again later.")
        await log_error(client, username, f"Stream fetch failed for {slug}")
        return

    sources = data["sources"]
    dl_url = None

    # Prioritize direct download links
    direct_download_source = next((s for s in sources if s["type"] == "direct_download"), None)
    if direct_download_source:
        dl_url = direct_download_source["url"]
        log.info("Using direct download URL: %s", dl_url)

    filename = f"{slug}.mp4"
    downloaded = False

    # Progress callback with stylish bar
    async def on_progress(pct):
        elapsed = int(time.time() - start_time)
        bar = _progress_bar(pct)
        await _safe_edit(
            callback_query,
            f"⬇️ **Downloading...**\\n\\n"
            f"{bar}\\n\\n"
            f"⏱ **Elapsed:** {elapsed}s\\n"
            f"📁 **File:** {slug}.mp4"
        )
        if log_msg_id:
            await log_download_progress(client, log_msg_id, username, slug, pct)

    log.info("Sources for %s: dl_url=%s, source_count=%d, sources=%s",
             slug, dl_url, len(sources),
             [(s["type"], s["label"], s["url"][:60]) for s in sources[:5]])

    if not dl_url and not sources:
        elapsed = int(time.time() - start_time)
        await _safe_edit(
            callback_query,
            "❌ **No download sources available for this video.**\\n\\n"
            "This title may be region-locked or not yet available for download on the server.\\n"
            "Try another episode or title."
        )
        await log_error(client, username, f"No sources/dl_url for {slug}")
        return

    if dl_url:
        downloaded = await _download_direct(dl_url, filename, on_progress)

    if not downloaded:
        # Fallback to iframe sources and try to extract video URL
        for source in sources:
            if source["type"] == "iframe" or source["type"] == "iframe_decoded":
                iframe_url = source["url"]
                log.info("Attempting to extract video from iframe: %s", iframe_url)
                # This is complex and might require a headless browser or more advanced scraping
                # For now, we will just log and skip if direct download is not available
                log.warning("Iframe video extraction not implemented. Skipping iframe source.")
                # If you want to implement this, you'd need to add a new function similar to _download_direct
                # that can parse the iframe content and find the actual video URL.
                # For now, we'll just break and report no download if direct isn't found.
                break
            if downloaded:
                break

    # ── Failed ──────────────────────────────────────────────────────
    if not downloaded:
        elapsed = int(time.time() - start_time)
        await _safe_edit(callback_query, f"❌ Download failed after {elapsed}s. No working streams found.")
        await log_error(client, username, f"All download strategies failed for {slug}")
        if os.path.exists(filename):
            os.remove(filename)
        return

    if not os.path.exists(filename) or os.path.getsize(filename) == 0:
        await _safe_edit(callback_query, "❌ Download produced an empty file.")
        await log_error(client, username, f"Empty file for {slug}")
        if os.path.exists(filename):
            os.remove(filename)
        return

    # ── Upload ──────────────────────────────────────────────────────
    try:
        file_size_mb = os.path.getsize(filename) / (1024 * 1024)
        elapsed = int(time.time() - start_time)
        await _safe_edit(
            callback_query,
            f"📤 **Uploading...** ({file_size_mb:.1f} MB)\n"
            f"⬇️ Downloaded in {elapsed}s"
        )
        if log_msg_id:
            await log_download_progress(client, log_msg_id, username, slug, 90)

        # Get video details for caption and catalog
        info = None
        try:
            info = await details(slug)
            series_name = _extract_series_name(slug)
            tags_str = ", ".join(info.get("tags", [])[:5])
            caption = (
                f"📺 **{info['name']}**\n"
                f"🏷 {tags_str}\n"
                f"Downloaded via @hentaiff_dl_bot"
            )
        except Exception:
            series_name = _extract_series_nam                    caption="Downloaded via @hentaiff_dl_bot",
        # Send to user
        sent = await client.send_document(
            chat_id=chat_id,
            document=filename,
            caption=caption,
        )
        await track_message(chat_id, sent.id)

        file_id = sent.document.file_id
        total_time = int(time.time() - start_time)

        await _safe_edit(
            callback_query,
            f"✅ **Done!** ({file_size_mb:.1f} MB in {total_time}s)\n"
            f"⏳ Auto-deletes in 4 hours. Save it!"
        )

        # Save to MongoDB cache (with file_size for validation)
        await db.Name.update_one(
            {"name": slug},
            {"$set": {"name": slug, "file_id": file_id, "file_size": sent.document.file_size}},
            upsert=True,
        )

        # Update series catalog (creates/updates channel message)
        try:
            await update_catalog(
                client=client,
                slug=slug,
                file_id=file_id,
                file_size=sent.document.file_size,
                series_name=info.get("name", "") if info else "",
                poster_url=info.get("poster_url", "") if info else "",
                tags=info.get("tags", []) if info else [],
            )
        except Exception:
            log.exception("Failed to update catalog for %s", slug)

        await log_upload_complete(client, log_msg_id, slug, file_id)

    except Exception:
        log.exception("Upload failed for %s", slug)
        await _safe_edit(callback_query, "❌ Something went wrong during upload.")
        await log_error(client, username, f"Upload failed for {slug}")
    finally:
        if os.path.exists(filename):
            os.remove(filename)


# ── Batch download all episodes ─────────────────────────────────────────

async def batch_download(client: Client, callback_query: CallbackQuery):
    """Download all episodes of a series (ball_<slug> callback)."""
    slug = callback_query.data.split("_", 1)[1]
    chat_id = callback_query.from_user.id
    username = callback_query.from_user.username

    log.info("=== BATCH DOWNLOAD === slug=%s user=%s", slug, chat_id)

    from utils.auth import is_approved
    if not await is_approved(chat_id):
        await callback_query.answer("⛔ No access.", show_alert=True)
        return

    try:
        await callback_query.answer("Starting batch download...")
    except Exception:
        pass

    # Get episode list
    try:
        info = await details(slug)
    except Exception:
        log.exception("Failed to get details for batch %s", slug)
        await callback_query.answer("❌ API error", show_alert=True)
        return

    episodes = info.get("episodes", [])
    if not episodes:
        episodes = [{"slug": slug, "name": info["name"]}]

    total = len(episodes)
    succeeded = 0
    failed = 0

    status_msg = await client.send_message(
        chat_id=chat_id,
        text=f"📥 **Batch Download Started**\n\nEpisodes: {total}\nProgress: 0/{total}",
    )

    db = get_db()

    for i, ep in enumerate(episodes):
        ep_slug = ep.get("slug", "")
        ep_name = ep.get("name", ep_slug)
        if not ep_slug:
            continue

        try:
            await status_msg.edit_text(
                f"📥 **Batch Download**\n\n"
                f"⬇️ Downloading: **{ep_name}** ({i + 1}/{total})\n"
                f"✅ Done: {succeeded} | ❌ Failed: {failed}"
            )
        except Exception:
            pass

        # Check cache
        cached = await db.Name.find_one({"name": ep_slug})
        if cached and cached.get("file_size", 0) > 50_000:
            try:
                await client.send_document(
                    chat_id=chat_id,
                    document=cached["file_id"],
                    caption=f"📺 **{ep_name}**\nDownloaded via @hentaiff_dl_bot",
                )
                succeeded += 1
                continue
            except Exception:
                await db.Name.delete_one({"name": ep_slug})

        # Fresh download
        try:
            data = await get_streams(ep_slug)
        except Exception:
            log.error("Batch: stream fetch failed for %s", ep_slug)
            failed += 1
            continue

        dl_url = data["dl_url"]
        if dl_url:
            m = re.match(r"https?://pixeldrain\.com/[du]/([A-Za-z0-9]+)", dl_url)
            if m:
                dl_url = f"https://pixeldrain.com/api/file/{m.group(1)}"

        streams = data["streams"]
        filename = f"{ep_slug}.mp4"
        downloaded = False

        if dl_url:
            downloaded = await _download_direct(dl_url, filename)
        if not downloaded:
            for s in streams:
                if s["kind"] == "hls":
                    downloaded = await _download_n_m3u8dl(s["url"], filename)
                    if not downloaded:
                        downloaded = await _download_hls_ffmpeg(s["url"], filename)
                    if downloaded:
                        break

        if not downloaded or not os.path.exists(filename) or os.path.getsize(filename) < 50_000:
            if os.path.exists(filename):
                os.remove(filename)
            failed += 1
            continue

        try:
            ep_info = await details(ep_slug)
            tags_str = ", ".join(ep_info.get("tags", [])[:5])
            caption = f"📺 **{ep_name}**\n🏷 {tags_str}\nDownloaded via @hentaiff_dl_bot"
        except Exception:
            caption = f"📺 **{ep_name}**\nDownloaded via @hentaiff_dl_bot"

        try:
            sent = await client.send_document(
                chat_id=chat_id,
                document=filename,
                caption=caption,
            )
            file_id = sent.document.file_id
            await db.Name.update_one(
                {"name": ep_slug},
                {"$set": {"name": ep_slug, "file_id": file_id, "file_size": sent.document.file_size}},
                upsert=True,
            )
            succeeded += 1
        except Exception:
            log.exception("Batch: upload failed for %s", ep_slug)
            failed += 1
        finally:
            if os.path.exists(filename):
                os.remove(filename)

    try:
        await status_msg.edit_text(
            f"✅ **Batch Download Complete!**\n\n"
            f"📺 Total: {total}\n"
            f"✅ Success: {succeeded}\n"
            f"❌ Failed: {failed}"
        )
    except Exception:
        pass
