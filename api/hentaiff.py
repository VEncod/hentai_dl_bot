import asyncio
import json
import logging
import random
import re
import time
from typing import Optional
from urllib.parse import quote_plus
import base64

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

BASE_URL = "https://hentaiff.com"

class HentaiFFScraper:
    """Scraper for hentaiff.com."""
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
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
        self._last_request = 0

    def _get(self, url: str, timeout: int = 30, max_retries: int = 3) -> str:
        """Fetch HTML from URL with retry logic."""
        last_error = None
        for attempt in range(max_retries):
            try:
                elapsed = time.time() - self._last_request
                if elapsed < 1.5:
                    time.sleep(1.5 - elapsed + random.uniform(0.1, 0.5))
                self._last_request = time.time()
                resp = self.session.get(url, timeout=timeout)
                resp.raise_for_status()
                return resp.text
            except requests.exceptions.RequestException as e:
                log.warning(f"Request failed on attempt {attempt + 1}/{max_retries}: {e}")
                last_error = e
                time.sleep(2 + attempt * 2)
        raise Exception(f"Failed after {max_retries} attempts. Last error: {last_error}")

    def search(self, query: str) -> list[dict]:
        """Search hentaiff.com for content."""
        encoded_query = quote_plus(query)
        url = f"{BASE_URL}/?s={encoded_query}"
        log.info(f"Searching for '{query}' on {url}")
        
        html = self._get(url)
        soup = BeautifulSoup(html, 'html.parser')
        
        results = []
        for article in soup.find_all('article'):
            title_tag = article.find('h2', class_='entry-title')
            link_tag = article.find('a')
            img_tag = article.find('img')
            
            if title_tag and link_tag:
                title = title_tag.text.strip()
                item_url = link_tag.get('href')
                cover = img_tag.get('src') if img_tag else None

                # Extract ID from URL (e.g., /anime/fella-hame-lips/ -> fella-hame-lips)
                match = re.search(r'/anime/([^/]+)/', item_url)
                series_id = match.group(1) if match else None

                if series_id:
                    results.append({
                        'id': series_id,
                        'slug': series_id,
                        'title': title,
                        'name': title,
                        'cover': cover,
                        'poster_url': cover,
                        'url': item_url,
                    })
        log.info(f"Found {len(results)} results")
        return results

    def details(self, series_id: str) -> dict:
        """Get detailed info about a hentai series."""
        url = f"{BASE_URL}/anime/{series_id}/"
        log.info(f"Fetching details for {series_id} from {url}")
        
        html = self._get(url)
        soup = BeautifulSoup(html, 'html.parser')
        
        title = soup.find('h1', class_='entry-title').text.strip() if soup.find('h1', class_='entry-title') else series_id.replace('-', ' ').title()
        cover = soup.select_one('.anime-thumbnail img')
        cover_url = cover.get('src') if cover else None

        summary = ""
        summary_div = soup.find('div', class_='entry-content')
        if summary_div:
            summary_p = summary_div.find('p')
            if summary_p:
                summary = summary_p.text.strip()

        genres = []
        genre_div = soup.find('div', class_='genres')
        if genre_div:
            for a_tag in genre_div.find_all('a'):
                genres.append({
                    'name': a_tag.text.strip(),
                    'url': a_tag.get('href')
                })

        episodes = []
        ep_list = soup.find('div', class_='eplister')
        if ep_list:
            for li in ep_list.find_all('li'):
                link = li.find('a')
                num_tag = li.find('div', class_='epl-num')
                title_tag = li.find('div', class_='epl-title')
                date_tag = li.find('div', class_='epl-date')
                
                if link:
                    ep_title = title_tag.text.strip() if title_tag else link.text.strip()
                    ep_url = link.get('href')
                    ep_number = num_tag.text.strip() if num_tag else None
                    ep_date = date_tag.text.strip() if date_tag else None

                    # Extract episode ID from URL
                    ep_match = re.search(r'hentaiff.com/([^/]+)/', ep_url)
                    ep_id = ep_match.group(1) if ep_match else None

                    if ep_id:
                        episodes.append({
                            'id': ep_id,
                            'slug': ep_id,
                            'title': ep_title,
                            'number': ep_number,
                            'released': ep_date,
                            'url': ep_url
                        })
        
        return {
            'id': series_id,
            'slug': series_id,
            'title': title,
            'name': title,
            'cover': cover_url,
            'poster_url': cover_url,
            'summary': summary,
            'genres': genres,
            'episodes': episodes,
            'totalEpisodes': len(episodes)
        }

    def get_streams(self, ep_id: str) -> dict:
        """Get video sources for an episode."""
        url = f"{BASE_URL}/{ep_id}/"
        log.info(f"Fetching streams for episode {ep_id} from {url}")

        html = self._get(url)
        soup = BeautifulSoup(html, 'html.parser')

        sources = []

        # Method 1: Find iframe in player-embed div
        embed_div = soup.find('div', class_='player-embed')
        if embed_div:
            iframe = embed_div.find('iframe')
            if iframe:
                iframe_src = iframe.get('src')
                if iframe_src:
                    sources.append({
                        'url': iframe_src,
                        'label': 'Stream',
                        'type': 'iframe',
                    })
        
        # Method 2: Check for select options (mirrors) and decode base64
        select = soup.find('select', class_='mirror')
        if select:
            for option in select.find_all('option'):
                val = option.get('value')
                if val and val != "":
                    try:
                        decoded_html = base64.b64decode(val).decode('utf-8')
                        iframe_match = re.search(r'src="([^"]+)"', decoded_html)
                        if iframe_match:
                            sources.append({
                                'url': iframe_match.group(1),
                                'label': option.text.strip(),
                                'type': 'iframe_decoded',
                            })
                    except Exception as e:
                        log.warning(f"Failed to decode mirror value {val}: {e}")

        # Method 3: Look for direct download links (if any)
        download_link = soup.find('a', text='Download')
        if download_link:
            dl_url = download_link.get('href')
            if dl_url:
                sources.append({
                    'url': dl_url,
                    'label': 'Download',
                    'type': 'direct_download',
                })

        return {'sources': sources, 'dl_url': sources[0]['url'] if sources else ''}


# Helper function to extract numbers
def _get_number(text: str) -> Optional[int]:
    match = re.search(r'\d+', text)
    if match:
        return int(match.group(0))
    return None
