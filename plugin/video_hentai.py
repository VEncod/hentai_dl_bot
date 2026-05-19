import asyncio
import logging
import os
from datetime import datetime, timezone

import aiohttp
from pyrogram import Client
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from api.hanime import get_streams, details
from utils.auth import approved_only
from utils.db import get_db
from utils.logger import (
    log_download_start, log_download_progress, log_upload_complete,
    log_error, get_main_channel,
)

log = logging.getLogger(__name__)


# ── Stream link buttons ─────────────────────────────────────────────────

@approved_only
async def hentailink(client: Client, callback_query: CallbackQuery):
    """Show streaming links (link_<slug> callback)."""
    slug = callback_query.data.split("_", 1)[1]

    try:
        data = await get_streams(slug)
    except Exception:
        log.exception("Failed to fetch streams for slug=%s", slug)
        await callback_query.answer("❌ API unavailable, try again later.", show_alert=True)
        return

    streams = data["streams"]
    dl_url = data["dl_url"]

    if not streams and not dl_url:
        await callback_query.answer("No stream links available.", show_alert=True)
        return

    keyboard = []
    seen_heights = set()
    for stream in streams:
        height = stream["height"]
        url = stream["url"]
        kind = stream["kind"]

        if not url or height in seen_heights:
            continue
        seen_heights.add(height)

        label = f"{'▶️' if kind == 'hls' else '📥'} {height}p ({kind.upper()})"
        if stream.get("filesize_mbs"):
            label += f" — {stream['filesize_mbs']:.0f}MB"

        keyboard.append([InlineKeyboardButton(label, url=url)])

    if dl_url:
        keyboard.append([InlineKeyboardButton("⬇️ Direct Download", url=dl_url)])

    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data=f"info_{slug}")])

    await callback_query.edit_message_text(
        f"▶️ Streaming **{slug}**\n"
        f"https://hanime.tv/videos/hentai/{slug}\n\n"
        "Please share the bot if you like it ☺️",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ── Download helpers ────────────────────────────────────────────────────

async def _download_direct(url: str, filename: str) -> bool:
    """Download a file directly via aiohttp."""
    try:
        timeout = aiohttp.ClientTimeout(total=300)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                resp.raise_for_status()
                with open(filename, "wb") as f:
                    async for chunk in resp.content.iter_chunked(1024 * 64):
                        f.write(chunk)
        return True
    except Exception:
        log.exception("Direct download failed for url=%s", url)
        return False


async def _download_hls(url: str, filename: str) -> bool:
    """Download an HLS stream via ffmpeg."""
    try:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y",
            "-headers", "Referer: https://hanime.tv/\r\nOrigin: https://hanime.tv\r\n",
            "-i", url,
            "-c", "copy",
            "-bsf:a", "aac_adtstoasc",
            filename,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()

        if process.returncode != 0:
            log.error("ffmpeg failed: %s", stderr.decode(errors="replace")[-500:])
            return False
        return True
    except Exception:
        log.exception("ffmpeg download failed for url=%s", url)
        return False


def _extract_series_name(slug: str) -> str:
    """Extract series name from slug (remove trailing episode number)."""
    # e.g., "overflow-1" -> "overflow", "ane-yome-quartet-2" -> "ane-yome-quartet"
    parts = slug.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0]
    return slug


# ── Download handler ────────────────────────────────────────────────────

