"""
Async wrapper around hentaihaven.xxx APIs (switched from hanime.tv).

Functions:
    search(query, page=0)   -> list of hit dicts
    details(slug)           -> dict with video metadata + episodes
    get_streams(slug)       -> dict with 'streams' list and 'dl_url'

Backward compatible with old hanime.tv signatures.
"""

import asyncio
import json
import logging
import re
from urllib.parse import urljoin

try:
    import cloudscraper
    from bs4 import BeautifulSoup
except ImportError:
    cloudscraper = None
    BeautifulSoup = None

try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None

log = logging.getLogger(__name__)

BASE_URL = "https://hentaihaven.xxx"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": BASE_URL,
}


def _create_scraper():
    """Create a cloudscraper instance that bypasses Cloudflare."""
    if cloudscraper is None:
        raise ImportError("cloudscraper is required. Run: pip install cloudscraper")
    return cloudscraper.create_scraper(
        browser={
            "browser": "chrome",
            "platform": "windows",
            "desktop": True,
        },
    )


def _slug_from_url(url: str) -> str:
    """Extract slug from hentaihaven URL."""
    match = re.search(r"/watch/([^/]+)/episode-(\d+)", url)
    if match:
        return f"{match.group(1)}-{match.group(2)}"
    match = re.search(r"/watch/([^/]+)/?", url)
    if match:
        return match.group(1)
    return url.strip("/").split("/")[-1]


def _episode_from_slug(slug: str) -> tuple[str, int]:
    """Split 'overflow-1' into ('overflow', 1)."""
    parts = slug.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0], int(parts[1])
    return slug, 1


def _unix_to_date(ts: int | float | None) -> str:
    from datetime import datetime, timezone
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


# ── Search ──────────────────────────────────────────────────────────────

async def search(query: str, page: int = 0) -> list[dict]:
    """Search hentaihaven.xxx. Returns list of hit dicts compatible with old hanime API."""
    if BeautifulSoup is None:
        raise ImportError("beautifulsoup4 is required. Run: pip install beautifulsoup4")

    scraper = _create_scraper()
    try:
        search_url = f"{BASE_URL}/search/{query}"
        resp = scraper.get(search_url, headers=_HEADERS, timeout=20)
        resp.raise_for_status()
        html = resp.text
    except Exception:
        log.exception("Search failed for query=%r", query)
        return []

    soup = BeautifulSoup(html, 'html.parser')
    
    # Find all watch links in the page
    watch_links = soup.find_all('a', href=re.compile(r'/watch/[^/]+/$'))
    
    seen = set()
    hits = []
    for a in watch_links:
        href = a.get('href', '')
        if not href or href in seen:
            continue
        seen.add(href)
        
        series_slug = href.rstrip('/').split('/')[-1]
        if not series_slug:
            continue
            
        # Get title from image alt or text
        title = "Unknown"
        img = a.find('img')
        if img:
            alt = img.get('alt', '')
            title = alt.replace(' cover', '').strip() or "Unknown"
        
        poster_url = ""
        if img:
            poster_url = img.get('src', '')
        
        slug = series_slug  # First episode
        
        hits.append({
            "id": 0,
            "slug": slug,
            "name": title,
            "url": href,
            "poster_url": poster_url,
            "cover_url": poster_url,
            "description": "",
            "views": 0,
            "interests": 0,
            "likes": 0,
            "dislikes": 0,
            "duration_in_ms": 0,
            "brand": "N/A",
            "tags": [],
            "titles": [title],
            "created_at": 0,
            "released_at": 0,
        })
    
    return hits


# ── Video Details ───────────────────────────────────────────────────────

