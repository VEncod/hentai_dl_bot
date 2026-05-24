"""
Hanime.tv API wrapper.

Uses hanime.tv's native API endpoints:
- Search: https://search.htv-services.com/ (POST)
- Video details + streams: https://hanime.tv/api/v8/video?id={slug} (GET)
- Signed manifest: https://hanime.tv/api/v8/guest/videos/{id}/manifest (GET)
  Requires WASM-generated x-signature + x-time headers (see tools/get_signature.js)

Stream URLs on hanime.tv are now hosted at r2.1hanime.com and require
a signed manifest API call to obtain real (signed) URLs.
"""

import json
import logging
import os
import random
import re
import subprocess
import time
from typing import Optional
from html import unescape

import requests

log = logging.getLogger(__name__)

SEARCH_URL  = "https://search.htv-services.com/"
VIDEO_URL   = "https://hanime.tv/api/v8/video"
MANIFEST_URL = "https://hanime.tv/api/v8/guest/videos/{video_id}/manifest"
BASE_URL    = "https://hanime.tv"

# Path to the Node.js signature generator
_TOOLS_DIR = os.path.join(os.path.dirname(__file__), '..', 'tools')
_SIG_SCRIPT = os.path.join(_TOOLS_DIR, 'get_signature.js')


class HanimeAPI:
    """Wrapper for hanime.tv's native API."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Origin': BASE_URL,
            'Referer': BASE_URL + '/',
        })
        self._last_request = 0
        self._sig_cache: dict = {}   # {timestamp: (ssignature, stime)}
        self._sig_ttl = 30           # seconds before refreshing signature

    # ──────────────────────────────────────────────────────────────────
    # Signature / auth helpers
    # ──────────────────────────────────────────────────────────────────

    def _get_signature(self) -> tuple[str, int]:
        """
        Run tools/get_signature.js via Node.js to obtain a fresh
        WASM-computed x-signature + x-time pair.

        Returns (ssignature_hex, stime_unix_int).
        Raises RuntimeError on failure.
        """
        # Return cached sig if still fresh
        if self._sig_cache:
            sig, stime, generated_at = (
                self._sig_cache.get('sig'),
                self._sig_cache.get('stime'),
                self._sig_cache.get('generated_at', 0),
            )
            if time.time() - generated_at < self._sig_ttl:
                return sig, stime

        if not os.path.exists(_SIG_SCRIPT):
            raise RuntimeError(
                f"Signature script not found at {_SIG_SCRIPT}. "
                "Run: git pull to get tools/get_signature.js"
            )

        log.info("Generating hanime.tv WASM signature...")
        try:
            result = subprocess.run(
                ['node', _SIG_SCRIPT],
                capture_output=True, text=True, timeout=15
            )
        except FileNotFoundError:
            raise RuntimeError("Node.js not found. Install Node.js 18+ to generate signatures.")
        except subprocess.TimeoutExpired:
            raise RuntimeError("Signature generation timed out (>15s)")

        # The script writes all intermediate JSON lines; take the last valid one
        lines = [l.strip() for l in result.stdout.splitlines() if l.strip().startswith('{"ssignature"')]
        if not lines:
            raise RuntimeError(
                f"No signature in output. stderr: {result.stderr[:300]}"
            )

        data = json.loads(lines[-1])
        sig   = data['ssignature']
        stime = int(data['stime'])

        self._sig_cache = {'sig': sig, 'stime': stime, 'generated_at': time.time()}
        log.info(f"Signature obtained: stime={stime}")
        return sig, stime

    def _auth_headers(self) -> dict:
        """Build request headers with valid x-signature + x-time."""
        try:
            sig, stime = self._get_signature()
            return {
                'x-signature-version': 'web2',
                'x-signature': sig,
                'x-time': str(stime),
                'x-session-token': '',
                'x-user-license': '',
            }
        except Exception as e:
            log.warning(f"Could not get WASM signature: {e}. Using placeholder.")
            return {
                'x-signature-version': 'web2',
                'x-signature': 'nonce',
                'x-time': str(int(time.time())),
            }

    # ──────────────────────────────────────────────────────────────────
    # HTTP helper
    # ──────────────────────────────────────────────────────────────────

    def _request(self, method: str, url: str, **kwargs) -> dict:
        """Rate-limited request with retries."""
        max_retries = 3
        last_error = None

        for attempt in range(max_retries):
            try:
                elapsed = time.time() - self._last_request
                if elapsed < 0.5:
                    time.sleep(0.5 - elapsed + random.uniform(0.1, 0.3))
                self._last_request = time.time()

                resp = self.session.request(method, url, timeout=30, **kwargs)
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.RequestException as e:
                log.warning(f"Request failed (attempt {attempt+1}/{max_retries}): {e}")
                last_error = e
                time.sleep(1 + attempt * 2)

        raise Exception(f"Failed after {max_retries} attempts. Last: {last_error}")

    # ──────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────

    def search(self, query: str, page: int = 0) -> list[dict]:
        """
        Search for hentai videos.
        Returns a list of results with: id, name, slug, cover_url, tags, etc.
        """
        payload = {
            "search_text": query,
            "tags": [], "tags_mode": "AND",
            "brands": [], "blacklist": [],
            "order_by": "created_at_unix",
            "ordering": "desc",
            "page": page,
        }
        log.info(f"Searching for '{query}'")
        data = self._request("POST", SEARCH_URL, json=payload)

        hits_raw = data.get("hits", "[]")
        hits = json.loads(hits_raw) if isinstance(hits_raw, str) else hits_raw

        results = []
        for h in hits:
            results.append({
                'id':          h.get('id'),
                'slug':        h.get('slug', ''),
                'name':        h.get('name', ''),
                'title':       h.get('name', ''),
                'cover_url':   h.get('cover_url', ''),
                'poster_url':  h.get('poster_url', h.get('cover_url', '')),
                'cover':       h.get('cover_url', ''),
                'tags':        h.get('tags', []),
                'views':       h.get('views', 0),
                'brand':       h.get('brand', ''),
                'description': h.get('description', ''),
                'url':         f"{BASE_URL}/videos/hentai/{h.get('slug', '')}",
            })

        log.info(f"Found {len(results)} results for '{query}'")
        return results

    def details(self, slug: str) -> dict:
        """
        Get detailed info + streams for a video.
        Also attempts the signed manifest endpoint to resolve real r2.1hanime.com URLs.
        """
        log.info(f"Fetching details for '{slug}'")
        data = self._request(
            "GET", VIDEO_URL,
            params={"id": slug},
            headers=self._auth_headers(),
        )

        video = data.get("hentai_video", {})
        video_id = video.get("id")

        # Basic metadata
        tags = [t.get("text", "") for t in video.get("hentai_tags", [])]
        description = re.sub(r'<[^>]+>', '', video.get("description", ""))
        description = unescape(description).strip()

        # Streams from the basic API (may have placeholder/fake URLs)
        streams = self._parse_streams(data.get("videos_manifest", {}))

        # Attempt signed manifest to get real r2.1hanime.com URLs
        if video_id:
            try:
                real_streams = self._get_manifest_streams(video_id, slug)
                if real_streams:
                    streams = real_streams
                    log.info(f"Using signed manifest streams for {slug}")
            except Exception as e:
                log.warning(f"Manifest fetch failed for {slug}: {e}. Using fallback streams.")

        # Related episodes
        episodes = []
        for ep in video.get("hentai_franchise_hentai_videos", []):
            episodes.append({
                'id':        ep.get('id'),
                'slug':      ep.get('slug', ''),
                'name':      ep.get('name', ''),
                'title':     ep.get('name', ''),
                'cover_url': ep.get('cover_url', ''),
                'poster_url':ep.get('poster_url', ''),
            })

        return {
            'id':           video_id,
            'slug':         video.get('slug', slug),
            'name':         video.get('name', slug.replace('-', ' ').title()),
            'title':        video.get('name', slug.replace('-', ' ').title()),
            'description':  description,
            'summary':      description,
            'poster_url':   video.get('poster_url', ''),
            'cover_url':    video.get('cover_url', ''),
            'cover':        video.get('cover_url', ''),
            'tags':         tags,
            'genres':       tags,
            'brand':        video.get('brand', ''),
            'views':        video.get('views', 0),
            'likes':        video.get('likes', 0),
            'streams':      streams,
            'episodes':     episodes,
            'totalEpisodes':len(episodes),
            'url':          f"{BASE_URL}/videos/hentai/{video.get('slug', slug)}",
        }

    def get_streams(self, slug: str) -> dict:
        """Get streaming URLs for a video. Returns streams list + best dl_url."""
        info = self.details(slug)
        streams = info.get('streams', [])

        # Best stream = highest quality
        best = None
        for s in streams:
            h = int(s.get('height', 0) or 0)
            if not best or h > int(best.get('height', 0) or 0):
                best = s

        dl_url = best.get('url', '') if best else ''
        return {
            'streams': streams,
            'dl_url': dl_url,
            'sources': [
                {'url': s['url'], 'label': f"{s['height']}p", 'type': s.get('kind', '')}
                for s in streams
            ],
        }

    # ──────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────

    def _parse_streams(self, manifest: dict) -> list[dict]:
        """Parse the videos_manifest structure into a flat stream list."""
        streams = []
        for server in manifest.get("servers", []):
            for s in server.get("streams", []):
                streams.append({
                    'url':           s.get('url', ''),
                    'height':        s.get('height', ''),
                    'width':         s.get('width', 0),
                    'size_mbs':      s.get('filesize_mbs', 0),
                    'kind':          s.get('kind', ''),
                    'extension':     s.get('extension', ''),
                    'is_downloadable': s.get('is_downloadable', False),
                    'server':        server.get('name', ''),
                    'filename':      s.get('filename', ''),
                })
        return streams

    def _get_manifest_streams(self, video_id: int, slug: str) -> list[dict]:
        """
        Fetch the signed manifest from /api/v8/guest/videos/{id}/manifest.
        Returns parsed stream list, or empty list on failure.

        NOTE: The manifest response body is AES-CBC encrypted.
        Decryption key/IV are set by the hanime.tv WASM module via window.key
        and window.iv — extraction of those constants is TODO.
        For now we log the raw response for analysis.
        """
        sig, stime = self._get_signature()

        headers = {
            'x-signature-version': 'web2',
            'x-signature': sig,
            'x-time': str(stime),
            'x-session-token': '',
            'x-user-license': '',
            'Accept': 'application/json',
            'Origin': BASE_URL,
            'Referer': f"{BASE_URL}/videos/hentai/{slug}",
        }

        url = MANIFEST_URL.format(video_id=video_id)
        log.debug(f"Fetching manifest: {url}")

        resp = self.session.get(url, headers=headers, timeout=15)

        if resp.status_code == 200:
            try:
                data = resp.json()
                log.debug(f"Manifest response keys: {list(data.keys())}")

                # If response has signed_url or streams directly
                if 'videos_manifest' in data:
                    return self._parse_streams(data['videos_manifest'])

                # Raw encrypted hex string — needs AES-CBC decrypt with window.key/iv
                # TODO: extract window.key and window.iv from WASM to enable decryption
                if isinstance(data, str) and len(data) > 32:
                    log.debug(f"Manifest is encrypted hex ({len(data)} chars) — decryption TODO")

            except Exception as e:
                log.debug(f"Manifest parse error: {e}")

        elif resp.status_code == 401:
            log.warning(f"Manifest unauthorized for video {video_id} — signature may be stale")
        else:
            log.debug(f"Manifest {resp.status_code} for video {video_id}")

        return []
