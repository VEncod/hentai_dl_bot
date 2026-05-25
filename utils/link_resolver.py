"""
Shortened Link Resolver for Hindi Dub channels.

Many Hindi dub channels post shortened/monetized links instead of direct
video files. This module:

1. Detects shortened links in messages
2. Sends them to @Nick_Bypass_Bot to get the real URL
3. Follows the real URL to its destination:
   - Telegram channel message → grabs the video file
   - Telegram bot deep link  → starts the bot, waits for video
4. Returns the file_id of the resolved video

Requires: userbot (Pyrogram user session)
"""

import asyncio
import logging
import re
import time

from pyrogram import Client
from pyrogram.types import Message

log = logging.getLogger(__name__)

BYPASS_BOT = "Nick_Bypass_Bot"
BYPASS_TIMEOUT = 60       # seconds to wait for bypass bot reply
BOT_REPLY_TIMEOUT = 30    # seconds to wait for a bot to send video after /start
CHANNEL_FETCH_TIMEOUT = 15

# Common shortener domains — if a URL matches any of these, it needs bypassing
SHORTENER_DOMAINS = [
    "bit.ly", "tinyurl.com", "goo.gl", "t.co", "is.gd", "v.gd",
    "shrinkme.io", "shrinkme.in", "shrinke.me",
    "linkvertise.com", "link-target.net", "link-to.net",
    "za.gl", "za.gy",
    "ouo.io", "ouo.press",
    "exe.io", "exey.io", "exe.app",
    "gplinks.co", "gplinks.in",
    "shareus.io", "shareus.in", "shareus.site",
    "terabox.link", "teraboxlink.com",
    "adrinolinks.in", "adrinolinks.com",
    "mdiskshortner.link",
    "indianshortner.com",
    "earnl.ink", "earnlink.io",
    "links.shortenbuddy.com",
    "short-url.link",
    "shortingly.me", "shortingly.in",
    "tnlink.in", "tnshort.net",
    "xpshort.com",
    "dulink.in",
    "atglinks.com",
    "mplaylink.com",
    "rocklinks.net",
    "urlshortx.com",
    "pdiskshortener.com",
    "telegram.me", "t.me",  # deep links are handled separately
]

# Regex to extract URLs from text
URL_REGEX = re.compile(
    r'https?://[^\s<>\[\]()\"\']+',
    re.IGNORECASE,
)

# Telegram deep link patterns
TG_CHANNEL_MSG = re.compile(
    r'(?:https?://)?(?:t\.me|telegram\.me)/([a-zA-Z0-9_]+)/(\d+)',
)
TG_BOT_START = re.compile(
    r'(?:https?://)?(?:t\.me|telegram\.me)/([a-zA-Z0-9_]+)\?start=([a-zA-Z0-9_-]+)',
)
TG_BOT_LINK = re.compile(
    r'(?:https?://)?(?:t\.me|telegram\.me)/([a-zA-Z0-9_]+)',
)
TG_INVITE_LINK = re.compile(
    r'(?:https?://)?(?:t\.me|telegram\.me)/(?:\+|joinchat/)([a-zA-Z0-9_-]+)',
)


def extract_urls(text: str) -> list[str]:
    """Extract all URLs from text."""
    if not text:
        return []
    return URL_REGEX.findall(text)


def is_shortened_url(url: str) -> bool:
    """Check if a URL is from a known shortener service."""
    try:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc.lower()
        # Remove www.
        domain = domain.removeprefix("www.")
        return any(domain == sd or domain.endswith("." + sd) for sd in SHORTENER_DOMAINS)
    except Exception:
        return False


def is_telegram_link(url: str) -> bool:
    """Check if a URL is a Telegram deep link."""
    return bool(TG_CHANNEL_MSG.search(url) or TG_BOT_START.search(url))