async def details(slug: str) -> dict:
    """
    Get detailed info for a video by slug.
    Returns dict with same shape as old hanime API for compatibility.
    """
    series, ep_num = _episode_from_slug(slug)
    url = f"{BASE_URL}/watch/{series}/episode-{ep_num}"

    scraper = _create_scraper()
    try:
        resp = scraper.get(url, headers=_HEADERS, timeout=20)
        resp.raise_for_status()
        html = resp.text
    except Exception:
        log.exception("Details fetch failed for slug=%s", slug)
        return {
            "name": slug,
            "slug": slug,
            "views": 0,
            "poster_url": "",
            "cover_url": "",
            "description": "",
            "released_date": "N/A",
            "likes": 0,
            "dislikes": 0,
            "duration": "N/A",
            "duration_ms": 0,
            "brand": "N/A",
            "tags": [],
            "titles": [],
            "episodes": [],
        }

    soup = BeautifulSoup(html, 'html.parser')
    
    # Extract title
    title = series.replace('-', ' ').title()
    title_tag = soup.find('title')
    if title_tag:
        title = title_tag.get_text(strip=True).split(" - ")[0]
    
    poster_url = ""
    og_image = soup.find('meta', property='og:image')
    if og_image and og_image.get('content'):
        poster_url = og_image['content']
    
    description = ""
    og_desc = soup.find('meta', property='og:description')
    if og_desc and og_desc.get('content'):
        description = og_desc['content']
    
    # Build episode list - look for episode links on the page
    episodes = []
    ep_links = soup.find_all('a', href=re.compile(rf'/watch/{series}/episode-(\d+)'))
    ep_numbers = set()
    for link in ep_links:
        ep_match = re.search(r'/episode-(\d+)', link.get('href', ''))
        if ep_match:
            ep_numbers.add(int(ep_match.group(1)))
    
    if ep_numbers:
        for i in sorted(ep_numbers):
            episodes.append({
                "name": f"Episode {i}",
                "slug": f"{series}-{i}",
                "poster_url": poster_url,
            })
    
    # If no episodes found, just use the current one
    if not episodes:
        episodes.append({
            "name": f"Episode {ep_num}",
            "slug": slug,
            "poster_url": poster_url,
        })

    return {
        "name": title,
        "slug": slug,
        "views": 0,
        "poster_url": poster_url,
        "cover_url": poster_url,
        "description": description,
        "released_date": "N/A",
        "likes": 0,
        "dislikes": 0,
        "duration": "N/A",
        "duration_ms": 0,
        "brand": "N/A",
        "tags": [],
        "titles": [],
        "episodes": episodes,
    }


# ── Streams ─────────────────────────────────────────────────────────────

async def _get_streams_with_playwright(slug: str) -> dict:
    """Use Playwright to intercept real stream URLs from the player."""
    if async_playwright is None:
        raise ImportError("playwright is required. Run: pip install playwright")

    series, ep_num = _episode_from_slug(slug)
    url = f"{BASE_URL}/watch/{series}/episode-{ep_num}"

    log.info("Using Playwright to get streams for %s", slug)
    stream_urls = []
    seen_urls = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        async def handle_response(response):
            resp_url = response.url
            ct = response.headers.get("content-type", "")
            if "m3u8" in resp_url or "application/vnd.apple.mpegurl" in ct:
                if resp_url not in seen_urls:
                    seen_urls.add(resp_url)
                    log.info("Intercepted stream URL: %s", resp_url)
                    # Determine quality from URL
                    quality = 0
                    if "480" in resp_url or "480p" in resp_url:
                        quality = 480
                    elif "720" in resp_url or "720p" in resp_url:
                        quality = 720
                    elif "1080" in resp_url or "1080p" in resp_url:
                        quality = 1080

                    stream_urls.append({
                        "url": resp_url,
                        "height": quality,
                        "width": quality * 16 // 9 if quality else 1280,
                        "kind": "hls",
                        "filename": f"{slug}.mp4",
                        "filesize_mbs": 0,
                        "is_downloadable": True,
                    })

        page.on("response", handle_response)

        try:
            await page.goto(url, timeout=60000)
            await asyncio.sleep(8)
        except Exception:
            log.exception("Playwright page load failed")
        finally:
            await browser.close()

    stream_urls.sort(key=lambda s: s.get("height", 0) or 0, reverse=True)
    dl_url = stream_urls[0]["url"] if stream_urls else ""

    return {
        "streams": stream_urls,
        "dl_url": dl_url,
    }


async def get_streams(slug: str) -> dict:
    """
    Get stream URLs for a video.
    Uses Playwright for reliable interception of HLS stream URLs.
    """
    return await _get_streams_with_playwright(slug)
