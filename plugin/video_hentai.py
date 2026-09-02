import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from wzgram import Client
from wzgram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

import re

from api.hanime_api import HanimeAPI, BASE_URL

hanime_api = HanimeAPI()
from utils.auth import approved_only
from utils.fsub import force_sub
from utils.db import get_db
from utils.catalog import update_catalog
from utils.poster import download_thumbnail
from utils.autodelete import track_message, clear_chat_history
from utils.logger import (
    log_download_start, log_download_progress, log_upload_complete,
    log_error, get_main_channel,
)

log = logging.getLogger(__name__)

N_M3U8DL_RE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "binary", "N_m3u8DL-RE")

DOWNLOAD_TIMEOUT = 120
FFMPEG_TIMEOUT = 60
N_M3U8DL_TIMEOUT = 60

PROGRESS_UPDATE_INTERVAL = 2.0
PROGRESS_MIN_PERCENT_STEP = 3


from utils.slug_map import get_short_slug, resolve_slug


async def hentailink(client: Client, callback_query: CallbackQuery):
    log.info("=== LINK HANDLER CALLED === data=%s", callback_query.data)
    raw_slug = callback_query.data.split("_", 1)[1]
    slug = await resolve_slug(raw_slug)
    short_key = await get_short_slug(slug)

    try:
        data = await asyncio.to_thread(hanime_api.get_streams, slug)
        info = await asyncio.to_thread(hanime_api.details, slug)
    except Exception:
        data = {}
        info = {}

    streams = data.get("streams", [])
    watch_url = info.get("url", "")
    title = info.get("title") or info.get("name") or slug

    qualities = [s.get("label", f"{s.get('height')}p") for s in streams if s.get("label") or s.get("height")]
    qualities_str = " • ".join(qualities) if qualities else "1080p Full HD"

    text = (
        f"📺 **{title}**\n\n"
        f"✨ **Available Qualities:** {qualities_str}\n"
    )
    if watch_url:
        text += f"🌐 **Web Stream:** [Watch on Web]({watch_url})\n\n"
    text += "Use the **Download** button below to download the highest quality directly in Telegram."

    keyboard = [
        [InlineKeyboardButton("⬇️ Download Now", callback_data=f"dlt_{short_key}")],
        [InlineKeyboardButton("⬅️ Back to Info", callback_data=f"info_{short_key}")]
    ]

    await callback_query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        disable_web_page_preview=False,
    )


def _progress_bar(pct: float, length: int = 12) -> str:
    filled = int(length * pct / 100)
    empty = length - filled
    bar = "█" * filled + "▒" * empty
    return f"[{bar}] {pct:.1f}%"


def _progress_bar_detailed(pct: float, length: int = 14) -> str:
    filled = int(length * pct / 100)
    empty = length - filled
    bar = "█" * filled + "░" * empty
    return f"[{bar}] {pct:.1f}%"