def classify_telegram_link(url: str) -> dict | None:
    """
    Parse a Telegram link and classify it.
    Returns:
      {"type": "channel_message", "channel": "...", "message_id": 123}
      {"type": "bot_start", "bot": "...", "param": "..."}
      {"type": "invite", "hash": "..."}
      {"type": "bot", "bot": "..."}
      None if not a Telegram link
    """
    # Invite link: t.me/+xxxxx or t.me/joinchat/xxxxx
    # Must check BEFORE channel_message since +xxx could match other patterns
    m = TG_INVITE_LINK.search(url)
    if m:
        return {"type": "invite", "hash": m.group(1)}

    # Channel message: t.me/channel/123
    m = TG_CHANNEL_MSG.search(url)
    if m:
        name = m.group(1)
        msg_id = int(m.group(2))
        return {"type": "channel_message", "channel": name, "message_id": msg_id}

    # Bot with start param: t.me/bot?start=xxx
    m = TG_BOT_START.search(url)
    if m:
        return {"type": "bot_start", "bot": m.group(1), "param": m.group(2)}

    # Plain bot/channel link: t.me/name
    m = TG_BOT_LINK.search(url)
    if m:
        name = m.group(1)
        return {"type": "bot", "bot": name}

    return None


# ── Bypass Bot Interaction ───────────────────────────────────────────────

async def bypass_link(ub: Client, url: str) -> str | None:
    """
    Send a shortened URL to @Nick_Bypass_Bot and wait for the bypassed URL.
    Returns the bypassed URL string, or None on failure.
    """
    log.info("Bypassing link via @%s: %s", BYPASS_BOT, url[:80])

    try:
        # Send the link to the bypass bot
        await ub.send_message(BYPASS_BOT, url)

        # Wait for a reply
        start = time.time()
        last_msg_id = 0

        while time.time() - start < BYPASS_TIMEOUT:
            await asyncio.sleep(2)

            # Get recent messages from the bypass bot
            async for msg in ub.get_chat_history(BYPASS_BOT, limit=5):
                if msg.id <= last_msg_id:
                    continue
                last_msg_id = max(last_msg_id, msg.id)

                # Skip our own messages
                if msg.outgoing:
                    continue

                # Check for URLs in the reply
                text = (msg.text or "") + " " + (msg.caption or "")
                urls = extract_urls(text)

                if urls:
                    bypassed = urls[0]
                    log.info("Bypass bot returned: %s", bypassed[:80])
                    return bypassed

                # Check for inline buttons with URLs
                if msg.reply_markup:
                    for row in msg.reply_markup.inline_keyboard:
                        for btn in row:
                            if btn.url:
                                log.info("Bypass bot returned (button): %s", btn.url[:80])
                                return btn.url

                # Check if bot says it failed
                lower_text = text.lower()
                if any(w in lower_text for w in ["error", "failed", "not supported", "invalid"]):
                    log.warning("Bypass bot reported error: %s", text[:200])
                    return None

        log.warning("Bypass bot timeout after %ds for %s", BYPASS_TIMEOUT, url[:80])
        return None

    except Exception as e:
        log.error("Bypass bot interaction failed: %s", e)
        return None


# ── Telegram Link Resolution ────────────────────────────────────────────

async def resolve_channel_message(ub: Client, channel: str, message_id: int) -> dict | None:
    """
    Fetch a specific message from a Telegram channel and extract the video file.
    Returns {file_id, file_name, file_size} or None.
    """
    log.info("Resolving channel message: @%s/%d", channel, message_id)
    try:
        msgs = await ub.get_messages(channel, message_id)
        msg = msgs if not isinstance(msgs, list) else msgs[0]

        if msg.video:
            return {
                "file_id": msg.video.file_id,
                "file_name": msg.video.file_name or f"video_{message_id}.mp4",
                "file_size": msg.video.file_size or 0,
                "source": f"@{channel}/{message_id}",
            }
        if msg.document:
            mime = msg.document.mime_type or ""
            fname = msg.document.file_name or ""
            if "video" in mime or any(fname.lower().endswith(e) for e in ['.mp4', '.mkv', '.avi']):
                return {
                    "file_id": msg.document.file_id,
                    "file_name": msg.document.file_name or f"doc_{message_id}",
                    "file_size": msg.document.file_size or 0,
                    "source": f"@{channel}/{message_id}",
                }

        # Maybe the message has a forwarded video or buttons leading to the file
        # Check if message has buttons with links (another level of redirection)
        if msg.reply_markup:
            for row in msg.reply_markup.inline_keyboard:
                for btn in row:
                    if btn.url:
                        tg = classify_telegram_link(btn.url)
                        if tg and tg["type"] == "channel_message":
                            # Recursion — but only one level deep
                            return await resolve_channel_message(
                                ub, tg["channel"], tg["message_id"]
                            )

        log.info("Channel message @%s/%d has no video file", channel, message_id)
        return None

    except Exception as e:
        log.warning("Failed to fetch @%s/%d: %s", channel, message_id, e)
        return None


