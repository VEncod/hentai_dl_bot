import asyncio
import logging
import os
import re
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

from api.hanime_api import HanimeAPI, BASE_URL
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
from utils.slug_map import get_short_slug, resolve_slug

log = logging.getLogger(__name__)

N_M3U8DL_RE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "binary", "N_m3u8DL-RE")

DOWNLOAD_TIMEOUT = 120
FFMPEG_TIMEOUT = 60
N_M3U8DL_TIMEOUT = 60

PROGRESS_UPDATE_INTERVAL = 3.5

ACTIVE_DOWNLOADS = {}
hanime_api = HanimeAPI()


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
    text += "Use the buttons below to choose your desired quality to download."

    buttons = []
    has_4k = any(s.get("height", 0) >= 2160 or "4k" in s.get("label", "").lower() for s in streams)
    has_1080 = any(s.get("height", 0) == 1080 or "1080" in s.get("label", "").lower() for s in streams)
    has_720 = any(s.get("height", 0) == 720 or "720" in s.get("label", "").lower() for s in streams)
    has_480 = any(s.get("height", 0) == 480 or "480" in s.get("label", "").lower() for s in streams)

    if has_4k:
        buttons.append([InlineKeyboardButton("✨ Download 4K (2160p)", callback_data=f"dlt_{short_key}_4k")])
    if has_1080:
        buttons.append([InlineKeyboardButton("📺 Download 1080p (Full HD)", callback_data=f"dlt_{short_key}_1080")])
    if has_720:
        buttons.append([InlineKeyboardButton("📱 Download 720p (HD)", callback_data=f"dlt_{short_key}_720")])
    if has_480:
        buttons.append([InlineKeyboardButton("📼 Download 480p (SD)", callback_data=f"dlt_{short_key}_480")])
    if not (has_4k or has_1080 or has_720 or has_480):
        buttons.append([InlineKeyboardButton("⬇️ Download Video", callback_data=f"dlt_{short_key}_best")])

    buttons.append([InlineKeyboardButton("⬅️ Back to Info", callback_data=f"info_{short_key}")])

    await callback_query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons),
        disable_web_page_preview=False,
    )


def _progress_bar_detailed(pct: float, length: int = 12) -> str:
    pct = max(0.0, min(100.0, pct))
    filled = int(length * pct / 100)
    empty = length - filled
    bar = "█" * filled + "░" * empty
    return f"[{bar}] {pct:.1f}%"


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
    if seconds <= 0:
        return "calculating..."
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
        self.last_update_bytes = 0
        self.current_speed = 0.0
        self.eta_seconds = 0.0
        self.last_reported_time = 0.0

    def update(self, downloaded: int) -> dict:
        now = time.time()
        self.downloaded = downloaded

        time_delta = now - self.last_update_time
        if time_delta >= 1.0:
            bytes_delta = downloaded - self.last_update_bytes
            self.current_speed = bytes_delta / time_delta
            self.last_update_time = now
            self.last_update_bytes = downloaded
            if self.current_speed > 0 and self.total_size > 0:
                remaining = max(0, self.total_size - downloaded)
                self.eta_seconds = remaining / self.current_speed

        pct = (downloaded / self.total_size * 100) if self.total_size > 0 else 0
        elapsed = now - self.start_time

        return {
            "pct": min(100.0, pct),
            "downloaded": downloaded,
            "total": self.total_size,
            "speed": self.current_speed,
            "eta": self.eta_seconds,
            "elapsed": elapsed,
        }

    def should_update_ui(self, pct: float) -> bool:
        now = time.time()
        if (now - self.last_reported_time) >= PROGRESS_UPDATE_INTERVAL:
            self.last_reported_time = now
            return True
        return False

    def format_message(self, stats: dict, title: str = "Downloading...", slug: str = "") -> str:
        pct = stats["pct"]
        bar = _progress_bar_detailed(pct)
        speed = _format_speed(stats["speed"])
        eta = _format_time(stats["eta"])
        downloaded = _format_size(stats["downloaded"])
        total = _format_size(stats["total"]) if stats["total"] > 0 else "unknown"

        return (
            f"📥 **{title}**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{bar}\n\n"
            f"📊 **Size:** {downloaded} / {total}\n"
            f"⚡ **Speed:** {speed}\n"
            f"⏳ **ETA:** {eta}"
        )