def _get_progress_emoji(pct: float) -> str:
    if pct < 10:
        return "🆕"
    elif pct < 25:
        return "🚀"
    elif pct < 50:
        return "⏳"
    elif pct < 75:
        return "🔥"
    elif pct < 90:
        return "⚡"
    elif pct < 100:
        return "🏁"
    else:
        return "✅"


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def _format_time(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        m = int(seconds // 60)
        s = int(seconds % 60)
        return f"{m}m {s}s"
    else:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h}h {m}m"


def _format_speed(bytes_per_sec: float) -> str:
    if bytes_per_sec < 1024:
        return f"{bytes_per_sec:.0f} B/s"
    elif bytes_per_sec < 1024 * 1024:
        return f"{bytes_per_sec / 1024:.1f} KB/s"
    else:
        return f"{bytes_per_sec / (1024 * 1024):.1f} MB/s"


class DownloadProgressTracker:
    def __init__(self, total_size: int, start_time: float):
        self.total_size = total_size
        self.start_time = start_time
        self.downloaded = 0
        self.last_update_time = start_time
        self.last_update_downloaded = 0
        self.current_speed = 0.0
        self.eta_seconds = 0.0
        self.last_reported_pct = -1.0
        self.last_reported_time = 0.0
    
    def update(self, downloaded: int) -> dict:
        now = time.time()
        self.downloaded = downloaded
        
        time_delta = now - self.last_update_time
        if time_delta > 0:
            bytes_delta = downloaded - self.last_update_downloaded
            self.current_speed = bytes_delta / time_delta
        
        if self.current_speed > 0 and self.total_size > 0:
            remaining = self.total_size - downloaded
            self.eta_seconds = remaining / self.current_speed
        else:
            self.eta_seconds = 0
        
        self.last_update_time = now
        self.last_update_downloaded = downloaded
        
        pct = (downloaded / self.total_size * 100) if self.total_size > 0 else 0
        elapsed = now - self.start_time
        
        return {
            "pct": pct,
            "downloaded": downloaded,
            "total": self.total_size,
            "speed": self.current_speed,
            "eta": self.eta_seconds,
            "elapsed": elapsed,
        }
    
    def should_update_ui(self, pct: float) -> bool:
        now = time.time()
        time_since_last = now - self.last_reported_time
        pct_change = abs(pct - self.last_reported_pct)
        
        if time_since_last >= PROGRESS_UPDATE_INTERVAL or pct_change >= PROGRESS_MIN_PERCENT_STEP:
            self.last_reported_pct = pct
            self.last_reported_time = now
            return True
        return False
    
    def format_message(self, stats: dict, title: str = "Downloading...", slug: str = "") -> str:
        pct = stats["pct"]
        emoji = _get_progress_emoji(pct)
        bar = _progress_bar_detailed(pct)
        speed = _format_speed(stats["speed"])
        eta = _format_time(stats["eta"]) if stats["eta"] > 0 else "calculating..."
        elapsed = _format_time(stats["elapsed"])
        downloaded = _format_size(stats["downloaded"])
        total = _format_size(stats["total"]) if stats["total"] > 0 else "unknown"
        
        # Build status line with emoji
        status_line = f"{emoji} {title}"
        
        # Build progress details
        msg = (
            f"{status_line}\n\n"
            f"{bar}\n\n"
            f"📦 Size: {downloaded} / {total}\n"
            f"⚡ Speed: {speed}\n"
            f"⏱ Elapsed: {elapsed}\n"
            f"⏳ ETA: {eta}"
        )
        if slug:
            msg += f"\n📄 File: {slug}.mp4"
        return msg


class UploadProgressTracker:
    def __init__(self, total_size: int, start_time: float):
        self.total_size = total_size
        self.start_time = start_time
        self.uploaded = 0
        self.last_update_time = start_time
        self.last_update_uploaded = 0
        self.current_speed = 0.0
        self.eta_seconds = 0.0
        self.last_reported_pct = -1.0
        self.last_reported_time = 0.0
    
    def update(self, current: int, total: int) -> dict:
        now = time.time()
        self.uploaded = current
        self.total_size = total
        
        time_delta = now - self.last_update_time
        if time_delta > 0:
            bytes_delta = current - self.last_update_uploaded
            self.current_speed = bytes_delta / time_delta
        
        if self.current_speed > 0 and total > 0:
            remaining = total - current
            self.eta_seconds = remaining / self.current_speed
        else:
            self.eta_seconds = 0
        
        self.last_update_time = now
        self.last_update_uploaded = current
        
        pct = (current / total * 100) if total > 0 else 0
        elapsed = now - self.start_time
        
        return {
            "pct": pct,
            "uploaded": current,
            "total": total,
            "speed": self.current_speed,
            "eta": self.eta_seconds,
            "elapsed": elapsed,
        }
    
    def should_update_ui(self, pct: float) -> bool:
        now = time.time()
        time_since_last = now - self.last_reported_time
        pct_change = abs(pct - self.last_reported_pct)
        
        if time_since_last >= PROGRESS_UPDATE_INTERVAL or pct_change >= PROGRESS_MIN_PERCENT_STEP:
            self.last_reported_pct = pct
            self.last_reported_time = now
            return True
        return False
    
    def format_message(self, stats: dict, slug: str = "") -> str:
        pct = stats["pct"]
        emoji = _get_progress_emoji(pct)
        bar = _progress_bar_detailed(pct)
        speed = _format_speed(stats["speed"])
        eta = _format_time(stats["eta"]) if stats["eta"] > 0 else "calculating..."
        elapsed = _format_time(stats["elapsed"])
        uploaded = _format_size(stats["uploaded"])
        total = _format_size(stats["total"]) if stats["total"] > 0 else "unknown"
        
        # Build status line with emoji
        status_line = f"{emoji} Uploading..."
        
        msg = (
            f"{status_line}\n\n"
            f"{bar}\n\n"
            f"📦 Size: {uploaded} / {total}\n"
            f"⚡ Speed: {speed}\n"
            f"⏱ Elapsed: {elapsed}\n"
            f"⏳ ETA: {eta}"
        )
        if slug:
            msg += f"\n📄 File: {slug}.mp4"
        return msg


ACTIVE_DOWNLOADS = {}


async def cancel_download_callback(client: Client, callback_query: CallbackQuery):
    chat_id = callback_query.from_user.id
    if chat_id in ACTIVE_DOWNLOADS:
        dl_info = ACTIVE_DOWNLOADS[chat_id]
        dl_info["cancelled"] = True
        proc = dl_info.get("process")
        if proc:
            try:
                proc.kill()
            except Exception:
                pass
        filename = dl_info.get("filename")
        if filename and os.path.exists(filename):
            try:
                os.unlink(filename)
            except Exception:
                pass
        slug = dl_info.get("slug", "")
        ACTIVE_DOWNLOADS.pop(chat_id, None)
        await callback_query.answer("🛑 Download cancelled!", show_alert=True)
        await _safe_edit(
            callback_query,
            f"🛑 **Download Cancelled**\n\n"
            f"Download of **{slug}** was stopped by user."
        )
    else:
        await callback_query.answer("No active download to cancel.", show_alert=True)


async def _safe_edit(callback_query: CallbackQuery, text: str, reply_markup=None):
    try:
        await callback_query.edit_message_text(text, reply_markup=reply_markup)
    except Exception:
        pass


async def _download_direct(url: str, filename: str, progress_cb=None, referer: str = "") -> bool:
    try:
        if not referer:
            if "oppai.stream" in url or "myspacecat.pictures" in url:
                referer = "https://oppai.stream/"
            elif "hentai.tv" in url or "nhplayer.com" in url or "1hanime.com" in url:
                referer = "https://hentai.tv/"

        timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT, connect=10, sock_read=60)
        connector = aiohttp.TCPConnector(limit=5, force_close=False)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "*/*",
        }
        if referer:
            headers["Referer"] = referer
            headers["Origin"] = referer.rstrip("/")

        async with aiohttp.ClientSession(timeout=timeout, connector=connector, headers=headers) as session:
            async with session.get(url) as resp:
                resp.raise_for_status()

                ct = resp.content_type or ""
                if "text/html" in ct or "application/json" in ct:
                    log.error("URL returned %s instead of video: %s", ct, url)
                    return False

                total = resp.content_length or 0
                log.info("Downloading %s - size: %s, type: %s",
                         url[:80], _format_size(total) if total else "unknown", ct)

                downloaded = 0
                start_time = time.time()
                # Create tracker with total size for proper progress calculation
                tracker = DownloadProgressTracker(total if total > 0 else 100_000_000, start_time)

                with open(filename, "wb") as f:
                    async for chunk in resp.content.iter_chunked(256 * 1024):
                        f.write(chunk)
                        downloaded += len(chunk)

                        if progress_cb and tracker:
                            stats = tracker.update(downloaded)
                            if tracker.should_update_ui(stats["pct"]):
                                await progress_cb(stats)

                if progress_cb and tracker:
                    stats = tracker.update(downloaded)
                    await progress_cb(stats)

        file_size = os.path.getsize(filename)
        if file_size < 50_000:
            log.error("Downloaded file too small (%d bytes), likely not a video: %s", file_size, url)
            os.remove(filename)
            return False

        log.info("Download complete: %s (%s)", filename, _format_size(file_size))
        return True
    except asyncio.TimeoutError:
        log.error("Direct download timed out for url=%s", url)
        return False
    except Exception:
        log.exception("Direct download failed for url=%s", url)
        return False


