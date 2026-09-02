"""Scrapers for the bot's supported content sources.

The public class name is kept for compatibility with the existing plugins.
Results carry a source prefix in their slug because the two sites use
different identifiers and can contain the same title.
"""

import base64
import html
import logging
import re
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

HENTAI_TV_BASE = "https://hentai.tv"
OPPAI_BASE = "https://oppai.stream"
BASE_URL = OPPAI_BASE

SOURCE_PREFIXES = {
    "htv": "hentai.tv",
    "oppai": "oppai.stream",
}


def _prefixed(source: str, value: str) -> str:
    return f"{source}__{value}"


def _split_slug(slug: str) -> tuple[str, str]:
    for prefix in SOURCE_PREFIXES:
        marker = f"{prefix}__"
        if slug.startswith(marker):
            return prefix, slug[len(marker):]
    return "oppai", slug


def _absolute(base: str, value: str) -> str:
    return urljoin(base + "/", html.unescape(value or ""))


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class HanimeAPI:
    """Search and resolve videos from hentai.tv and oppai.stream."""

    _shared_session = requests.Session()
    _session_loaded = False

    def __init__(self):
        self.session = self._shared_session
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })

    @classmethod
    async def load_saved_session(cls) -> None:
        """Load the bot-wide Oppai cookie jar after MongoDB is initialized."""
        if cls._session_loaded:
            return
        from utils.db import get_db

        saved = await get_db().oppai_auth.find_one({"_id": "session"})
        if saved and saved.get("cookies"):
            cls._shared_session.cookies.update(saved["cookies"])
            log.info("Loaded saved Oppai session")
        cls._session_loaded = True

    def login_oppai(self, username: str, password: str) -> tuple[bool, str, dict]:
        """Log in to Oppai and return success, message, and serializable cookies."""
        response = self.session.post(
            f"{OPPAI_BASE}/actions/auth.php?a=l",
            data={"username": username, "password": password},
            headers={"Referer": f"{OPPAI_BASE}/auth.php?a=login"},
            timeout=20,
            allow_redirects=True,
        )
        page = BeautifulSoup(response.text, "html.parser")
        text = page.get_text(" ", strip=True).lower()
        failed = any(term in text for term in ("incorrect", "invalid", "wrong password", "login failed"))
        success = response.url.rstrip("/") != f"{OPPAI_BASE}/auth.php?a=login" and not failed
        cookies = self.session.cookies.get_dict(domain="oppai.stream")
        if success and cookies:
            return True, "Oppai login successful.", cookies
        return False, "Oppai rejected the login. Cloudflare verification or invalid credentials may be required.", {}

    def logout_oppai(self) -> None:
        self.session.cookies.clear()

    def oppai_logged_in(self) -> bool:
        return bool(self.session.cookies.get_dict(domain="oppai.stream"))

    def search(self, query: str, page: int = 0) -> list[dict]:
        """Search both configured sources and return a common result shape."""
        results = []
        for source, searcher in (("htv", self._search_hentai_tv), ("oppai", self._search_oppai)):
            try:
                results.extend(searcher(query, page))
            except Exception:
                log.exception("%s search failed for %r", SOURCE_PREFIXES[source], query)

        # Prefer exact title matches and remove duplicate source records.
        query_tokens = {token for token in re.findall(r"\w+", query.lower()) if len(token) > 1}
        exact = [item for item in results if item["title"].lower() == query.lower()]
        if exact:
            results = exact + [item for item in results if item not in exact]
        elif query_tokens:
            relevant = [
                item for item in results
                if any(token in item["title"].lower() or token in item["slug"].lower() for token in query_tokens)
            ]
            if relevant:
                results = relevant
        return results[:40]

    def details(self, slug: str) -> dict:
        source, raw_slug = _split_slug(slug)
        if source == "htv":
            return self._hentai_tv_details(raw_slug)
        return self._oppai_details(raw_slug)

    def get_streams(self, slug: str) -> dict:
        info = self.details(slug)
        streams = info.get("streams", [])
        # The source sites expose 4k, 1080p, and 720p independently. Keep
        # quality preference deterministic and fall back when 4k is absent.
        quality_order = {"4k": 0, "2160": 0, "1080": 1, "720": 2}
        streams = sorted(
            streams,
            key=lambda item: (quality_order.get(str(item.get("height", "")).lower(), 3),
                              -_int(item.get("height"))),
        )
        dl_url = streams[0].get("url", "") if streams else ""
        return {
            "streams": streams,
            "dl_url": dl_url,
            "sources": [
                {
                    "url": item["url"],
                    "label": item.get("label", f"{item.get('height', 720)}p"),
                    "type": item.get("extension", ""),
                }
                for item in streams if item.get("url")
            ],
        }

    def _search_hentai_tv(self, query: str, page: int = 0) -> list[dict]:
        response = self.session.get(
            f"{HENTAI_TV_BASE}/api/search",
            params={"q": query, "limit": 40},
            timeout=15,
        )
        response.raise_for_status()
        results = []
        for video in response.json().get("videos", []):
            raw_slug = video.get("slug")
            if not raw_slug:
                continue
            cover = _absolute(HENTAI_TV_BASE, video.get("cover") or video.get("featureImage") or video.get("thumb"))
            slug = _prefixed("htv", raw_slug)
            results.append({
                "id": slug, "slug": slug,
                "name": video.get("title") or raw_slug,
                "title": video.get("title") or raw_slug,
                "cover_url": cover, "poster_url": cover, "cover": cover,
                "tags": video.get("tags", []), "views": video.get("views", 0),
                "brand": video.get("brand", "hentai.tv"),
                "description": video.get("description", ""),
                "url": f"{HENTAI_TV_BASE}/watch/{raw_slug}",
            })
        return results

    def _search_oppai(self, query: str, page: int = 0) -> list[dict]:
        response = self.session.get(
            f"{OPPAI_BASE}/actions/search.php",
            params={"text": query, "order": "recent", "page": page + 1, "limit": 40,
                    "genres": "", "blacklist": "", "studio": "", "ibt": 0, "swa": 0},
            timeout=15,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        results = []
        for card in soup.select(".episode-shown"):
            link = card.select_one("a[href*='/watch?e=']")
            if not link:
                continue
            raw_url = _absolute(OPPAI_BASE, link.get("href"))
            raw_slug = parse_qs(urlparse(raw_url).query).get("e", [""])[0]
            if not raw_slug:
                continue
            title_node = card.select_one("font.title")
            title = title_node.get_text(" ", strip=True) if title_node else card.get("name", raw_slug)
            cover_node = card.select_one("img.cover-img-in")
            cover = _absolute(OPPAI_BASE, cover_node.get("original") or cover_node.get("src")) if cover_node else ""
            tags = [tag.get_text(" ", strip=True) for tag in card.select(".tags-video .fh-tag")]
            tags.extend(x.strip() for x in (card.get("tags") or "").split(",") if x.strip() and x.strip() not in tags)
            slug = _prefixed("oppai", raw_slug)
            results.append({
                "id": slug, "slug": slug, "name": title, "title": title,
                "cover_url": cover, "poster_url": cover, "cover": cover,
                "tags": tags, "views": 0, "brand": "oppai.stream",
                "description": card.get("desc", ""), "url": raw_url,
            })
        return results

    def _hentai_tv_details(self, raw_slug: str) -> dict:
        response = self.session.get(
            f"{HENTAI_TV_BASE}/api/search",
            params={"q": raw_slug.rsplit("-episode-", 1)[0], "limit": 40},
            timeout=15,
        )
        response.raise_for_status()
        videos = response.json().get("videos", [])
        video = next((item for item in videos if item.get("slug") == raw_slug), videos[0] if videos else {})
        if not video:
            return self._empty_details(_prefixed("htv", raw_slug), "hentai.tv")
        cover = _absolute(HENTAI_TV_BASE, video.get("cover") or video.get("featureImage") or video.get("thumb"))
        slug = _prefixed("htv", raw_slug)
        series_videos = [item for item in videos if item.get("titleId") == video.get("titleId")]
        episodes = []
        for item in sorted(series_videos, key=lambda entry: _int(entry.get("ep"), 0)):
            episode_slug = _prefixed("htv", item.get("slug", ""))
            episodes.append({
                "id": episode_slug,
                "slug": episode_slug,
                "name": f"Episode {item.get('ep', 1)}",
                "title": f"Episode {item.get('ep', 1)}",
                "url": f"{HENTAI_TV_BASE}/watch/{item.get('slug', '')}",
            })
        if not episodes:
            episodes = [{"id": slug, "slug": slug, "name": f"Episode {video.get('ep', 1)}",
                         "title": f"Episode {video.get('ep', 1)}", "url": f"{HENTAI_TV_BASE}/watch/{raw_slug}"}]
        return {
            "id": slug, "slug": slug, "name": video.get("title", raw_slug),
            "title": video.get("title", raw_slug), "description": video.get("description", ""),
            "summary": video.get("description", ""), "poster_url": cover, "cover_url": cover,
            "poster": cover, "cover": cover, "tags": video.get("tags", []),
            "genres": video.get("tags", []), "brand": video.get("brand", "hentai.tv"),
            "views": video.get("views", 0), "likes": video.get("likes", 0),
            "episodes": episodes,
            "totalEpisodes": len(episodes), "url": f"{HENTAI_TV_BASE}/watch/{raw_slug}",
            "streams": self._hentai_tv_streams(video.get("embedUrl", "")),
        }

    def _hentai_tv_streams(self, embed_url: str) -> list[dict]:
        if not embed_url:
            return []
        response = self.session.get(embed_url, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        item = soup.select_one(".servers li[data-id]")
        if not item:
            return []
        player_url = _absolute("https://nhplayer.com", item.get("data-id"))
        encoded = parse_qs(urlparse(player_url).query).get("vid", [""])[0]
        try:
            decoded = base64.b64decode(encoded).decode("utf-8")
            video_url = decoded.split("|", 1)[0]
        except Exception:
            return []
        return [{"url": video_url, "height": 1080, "kind": "mp4", "extension": "mp4", "label": "1080p", "is_downloadable": True}]

    def _oppai_details(self, raw_slug: str) -> dict:
        response = self.session.get(f"{OPPAI_BASE}/watch", params={"e": raw_slug}, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        if "locked.php" in response.url or soup.select_one(".login-required, .requires-login"):
            log.info("Oppai episode requires login: %s", raw_slug)
            return self._empty_details(_prefixed("oppai", raw_slug), "oppai.stream")
        title = soup.select_one("h1")
        title_text = title.get_text(" ", strip=True) if title else raw_slug.replace("-", " ").title()
        title_text = re.sub(r"\s+Ep\s+\d+$", "", title_text, flags=re.I)
        description = soup.select_one(".description")
        poster = soup.select_one("video[poster]")
        tags = [tag.get_text(" ", strip=True) for tag in soup.select(".tags .tag h5")]
        streams = []
        for resolution, source_url in re.findall(r'["\'](4k|1080|720)["\']\s*:\s*["\']([^"\']+)', response.text, re.I):
            source_url = html.unescape(source_url).replace("\\/", "/")
            if any(item["url"] == source_url for item in streams):
                continue
            streams.append({"url": html.unescape(source_url), "height": resolution, "kind": "mp4" if resolution != "4k" else "webm",
                            "extension": "mp4" if resolution != "4k" else "webm", "label": "4K" if resolution == "4k" else f"{resolution}p", "is_downloadable": True})
        if not streams:
            source = soup.select_one("video#episode source")
            if source and source.get("src"):
                streams.append({"url": source["src"], "height": 720, "kind": "mp4", "extension": "mp4", "label": "720p", "is_downloadable": True})
        slug = _prefixed("oppai", raw_slug)
        episode_match = re.search(r"(?:-|%20)(\d+)$", raw_slug)
        episode = _int(episode_match.group(1), 1) if episode_match else 1
        return {
            "id": slug, "slug": slug, "name": title_text, "title": title_text,
            "description": description.get_text(" ", strip=True) if description else "",
            "summary": description.get_text(" ", strip=True) if description else "",
            "poster_url": poster.get("poster", "") if poster else "", "cover_url": poster.get("poster", "") if poster else "",
            "poster": poster.get("poster", "") if poster else "", "cover": poster.get("poster", "") if poster else "",
            "tags": tags, "genres": tags, "brand": "oppai.stream", "views": 0, "likes": 0,
            "streams": streams, "episodes": [{"id": slug, "slug": slug, "name": f"Episode {episode}", "title": f"Episode {episode}", "url": response.url}],
            "totalEpisodes": 1, "url": response.url,
        }

    @staticmethod
    def _empty_details(slug: str, brand: str) -> dict:
        title = slug.split("__", 1)[-1].replace("-", " ").title()
        return {"id": slug, "slug": slug, "name": title, "title": title, "description": "", "summary": "",
                "poster_url": "", "cover_url": "", "poster": "", "cover": "", "tags": [], "genres": [],
                "brand": brand, "views": 0, "likes": 0, "streams": [], "episodes": [], "totalEpisodes": 0, "url": ""}
