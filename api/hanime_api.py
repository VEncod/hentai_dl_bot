"""Scrapers for hentaicity.com (default source) and oppai.stream (4K quality source).

The public class name HanimeAPI is kept for backwards compatibility with the plugins.
All items carry a source prefix ('hcity__' for hentaicity.com, 'oppai__' for oppai.stream).
"""

import base64
import concurrent.futures
import html
import logging
import re
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

HENTAICITY_BASE = "https://www.hentaicity.com"
OPPAI_BASE = "https://oppai.stream"
BASE_URL = HENTAICITY_BASE

SOURCE_PREFIXES = {
    "hcity": "hentaicity.com",
    "htv": "hentaicity.com",
    "oppai": "oppai.stream",
}


def _prefixed(source: str, value: str) -> str:
    return f"{source}__{value}"


def _split_slug(slug: str) -> tuple[str, str]:
    for prefix in SOURCE_PREFIXES:
        marker = f"{prefix}__"
        if slug.startswith(marker):
            return prefix, slug[len(marker):]
    return "hcity", slug


def _absolute(base: str, value: str) -> str:
    return urljoin(base + "/", html.unescape(value or ""))


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clean_tokens(title: str) -> list[str]:
    """Extract clean lowercase alphanumeric words from a title."""
    return re.findall(r"[a-zA-Z0-9]+", (title or "").lower())


def _are_same_series(title1: str, title2: str) -> bool:
    """Determine if two titles refer to the same hentai series.
    
    Considers titles the same if:
    - 3 or 4 same words match, OR
    - The first 2-3 words are identical (e.g. 'Imaizumi Takes all the Girls' vs 'Imaizumi Takes all the Women'), OR
    - One title is a substring / sub-phrase of the other.
    """
    t1 = (title1 or "").strip().lower()
    t2 = (title2 or "").strip().lower()
    if t1 == t2:
        return True

    words1 = _clean_tokens(t1)
    words2 = _clean_tokens(t2)
    if not words1 or not words2:
        return False

    min_len = min(len(words1), len(words2))
    # Check if first 3 words match (e.g. 'imaizumi takes all')
    if min_len >= 3 and words1[:3] == words2[:3]:
        return True
    if min_len == 2 and words1[:2] == words2[:2]:
        return True
    if min_len == 1 and words1[0] == words2[0] and len(words1[0]) >= 4:
        return True

    # Check if 3 or more words match anywhere in the titles
    common_words = set(words1) & set(words2)
    if len(common_words) >= 3:
        return True

    # Sub-phrase match
    if (len(t1) >= 5 and t1 in t2) or (len(t2) >= 5 and t2 in t1):
        return True

    return False