async def _download_n_m3u8dl(url: str, filename: str, progress_cb=None, referer: str = "") -> bool:
    if not os.path.exists(N_M3U8DL_RE):
        log.warning("N_m3u8DL-RE binary not found at %s", N_M3U8DL_RE)
        return False

    ref = referer or ("https://oppai.stream/" if "oppai" in url else "https://hentai.tv/")
    try:
        try:
            os.chmod(N_M3U8DL_RE, 0o755)
        except Exception:
            pass
        start_time = time.time()
        
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
            "-H", f"Referer: {ref}",
            "-H", f"Origin: {ref.rstrip('/')}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        for dl in ACTIVE_DOWNLOADS.values():
            dl["process"] = process

        if progress_cb:
            expected_duration = 90
            tracker = DownloadProgressTracker(100, start_time)
            
            async def monitor_progress():
                while process.returncode is None:
                    await asyncio.sleep(2)
                    elapsed = time.time() - start_time
                    estimated_pct = min(95, (elapsed / expected_duration) * 100)
                    stats = tracker.update(int(estimated_pct))
                    stats["pct"] = estimated_pct
                    stats["downloaded"] = int(estimated_pct)
                    stats["total"] = 100
                    stats["speed"] = 0
                    stats["eta"] = max(0, expected_duration - elapsed)
                    if tracker.should_update_ui(estimated_pct):
                        await progress_cb(stats)
            
            monitor_task = asyncio.create_task(monitor_progress())
        
        try:
            _, stderr = await asyncio.wait_for(
                process.communicate(), timeout=N_M3U8DL_TIMEOUT
            )
        except asyncio.TimeoutError:
            log.error("N_m3u8DL-RE timed out for %s, killing process", url)
            process.kill()
            await process.wait()
            return False
        finally:
            if progress_cb:
                monitor_task.cancel()
                try:
                    await monitor_task
                except asyncio.CancelledError:
                    pass

        if process.returncode != 0:
            log.error("N_m3u8DL-RE failed (rc=%d): %s", process.returncode, stderr.decode(errors="replace")[-500:])
            return False

        stem = Path(filename).stem
        for ext in [".mp4", ".mkv", ".ts"]:
            candidate = stem + ext
            if os.path.exists(candidate) and os.path.getsize(candidate) > 0:
                if candidate != filename:
                    os.rename(candidate, filename)
                return True

        if os.path.exists(filename) and os.path.getsize(filename) > 0:
            return True

        log.error("N_m3u8DL-RE completed but output file not found for %s", url)
        return False

    except Exception:
        log.exception("N_m3u8DL-RE download failed for url=%s", url)
        return False


