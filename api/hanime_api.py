"""
HentaiHaven Multi-Source API Wrapper.

100% Native Integration with HentaiHaven (hentaihaven.xxx).
Provides:
- Search on hentaihaven.xxx
- Details, metadata, posters, episodes on hentaihaven.xxx
- Direct decrypted HLS (.m3u8) / MP4 stream extraction from hentaihaven.xxx player logic
"""

import base64
import codecs
import json
import logging
import os
import re
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

HENTAIHAVEN_BASE = "https://hentaihaven.xxx"
BASE_URL = HENTAIHAVEN_BASE


class HanimeAPI:
    """Native HentaiHaven (hentaihaven.xxx) API & Scraper Provider."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Origin': HENTAIHAVEN_BASE,
            'Referer': HENTAIHAVEN_BASE + '/',
        })

    def search(self, query: str, page: int = 0) -> list[dict]:
        """Search videos on HentaiHaven."""
        log.info(f"Searching HentaiHaven for '{query}' (page={page})")
        return self._search_hentaihaven(query, page)

    def details(self, slug: str) -> dict:
        """Fetch video details & native decrypted streams from HentaiHaven."""
        log.info(f"Fetching HentaiHaven details for '{slug}'")
        return self._details_hentaihaven(slug)

    def get_streams(self, slug: str) -> dict:
        """Get streaming & direct download URLs for a video from HentaiHaven."""
        info = self.details(slug)
        streams = info.get('streams', [])

        best = None
        for s in streams:
            if not best:
                best = s
            elif s.get('kind') == 'mp4':
                best = s
            elif int(s.get('height', 0) or 0) > int(best.get('height', 0) or 0):
                best = s

        dl_url = best.get('url', '') if best else ''
        log.info(f"Sources for {slug}: dl_url={dl_url[:60] if dl_url else 'NONE'}, streams={len(streams)}")
        return {
            'streams': streams,
            'dl_url': dl_url,
            'sources': [
                {'url': s['url'], 'label': f"{s.get('height', 720)}p", 'type': s.get('extension', '')}
                for s in streams if s.get('url')
            ],
        }

    # ── HentaiHaven Scraper Engine ─────────────────────────────────────

    def _search_hentaihaven(self, query: str, page: int = 0) -> list[dict]:
        results = []
        try:
            url = f"{HENTAIHAVEN_BASE}/?s={quote(query)}"
            resp = self.session.get(url, timeout=10)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if "/watch/" in href and not re.search(r"/episode-\d+", href) and not re.search(r"/season-\d+", href):
                        slug_match = re.search(r"/watch/([^/]+)", href)
                        if slug_match:
                            slug = slug_match.group(1).strip("/")
                            img = a.find("img")
                            cover = (img.get("src") or img.get("data-src") or "") if img else ""
                            
                            title = ""
                            if img and img.get("alt"):
                                title = img.get("alt").strip()
                            if not title and a.get("title"):
                                title = a.get("title").strip()
                            if not title:
                                title = a.text.strip()

                            title = re.sub(r"^(HD)?\s*\d{1,2}:\d{2}\s*", "", title, flags=re.IGNORECASE)
                            title = re.sub(r"\s*cover$", "", title, flags=re.IGNORECASE).strip()
                            title = re.sub(r"\s*Hentai\s*$", "", title, flags=re.IGNORECASE).strip()

                            full_url = href if href.startswith("http") else f"{HENTAIHAVEN_BASE}{href}"

                            if title and slug and not any(item["slug"] == slug for item in results):
                                results.append({
                                    'id':          slug,
                                    'slug':        slug,
                                    'name':        title,
                                    'title':       title,
                                    'cover_url':   cover,
                                    'poster_url':  cover,
                                    'cover':       cover,
                                    'tags':        [],
                                    'views':       0,
                                    'brand':       "HentaiHaven",
                                    'description': f"Watch {title} on HentaiHaven",
                                    'url':         full_url,
                                })
        except Exception as e:
            log.error(f"HentaiHaven search failed for '{query}': {e}")

        # Filter results for keyword relevance
        tokens = [re.sub(r"[^\w]", "", t.lower()) for t in query.split()]
        keywords = [t for t in tokens if len(t) >= 2 and t not in {"a", "an", "the", "in", "on", "at", "to", "for", "of", "with", "by", "from", "no", "and", "or", "is", "ep", "episode", "san"}]
        if keywords:
            matched = [
                item for item in results
                if any(kw in item["title"].lower() or kw in item["slug"].lower() for kw in keywords)
            ]
            if matched:
                results = matched

        log.info(f"HentaiHaven search returned {len(results)} items for '{query}'")
        return results

    def _details_hentaihaven(self, slug: str) -> dict:
        main_url = f"{HENTAIHAVEN_BASE}/watch/{slug}/"
        resp = self.session.get(main_url, timeout=8)

        if resp.status_code != 200:
            main_url = f"{HENTAIHAVEN_BASE}/watch/{slug}/episode-1"
            resp = self.session.get(main_url, timeout=8)

        if resp.status_code != 200:
            log.error(f"Failed to fetch HentaiHaven page for {slug} (HTTP {resp.status_code})")
            return {
                'id': slug, 'slug': slug, 'name': slug.replace("-", " ").title(),
                'title': slug.replace("-", " ").title(), 'description': '',
                'summary': '', 'poster_url': '', 'cover_url': '', 'poster': '',
                'cover': '', 'tags': [], 'genres': [], 'brand': 'HentaiHaven', 'views': 0,
                'likes': 0, 'streams': [], 'episodes': [], 'totalEpisodes': 0, 'url': main_url,
                'dl_url': ''
            }

        soup = BeautifulSoup(resp.text, "html.parser")
        title_el = soup.select_one("h1") or soup.select_one(".entry-title")
        title = title_el.text.strip() if title_el else slug.replace("-", " ").title()
        title = re.sub(r"\s*-\s*Episode\s*\d+.*$", "", title, flags=re.IGNORECASE).strip()
        title = re.sub(r"\s*Hentai\s*$", "", title, flags=re.IGNORECASE).strip()

        desc_el = soup.select_one(".entry-content") or soup.select_one("meta[name=\"description\"]")
        desc = desc_el.get("content", "") if desc_el and desc_el.name == "meta" else (desc_el.text.strip() if desc_el else f"Watch {title} on HentaiHaven")

        poster = ""
        m_img = soup.select_one("meta[property=\"og:image\"]")
        if m_img:
            poster = m_img.get("content", "")

        tags = [a.text.strip() for a in soup.select("a[href*=\"/series/\"]") if a.text.strip()]

        episodes = []
        ep_urls_to_try = []

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if f"/watch/{slug}/" in href and ("episode-" in href or "season-" in href):
                ep_match = re.search(r"episode-(\d+)", href)
                ep_num = ep_match.group(1) if ep_match else "1"
                ep_slug = f"{slug}-episode-{ep_num}" if ep_num != "1" else slug
                
                full_ep_url = href if href.startswith("http") else f"{HENTAIHAVEN_BASE}{href}"
                if full_ep_url not in ep_urls_to_try:
                    ep_urls_to_try.append(full_ep_url)

                if not any(e["slug"] == ep_slug for e in episodes):
                    episodes.append({
                        "id": ep_slug,
                        "slug": ep_slug,
                        "name": f"Episode {ep_num}",
                        "title": f"Episode {ep_num}",
                        "url": full_ep_url
                    })

        # Sort episodes by episode number ascending
        episodes.sort(key=lambda x: int(re.search(r"\d+", x["name"]).group(0)) if re.search(r"\d+", x["name"]) else 1)

        if not episodes:
            episodes = [{"id": slug, "slug": slug, "name": title, "title": title, "url": main_url}]

        # Find iframe for video stream (check main page first, then episode 1 page)
        iframe = soup.find("iframe", src=True)
        active_ep_url = main_url

        if not iframe:
            target_ep_url = f"{HENTAIHAVEN_BASE}/watch/{slug}/episode-1"
            if ep_urls_to_try:
                target_ep_url = ep_urls_to_try[-1]  # episode-1 is usually last in descending list

            resp_ep = self.session.get(target_ep_url, timeout=8)
            if resp_ep.status_code == 200:
                soup_ep = BeautifulSoup(resp_ep.text, "html.parser")
                iframe = soup_ep.find("iframe", src=True)
                active_ep_url = target_ep_url

        streams = []
        if iframe:
            player_url = iframe["src"]
            if player_url.startswith("//"):
                player_url = "https:" + player_url

            try:
                p_headers = {
                    'User-Agent': self.session.headers['User-Agent'],
                    'Referer': active_ep_url,
                    'Origin': HENTAIHAVEN_BASE,
                }
                r_p = self.session.get(player_url, headers=p_headers, timeout=8)
                soup_p = BeautifulSoup(r_p.text, "html.parser")
                meta = soup_p.find("meta", attrs={"name": "x-secure-token"})
                if meta:
                    token = meta["content"].replace("sha512-", "")

                    # Decrypt token via ROT13 + Base64
                    step1 = codecs.encode(token, "rot13")
                    step1_dec = base64.b64decode(step1).decode("utf-8")
                    step2 = codecs.encode(step1_dec, "rot13")
                    step2_dec = base64.b64decode(step2).decode("utf-8")
                    step3 = codecs.encode(step2_dec, "rot13")
                    step3_dec = base64.b64decode(step3).decode("utf-8")

                    config = json.loads(step3_dec)
                    en = config.get("en")
                    iv = config.get("iv")

                    if en and iv:
                        api_url = f"{HENTAIHAVEN_BASE}/wp-content/plugins/player-logic/api.php"
                        api_headers = {
                            'User-Agent': self.session.headers['User-Agent'],
                            'Origin': HENTAIHAVEN_BASE,
                            'Referer': player_url
                        }
                        data = {
                            "action": "zarat_get_data_player_ajax",
                            "a": en,
                            "b": iv
                        }
                        r_api = self.session.post(api_url, data=data, headers=api_headers, timeout=8)
                        res_json = r_api.json()
                        if res_json.get("status") and res_json.get("data", {}).get("sources"):
                            for src in res_json["data"]["sources"]:
                                s_url = src.get("src")
                                if s_url:
                                    streams.append({
                                        "url": s_url,
                                        "height": 720,
                                        "kind": "hls" if "m3u8" in s_url else "mp4",
                                        "extension": "m3u8" if "m3u8" in s_url else "mp4",
                                        "is_downloadable": True
                                    })
            except Exception as e:
                log.error(f"HentaiHaven stream extraction failed for {slug}: {e}")

        dl_url = streams[0]["url"] if streams else ""

        return {
            'id':           slug,
            'slug':         slug,
            'name':         title,
            'title':        title,
            'description':  desc,
            'summary':      desc,
            'poster_url':   poster,
            'cover_url':    poster,
            'poster':       poster,
            'cover':        poster,
            'tags':         tags,
            'genres':       tags,
            'brand':        "HentaiHaven",
            'views':        0,
            'likes':        0,
            'streams':      streams,
            'dl_url':       dl_url,
            'episodes':     episodes,
            'totalEpisodes':len(episodes),
            'url':          main_url,
        }
