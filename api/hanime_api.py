"""Scrapers for hentai.tv (default source) and oppai.stream (4K quality source).

The public class name HanimeAPI is kept for backwards compatibility with the plugins.
All items carry a source prefix ('htv__' for hentai.tv, 'oppai__' for oppai.stream).
"""

import base64
import concurrent.futures
import html
import logging
import re
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

HENTAI_TV_BASE = "https://hentai.tv"
OPPAI_BASE = "https://oppai.stream"
BASE_URL = HENTAI_TV_BASE

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
    return "htv", slug


def _absolute(base: str, value: str) -> str:
    return urljoin(base + "/", html.unescape(value or ""))


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class HanimeAPI:
    """Content API wrapper for hentai.tv (default source) and oppai.stream (4K source)."""

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
        """Load saved Oppai cookies from MongoDB if available."""
        if cls._session_loaded:
            return
        from utils.db import get_db

        saved = await get_db().oppai_auth.find_one({"_id": "session"})
        if saved and saved.get("cookies"):
            cls._shared_session.cookies.update(saved["cookies"])
            log.info("Loaded saved Oppai session cookies")
        cls._session_loaded = True

    def login_oppai(self, username: str, password: str) -> tuple[bool, str, dict]:
        """Log in to Oppai.stream for account-locked content."""
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
        return False, "Oppai login rejected.", {}

    def logout_oppai(self) -> None:
        self.session.cookies.clear()

    def oppai_logged_in(self) -> bool:
        return bool(self.session.cookies.get_dict(domain="oppai.stream"))

    def search(self, query: str, page: int = 0, source: str | None = None) -> list[dict]:
        """Search hentai.tv, oppai.stream, or both concurrently."""
        results = []
        clean_q = (query or "").strip()
        if not clean_q:
            return results

        sources_to_run = []
        if source in (None, "both", "htv", "hentai_tv"):
            sources_to_run.append("htv")
        if source in (None, "both", "oppai", "oppai_stream"):
            sources_to_run.append("oppai")

        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(sources_to_run))) as executor:
            future_to_src = {}
            if "htv" in sources_to_run:
                future_to_src[executor.submit(self._search_hentai_tv, clean_q, page)] = "htv"
            if "oppai" in sources_to_run:
                future_to_src[executor.submit(self._search_oppai, clean_q, page)] = "oppai"

            for future in concurrent.futures.as_completed(future_to_src, timeout=12):
                src_name = future_to_src[future]
                try:
                    res = future.result()
                    if res:
                        results.extend(res)
                except Exception:
                    log.exception("%s search failed for query %r", src_name, clean_q)

        # Prioritize exact title matches
        query_clean = clean_q.lower()
        exact = [item for item in results if item.get("name", "").lower() == query_clean]
        non_exact = [item for item in results if item not in exact]

        combined = exact + non_exact
        return combined[:40]

    def search_hentai_tv(self, query: str, page: int = 0) -> list[dict]:
        return self._search_hentai_tv(query, page)

    def search_oppai(self, query: str, page: int = 0) -> list[dict]:
        return self._search_oppai(query, page)

    def details(self, slug: str) -> dict:
        source, raw_slug = _split_slug(slug)
        if source == "htv":
            return self._hentai_tv_details(raw_slug)
        return self._oppai_details(raw_slug)

    def get_streams(self, slug: str, target_quality: str | None = None) -> dict:
        info = self.details(slug)
        streams = list(info.get("streams", []))

        # If slug is from hentai.tv, cross-resolve working direct mirror streams (1080p/720p/4K)
        if slug.startswith("htv__"):
            try:
                title = info.get("title") or info.get("name") or ""
                ep = 1
                m_ep = re.search(r"-episode-(\d+)$", slug)
                if m_ep:
                    ep = int(m_ep.group(1))

                search_term = re.sub(r"—\s*", " ", title)
                search_term = re.sub(r"\b(Season|The Animation|OVA|Episode\s*\d+)\b", "", search_term, flags=re.I)
                search_term = re.sub(r"\s+", " ", search_term).strip()

                if search_term:
                    r_oppai = self.session.get(
                        f"{OPPAI_BASE}/actions/search.php",
                        params={"text": search_term, "limit": 40},
                        timeout=8,
                    )
                    soup = BeautifulSoup(r_oppai.text, "html.parser")
                    match_slug = None
                    for card in soup.select(".episode-shown"):
                        link = card.select_one('a[href*="/watch?e="]')
                        if link:
                            raw_e = link.get("href").split("?e=")[1].split("&")[0]
                            m = re.search(r"[-_](\d+)$", raw_e)
                            if m and int(m.group(1)) == ep:
                                match_slug = raw_e
                                break
                    if not match_slug and soup.select(".episode-shown"):
                        first_link = soup.select_one('.episode-shown a[href*="/watch?e="]')
                        if first_link:
                            match_slug = first_link.get("href").split("?e=")[1].split("&")[0]

                    if match_slug:
                        m_det = self.details(f"oppai__{match_slug}")
                        for st in m_det.get("streams", []):
                            if st.get("url") and not any(s.get("url") == st["url"] for s in streams):
                                streams.append(st)
            except Exception as e:
                log.debug("Mirror resolution failed for %s: %s", slug, e)

        # Sort quality preference: 4K/2160p (0) > 1080p (1) > 720p (2)
        quality_order = {"4k": 0, "2160": 0, "1080": 1, "720": 2}
        streams = sorted(
            streams,
            key=lambda item: (
                quality_order.get(str(item.get("height", "")).lower(), 3),
                -_int(item.get("height", 0))
            ),
        )

        # If a specific target quality was selected by user, prioritize it
        if target_quality:
            tq = str(target_quality).lower().strip()
            if tq in ("4k", "2160", "2160p"):
                matched = [s for s in streams if s.get("height", 0) >= 2160 or "4k" in s.get("label", "").lower()]
            elif tq in ("1080", "1080p"):
                matched = [s for s in streams if s.get("height", 0) == 1080 or "1080" in s.get("label", "").lower()]
            elif tq in ("720", "720p"):
                matched = [s for s in streams if s.get("height", 0) == 720 or "720" in s.get("label", "").lower()]
            else:
                matched = []
            if matched:
                rest = [s for s in streams if s not in matched]
                streams = matched + rest

        dl_url = streams[0].get("url", "") if streams else ""
        return {
            "streams": streams,
            "dl_url": dl_url,
            "sources": [
                {
                    "url": item["url"],
                    "label": item.get("label", f"{item.get('height', 1080)}p"),
                    "type": item.get("extension", "mp4"),
                    "referer": item.get("referer", ""),
                }
                for item in streams if item.get("url")
            ],
        }

    def _search_hentai_tv(self, query: str, page: int = 0) -> list[dict]:
        response = self.session.get(
            f"{HENTAI_TV_BASE}/api/search",
            params={"q": query, "limit": 40},
            timeout=10,
        )
        response.raise_for_status()
        results = []
        try:
            data = response.json()
        except Exception:
            return results

        videos = data.get("videos", [])
        if not videos:
            return results

        # Group videos by Series Album (titleSlug / title)
        series_map = {}
        for v in videos:
            raw_slug = v.get("slug")
            if not raw_slug:
                continue
            key = v.get("titleSlug") or v.get("title") or raw_slug
            if key not in series_map:
                cover = _absolute(HENTAI_TV_BASE, v.get("cover") or v.get("featureImage") or v.get("thumb"))
                series_map[key] = {
                    "slug": _prefixed("htv", raw_slug),
                    "title": v.get("title") or raw_slug,
                    "cover": cover,
                    "quality": v.get("quality") or "1080p",
                    "tags": v.get("tags", []),
                    "brand": v.get("brand", "hentai.tv"),
                    "description": v.get("description", ""),
                    "episodes": [],
                }
            series_map[key]["episodes"].append(v)

        for key, item in series_map.items():
            ep_count = len(item["episodes"])
            ep_label = f" ({ep_count} Ep)" if ep_count == 1 else f" ({ep_count} Eps)"
            results.append({
                "id": item["slug"],
                "slug": item["slug"],
                "name": item["title"],
                "title": f"{item['title']}{ep_label} [{item['quality']}]",
                "source": "hentai.tv",
                "quality": item["quality"],
                "cover_url": item["cover"],
                "poster_url": item["cover"],
                "cover": item["cover"],
                "tags": item["tags"],
                "views": 0,
                "brand": item["brand"],
                "description": item["description"],
                "episodes_count": ep_count,
            })
        return results

    def _search_oppai(self, query: str, page: int = 0) -> list[dict]:
        response = self.session.get(
            f"{OPPAI_BASE}/actions/search.php",
            params={
                "text": query,
                "order": "recent",
                "page": page + 1,
                "limit": 40,
                "genres": "",
                "blacklist": "",
                "studio": "",
                "ibt": 0,
                "swa": 0,
            },
            timeout=10,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        
        cards = soup.select(".episode-shown")
        if not cards:
            return []

        series_map = {}
        for card in cards:
            link = card.select_one("a[href*='/watch?e=']")
            if not link:
                continue
            raw_url = _absolute(OPPAI_BASE, link.get("href"))
            raw_slug = parse_qs(urlparse(raw_url).query).get("e", [""])[0]
            if not raw_slug:
                continue
            title_node = card.select_one("font.title")
            title = title_node.get_text(" ", strip=True) if title_node else card.get("name", raw_slug)
            
            # Extract series title by stripping trailing Ep/Episode number
            series_title = re.sub(r"\s+Ep\s+\d+$", "", title, flags=re.I).strip()
            series_title = re.sub(r"[-_]\d+$", "", series_title).replace("-", " ").strip()
            if not series_title:
                series_title = title

            cover_node = card.select_one("img.cover-img-in")
            cover = _absolute(OPPAI_BASE, cover_node.get("original") or cover_node.get("src")) if cover_node else ""
            tags = [tag.get_text(" ", strip=True) for tag in card.select(".tags-video .fh-tag")]
            tags.extend(x.strip() for x in (card.get("tags") or "").split(",") if x.strip() and x.strip() not in tags)
            
            is_4k = any(t.lower() == "4k" for t in tags) or "4k" in (card.get("tags") or "").lower()
            quality_label = "4K" if is_4k else "1080p"
            slug = _prefixed("oppai", raw_slug)

            if series_title not in series_map:
                series_map[series_title] = {
                    "slug": slug,
                    "title": series_title,
                    "quality": quality_label,
                    "cover": cover,
                    "tags": tags,
                    "brand": "oppai.stream (4K)" if is_4k else "oppai.stream",
                    "description": card.get("desc", ""),
                    "episodes": [],
                }
            if is_4k:
                series_map[series_title]["quality"] = "4K"
                series_map[series_title]["brand"] = "oppai.stream (4K)"
            series_map[series_title]["episodes"].append(raw_slug)

        results = []
        for series_title, item in series_map.items():
            ep_count = len(item["episodes"])
            ep_label = f" ({ep_count} Ep)" if ep_count == 1 else f" ({ep_count} Eps)"
            results.append({
                "id": item["slug"],
                "slug": item["slug"],
                "name": item["title"],
                "title": f"{item['title']}{ep_label} [{item['quality']}]",
                "source": "oppai.stream",
                "quality": item["quality"],
                "cover_url": item["cover"],
                "poster_url": item["cover"],
                "cover": item["cover"],
                "tags": item["tags"],
                "views": 0,
                "brand": item["brand"],
                "description": item["description"],
                "episodes_count": ep_count,
            })
        return results

    def _hentai_tv_details(self, raw_slug: str) -> dict:
        search_term = raw_slug.rsplit("-episode-", 1)[0]
        response = self.session.get(
            f"{HENTAI_TV_BASE}/api/search",
            params={"q": search_term, "limit": 40},
            timeout=15,
        )
        response.raise_for_status()
        videos = response.json().get("videos", [])
        video = next((item for item in videos if item.get("slug") == raw_slug), videos[0] if videos else {})
        if not video:
            return self._empty_details(_prefixed("htv", raw_slug), "hentai.tv")
        
        cover = _absolute(HENTAI_TV_BASE, video.get("cover") or video.get("featureImage") or video.get("thumb"))
        slug = _prefixed("htv", raw_slug)
        title_id = video.get("titleId")
        title_slug = video.get("titleSlug")
        
        # Collect all episodes in series
        series_videos = [
            item for item in videos 
            if (title_id and item.get("titleId") == title_id) or (title_slug and item.get("titleSlug") == title_slug)
        ]
        episodes = []
        for item in sorted(series_videos, key=lambda entry: _int(entry.get("ep"), 0)):
            ep_slug = _prefixed("htv", item.get("slug", ""))
            ep_num = item.get("ep", 1)
            episodes.append({
                "id": ep_slug,
                "slug": ep_slug,
                "name": f"Episode {ep_num}",
                "title": f"Episode {ep_num}",
                "ep": ep_num,
                "url": f"{HENTAI_TV_BASE}/watch/{item.get('slug', '')}",
            })
        if not episodes:
            episodes = [{
                "id": slug,
                "slug": slug,
                "name": f"Episode {video.get('ep', 1)}",
                "title": f"Episode {video.get('ep', 1)}",
                "ep": video.get("ep", 1),
                "url": f"{HENTAI_TV_BASE}/watch/{raw_slug}",
            }]

        streams = self._hentai_tv_streams(video.get("embedUrl", ""))
        return {
            "id": slug,
            "slug": slug,
            "name": video.get("title", raw_slug),
            "title": video.get("title", raw_slug),
            "description": video.get("description", ""),
            "summary": video.get("description", ""),
            "poster_url": cover,
            "cover_url": cover,
            "poster": cover,
            "cover": cover,
            "tags": video.get("tags", []),
            "genres": video.get("tags", []),
            "brand": "hentai.tv (Default Source)",
            "views": video.get("views", 0),
            "likes": video.get("likes", 0),
            "episodes": episodes,
            "totalEpisodes": len(episodes),
            "url": f"{HENTAI_TV_BASE}/watch/{raw_slug}",
            "streams": streams,
        }

    def _hentai_tv_streams(self, embed_url: str) -> list[dict]:
        if not embed_url:
            return []
        try:
            response = self.session.get(embed_url, headers={"Referer": f"{HENTAI_TV_BASE}/"}, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            item = soup.select_one(".servers li[data-id]")
            if not item:
                return []
            player_url = _absolute("https://nhplayer.com", item.get("data-id"))
            encoded = parse_qs(urlparse(player_url).query).get("vid", [""])[0]
            decoded = base64.b64decode(encoded).decode("utf-8", errors="ignore")
            video_url = decoded.split("|", 1)[0]
            if video_url:
                return [{
                    "url": video_url,
                    "height": 1080,
                    "kind": "mp4",
                    "extension": "mp4",
                    "label": "1080p (Full HD)",
                    "is_downloadable": True,
                    "referer": "https://hentai.tv/",
                }]
        except Exception as e:
            log.warning("Failed to resolve hentai.tv streams from embedUrl=%s: %s", embed_url, e)
        return []

    def _oppai_details(self, raw_slug: str) -> dict:
        response = self.session.get(f"{OPPAI_BASE}/watch", params={"e": raw_slug}, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        
        if "locked.php" in response.url or soup.select_one(".login-required, .requires-login"):
            log.info("Oppai episode requires login: %s", raw_slug)
            return self._empty_details(_prefixed("oppai", raw_slug), "oppai.stream (4K)")

        title_el = soup.select_one("h1")
        title_text = title_el.get_text(" ", strip=True) if title_el else raw_slug.replace("-", " ").title()
        title_text = re.sub(r"\s+Ep\s+\d+$", "", title_text, flags=re.I)
        
        description_el = soup.select_one(".description")
        description = description_el.get_text(" ", strip=True) if description_el else ""
        
        poster_el = soup.select_one("video[poster]")
        poster = _absolute(OPPAI_BASE, poster_el.get("poster", "")) if poster_el else ""
        
        tags = [tag.get_text(" ", strip=True) for tag in soup.select(".tags .tag h5")]

        # Extract available stream resolutions
        default_source_el = soup.select_one("video#episode source") or soup.select_one("video source")
        default_src = default_source_el.get("src") if default_source_el else ""

        streams = []
        res_buttons = soup.select(".swap-res-ios[resolution]") or soup.select(".swap-resolution[resolution]")
        for btn in res_buttons:
            res = btn.get("resolution", "").lower().strip()
            if not res or res in ("auto",):
                continue
            
            ext = "webm" if res == "4k" else "mp4"
            for cls in btn.get("class", []):
                if cls.startswith("rtyp-"):
                    ext = cls.replace("rtyp-", "").lower()

            if default_src:
                stream_url = re.sub(r"/(?:720|1080|4k)/", f"/{res}/", default_src, flags=re.I)
                stream_url = re.sub(r"\.\w+(\?.*)?$", f".{ext}", stream_url)
            else:
                stream_url = ""

            height_val = 2160 if res == "4k" else (_int(res) if res.isdigit() else 1080)
            streams.append({
                "url": stream_url,
                "height": height_val,
                "kind": ext,
                "extension": ext,
                "label": "4K (2160p)" if res == "4k" else f"{res}p",
                "is_downloadable": True,
                "referer": "https://oppai.stream/",
            })

        if not streams and default_src:
            streams.append({
                "url": default_src,
                "height": 720,
                "kind": "mp4",
                "extension": "mp4",
                "label": "720p",
                "is_downloadable": True,
                "referer": "https://oppai.stream/",
            })

        streams.sort(key=lambda s: -s["height"])

        # Sibling episodes lookup
        episodes = []
        base_name = re.sub(r"[-_]\d+$", "", raw_slug)
        search_q = base_name.replace("-", " ").strip()
        try:
            r_search = self.session.get(
                f"{OPPAI_BASE}/actions/search.php",
                params={"text": search_q, "order": "recent", "limit": 40},
                timeout=10,
            )
            s_soup = BeautifulSoup(r_search.text, "html.parser")
            for card in s_soup.select(".episode-shown"):
                link = card.select_one("a[href*='/watch?e=']")
                if not link:
                    continue
                ep_url = urljoin(OPPAI_BASE, link.get("href"))
                ep_raw_slug = parse_qs(urlparse(ep_url).query).get("e", [""])[0]
                if not ep_raw_slug:
                    continue
                ep_match = re.search(r"[-_](\d+)$", ep_raw_slug)
                ep_num = _int(ep_match.group(1), 1) if ep_match else 1
                ep_slug = _prefixed("oppai", ep_raw_slug)
                episodes.append({
                    "id": ep_slug,
                    "slug": ep_slug,
                    "name": f"Episode {ep_num}",
                    "title": f"Episode {ep_num}",
                    "ep": ep_num,
                    "url": ep_url,
                })
            episodes.sort(key=lambda x: x["ep"])
        except Exception:
            pass

        slug = _prefixed("oppai", raw_slug)
        if not episodes:
            episodes = [{
                "id": slug,
                "slug": slug,
                "name": "Episode 1",
                "title": "Episode 1",
                "ep": 1,
                "url": f"{OPPAI_BASE}/watch?e={raw_slug}",
            }]

        is_4k = any(s["height"] >= 2160 for s in streams) or any(t.lower() == "4k" for t in tags)
        brand = "oppai.stream (4K)" if is_4k else "oppai.stream"

        return {
            "id": slug,
            "slug": slug,
            "name": title_text,
            "title": title_text,
            "description": description,
            "summary": description,
            "poster_url": poster,
            "cover_url": poster,
            "poster": poster,
            "cover": poster,
            "tags": tags,
            "genres": tags,
            "brand": brand,
            "views": 0,
            "likes": 0,
            "streams": streams,
            "episodes": episodes,
            "totalEpisodes": len(episodes),
            "url": f"{OPPAI_BASE}/watch?e={raw_slug}",
        }

    @staticmethod
    def _empty_details(slug: str, brand: str) -> dict:
        title = slug.split("__", 1)[-1].replace("-", " ").title()
        return {
            "id": slug,
            "slug": slug,
            "name": title,
            "title": title,
            "description": "",
            "summary": "",
            "poster_url": "",
            "cover_url": "",
            "poster": "",
            "cover": "",
            "tags": [],
            "genres": [],
            "brand": brand,
            "views": 0,
            "likes": 0,
            "streams": [],
            "episodes": [],
            "totalEpisodes": 0,
            "url": "",
        }