async def _download_hls_ffmpeg(url: str, filename: str, progress_cb=None, referer: str = "") -> bool:
    try:
        ref = referer or ("https://oppai.stream/" if "oppai" in url else "https://hentai.tv/")
        start_time = time.time()
        
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y",
            "-headers", f"Referer: {ref}\r\nOrigin: {ref.rstrip('/')}\r\n",
            "-i", url,
            "-c", "copy",
            "-bsf:a", "aac_adtstoasc",
            "-movflags", "+faststart",
            filename,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        for dl in ACTIVE_DOWNLOADS.values():
            dl["process"] = process

        if progress_cb:
            expected_duration = 120
            tracker = DownloadProgressTracker(100, start_time)
            
            async def monitor_progress():
                while process.returncode is None:
                    await asyncio.sleep(2)
                    elapsed = time.time() - start_time
                    estimated_pct = min(95, (elapsed / expected_duration) * 100)
                    stats = tracker.update(int(estimated_pct))
                    stats["pct"] = estimated_pct
                    stats["downloaded"] = int(estimated_pct)
                    stats["total"] = 100
                    stats["speed"] = 0
                    stats["eta"] = max(0, expected_duration - elapsed)
                    if tracker.should_update_ui(estimated_pct):
                        await progress_cb(stats)
            
            monitor_task = asyncio.create_task(monitor_progress())
        
        try:
            _, stderr = await asyncio.wait_for(
                process.communicate(), timeout=FFMPEG_TIMEOUT
            )
        except asyncio.TimeoutError:
            log.error("ffmpeg timed out for %s, killing process", url)
            process.kill()
            await process.wait()
            return False
        finally:
            if progress_cb:
                monitor_task.cancel()
                try:
                    await monitor_task
                except asyncio.CancelledError:
                    pass

        if process.returncode != 0:
            log.error("ffmpeg failed: %s", stderr.decode(errors="replace")[-500:])
            return False
        return True

    except Exception:
        log.exception("ffmpeg download failed for url=%s", url)
        return False


def _extract_series_name(slug: str) -> str:
    parts = slug.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0]
    return slug