class UploadProgressTracker:
    def __init__(self, total_size: int, start_time: float):
        self.total_size = total_size
        self.start_time = start_time
        self.uploaded = 0
        self.last_update_time = start_time
        self.last_update_bytes = 0
        self.current_speed = 0.0
        self.eta_seconds = 0.0
        self.last_reported_time = 0.0

    def update(self, current: int, total: int) -> dict:
        now = time.time()
        self.uploaded = current
        self.total_size = total

        time_delta = now - self.last_update_time
        if time_delta >= 1.0:
            bytes_delta = current - self.last_update_bytes
            self.current_speed = bytes_delta / time_delta
            self.last_update_time = now
            self.last_update_bytes = current
            if self.current_speed > 0 and total > 0:
                remaining = max(0, total - current)
                self.eta_seconds = remaining / self.current_speed

        pct = (current / total * 100) if total > 0 else 0
        elapsed = now - self.start_time

        return {
            "pct": min(100.0, pct),
            "uploaded": current,
            "total": total,
            "speed": self.current_speed,
            "eta": self.eta_seconds,
            "elapsed": elapsed,
        }

    def should_update_ui(self, pct: float) -> bool:
        now = time.time()
        if (now - self.last_reported_time) >= PROGRESS_UPDATE_INTERVAL:
            self.last_reported_time = now
            return True
        return False

    def format_message(self, stats: dict, slug: str = "") -> str:
        pct = stats["pct"]
        bar = _progress_bar_detailed(pct)
        speed = _format_speed(stats["speed"])
        eta = _format_time(stats["eta"])
        uploaded = _format_size(stats["uploaded"])
        total = _format_size(stats["total"]) if stats["total"] > 0 else "unknown"

        return (
            f"📤 **Uploading to Telegram...**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{bar}\n\n"
            f"📊 **Size:** {uploaded} / {total}\n"
            f"⚡ **Speed:** {speed}\n"
            f"⏳ **ETA:** {eta}"
        )


CANCELLED_DOWNLOADS = set()


async def cancel_download_callback(client: Client, callback_query: CallbackQuery):
    chat_id = callback_query.from_user.id
    log.info("=== CANCEL CALLBACK RECEIVED for chat_id=%s ===", chat_id)
    CANCELLED_DOWNLOADS.add(chat_id)

    if chat_id in ACTIVE_DOWNLOADS:
        dl_info = ACTIVE_DOWNLOADS[chat_id]
        dl_info["cancelled"] = True
        session = dl_info.get("session")
        if session and not session.closed:
            try:
                await session.close()
            except Exception:
                pass
        proc = dl_info.get("process")
        if proc:
            try:
                proc.kill()
            except Exception:
                pass
        task = dl_info.get("task")
        if task and not task.done():
            task.cancel()

        filename = dl_info.get("filename")
        if filename and os.path.exists(filename):
            try:
                os.unlink(filename)
            except Exception:
                pass

        try:
            await callback_query.answer("🛑 Download cancelled!", show_alert=True)
        except Exception:
            pass
        await _safe_edit(
            callback_query,
            f"🛑 **Download Cancelled**\n\n"
            f"Download was stopped by user."
        )
    else:
        try:
            await callback_query.answer("No active download to cancel.", show_alert=True)
        except Exception:
            pass


async def _safe_edit(callback_query: CallbackQuery, text: str, reply_markup=None):
    try:
        await callback_query.edit_message_text(text, reply_markup=reply_markup)
    except Exception:
        pass


async def _download_direct(url: str, filename: str, progress_cb=None, referer: str = "", chat_id: int = 0) -> bool:
    try:
        if "hentaicity.com" in url:
            referer = "https://www.hentaicity.com/"
        elif "oppai.stream" in url or "myspacecat.pictures" in url:
            referer = "https://oppai.stream/"
        elif not referer:
            referer = "https://www.hentaicity.com/"

        timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT, connect=10, sock_read=60)
        connector = aiohttp.TCPConnector(limit=5, force_close=False)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "*/*",
            "Referer": referer,
            "Origin": referer.rstrip("/"),
        }

        async with aiohttp.ClientSession(timeout=timeout, connector=connector, headers=headers) as session:
            if chat_id and chat_id in ACTIVE_DOWNLOADS:
                ACTIVE_DOWNLOADS[chat_id]["session"] = session

            async with session.get(url) as resp:
                if chat_id and chat_id in ACTIVE_DOWNLOADS:
                    ACTIVE_DOWNLOADS[chat_id]["response"] = resp

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
                tracker = DownloadProgressTracker(total if total > 0 else 0, start_time)

                with open(filename, "wb") as f:
                    async for chunk in resp.content.iter_chunked(512 * 1024):
                        if chat_id in CANCELLED_DOWNLOADS or (chat_id and ACTIVE_DOWNLOADS.get(chat_id, {}).get("cancelled")):
                            log.info("Download cancelled for chat %s", chat_id)
                            return False
                        f.write(chunk)
                        downloaded += len(chunk)

                        if progress_cb and tracker:
                            stats = tracker.update(downloaded)
                            if tracker.should_update_ui(stats["pct"]):
                                await progress_cb(stats)

                if progress_cb and tracker:
                    stats = tracker.update(downloaded)
                    await progress_cb(stats)

        if not os.path.exists(filename):
            return False
        file_size = os.path.getsize(filename)
        if file_size < 50_000:
            log.error("Downloaded file too small (%d bytes), likely not a video: %s", file_size, url)
            try:
                os.remove(filename)
            except OSError:
                pass
            return False

        return True
    except asyncio.CancelledError:
        log.info("Direct download cancelled")
        return False
    except Exception as e:
        log.error("Direct download failed for url=%s: %s", url[:80], e)
        return False