async def _join_channel_safe(ub: Client, channel: str) -> bool:
    """Join a channel silently. Returns True if joined/already member."""
    try:
        await ub.join_chat(channel)
        log.info("Joined channel: %s", channel)
        return True
    except Exception as e:
        err = str(e).lower()
        if "already" in err or "participant" in err:
            return True  # Already a member
        log.warning("Failed to join %s: %s", channel, e)
        return False


async def _handle_force_sub(ub: Client, msg, bot_username: str) -> bool:
    """
    Detect force-sub requirements in a bot message and handle them.
    Joins all required channels, then clicks the verify/check button.
    Returns True if force-sub was handled (caller should wait for next message).
    """
    text = (msg.text or "") + " " + (msg.caption or "")
    lower = text.lower()

    # Detect force-sub patterns
    force_sub_keywords = [
        "join", "subscribe", "channel", "must join", "please join",
        "join the", "join all", "not joined", "you must",
        "membership", "verify", "check again",
    ]
    is_force_sub = any(kw in lower for kw in force_sub_keywords)

    if not is_force_sub:
        return False

    log.info("Force-sub detected from @%s — joining required channels", bot_username)

    # Collect all channel links from text + buttons
    channels_to_join = set()

    # Extract channel links from text
    for url in extract_urls(text):
        tg = classify_telegram_link(url)
        if tg:
            if tg["type"] == "channel_message":
                channels_to_join.add(tg["channel"])
            elif tg["type"] in ("bot", "bot_start"):
                # Skip bot links — those are the bot itself or other bots
                pass
            else:
                channels_to_join.add(tg.get("bot", ""))

    # Extract channel links from inline buttons
    verify_button = None
    if msg.reply_markup:
        for row in msg.reply_markup.inline_keyboard:
            for btn in row:
                if btn.url:
                    tg = classify_telegram_link(btn.url)
                    if tg and tg["type"] in ("channel_message", "bot"):
                        ch = tg.get("channel", tg.get("bot", ""))
                        if ch and ch.lower() != bot_username.lower():
                            channels_to_join.add(ch)
                elif btn.callback_data:
                    # The verify/check button (no URL, just callback_data)
                    btn_text = (btn.text or "").lower()
                    if any(w in btn_text for w in [
                        "verify", "check", "joined", "✅", "done",
                        "confirm", "try again", "refresh",
                    ]):
                        verify_button = btn

    # Remove empty strings
    channels_to_join.discard("")

    if not channels_to_join:
        log.info("Force-sub detected but no channels found to join")
        return False

    log.info("Joining %d channels for force-sub: %s", len(channels_to_join), channels_to_join)

    # Join all channels
    joined = 0
    for ch in channels_to_join:
        if await _join_channel_safe(ub, ch):
            joined += 1
        await asyncio.sleep(0.5)  # Rate limit

    log.info("Joined %d/%d channels for @%s", joined, len(channels_to_join), bot_username)

    # Click the verify button
    if verify_button:
        try:
            await msg.click(verify_button.callback_data)
            log.info("Clicked verify button on @%s", bot_username)
        except Exception as e:
            log.warning("Failed to click verify button: %s", e)
            # Fallback: resend the /start command
            pass

    # Small delay for bot to process verification
    await asyncio.sleep(2)

    return True


