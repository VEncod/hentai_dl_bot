"""
HentaiHaven scraper - Python implementation.
Uses cloudscraper for Cloudflare bypass and beautifulsoup4 for HTML parsing.
Includes retry logic and rotating browser profiles for cloud server compatibility.

Based on: https://github.com/sulvii/hentai-api
"""

import asyncio
import json
import logging
import random
import re
import time
from typing import Optional
from urllib.parse import quote_plus

import cloudscraper
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

BASE_URL = "https://hentaihaven.xxx"

# Browser profiles to rotate through
BROWSER_PROFILES = [
    {'browser': 'chrome', 'platform': 'linux', 'desktop': True},
    {'browser': 'chrome', 'platform': 'windows', 'desktop': True},
    {'browser': 'firefox', 'platform': 'linux', 'desktop': True},
    {'browser': 'firefox', 'platform': 'windows', 'desktop': True},
]


class HentaiHavenScraper:
    """Scraper for hentaihaven.xxx with Cloudflare bypass."""
    
    def __init__(self):
        self._create_scraper()
        self._last_request = 0
    
    def _create_scraper(self):
        """Create a new cloudscraper instance with random browser profile."""
        profile = random.choice(BROWSER_PROFILES)
        self.scraper = cloudscraper.create_scraper(
            browser=profile,
            delay=5,
        )
        # Set realistic headers
        self.scraper.headers.update({
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Cache-Control': 'max-age=0',
        })
    
    def get(self, url: str, timeout: int = 30, max_retries: int = 3) -> str:
        """Fetch HTML from URL with retry logic and Cloudflare bypass."""
        last_error = None
        
        for attempt in range(max_retries):
            try:
                # Rate limiting - wait between requests
                elapsed = time.time() - self._last_request
                if elapsed < 1.5:
                    time.sleep(1.5 - elapsed + random.uniform(0.1, 0.5))
                
                self._last_request = time.time()
                resp = self.scraper.get(url, timeout=timeout)
                
                if resp.status_code == 200:
                    return resp.text
                elif resp.status_code == 403:
                    log.warning(f"Got 403 on attempt {attempt + 1}/{max_retries}, rotating scraper...")
                    self._create_scraper()
                    time.sleep(2 + attempt * 2)
                    continue
                elif resp.status_code == 503:
                    log.warning(f"Got 503 (Cloudflare challenge) on attempt {attempt + 1}/{max_retries}")
                    time.sleep(3 + attempt * 2)
                    continue
                else:
                    resp.raise_for_status()
                    
            except cloudscraper.exceptions.CloudflareChallengeError as e:
                log.warning(f"Cloudflare challenge failed on attempt {attempt + 1}: {e}")
                last_error = e
                self._create_scraper()
                time.sleep(3 + attempt * 3)
            except Exception as e:
                log.error(f"Request failed on attempt {attempt + 1}: {e}")
                last_error = e
                if attempt < max_retries - 1:
                    self._create_scraper()
                    time.sleep(2 + attempt * 2)
        
        raise Exception(f"Failed after {max_retries} attempts. Last error: {last_error}")
    
    def search(self, query: str) -> list[dict]:
        """Search hentaihaven.xxx for content."""
        encoded_query = quote_plus(query)
        url = f"{BASE_URL}/?s={encoded_query}&post_type=wp-manga"
        log.info(f"Searching for '{query}'")
        
        html = self.get(url)
        soup = BeautifulSoup(html, 'html.parser')
        
        results = []
        seen = set()
        
        # Find all search result cards
        for content in soup.find_all('div', class_='c-tabs-item__content'):
            try:
                # Extract image and title first
                img = content.find('img')
                if not img:
                    continue
                
                cover = img.get('src', '') or img.get('data-src', '')
                alt = img.get('alt', '')
                title = alt.replace(' cover', '').strip() or 'Unknown'
                
                # Find the main watch link
                link = content.find('a', href=re.compile(r'.+/watch/[^/]+/?$'))
                if not link:
                    # Try broader match
                    link = content.find('a', href=re.compile(r'hentaihaven'))
                if not link:
                    continue
                
                href = link.get('href', '')
                
                # Extract series ID
                match = re.search(r'/watch/([^/]+)/?', href)
                if not match:
                    continue
                series_id = match.group(1)
                
                if series_id in seen:
                    continue
                seen.add(series_id)
                
                # Extract alternative title
                alternative = ''
                alt_div = content.find('div', class_='mg_alternative')
                if alt_div:
                    content_div = alt_div.find('div', class_='summary-content')
                    if content_div:
                        alternative = content_div.text.strip()
                
                # Extract author
                author = ''
                author_div = content.find('div', class_='mg_author')
                if author_div:
                    content_div = author_div.find('div', class_='summary-content')
                    if content_div:
                        author = content_div.text.strip()
                
                # Extract release year
                release_div = content.find('div', class_='mg_release')
                released = 0
                if release_div:
                    content_div = release_div.find('div', class_='summary-content')
                    if content_div:
                        released = _get_number(content_div.text) or 0
                
                # Extract episode count
                chap_el = content.find('span', class_='chapter')
                total_episodes = _get_number(chap_el.text) if chap_el else 1
                
                # Extract rating
                rating = 0.0
                rating_span = content.find('span', class_='total_votes')
                if rating_span:
                    try:
                        rating = float(rating_span.text.strip())
                    except (ValueError, TypeError):
                        pass
                
                # Extract genres
                genres = []
                for genre_link in content.find_all('a', href=re.compile(r'/genre/')):
                    genres.append({
                        'name': genre_link.text.strip(),
                        'url': genre_link.get('href', '')
                    })
                
                results.append({
                    'id': series_id,
                    'slug': series_id,
                    'title': title,
                    'name': title,
                    'cover': cover.replace(' ', '%20') if cover else '',
                    'poster_url': cover,
                    'rating': rating,
                    'released': released,
                    'genres': genres,
                    'totalEpisodes': total_episodes,
                    'alternative': alternative,
                    'author': author,
                    'url': href,
                })
            except Exception as e:
                log.warning(f"Failed to parse search result: {e}")
                continue
        
        log.info(f"Found {len(results)} results")
        return results
    
    def details(self, series_id: str) -> dict:
        """Get detailed info about a hentai series."""
        url = f"{BASE_URL}/watch/{series_id}"
        log.info(f"Fetching details for {series_id}")
        
        html = self.get(url)
        
        if not html or "webpage has been blocked" in html:
            log.error(f"Page blocked for {series_id}")
            return {}
        
        soup = BeautifulSoup(html, 'html.parser')
        
        # Extract title
        title = series_id.replace('-', ' ').title()
        title_el = soup.find('h1', class_='post-title')
        if title_el:
            title = title_el.text.strip()
        
        # Extract cover
        cover = ''
        # Try multiple cover selectors
        cover_img = soup.select_one('.summary_image img')
        if cover_img:
            cover = cover_img.get('src', '') or cover_img.get('data-src', '')
        if not cover:
            for img in soup.find_all('img'):
                src = img.get('src', '') or img.get('data-src', '')
                alt = img.get('alt', '')
                if 'cover' in alt.lower() or 'cover' in src.lower() or series_id in src.lower():
                    cover = src
                    break
        
        # Extract summary
        summary = ""
        desc_el = soup.find('div', class_='description-summary')
        if desc_el:
            p = desc_el.find('p')
            summary = p.text.strip() if p else desc_el.text.strip()
        if not summary:
            # Try manga-excerpt
            excerpt = soup.find('div', class_='manga-excerpt')
            if excerpt:
                summary = excerpt.text.strip()
        
        # Extract genres
        genres = []
        genre_div = soup.find('div', class_='genres-content')
        if genre_div:
            for a in genre_div.find_all('a'):
                genres.append({
                    'name': a.text.strip(),
                    'url': a.get('href', '')
                })
        
        # Extract episodes
        episodes = []
        ep_elements = soup.find_all('li', class_='wp-manga-chapter')
        total_episodes = len(ep_elements)
        
        for i, ep_el in enumerate(ep_elements):
            try:
                link = ep_el.find('a')
                if not link:
                    continue
                
                ep_title = link.text.strip()
                ep_number = total_episodes - i
                
                # Extract episode ID from href
                href = link.get('href', '')
                # Try to extract episode slug
                ep_match = re.search(r'/watch/([^/]+)/([^/]+)/?', href)
                if ep_match:
                    ep_id = f"{ep_match.group(1)}/{ep_match.group(2)}"
                else:
                    ep_id = f"{series_id}/episode-{ep_number}"
                
                date_el = ep_el.find('span', class_='chapter-release-date')
                ep_date = date_el.text.strip() if date_el else ''
                
                episodes.append({
                    'id': ep_id,
                    'slug': f"{series_id}-{ep_number}",
                    'title': ep_title,
                    'number': ep_number,
                    'released': ep_date,
                })
            except Exception as e:
                log.warning(f"Failed to parse episode: {e}")
                continue
        
        return {
            'id': series_id,
            'slug': series_id,
            'title': title,
            'name': title,
            'cover': cover.replace(' ', '%20') if cover else '',
            'poster_url': cover,
            'summary': summary,
            'genres': genres,
            'totalEpisodes': total_episodes,
            'episodes': episodes,
        }
    
    def get_streams(self, ep_id: str) -> dict:
        """Get video sources for an episode."""
        if not ep_id:
            return {'sources': [], 'dl_url': ''}
        
        # ep_id can be in format "series-id/episode-X" or "series-id-X"
        if '/' in ep_id:
            page_url = f"{BASE_URL}/watch/{ep_id}"
        else:
            # Parse episode ID (format: series-1)
            parts = ep_id.rsplit('-', 1)
            if len(parts) != 2:
                log.error(f"Invalid episode ID: {ep_id}")
                return {'sources': [], 'dl_url': ''}
            series_id, ep_num = parts
            page_url = f"{BASE_URL}/watch/{series_id}/episode-{ep_num}"
        
        log.info(f"Fetching streams from {page_url}")
        
        html = self.get(page_url)
        soup = BeautifulSoup(html, 'html.parser')
        
        sources = []
        
        # Method 1: Find iframe player
        iframe = soup.find('iframe', class_='player_logic_item')
        if not iframe:
            iframe = soup.find('iframe', attrs={'allowfullscreen': True})
        if not iframe:
            iframe = soup.find('iframe')
        
        if iframe:
            iframe_src = iframe.get('src', '') or iframe.get('data-src', '')
            if iframe_src:
                if iframe_src.startswith('//'):
                    iframe_src = 'https:' + iframe_src
                sources.append({
                    'url': iframe_src,
                    'label': 'Stream',
                    'type': 'iframe',
                })
        
        # Method 2: Look for direct video sources in script tags
        for script in soup.find_all('script'):
            script_text = script.string or ''
            # Look for video URLs in JavaScript
            video_urls = re.findall(r'(?:src|file|url)\s*[:=]\s*["\']([^"\']+\.(?:mp4|m3u8)[^"\']*)["\']', script_text)
            for vurl in video_urls:
                if vurl.startswith('//'):
                    vurl = 'https:' + vurl
                sources.append({
                    'url': vurl,
                    'label': 'Direct',
                    'type': 'mp4' if '.mp4' in vurl else 'hls',
                })
        
        # Method 3: Look for download links
        dl_links = soup.find_all('a', href=re.compile(r'\.mp4|download'))
        for dl in dl_links:
            href = dl.get('href', '')
            if href and 'mp4' in href:
                sources.append({
                    'url': href,
                    'label': dl.text.strip() or 'Download',
                    'type': 'mp4',
                })
        
        dl_url = sources[0]['url'] if sources else ''
        
        if not sources:
            log.error(f"No video sources found for {ep_id}")
        
        return {
            'sources': sources,
            'dl_url': dl_url,
        }


def _get_number(s: str) -> Optional[int]:
    """Extract first number from string"""
    match = re.search(r'\d+', s)
    return int(match.group()) if match else None


# ── Module-level functions for backward compatibility ──

_scraper = None


def _get_scraper() -> HentaiHavenScraper:
    global _scraper
    if _scraper is None:
        _scraper = HentaiHavenScraper()
    return _scraper


def reset_scraper():
    """Reset the scraper instance (useful after persistent 403 errors)."""
    global _scraper
    _scraper = None
    log.info("Scraper instance reset")


async def search(query: str, page: int = 0) -> list[dict]:
    """Search for hentai - runs in thread to avoid blocking."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _get_scraper().search, query)


async def details(series_id: str) -> dict:
    """Get series details - runs in thread to avoid blocking."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _get_scraper().details, series_id)


async def get_streams(ep_id: str) -> dict:
    """Get video streams - runs in thread to avoid blocking."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _get_scraper().get_streams, ep_id)