async def _download_n_m3u8dl(url: str, filename: str, progress_cb=None, referer: str = "") -> bool:
    if not os.path.isfile(N_M3U8DL_RE) or not os.access(N_M3U8DL_RE, os.X_OK):
        return False

    out_name = Path(filename).stem
    save_dir = Path(filename).parent.resolve()

    cmd = [
        N_M3U8DL_RE,
        url,
        "--save-dir", str(save_dir),
        "--save-name", out_name,
        "--auto-select",
        "--binary-merge",
        "--log-level", "INFO",
    ]
    if referer:
        cmd.extend(["--header", f"Referer: {referer}"])

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        await asyncio.wait_for(proc.communicate(), timeout=N_M3U8DL_TIMEOUT)
        return os.path.exists(filename) and os.path.getsize(filename) > 50_000
    except Exception:
        return False


async def _download_hls_ffmpeg(url: str, filename: str, progress_cb=None, referer: str = "") -> bool:
    cmd = [
        "ffmpeg", "-y",
        "-headers", f"Referer: {referer}\r\nUser-Agent: Mozilla/5.0\r\n" if referer else "User-Agent: Mozilla/5.0\r\n",
        "-i", url,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        filename,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=FFMPEG_TIMEOUT)
        return os.path.exists(filename) and os.path.getsize(filename) > 50_000
    except Exception:
        return False


def _extract_series_name(slug: str) -> str:
    s = slug.replace("hcity__", "").replace("htv__", "").replace("oppai__", "")
    s = re.sub(r"-episode-\d+$", "", s, flags=re.I)
    s = re.sub(r"[-_]\d+$", "", s)
    return s.replace("-", " ").strip().title()