async def hentaidl(client: Client, callback_query: CallbackQuery):
    """Download the video directly via Pixeldrain (dlt_<slug>)."""
    log.info("=== DOWNLOAD HANDLER === data=%s user=%s",
             callback_query.data, callback_query.from_user.id)

    from utils.auth import is_approved
    user_id = callback_query.from_user.id
    if not await is_approved(user_id):
        await callback_query.answer("No access.", show_alert=True)
        return

    raw_slug = callback_query.data.split("_", 1)[1]
    slug = await resolve_slug(raw_slug)
    chat_id = callback_query.from_user.id
    username = callback_query.from_user.username
    db = get_db()

    start_time = time.time()

    # Clear old messages before starting download
    await clear_chat_history(client, chat_id, preserve_message_ids=[callback_query.message.id])

    # ── CHECK CACHE FIRST ─────────────────────────────────────────
    cached = await db.Name.find_one({"name": slug})
    if cached and cached.get("file_size", 0) > 50_000:
        file_id = cached.get("file_id")
        if file_id:
            log.info("Cache hit for %s — sending existing file", slug)
            thumb_path = None
            try:
                info = await asyncio.to_thread(hanime_api.details, slug)
                tags_str = ", ".join(info.get("tags", [])[:5])
                caption = (
                    f"{info['name']}\n"
                    f"Tags: {tags_str}\n"
                    f"📦 Already downloaded!\n"
                    f"Downloaded via @hanime_dl_bot"
                )
                thumb_url = info.get("poster_url") or info.get("cover_url") or ""
                if thumb_url:
                    thumb_path = await download_thumbnail(thumb_url)
            except Exception:
                caption = f"{slug}\n📦 Already downloaded!\nDownloaded via @hanime_dl_bot"
            
            try:
                sent = await client.send_document(
                    chat_id=chat_id,
                    document=file_id,
                    caption=caption,
                    thumb=thumb_path,
                )
                await track_message(chat_id, sent.id)
                await _safe_edit(
                    callback_query,
                    f"✅ **Already Downloaded!**\n\n"
                    f"📄 {slug}\n"
                    f"💾 File sent from cache.\n\n"
                    f"Auto-deletes in 10 minutes. Save it!"
                )
                return
            except Exception:
                log.warning("Cache send failed for %s, will re-download", slug)
                await db.Name.delete_one({"name": slug})
            finally:
                if thumb_path and os.path.exists(thumb_path):
                    try:
                        os.unlink(thumb_path)
                    except OSError:
                        pass

    await _safe_edit(callback_query, f"🚀 **Preparing Download**\n\n{_progress_bar(0)}\n\n⏳ Please wait...")

    log_msg_id = await log_download_start(client, username, slug)

    # Fetch streams
    try:
        data = await asyncio.to_thread(hanime_api.get_streams, slug)
    except Exception:
        log.exception("Failed to fetch streams for slug=%s", slug)
        await _safe_edit(callback_query, "API unavailable. Please try again later.")
        await log_error(client, username, f"Stream fetch failed for {slug}")
        return

    dl_url = data.get("dl_url", "")
    streams = data.get("streams", [])
    sources = data.get("sources", [])

    log.info("Sources for %s: dl_url=%s, streams=%d, sources=%d",
             slug, dl_url[:60] if dl_url else None, len(streams), len(sources))

    if not dl_url and not streams:
        elapsed = int(time.time() - start_time)
        await _safe_edit(
            callback_query,
            "❌ **No Download Sources Available**\n\n"
            "This title may be region-locked or not yet available for download.\n"
            "Try another episode or title."
        )
        await log_error(client, username, f"No sources/dl_url for {slug}")
        return

    # Determine file extension and referer
    primary_stream = streams[0] if streams else {}
    ext = primary_stream.get("extension") or ("webm" if dl_url.endswith(".webm") else "mp4")
    referer = primary_stream.get("referer", "")
    quality_label = primary_stream.get("label", "1080p")
    filename = f"{slug}.{ext}"

    cancel_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛑 Stop Download", callback_data=f"canceldl_{chat_id}")]
    ])

    ACTIVE_DOWNLOADS[chat_id] = {
        "task": asyncio.current_task(),
        "slug": slug,
        "cancelled": False,
        "process": None,
        "filename": filename,
    }

    try:
        downloaded = False
        active_stream_label = quality_label

        # Progress callback with detailed stats
        async def on_progress(stats):
            if ACTIVE_DOWNLOADS.get(chat_id, {}).get("cancelled"):
                raise asyncio.CancelledError("Download cancelled by user")
            msg = tracker.format_message(stats, title=f"Downloading [{active_stream_label}]...", slug=slug)
            await _safe_edit(callback_query, msg, reply_markup=cancel_keyboard)
            if log_msg_id:
                await log_download_progress(client, log_msg_id, username, slug, stats["pct"])

        # Build candidate streams list (trying best qualities first)
        candidate_urls = []
        if dl_url:
            candidate_urls.append({
                "url": dl_url,
                "referer": referer,
                "kind": primary_stream.get("kind", ""),
                "label": quality_label,
                "extension": ext,
            })
        for s in streams:
            if s.get("url") and not any(c["url"] == s["url"] for c in candidate_urls):
                candidate_urls.append(s)

        for candidate in candidate_urls:
            c_url = candidate.get("url", "")
            if not c_url:
                continue
            c_ref = candidate.get("referer", referer)
            c_kind = candidate.get("kind", "")
            c_label = candidate.get("label", quality_label)
            c_ext = candidate.get("extension", "mp4")
            active_stream_label = c_label
            
            # Adjust filename extension if needed
            if c_ext and filename.rsplit(".", 1)[-1] != c_ext:
                filename = f"{slug}.{c_ext}"
                if chat_id in ACTIVE_DOWNLOADS:
                    ACTIVE_DOWNLOADS[chat_id]["filename"] = filename

            if not c_url.endswith(".m3u8") and c_kind != "hls":
                log.info("Attempting direct download for %s: %s", slug, c_url[:80])
                tracker = DownloadProgressTracker(0, start_time)
                downloaded = await _download_direct(c_url, filename, on_progress, referer=c_ref)
            elif ".m3u8" in c_url or c_kind == "hls":
                log.info("Attempting HLS download for %s: %s", slug, c_url[:80])
                tracker = DownloadProgressTracker(0, start_time)
                downloaded = await _download_n_m3u8dl(c_url, filename, on_progress, referer=c_ref)
                if not downloaded:
                    downloaded = await _download_hls_ffmpeg(c_url, filename, on_progress, referer=c_ref)

            if downloaded and os.path.exists(filename) and os.path.getsize(filename) > 50_000:
                log.info("Successfully downloaded candidate stream %s for %s", c_label, slug)
                break
            else:
                downloaded = False
                if os.path.exists(filename):
                    try:
                        os.remove(filename)
                    except OSError:
                        pass

        # Failed
        if not downloaded:
            if ACTIVE_DOWNLOADS.get(chat_id, {}).get("cancelled"):
                return
            elapsed = int(time.time() - start_time)
            is_oppai = "oppai" in slug or any("oppai" in s.get("referer", "") for s in streams)
            err_extra = "\n\n💡 *Note: Some 18+ titles on Oppai require an account. Use `/oppai_login <username> <password>` to unlock.*" if is_oppai else ""
            await _safe_edit(
                callback_query,
                f"❌ **Download Unavailable**\n\n"
                f"Could not download **{slug}** (stream server rejected or requires login).{err_extra}\n\n"
                f"Try another episode or title."
            )
            await log_error(client, username, f"All download strategies failed for {slug}")
            if os.path.exists(filename):
                os.remove(filename)
            return
    finally:
        ACTIVE_DOWNLOADS.pop(chat_id, None)

    if not os.path.exists(filename) or os.path.getsize(filename) == 0:
        await _safe_edit(callback_query, "Download produced an empty file.")
        await log_error(client, username, f"Empty file for {slug}")
        if os.path.exists(filename):
            os.remove(filename)
        return

    # Upload with progress
    try:
        file_size = os.path.getsize(filename)
        file_size_mb = file_size / (1024 * 1024)
        elapsed = int(time.time() - start_time)
        
        # Create upload progress tracker
        upload_tracker = UploadProgressTracker(file_size, time.time())
        
        async def upload_progress(current, total):
            stats = upload_tracker.update(current, total)
            if upload_tracker.should_update_ui(stats["pct"]):
                msg = upload_tracker.format_message(stats, slug=slug)
                await _safe_edit(callback_query, msg)
                if log_msg_id:
                    await log_download_progress(client, log_msg_id, username, slug, 90 + stats["pct"] * 0.1)

        await _safe_edit(
            callback_query,
            f"Uploading... ({file_size_mb:.1f} MB)\n"
            f"Downloaded in {elapsed}s"
        )
        if log_msg_id:
            await log_download_progress(client, log_msg_id, username, slug, 90)

        # Get video details + episode thumbnail
        info = None
        thumb_path = None
        try:
            info = await asyncio.to_thread(hanime_api.details, slug)
            series_name = _extract_series_name(slug)
            tags_str = ", ".join(info.get("tags", [])[:5])
            brand = info.get("brand") or ("oppai.stream (4K)" if "4K" in quality_label else "hentai.tv")
            caption = (
                f"🎬 **{info.get('title') or info.get('name') or slug}**\n\n"
                f"✨ **Quality:** {quality_label}\n"
                f"🌐 **Source:** {brand}\n"
                f"🔖 **Tags:** {tags_str}\n\n"
                f"Downloaded via @hentai_dl_bot"
            )
            # Episode thumbnail
            thumb_url = info.get("poster_url") or info.get("cover_url") or ""
            if thumb_url:
                thumb_path = await download_thumbnail(thumb_url)
        except Exception:
            series_name = _extract_series_name(slug)
            caption = f"{slug}\nDownloaded via @hanime_dl_bot"
        
        # Send to user with progress callback + thumbnail
        try:
            sent = await client.send_document(
                chat_id=chat_id,
                document=filename,
                caption=caption,
                thumb=thumb_path,
                progress=upload_progress,
            )
        finally:
            if thumb_path and os.path.exists(thumb_path):
                try:
                    os.unlink(thumb_path)
                except OSError:
                    pass
        await track_message(chat_id, sent.id)

        file_id = sent.document.file_id
        total_time = int(time.time() - start_time)

        await _safe_edit(
            callback_query,
            f"✅ **Download Complete!**\n\n"
            f"📦 Size: {file_size_mb:.1f} MB\n"
            f"⏱ Total Time: {total_time}s\n\n"
            f"💾 Auto-deletes in 10 minutes. Save it!"
        )

        # Save to MongoDB cache
        await db.Name.update_one(
            {"name": slug},
            {"$set": {"name": slug, "file_id": file_id, "file_size": sent.document.file_size}},
            upsert=True,
        )

        # Update series catalog with poster
        try:
            # Get poster URL with fallback to cover_url
            poster_url = ""
            if info:
                poster_url = info.get("cover_url", "") or info.get("poster_url", "") or info.get("cover", "")
            
            log.info("Updating catalog for %s with poster_url=%s", slug, poster_url[:60] if poster_url else "None")
            
            await update_catalog(
                client=client,
                slug=slug,
                file_id=file_id,
                file_size=sent.document.file_size,
                series_name=info.get("name", "") if info else "",
                poster_url=poster_url,
                tags=info.get("tags", []) if info else [],
            )
        except Exception:
            log.exception("Failed to update catalog for %s", slug)

        await log_upload_complete(client, log_msg_id, slug, file_id)

    except Exception:
        log.exception("Upload failed for %s", slug)
        await _safe_edit(callback_query, "Something went wrong during upload.")
        await log_error(client, username, f"Upload failed for {slug}")
    finally:
        if os.path.exists(filename):
            os.remove(filename)