async def resolve_bot_start(ub: Client, bot_username: str, start_param: str) -> dict | None:
    """
    Start a bot with a deep link parameter and wait for it to send a video file.
    Handles force-sub bots: auto-joins required channels, clicks verify, then
    waits for the actual file.
    Returns {file_id, file_name, file_size} or None.
    """
    log.info("Starting bot @%s with param=%s", bot_username, start_param)

    max_attempts = 3  # Retry /start after force-sub handling

    try:
        for attempt in range(max_attempts):
            # Send /start command
            cmd = f"/start {start_param}" if start_param else "/start"
            await ub.send_message(bot_username, cmd)

            start_t = time.time()
            last_msg_id = 0
            force_sub_handled = False

            while time.time() - start_t < BOT_REPLY_TIMEOUT:
                await asyncio.sleep(2)

                async for msg in ub.get_chat_history(bot_username, limit=5):
                    if msg.id <= last_msg_id:
                        continue
                    last_msg_id = max(last_msg_id, msg.id)

                    if msg.outgoing:
                        continue

                    # Check for video file
                    if msg.video:
                        return {
                            "file_id": msg.video.file_id,
                            "file_name": msg.video.file_name or f"video_{msg.id}.mp4",
                            "file_size": msg.video.file_size or 0,
                            "source": f"@{bot_username}",
                        }

                    if msg.document:
                        mime = msg.document.mime_type or ""
                        fname = msg.document.file_name or ""
                        if "video" in mime or any(fname.lower().endswith(e) for e in ['.mp4', '.mkv', '.avi']):
                            return {
                                "file_id": msg.document.file_id,
                                "file_name": msg.document.file_name or f"doc_{msg.id}",
                                "file_size": msg.document.file_size or 0,
                                "source": f"@{bot_username}",
                            }

                    # Check if bot sent a redirect link
                    text = (msg.text or "") + " " + (msg.caption or "")
                    urls = extract_urls(text)
                    for url in urls:
                        tg = classify_telegram_link(url)
                        if tg and tg["type"] == "channel_message":
                            return await resolve_channel_message(ub, tg["channel"], tg["message_id"])

                    # Check inline buttons for redirect links
                    if msg.reply_markup:
                        for row in msg.reply_markup.inline_keyboard:
                            for btn in row:
                                if btn.url:
                                    tg = classify_telegram_link(btn.url)
                                    if tg and tg["type"] == "channel_message":
                                        return await resolve_channel_message(
                                            ub, tg["channel"], tg["message_id"]
                                        )

                    # Handle force-sub (join channels + verify)
                    if not force_sub_handled:
                        handled = await _handle_force_sub(ub, msg, bot_username)
                        if handled:
                            force_sub_handled = True
                            log.info("Force-sub handled for @%s, attempt %d — retrying /start",
                                     bot_username, attempt + 1)
                            break  # Break inner while, retry /start

            if force_sub_handled:
                # Retry /start after joining channels
                continue

            # No force-sub, no video — give up
            break

        log.info("Bot @%s didn't send a video after %d attempts", bot_username, max_attempts)
        return None

    except Exception as e:
        log.warning("Bot @%s interaction failed: %s", bot_username, e)
        return None


# ── Master Resolver ──────────────────────────────────────────────────────

async def resolve_link(ub: Client, url: str, progress_cb=None) -> dict | None:
    """
    Resolve a single URL to a video file.
    - t.me links → resolve directly (channel msg or bot start)
    - Shortened links → bypass via @Nick_Bypass_Bot → then resolve
    """
    # Telegram link → resolve directly (don't send to bypass bot)
    tg = classify_telegram_link(url)
    if tg:
        return await _resolve_tg_link(ub, tg, progress_cb)

    # Shortened link → bypass first, then resolve
    if progress_cb:
        await progress_cb("🔓 Bypassing shortened link...")

    bypassed_url = await bypass_link(ub, url)
    if not bypassed_url:
        log.info("Link bypass failed for: %s", url[:80])
        return None

    tg = classify_telegram_link(bypassed_url)
    if tg:
        return await _resolve_tg_link(ub, tg, progress_cb)

    log.info("Bypassed URL is not a Telegram link: %s", bypassed_url[:80])
    return None