@approved_only
@force_sub
async def hentaidl(client: Client, callback_query: CallbackQuery):
    """Download video with quality selection support."""
    raw_data = callback_query.data.split("_", 1)[1]
    target_quality = None
    if raw_data.endswith("_4k"):
        target_quality = "4k"
        raw_slug = raw_data[:-3]
    elif raw_data.endswith("_1080"):
        target_quality = "1080"
        raw_slug = raw_data[:-5]
    elif raw_data.endswith("_720"):
        target_quality = "720"
        raw_slug = raw_data[:-4]
    elif raw_data.endswith("_480"):
        target_quality = "480"
        raw_slug = raw_data[:-4]
    elif raw_data.endswith("_best"):
        target_quality = "best"
        raw_slug = raw_data[:-5]
    else:
        raw_slug = raw_data

    slug = await resolve_slug(raw_slug)
    chat_id = callback_query.from_user.id
    username = callback_query.from_user.username
    log.info("=== DOWNLOAD HANDLER === slug=%s quality=%s user=%s", slug, target_quality, chat_id)

    # Check cache
    db = get_db()
    cache_key = f"{slug}_{target_quality}" if target_quality else slug
    cached = await db.Name.find_one({"name": cache_key}) or await db.Name.find_one({"name": slug})

    if cached and cached.get("file_size", 0) > 50_000:
        thumb_path = None
        try:
            info_c = await asyncio.to_thread(hanime_api.details, slug)
            thumb_url = info_c.get("poster_url") or info_c.get("cover_url") or ""
            if thumb_url:
                thumb_path = await download_thumbnail(thumb_url)
        except Exception:
            pass

        try:
            sent = await client.send_document(
                chat_id=chat_id,
                document=cached["file_id"],
                caption=f"🎬 **{info_c.get('title') or slug}**\n💾 *Served from cache*\n\nDownloaded via @hentai_dl_bot",
                thumb=thumb_path,
            )
            await track_message(chat_id, sent.id)
            await _safe_edit(
                callback_query,
                f"✅ **Sent from Cache!**\n\n"
                f"📄 {slug}\n"
                f"Auto-deletes in 10 minutes."
            )
            return
        except Exception:
            await db.Name.delete_one({"name": cache_key})
        finally:
            if thumb_path and os.path.exists(thumb_path):
                try:
                    os.unlink(thumb_path)
                except OSError:
                    pass

    start_time = time.time()
    await _safe_edit(callback_query, f"🚀 **Preparing Download**\n\n[{'░'*12}] 0.0%\n\n⏳ Resolving video stream...")

    # Fetch streams
    try:
        data = await asyncio.to_thread(hanime_api.get_streams, slug, target_quality)
    except Exception:
        log.exception("Failed to fetch streams for slug=%s", slug)
        await _safe_edit(callback_query, "❌ API unavailable. Please try again later.")
        return

    dl_url = data.get("dl_url", "")
    streams = data.get("streams", [])

    if not dl_url and not streams:
        is_oppai = "oppai" in slug
        extra = "\n\n💡 *If this title is on Oppai, it may require `/oppai_login`.*" if is_oppai else ""
        await _safe_edit(
            callback_query,
            f"❌ **No Download Stream Available**\n\n"
            f"The server returned no stream for **{slug}**.{extra}"
        )
        return

    primary_stream = streams[0] if streams else {}
    ext = primary_stream.get("extension") or ("webm" if dl_url.endswith(".webm") else "mp4")
    referer = primary_stream.get("referer", "https://www.hentaicity.com/")
    quality_label = primary_stream.get("label", "1080p")
    filename = f"{slug}.{ext}"

    cancel_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛑 Stop Download", callback_data=f"canceldl_{chat_id}")]
    ])

    CANCELLED_DOWNLOADS.discard(chat_id)
    ACTIVE_DOWNLOADS[chat_id] = {
        "task": asyncio.current_task(),
        "slug": slug,
        "cancelled": False,
        "filename": filename,
        "session": None,
        "response": None,
    }

    try:
        downloaded = False
        active_stream_label = quality_label

        async def on_progress(stats):
            if chat_id in CANCELLED_DOWNLOADS or ACTIVE_DOWNLOADS.get(chat_id, {}).get("cancelled"):
                raise asyncio.CancelledError("Cancelled by user")
            msg = tracker.format_message(stats, title=f"Downloading [{active_stream_label}]", slug=slug)
            await _safe_edit(callback_query, msg, reply_markup=cancel_keyboard)

        # Build stream candidate list
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
            # STOP immediately if user tapped cancel!
            if chat_id in CANCELLED_DOWNLOADS or ACTIVE_DOWNLOADS.get(chat_id, {}).get("cancelled"):
                log.info("Download cancelled by user for chat %s, halting candidate loop", chat_id)
                return

            c_url = candidate.get("url", "")
            if not c_url:
                continue
            c_ref = candidate.get("referer", referer)
            c_kind = candidate.get("kind", "")
            c_label = candidate.get("label", quality_label)
            c_ext = candidate.get("extension", "mp4")
            active_stream_label = c_label

            if c_ext and filename.rsplit(".", 1)[-1] != c_ext:
                filename = f"{slug}.{c_ext}"
                if chat_id in ACTIVE_DOWNLOADS:
                    ACTIVE_DOWNLOADS[chat_id]["filename"] = filename

            if not c_url.endswith(".m3u8") and c_kind != "hls":
                log.info("Trying direct download for %s: %s", slug, c_url[:80])
                tracker = DownloadProgressTracker(0, start_time)
                downloaded = await _download_direct(c_url, filename, on_progress, referer=c_ref, chat_id=chat_id)
            elif ".m3u8" in c_url or c_kind == "hls":
                log.info("Trying HLS download for %s: %s", slug, c_url[:80])
                tracker = DownloadProgressTracker(0, start_time)
                downloaded = await _download_n_m3u8dl(c_url, filename, on_progress, referer=c_ref)
                if not downloaded and chat_id not in CANCELLED_DOWNLOADS and not ACTIVE_DOWNLOADS.get(chat_id, {}).get("cancelled"):
                    downloaded = await _download_hls_ffmpeg(c_url, filename, on_progress, referer=c_ref)

            if chat_id in CANCELLED_DOWNLOADS or ACTIVE_DOWNLOADS.get(chat_id, {}).get("cancelled"):
                log.info("Download cancelled during candidate processing for chat %s", chat_id)
                return

            if downloaded and os.path.exists(filename) and os.path.getsize(filename) > 50_000:
                log.info("Download completed successfully: %s (%s)", filename, c_label)
                break
            else:
                downloaded = False
                if os.path.exists(filename):
                    try:
                        os.remove(filename)
                    except OSError:
                        pass

        if not downloaded:
            if chat_id in CANCELLED_DOWNLOADS or ACTIVE_DOWNLOADS.get(chat_id, {}).get("cancelled"):
                return
            elapsed = int(time.time() - start_time)
            is_oppai = "oppai" in slug
            err_extra = "\n\n💡 *Note: If this title requires an account, use `/oppai_login`.*" if is_oppai else ""
            await _safe_edit(
                callback_query,
                f"❌ **Download Unavailable**\n\n"
                f"Could not download **{slug}** ({quality_label}) after {elapsed}s.{err_extra}\n\n"
                f"Please try another quality or episode."
            )
            return
    finally:
        ACTIVE_DOWNLOADS.pop(chat_id, None)
        CANCELLED_DOWNLOADS.discard(chat_id)

    if not os.path.exists(filename) or os.path.getsize(filename) < 50_000:
        await _safe_edit(callback_query, "❌ Download produced an empty or corrupted file.")
        if os.path.exists(filename):
            try:
                os.remove(filename)
            except OSError:
                pass
        return

    # Upload Phase with strict 3.5s rate limiting
    try:
        file_size = os.path.getsize(filename)
        upload_tracker = UploadProgressTracker(file_size, time.time())

        async def upload_progress(current, total):
            stats = upload_tracker.update(current, total)
            if upload_tracker.should_update_ui(stats["pct"]):
                msg = upload_tracker.format_message(stats, slug=slug)
                await _safe_edit(callback_query, msg)

        await _safe_edit(
            callback_query,
            f"📤 **Uploading to Telegram...**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"[{'░'*12}] 0.0%\n\n"
            f"📊 **Size:** {_format_size(file_size)}\n"
            f"✨ **Quality:** {quality_label}"
        )

        info = None
        thumb_path = None
        try:
            info = await asyncio.to_thread(hanime_api.details, slug)
            series_name = _extract_series_name(slug)
            tags_str = ", ".join(info.get("tags", [])[:5])
            brand = info.get("brand") or "hentaicity.com"
            caption = (
                f"🎬 **{info.get('title') or slug}**\n\n"
                f"✨ **Quality:** {quality_label}\n"
                f"🌐 **Source:** {brand}\n"
                f"🔖 **Tags:** {tags_str}\n\n"
                f"Downloaded via @hentai_dl_bot"
            )
            thumb_url = info.get("poster_url") or info.get("cover_url") or ""
            if thumb_url:
                thumb_path = await download_thumbnail(thumb_url)
        except Exception:
            series_name = _extract_series_name(slug)
            caption = f"🎬 **{slug}**\nDownloaded via @hentai_dl_bot"

        sent = None
        try:
            sent = await client.send_video(
                chat_id=chat_id,
                video=filename,
                caption=caption,
                progress=upload_progress,
                thumb=thumb_path,
                supports_streaming=True,
            )
        except Exception as e_vid:
            log.warning("send_video failed (%s), falling back to send_document", e_vid)
            sent = await client.send_document(
                chat_id=chat_id,
                document=filename,
                caption=caption,
                progress=upload_progress,
                thumb=thumb_path,
            )

        await track_message(chat_id, sent.id)
        file_id = (sent.video.file_id if sent.video else sent.document.file_id) if sent else ""

        # Cache file in DB
        if file_id:
            await db.Name.update_one(
                {"name": cache_key},
                {"$set": {"name": cache_key, "file_id": file_id, "file_size": file_size, "slug": slug, "quality": quality_label}},
                upsert=True,
            )

        await _safe_edit(
            callback_query,
            f"✅ **Download Complete!**\n\n"
            f"🎬 **{info.get('title') if info else slug}**\n"
            f"✨ **Quality:** {quality_label}\n"
            f"📊 **Size:** {_format_size(file_size)}\n\n"
            f"Auto-deletes in 10 minutes. Save it to your saved messages!"
        )

        # ── Update Series Catalog in Main Channel (Poster + Get Episodes Button) ──
        try:
            poster_url = info.get("cover_url", "") or info.get("poster_url", "") if info else ""
            tags_list = info.get("tags", []) if info else []
            await update_catalog(
                client=client,
                slug=slug,
                file_id=file_id,
                file_size=file_size,
                series_name=series_name,
                poster_url=poster_url,
                tags=tags_list,
            )
        except Exception as e:
            log.warning("Catalog update failed: %s", e)

    except Exception as e:
        log.exception("Upload failed for %s: %s", slug, e)
        await _safe_edit(callback_query, "❌ Upload to Telegram failed. Please try again.")
    finally:
        if thumb_path and os.path.exists(thumb_path):
            try:
                os.unlink(thumb_path)
            except OSError:
                pass
        if os.path.exists(filename):
            try:
                os.remove(filename)
            except OSError:
                pass