@approved_only
async def hentaidl(client: Client, callback_query: CallbackQuery):
    """Download and send the video (dlt_<slug> callback)."""
    slug = callback_query.data.split("_", 1)[1]
    chat_id = callback_query.from_user.id
    username = callback_query.from_user.username
    db = get_db()

    # Notify user
    await callback_query.edit_message_text(
        "⏳ Fetching hentai for you...\nStatus: **DOWNLOADING**"
    )

    # Log download start
    log_msg_id = await log_download_start(client, username, slug)

    # Check cache first
    cached = await db.Name.find_one({"name": slug})
    if cached:
        await callback_query.edit_message_text("📤 **Uploading from cache...**")
        if log_msg_id:
            await log_download_progress(client, log_msg_id, username, slug, 100)
        try:
            await client.send_document(
                chat_id=chat_id,
                document=cached["file_id"],
                caption="Downloaded via @hanime_dl_bot",
            )
            await log_upload_complete(client, log_msg_id, slug, cached["file_id"])
        except Exception:
            log.exception("Failed to send cached file for %s", slug)
            await callback_query.edit_message_text("❌ Failed to send file. Cache may be stale.")
            await log_error(client, username, f"Cache send failed for {slug}")
        return

    # Fetch streams
    try:
        data = await get_streams(slug)
    except Exception:
        log.exception("Failed to fetch streams for download slug=%s", slug)
        await callback_query.edit_message_text("❌ API unavailable. Please try again later.")
        await log_error(client, username, f"Stream fetch failed for {slug}")
        return

    streams = data["streams"]
    dl_url = data["dl_url"]

    filename = f"{slug}.mp4"
    downloaded = False

    # Strategy 1: Direct download URL
    if dl_url:
        log.info("Downloading %s via direct URL", slug)
        if log_msg_id:
            await log_download_progress(client, log_msg_id, username, slug, 10)
        downloaded = await _download_direct(dl_url, filename)

    # Strategy 2: MP4 streams
    if not downloaded:
        mp4_streams = [s for s in streams if s["kind"] == "mp4" and s["url"]]
        for stream in mp4_streams:
            log.info("Trying MP4 stream %dp for %s", stream["height"], slug)
            if log_msg_id:
                await log_download_progress(client, log_msg_id, username, slug, 30)
            downloaded = await _download_direct(stream["url"], filename)
            if downloaded:
                break

    # Strategy 3: HLS via ffmpeg
    if not downloaded:
        hls_streams = [s for s in streams if s["kind"] == "hls" and s["url"]]
        for stream in hls_streams:
            log.info("Trying HLS stream %dp for %s", stream["height"], slug)
            if log_msg_id:
                await log_download_progress(client, log_msg_id, username, slug, 50)
            downloaded = await _download_hls(stream["url"], filename)
            if downloaded:
                break

    if not downloaded:
        await callback_query.edit_message_text("❌ No downloadable streams found.")
        await log_error(client, username, f"No downloadable streams for {slug}")
        if os.path.exists(filename):
            os.remove(filename)
        return

    if not os.path.exists(filename) or os.path.getsize(filename) == 0:
        await callback_query.edit_message_text("❌ Download produced an empty file.")
        await log_error(client, username, f"Empty file for {slug}")
        if os.path.exists(filename):
            os.remove(filename)
        return

    try:
        if log_msg_id:
            await log_download_progress(client, log_msg_id, username, slug, 80)
        await callback_query.edit_message_text("📤 **Uploading...**")

        # Get video details for caption
        try:
            info = await details(slug)
            series_name = _extract_series_name(slug)
            tags_str = ", ".join(info.get("tags", [])[:5])
            caption = (
                f"📺 **{info['name']}**\n"
                f"🏷 {tags_str}\n"
                f"Downloaded via @hanime_dl_bot"
            )
        except Exception:
            series_name = _extract_series_name(slug)
            caption = f"Downloaded via @hanime_dl_bot"

        # Send to user
        sent = await client.send_document(
            chat_id=chat_id,
            document=filename,
            caption=caption,
        )

        file_id = sent.document.file_id

        # Send to main channel (archive)
        main_channel = await get_main_channel()
        channel_msg_id = None
        if main_channel:
            try:
                channel_msg = await client.send_document(
                    chat_id=main_channel,
                    document=file_id,
                    caption=caption,
                )
                channel_msg_id = channel_msg.id
            except Exception:
                log.warning("Failed to send to main channel for %s", slug)

        # Save to MongoDB cache
        await db.Name.update_one(
            {"name": slug},
            {"$set": {"name": slug, "file_id": file_id}},
            upsert=True,
        )

        # Save to archive (for /archive and /series commands)
        await db.archive.update_one(
            {"slug": slug},
            {"$set": {
                "slug": slug,
                "series": series_name,
                "file_id": file_id,
                "message_id": channel_msg_id,
                "channel_id": main_channel,
                "uploaded_at": datetime.now(timezone.utc),
            }},
            upsert=True,
        )

        await log_upload_complete(client, log_msg_id, slug, file_id)

    except Exception:
        log.exception("Upload failed for %s", slug)
        await callback_query.edit_message_text("❌ Something went wrong during upload.")
        await log_error(client, username, f"Upload failed for {slug}")
    finally:
        if os.path.exists(filename):
            os.remove(filename)