async def resolve_all_links(ub: Client, urls: list[str], progress_cb=None) -> dict | None:
    """
    Try to resolve ALL links from a message, not just the first one.
    Tries each link in order — returns the first one that gives a video file.

    Handles both t.me links (direct) and shortened links (via bypass bot).
    """
    if not urls:
        return None

    # Separate into telegram links and shortened links
    tg_links = []
    short_links = []
    for url in urls:
        if is_telegram_link(url):
            tg_links.append(url)
        elif is_shortened_url(url):
            short_links.append(url)
        else:
            # Unknown — try as shortened
            short_links.append(url)

    # Try t.me links first (faster, no bypass needed)
    for url in tg_links:
        tg = classify_telegram_link(url)
        if tg:
            if progress_cb:
                await progress_cb(f"📡 Trying Telegram link...")
            result = await _resolve_tg_link(ub, tg, progress_cb)
            if result:
                return result
            log.info("t.me link didn't resolve: %s", url[:80])

    # Then try shortened links via bypass bot
    for url in short_links:
        if progress_cb:
            await progress_cb(f"🔓 Bypassing shortened link...")
        bypassed = await bypass_link(ub, url)
        if not bypassed:
            log.info("Bypass failed for: %s", url[:80])
            continue
        tg = classify_telegram_link(bypassed)
        if tg:
            result = await _resolve_tg_link(ub, tg, progress_cb)
            if result:
                return result

    return None


async def resolve_invite_link(ub: Client, invite_hash: str, search_slug: str = "") -> dict | None:
    """
    Join a channel via invite link, then search inside for a video file.
    Returns {file_id, file_name, file_size, source} or None.
    """
    invite_url = f"https://t.me/+{invite_hash}"
    log.info("Resolving invite link: %s", invite_url)

    try:
        # Join the channel
        chat = await ub.join_chat(invite_url)
        chat_id = chat.id
        chat_title = chat.title or str(chat_id)
        log.info("Joined channel via invite: %s (%s)", chat_title, chat_id)

        # Wait a moment for the channel to load
        await asyncio.sleep(1)

        # Strategy 1: Check recent messages for video files
        async for msg in ub.get_chat_history(chat_id, limit=50):
            if msg.video:
                # If we have a slug, do a basic relevance check
                if search_slug:
                    text = (msg.caption or "").lower() + " " + (msg.text or "").lower()
                    slug_words = search_slug.replace("-", " ").lower().split()
                    key_words = [w for w in slug_words if len(w) > 2]
                    matches = sum(1 for w in key_words if w in text)
                    if matches < min(2, len(key_words)):
                        continue

                return {
                    "file_id": msg.video.file_id,
                    "file_name": msg.video.file_name or f"video_{msg.id}.mp4",
                    "file_size": msg.video.file_size or 0,
                    "source": f"{chat_title} (invite)",
                }

            if msg.document:
                mime = msg.document.mime_type or ""
                fname = msg.document.file_name or ""
                if "video" in mime or any(fname.lower().endswith(e) for e in ['.mp4', '.mkv', '.avi']):
                    return {
                        "file_id": msg.document.file_id,
                        "file_name": msg.document.file_name or f"doc_{msg.id}",
                        "file_size": msg.document.file_size or 0,
                        "source": f"{chat_title} (invite)",
                    }

        # Strategy 2: Search inside the channel if we have a slug
        if search_slug:
            from pyrogram.enums import MessagesFilter
            queries = search_slug.replace("-", " ").split()
            short_query = " ".join(queries[:3])
            for filt in [MessagesFilter.VIDEO, MessagesFilter.DOCUMENT]:
                try:
                    async for msg in ub.search_messages(
                        chat_id=chat_id, query=short_query, limit=20, filter=filt
                    ):
                        file_id, file_name, file_size = "", "", 0
                        if msg.video:
                            file_id = msg.video.file_id
                            file_name = msg.video.file_name or f"video_{msg.id}.mp4"
                            file_size = msg.video.file_size or 0
                        elif msg.document:
                            mime = msg.document.mime_type or ""
                            if "video" in mime:
                                file_id = msg.document.file_id
                                file_name = msg.document.file_name or f"doc_{msg.id}"
                                file_size = msg.document.file_size or 0
                        if file_id:
                            return {
                                "file_id": file_id,
                                "file_name": file_name,
                                "file_size": file_size,
                                "source": f"{chat_title} (invite)",
                            }
                except Exception as e:
                    log.debug("Search in invite channel failed: %s", e)

        log.info("No video found in invite channel %s", chat_title)
        return None

    except Exception as e:
        err = str(e).lower()
        if "already" in err or "participant" in err:
            # Already a member — try to get chat info and search
            log.info("Already in channel from invite %s, searching...", invite_hash)
            # Can't easily get chat_id from invite hash if already joined
            pass
        log.warning("Failed to resolve invite link %s: %s", invite_url, e)
        return None