@approved_only
@force_sub
async def batch_download(client: Client, callback_query: CallbackQuery):
    """Batch download all episodes of a series."""
    raw_slug = callback_query.data.split("_", 1)[1]
    slug = await resolve_slug(raw_slug)
    chat_id = callback_query.from_user.id
    log.info("=== BATCH DOWNLOAD === slug=%s user=%s", slug, chat_id)

    try:
        info = await asyncio.to_thread(hanime_api.details, slug)
    except Exception:
        await _safe_edit(callback_query, "❌ Failed to fetch series info for batch download.")
        return

    episodes = info.get("episodes", [])
    if not episodes:
        await _safe_edit(callback_query, "❌ No episodes found to batch download.")
        return

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

        pct = (i / total) * 100
        bar = _progress_bar_detailed(pct)
        try:
            await status_msg.edit_text(
                f"📥 **Batch Download**\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
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
            try:
                await client.send_document(
                    chat_id=chat_id,
                    document=cached["file_id"],
                    caption=f"🎬 **{ep_name}**\n💾 *Served from cache*\n\nDownloaded via @hentai_dl_bot",
                )
                succeeded += 1
                continue
            except Exception:
                await db.Name.delete_one({"name": ep_slug})

        # Fresh download
        try:
            data = await asyncio.to_thread(hanime_api.get_streams, ep_slug)
        except Exception:
            failed += 1
            continue

        dl_url = data.get("dl_url", "")
        streams = data.get("streams", [])
        primary_stream = streams[0] if streams else {}
        ext = primary_stream.get("extension") or ("webm" if dl_url.endswith(".webm") else "mp4")
        referer = primary_stream.get("referer", "https://www.hentaicity.com/")
        quality_label = primary_stream.get("label", "1080p")
        filename = f"{ep_slug}.{ext}"
        downloaded = False

        if dl_url and not dl_url.endswith(".m3u8"):
            downloaded = await _download_direct(dl_url, filename, referer=referer)
        if not downloaded:
            for s in streams:
                if s.get("kind") == "hls" and s.get("url"):
                    downloaded = await _download_n_m3u8dl(s["url"], filename, referer=referer)
                    if not downloaded:
                        downloaded = await _download_hls_ffmpeg(s["url"], filename, referer=referer)
                    if downloaded:
                        break

        if not downloaded or not os.path.exists(filename) or os.path.getsize(filename) < 50_000:
            if os.path.exists(filename):
                try:
                    os.remove(filename)
                except OSError:
                    pass
            failed += 1
            continue

        # Upload episode
        try:
            sent = await client.send_document(
                chat_id=chat_id,
                document=filename,
                caption=f"🎬 **{ep_name}** [{quality_label}]\n\nDownloaded via @hentai_dl_bot",
            )
            await track_message(chat_id, sent.id)

            await db.Name.update_one(
                {"name": ep_slug},
                {"$set": {"name": ep_slug, "file_id": sent.document.file_id, "file_size": sent.document.file_size}},
                upsert=True,
            )
            succeeded += 1
        except Exception:
            failed += 1
        finally:
            if os.path.exists(filename):
                try:
                    os.remove(filename)
                except OSError:
                    pass

    final_bar = _progress_bar_detailed(100.0)
    try:
        await status_msg.edit_text(
            f"✅ **Batch Download Finished!**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{final_bar}\n\n"
            f"📊 **Total:** {total}\n"
            f"✅ **Succeeded:** {succeeded}\n"
            f"❌ **Failed:** {failed}"
        )
    except Exception:
        pass