class HanimeAPI:
    """Content API wrapper for hentaicity.com (default source) and oppai.stream (4K source)."""

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
        """Search hentaicity.com, oppai.stream, or both concurrently."""
        results = []
        clean_q = (query or "").strip()
        if not clean_q:
            return results

        sources_to_run = []
        if source in (None, "both", "hcity", "htv", "hentaicity", "hentai_tv"):
            sources_to_run.append("hcity")
        if source in (None, "both", "oppai", "oppai_stream"):
            sources_to_run.append("oppai")

        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(sources_to_run))) as executor:
            future_to_src = {}
            if "hcity" in sources_to_run:
                future_to_src[executor.submit(self._search_hentaicity, clean_q, page)] = "hcity"
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

    def search_hentaicity(self, query: str, page: int = 0) -> list[dict]:
        return self._search_hentaicity(query, page)

    def search_oppai(self, query: str, page: int = 0) -> list[dict]:
        return self._search_oppai(query, page)

    def details(self, slug: str) -> dict:
        source, raw_slug = _split_slug(slug)
        if source in ("hcity", "htv"):
            return self._hentaicity_details(raw_slug)
        return self._oppai_details(raw_slug)

    def get_streams(self, slug: str, target_quality: str | None = None) -> dict:
        info = self.details(slug)
        streams = list(info.get("streams", []))

        # Sort quality preference: 4K/2160p (0) > 1080p (1) > 720p (2) > 480p (3)
        quality_order = {"4k": 0, "2160": 0, "1080": 1, "720": 2, "480": 3}
        streams = sorted(
            streams,
            key=lambda item: (
                quality_order.get(str(item.get("height", "")).lower(), 4),
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
            elif tq in ("480", "480p"):
                matched = [s for s in streams if s.get("height", 0) == 480 or "480" in s.get("label", "").lower()]
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
                    "referer": item.get("referer", HENTAICITY_BASE),
                }
                for item in streams if item.get("url")
            ],
        }

    def _search_hentaicity(self, query: str, page: int = 0) -> list[dict]:
        url = f"{HENTAICITY_BASE}/search/videos/{quote_plus(query)}"
        if page > 0:
            url += f"/{page + 1}"

        response = self.session.get(url, headers={"Referer": f"{HENTAICITY_BASE}/"}, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        series_list = []
        for item in soup.select(".item"):
            a = item.select_one('a[href*="/video/"]')
            img = item.select_one("img")
            if not a or not img:
                continue

            href = a.get("href", "")
            m_slug = re.search(r"/video/(.*?)\.html", href)
            if not m_slug:
                continue
            raw_slug = m_slug.group(1)

            full_title = img.get("alt") or a.get("title") or a.get_text(" ", strip=True)
            m_ser = re.search(r"^(.*?)(?:\s+\d+\s*[-:]|\s+\d+$)", full_title)
            series_name = m_ser.group(1).strip() if m_ser else full_title
            series_name = re.sub(r"\s+[-:]\s*$", "", series_name).strip()

            m_ep = re.search(r"(?:^|\s)(\d+)(?:\s*[-:]|$)", full_title)
            ep_num = int(m_ep.group(1)) if m_ep else 1

            cover = img.get("src") or img.get("data-src") or img.get("data-original") or ""
            if cover.startswith("//"):
                cover = "https:" + cover

            slug = _prefixed("hcity", raw_slug)
            ep_entry = {
                "slug": slug,
                "name": f"Episode {ep_num}",
                "ep": ep_num,
                "url": f"{HENTAICITY_BASE}/video/{raw_slug}.html",
            }

            # Find matching series using 3-4 word similarity / prefix matching
            matched = None
            for s_item in series_list:
                if _are_same_series(series_name, s_item["title"]):
                    matched = s_item
                    break

            if matched:
                if not any(e["slug"] == slug or e["ep"] == ep_num for e in matched["episodes"]):
                    matched["episodes"].append(ep_entry)
            else:
                series_list.append({
                    "slug": slug,
                    "title": series_name,
                    "cover": cover,
                    "episodes": [ep_entry],
                })

        results = []
        for data in series_list:
            data["episodes"].sort(key=lambda x: x["ep"])
            ep_count = len(data["episodes"])
            ep_label = f" ({ep_count} Ep)" if ep_count == 1 else f" ({ep_count} Eps)"
            results.append({
                "id": data["slug"],
                "slug": data["slug"],
                "name": data["title"],
                "title": f"{data['title']}{ep_label} [1080p]",
                "source": "hentaicity.com",
                "quality": "1080p",
                "cover_url": data["cover"],
                "poster_url": data["cover"],
                "cover": data["cover"],
                "tags": [],
                "views": 0,
                "brand": "hentaicity.com",
                "description": "",
                "episodes_count": ep_count,
            })
        return results

    def _hentaicity_details(self, raw_slug: str) -> dict:
        video_url = f"{HENTAICITY_BASE}/video/{raw_slug}.html" if not raw_slug.startswith("http") else raw_slug
        response = self.session.get(video_url, headers={"Referer": f"{HENTAICITY_BASE}/"}, timeout=12)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        h1 = soup.select_one("h1")
        full_title = h1.get_text(" ", strip=True) if h1 else raw_slug.replace("-", " ").title()

        m_ep = re.search(r"^(.*?)\s+(\d+)\s*[-:]\s*(.*)$", full_title)
        if m_ep:
            series_name = m_ep.group(1).strip()
            ep_num = int(m_ep.group(2))
        else:
            m_ep2 = re.search(r"^(.*?)\s+(\d+)$", full_title)
            if m_ep2:
                series_name = m_ep2.group(1).strip()
                ep_num = int(m_ep2.group(2))
            else:
                series_name = full_title
                ep_num = 1

        poster = ""
        v_tag = soup.select_one("video#video-id, video")
        if v_tag and v_tag.get("poster"):
            poster = v_tag["poster"]
        if not poster:
            p_img = soup.select_one("img[src*='/videos/']")
            if p_img:
                poster = p_img.get("src") or ""

        tags = [a.get_text(strip=True) for a in soup.select('a[href*="/tags/video/"]')]
        desc_el = soup.select_one(".video-description, .description, p")
        description = desc_el.get_text(" ", strip=True) if desc_el else "Watch in high quality on HentaiCity."

        # Extract streams
        streams = []
        source_tag = soup.select_one('video source[src*=".m3u8"], video source')
        m3u8_url = source_tag.get("src") if source_tag else ""

        m_path = re.search(r"/flv/(\d+/\d+)/", response.text)
        if m_path:
            path = m_path.group(1)
            if "1080p" in m3u8_url or "1080p" in response.text:
                streams.append({
                    "url": f"{HENTAICITY_BASE}/flv/{path}/1080p.mp4",
                    "height": 1080,
                    "kind": "mp4",
                    "extension": "mp4",
                    "label": "1080p (Full HD)",
                    "referer": f"{HENTAICITY_BASE}/",
                })
            if "720p" in m3u8_url or "720p" in response.text:
                streams.append({
                    "url": f"{HENTAICITY_BASE}/flv/{path}/720p.mp4",
                    "height": 720,
                    "kind": "mp4",
                    "extension": "mp4",
                    "label": "720p (HD)",
                    "referer": f"{HENTAICITY_BASE}/",
                })
            if "480p" in m3u8_url or "480p" in response.text:
                streams.append({
                    "url": f"{HENTAICITY_BASE}/flv/{path}/480p.mp4",
                    "height": 480,
                    "kind": "mp4",
                    "extension": "mp4",
                    "label": "480p (SD)",
                    "referer": f"{HENTAICITY_BASE}/",
                })

        if m3u8_url:
            streams.append({
                "url": m3u8_url,
                "height": 1080,
                "kind": "hls",
                "extension": "mp4",
                "label": "1080p (HLS)",
                "referer": f"{HENTAICITY_BASE}/",
            })

        # Sibling episodes lookup using 3-4 word similarity
        episodes = []
        words = _clean_tokens(series_name)
        search_q = " ".join(words[:2]) if len(words) >= 2 else (words[0] if words else series_name)
        try:
            r_search = self.session.get(
                f"{HENTAICITY_BASE}/search/videos/{quote_plus(search_q)}",
                headers={"Referer": f"{HENTAICITY_BASE}/"},
                timeout=8,
            )
            s_soup = BeautifulSoup(r_search.text, "html.parser")
            for it in s_soup.select(".item"):
                link = it.select_one('a[href*="/video/"]')
                img_it = it.select_one("img")
                if not link:
                    continue
                href = link.get("href", "")
                m_it = re.search(r"/video/(.*?)\.html", href)
                if not m_it:
                    continue
                ep_slug_raw = m_it.group(1)
                title_it = (img_it.get("alt") if img_it else "") or link.get("title") or link.get_text(" ", strip=True)

                m_ser_it = re.search(r"^(.*?)(?:\s+\d+\s*[-:]|\s+\d+$)", title_it)
                s_name_it = m_ser_it.group(1).strip() if m_ser_it else title_it

                # Match series with 3-4 same word logic
                if not _are_same_series(series_name, s_name_it):
                    continue

                m_ep_it = re.search(r"(?:^|\s)(\d+)(?:\s*[-:]|$)", title_it)
                ep_n = int(m_ep_it.group(1)) if m_ep_it else 1

                ep_s = _prefixed("hcity", ep_slug_raw)
                if not any(e["slug"] == ep_s or e["ep"] == ep_n for e in episodes):
                    episodes.append({
                        "id": ep_s,
                        "slug": ep_s,
                        "name": f"Episode {ep_n}",
                        "title": f"Episode {ep_n}",
                        "ep": ep_n,
                        "url": f"{HENTAICITY_BASE}/video/{ep_slug_raw}.html",
                    })
            episodes.sort(key=lambda x: x["ep"])
        except Exception as e:
            log.debug("HentaiCity sibling episodes lookup failed: %s", e)

        slug = _prefixed("hcity", raw_slug)
        if not episodes:
            episodes = [{
                "id": slug,
                "slug": slug,
                "name": f"Episode {ep_num}",
                "title": f"Episode {ep_num}",
                "ep": ep_num,
                "url": video_url,
            }]

        return {
            "id": slug,
            "slug": slug,
            "name": full_title,
            "title": full_title,
            "description": description,
            "summary": description,
            "poster_url": poster,
            "cover_url": poster,
            "poster": poster,
            "cover": poster,
            "tags": tags,
            "genres": tags,
            "brand": "hentaicity.com",
            "views": 0,
            "likes": 0,
            "streams": streams,
            "episodes": episodes,
            "totalEpisodes": len(episodes),
            "url": video_url,
        }

    def _search_oppai(self, query: str, page: int = 0) -> list[dict]:
        response = self.session.get(
            f"{OPPAI_BASE}/actions/search.php",
            params={
                "text": query,
                "order": "recent",
                "limit": 40,
                "offset": page * 40,
                "min": 0,
                "max": 0,
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

        series_list = []
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

            matched = None
            for s_item in series_list:
                if _are_same_series(series_title, s_item["title"]):
                    matched = s_item
                    break

            if matched:
                if raw_slug not in matched["episodes"]:
                    matched["episodes"].append(raw_slug)
                if is_4k:
                    matched["quality"] = "4K"
                    matched["brand"] = "oppai.stream (4K)"
            else:
                series_list.append({
                    "slug": slug,
                    "title": series_title,
                    "quality": quality_label,
                    "cover": cover,
                    "tags": tags,
                    "brand": "oppai.stream (4K)" if is_4k else "oppai.stream",
                    "description": card.get("desc", ""),
                    "episodes": [raw_slug],
                })

        results = []
        for item in series_list:
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
                "referer": f"{OPPAI_BASE}/",
            })

        if not streams and default_src:
            streams.append({
                "url": default_src,
                "height": 720,
                "kind": "mp4",
                "extension": "mp4",
                "label": "720p",
                "is_downloadable": True,
                "referer": f"{OPPAI_BASE}/",
            })

        streams.sort(key=lambda s: -s["height"])

        # Sibling episodes lookup using similarity matching
        episodes = []
        tokens = _clean_tokens(raw_slug)
        search_q = tokens[0] if tokens else raw_slug.split("-")[0]
        try:
            r_search = self.session.get(
                f"{OPPAI_BASE}/actions/search.php",
                params={"text": search_q, "order": "recent", "limit": 40},
                timeout=8,
            )
            s_soup = BeautifulSoup(r_search.text, "html.parser")
            clean_raw = raw_slug.replace("~", " ").replace("-", " ")
            for card in s_soup.select(".episode-shown"):
                link = card.select_one("a[href*='/watch?e=']")
                if not link:
                    continue
                ep_url = urljoin(OPPAI_BASE, link.get("href"))
                ep_raw_slug = parse_qs(urlparse(ep_url).query).get("e", [""])[0]
                if not ep_raw_slug:
                    continue

                title_node = card.select_one("font.title")
                card_title = title_node.get_text(" ", strip=True) if title_node else card.get("name", ep_raw_slug)

                clean_ep = ep_raw_slug.replace("~", " ").replace("-", " ")
                if not _are_same_series(card_title, clean_raw) and not _are_same_series(clean_ep, clean_raw):
                    continue

                ep_match = re.search(r"[-_](\d+)$", ep_raw_slug)
                ep_num = _int(ep_match.group(1), 1) if ep_match else 1
                ep_slug = _prefixed("oppai", ep_raw_slug)
                if not any(e["slug"] == ep_slug or e["ep"] == ep_num for e in episodes):
                    episodes.append({
                        "id": ep_slug,
                        "slug": ep_slug,
                        "name": f"Episode {ep_num}",
                        "title": f"Episode {ep_num}",
                        "ep": ep_num,
                        "url": ep_url,
                    })
            episodes.sort(key=lambda x: x["ep"])
        except Exception as e:
            log.debug("Oppai sibling episodes lookup failed: %s", e)

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