# Store search_slug globally for invite resolution
_current_search_slug = ""


def set_search_context(slug: str):
    global _current_search_slug
    _current_search_slug = slug


async def _resolve_tg_link(ub: Client, tg: dict, progress_cb=None) -> dict | None:
    """Resolve a classified Telegram link to a video file."""
    link_type = tg["type"]

    if link_type == "invite":
        if progress_cb:
            await progress_cb("📡 Joining channel via invite link...")
        return await resolve_invite_link(ub, tg["hash"], _current_search_slug)

    elif link_type == "channel_message":
        if progress_cb:
            await progress_cb(f"📡 Fetching from @{tg['channel']}...")
        return await resolve_channel_message(ub, tg["channel"], tg["message_id"])

    elif link_type == "bot_start":
        if progress_cb:
            await progress_cb(f"🤖 Talking to @{tg['bot']}...")
        return await resolve_bot_start(ub, tg["bot"], tg["param"])

    elif link_type == "bot":
        if progress_cb:
            await progress_cb(f"🤖 Starting @{tg['bot']}...")
        return await resolve_bot_start(ub, tg["bot"], "")

    return None


# ── Message Analysis ─────────────────────────────────────────────────────

def get_message_links(msg: Message) -> list[str]:
    """
    Extract download links from a message, filtering out non-download links
    like "how to download" guides, backup channels, etc.

    Returns links sorted by priority: download links first.
    """
    text = (msg.text or "") + " " + (msg.caption or "")
    lines = text.split("\n")

    download_urls = []
    other_urls = []

    # Keywords that indicate a line is NOT a download link
    skip_keywords = [
        "how to download", "how to", "tutorial", "guide",
        "backup", "back up", "backup channel", "join",
        "our channel", "main channel", "update channel",
        "support", "request", "contact",
    ]

    # Keywords that indicate a line IS a download link
    download_keywords = [
        "download", "⬇️", "⬇", "📥", "link", "get file",
        "click here", "tap here", "👇", "⤵️",
    ]

    for line in lines:
        line_lower = line.lower().strip()
        urls_in_line = extract_urls(line)
        if not urls_in_line:
            continue

        # Check if this line should be skipped
        is_skip = any(kw in line_lower for kw in skip_keywords)
        is_download = any(kw in line_lower for kw in download_keywords)

        for url in urls_in_line:
            if is_skip and not is_download:
                other_urls.append(url)
            else:
                download_urls.append(url)

    # Also check inline buttons — buttons labeled "Download" are high priority
    if msg.reply_markup:
        for row in msg.reply_markup.inline_keyboard:
            for btn in row:
                if not btn.url:
                    continue
                btn_text = (btn.text or "").lower()
                is_skip = any(kw in btn_text for kw in skip_keywords)
                is_dl = any(kw in btn_text for kw in download_keywords)
                if is_skip and not is_dl:
                    other_urls.append(btn.url)
                else:
                    download_urls.append(btn.url)

    # Return download links first, then others as fallback
    return download_urls + other_urls


def needs_link_resolution(msg: Message) -> bool:
    """
    Check if a message needs link resolution (has shortened links
    but no direct video file).
    """
    # If it already has a video, no resolution needed
    if msg.video or msg.animation:
        return False
    if msg.document:
        mime = msg.document.mime_type or ""
        if "video" in mime:
            return False

    # Check for links that need resolution
    links = get_message_links(msg)
    for url in links:
        if is_shortened_url(url) or is_telegram_link(url):
            return True

    return False