async def batch_download(client: Client, callback_query: CallbackQuery):
    """Download all episodes of a series (ball_<slug> callback)."""
    raw_slug = callback_query.data.split("_", 1)[1]
    slug = await resolve_slug(raw_slug)
    chat_id = callback_query.from_user.id
    username = callback_query.from_user.username

    log.info("=== BATCH DOWNLOAD === slug=%s user=%s", slug, chat_id)

    from utils.auth import is_approved
    if not await is_approved(chat_id):
        await callback_query.answer("No access.", show_alert=True)
        return

    # Clear old messages before starting batch
    await clear_chat_history(client, chat_id, preserve_message_ids=[callback_query.message.id])

    try:
        await callback_query.answer("Starting batch download...")
    except Exception:
        pass

    # Get episode list
    try:
        info = await asyncio.to_thread(hanime_api.details, slug)
    except Exception:
        log.exception("Failed to get details for batch %s", slug)
        await callback_query.answer("API error", show_alert=True)
        return

    episodes = info.get("episodes", [])
    if not episodes:
        episodes = [{"slug": slug, "name": info["name"]}]

    total = len(episodes)
    succeeded = 0
    failed = 0

    status_msg = await client.send_message(
        chat_id=chat_id,
        text=f"📥 **Batch Download Started**\n\n📺 Episodes: {total}\n✅ Progress: 0/{total}\n\n⏳ Starting...",
    )

    db = get_db()

    for i, ep in enumerate(episodes):
        ep_slug = ep.get("slug", "")
        ep_name = ep.get("name", ep_slug)
        if not ep_slug:
            continue

        try:
            progress_pct = ((i + 1) / total * 100) if total > 0 else 0
            bar = _progress_bar(progress_pct)
            await status_msg.edit_text(
                f"📥 **Batch Download**\n\n"
                f"{bar}\n\n"
                f"📺 Downloading: {ep_name}\n"
                f"📊 Progress: {i + 1}/{total}\n"
                f"✅ Done: {succeeded} | ❌ Failed: {failed}"
            )
        except Exception:
            pass

        # Check cache
        cached = await db.Name.find_one({"name": ep_slug})
        if cached and cached.get("file_size", 0) > 50_000:
            ep_thumb = None
            try:
                ep_info_c = await asyncio.to_thread(hanime_api.details, ep_slug)
                thumb_url = ep_info_c.get("poster_url") or ep_info_c.get("cover_url") or ""
                if thumb_url:
                    ep_thumb = await download_thumbnail(thumb_url)
            except Exception:
                pass
            try:
                await client.send_document(
                    chat_id=chat_id,
                    document=cached["file_id"],
                    caption=f"{ep_name}\nDownloaded via @hanime_dl_bot",
                    thumb=ep_thumb,
                )
                succeeded += 1
                continue
            except Exception:
                await db.Name.delete_one({"name": ep_slug})
            finally:
                if ep_thumb and os.path.exists(ep_thumb):
                    try:
                        os.unlink(ep_thumb)
                    except OSError:
                        pass

        # Fresh download with progress
        try:
            data = await asyncio.to_thread(hanime_api.get_streams, ep_slug)
        except Exception:
            log.error("Batch: stream fetch failed for %s", ep_slug)
            failed += 1
            continue

        dl_url = data.get("dl_url", "")
        streams = data.get("streams", [])
        primary_stream = streams[0] if streams else {}
        ext = primary_stream.get("extension") or ("webm" if dl_url.endswith(".webm") else "mp4")
        referer = primary_stream.get("referer", "")
        quality_label = primary_stream.get("label", "1080p")
        filename = f"{ep_slug}.{ext}"
        downloaded = False
        
        # Progress callback for batch downloads
        async def batch_progress(stats):
            try:
                bar = _progress_bar(stats["pct"])
                await status_msg.edit_text(
                    f"📥 **Batch Download**\n\n"
                    f"{bar}\n\n"
                    f"📺 Downloading: {ep_name} [{quality_label}]\n"
                    f"📊 Progress: {i + 1}/{total}\n"
                    f"✅ Done: {succeeded} | ❌ Failed: {failed}\n\n"
                    f"⚡ Speed: {_format_speed(stats['speed'])} | ⏳ ETA: {_format_time(stats['eta'])}"
                )
            except Exception:
                pass

        candidate_urls = []
        if dl_url:
            candidate_urls.append({"url": dl_url, "referer": referer, "kind": primary_stream.get("kind", ""), "extension": ext})
        for s in streams:
            if s.get("url") and not any(c["url"] == s["url"] for c in candidate_urls):
                candidate_urls.append(s)

        for candidate in candidate_urls:
            c_url = candidate.get("url", "")
            if not c_url:
                continue
            c_ref = candidate.get("referer", referer)
            c_kind = candidate.get("kind", "")
            c_ext = candidate.get("extension", "mp4")
            if c_ext and filename.rsplit(".", 1)[-1] != c_ext:
                filename = f"{ep_slug}.{c_ext}"

            if not c_url.endswith(".m3u8") and c_kind != "hls":
                downloaded = await _download_direct(c_url, filename, batch_progress, referer=c_ref)
            elif ".m3u8" in c_url or c_kind == "hls":
                downloaded = await _download_n_m3u8dl(c_url, filename, batch_progress, referer=c_ref)
                if not downloaded:
                    downloaded = await _download_hls_ffmpeg(c_url, filename, batch_progress, referer=c_ref)

            if downloaded and os.path.exists(filename) and os.path.getsize(filename) > 50_000:
                break
            else:
                downloaded = False
                if os.path.exists(filename):
                    try:
                        os.remove(filename)
                    except OSError:
                        pass

        if not downloaded or not os.path.exists(filename) or os.path.getsize(filename) < 50_000:
            if os.path.exists(filename):
                os.remove(filename)
            failed += 1
            continue

        ep_info = None
        ep_thumb = None
        try:
            ep_info = await asyncio.to_thread(hanime_api.details, ep_slug)
            tags_str = ", ".join(ep_info.get("tags", [])[:5])
            brand = ep_info.get("brand") or ("oppai.stream (4K)" if "4K" in quality_label else "hentai.tv")
            caption = (
                f"🎬 **{ep_name}**\n\n"
                f"✨ **Quality:** {quality_label}\n"
                f"🌐 **Source:** {brand}\n"
                f"🔖 **Tags:** {tags_str}\n\n"
                f"Downloaded via @hentai_dl_bot"
            )
            thumb_url = ep_info.get("poster_url") or ep_info.get("cover_url") or ""
            if thumb_url:
                ep_thumb = await download_thumbnail(thumb_url)
        except Exception:
            caption = f"🎬 **{ep_name}**\nDownloaded via @hentai_dl_bot"

        try:
            sent = await client.send_document(
                chat_id=chat_id,
                document=filename,
                caption=caption,
                thumb=ep_thumb,
            )
            file_id = sent.document.file_id
            await db.Name.update_one(
                {"name": ep_slug},
                {"$set": {"name": ep_slug, "file_id": file_id, "file_size": sent.document.file_size}},
                upsert=True,
            )
            
            # Update catalog for batch downloads too
            try:
                ep_info = None
                try:
                    ep_info = await asyncio.to_thread(hanime_api.details, ep_slug)
                except Exception:
                    pass
                
                poster_url = ""
                if ep_info:
                    poster_url = ep_info.get("cover_url", "") or ep_info.get("poster_url", "") or ep_info.get("cover", "")
                
                await update_catalog(
                    client=client,
                    slug=ep_slug,
                    file_id=file_id,
                    file_size=sent.document.file_size,
                    series_name=ep_info.get("name", "") if ep_info else ep_name,
                    poster_url=poster_url,
                    tags=ep_info.get("tags", []) if ep_info else [],
                )
            except Exception:
                log.exception("Batch: failed to update catalog for %s", ep_slug)
            
            succeeded += 1
        except Exception:
            log.exception("Batch: upload failed for %s", ep_slug)
            failed += 1
        finally:
            if ep_thumb and os.path.exists(ep_thumb):
                try:
                    os.unlink(ep_thumb)
                except OSError:
                    pass
            if os.path.exists(filename):
                os.remove(filename)

    try:
        success_pct = (succeeded / total * 100) if total > 0 else 0
        bar = _progress_bar(success_pct)
        await status_msg.edit_text(
            f"✅ **Batch Download Complete!**\n\n"
            f"{bar}\n\n"
            f"📊 Total: {total}\n"
            f"✅ Success: {succeeded}\n"
            f"❌ Failed: {failed}"
        )
    except Exception:
        pass
