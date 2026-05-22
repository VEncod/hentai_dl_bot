"""
HentaiHaven scraper - Python port of sulvii/hentai-api logic.
Directly scrapes HentaiHaven without external API dependency.

Based on: https://github.com/sulvii/hentai-api/blob/main/src/providers/hentai-haven.ts
"""

import asyncio
import base64
import json
import logging
import re
from typing import Optional
from datetime import datetime
from urllib.parse import urljoin

try:
    from bs4 import BeautifulSoup
    import cloudscraper
except ImportError:
    BeautifulSoup = None
    cloudscraper = None

log = logging.getLogger(__name__)

BASE_URL = "https://hentaihaven.xxx"


def _rot13(s: str) -> str:
    """ROT13 cipher"""
    result = []
    for c in s:
        if 'a' <= c <= 'z':
            result.append(chr((ord(c) - ord('a') + 13) % 26 + ord('a')))
        elif 'A' <= c <= 'Z':
            result.append(chr((ord(c) - ord('A') + 13) % 26 + ord('A')))
        else:
            result.append(c)
    return ''.join(result)


def _get_number(s: str) -> Optional[int]:
    """Extract first number from string"""
    match = re.search(r'\d+', s)
    return int(match.group()) if match else None


async def search(query: str) -> list[dict]:
    """
    Search HentaiHaven for content.
    Uses cloudscraper to bypass Cloudflare.
    """
    if not query:
        query = "Hatsukoi Jikan"
    
    if cloudscraper is None or BeautifulSoup is None:
        raise ImportError("cloudscraper and beautifulsoup4 are required")
    
    url = f"{BASE_URL}/?s={query}&post_type=wp-manga"
    log.info(f"Searching for '{query}'")
    
    try:
        scraper = cloudscraper.create_scraper()
        resp = scraper.get(url, timeout=30)
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        log.error(f"Search request failed: {e}")
        return []
    
    soup = BeautifulSoup(html, 'html.parser')
    results = []
    
    # Parse search results from c-tabs-item__content containers
    for content in soup.find_all('div', class_='c-tabs-item__content'):
        try:
            # Extract image
            img = content.find('img')
            cover = img.get('src', '') if img else ''
            
            # Extract link and ID
            link = content.find('a', href=re.compile(r'/watch/'))
            if not link:
                continue
            href = link.get('href', '')
            id_match = re.search(r'/watch/([^/]+)/', href)
            series_id = id_match.group(1) if id_match else ''
            if not series_id:
                continue
            
            # Extract title - can be in link text or h3
            title = link.text.strip()
            if not title:
                h3 = content.find('h3')
                if h3:
                    title = h3.text.strip()
            if not title:
                title = 'Unknown'
            
            # Extract alternative title
            alt_div = content.find('div', class_='mg_alternative')
            alternative = ''
            if alt_div:
                content_div = alt_div.find('div', class_='summary-content')
                if content_div:
                    alternative = content_div.text.strip()
            
            # Extract author
            author_div = content.find('div', class_='mg_author')
            author = ''
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
                    released = _get_number(content_div.text.strip()) or 0
            
            # Extract episode count
            chap_span = content.find('span', class_='chapter')
            total_episodes = _get_number(chap_span.text.strip()) if chap_span else 1
            
            # Extract rating
            rating_span = content.find('span', class_='total_votes')
            rating = float(rating_span.text.strip()) if rating_span else 0.0
            
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
                'cover': cover.replace(' ', '%20'),
                'poster_url': cover,
                'rating': rating,
                'released': released,
                'genres': genres,
                'totalEpisodes': total_episodes,
                'alternative': alternative,
                'author': author,
            })
        except Exception as e:
            log.warning(f"Failed to parse search result: {e}")
            continue
    
    log.info(f"Found {len(results)} results")
    return results


async def details(series_id: str) -> dict:
    """
    Get detailed info about a hentai series by ID.
    """
    if not series_id:
        return {}
    
    if cloudscraper is None or BeautifulSoup is None:
        raise ImportError("cloudscraper and beautifulsoup4 are required")
    
    url = f"{BASE_URL}/watch/{series_id}"
    log.info(f"Fetching details for {series_id}")
    
    try:
        scraper = cloudscraper.create_scraper()
        resp = scraper.get(url, timeout=30)
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        log.error(f"Details request failed: {e}")
        return {}
    
    if not html or "webpage has been blocked" in html:
        log.error(f"Page blocked or empty for {series_id}")
        return {}
    
    soup = BeautifulSoup(html, 'html.parser')
    
    # Extract title
    title_el = soup.find('h1', class_='post-title')
    title = title_el.text.strip() if title_el else 'Unknown'
    
    # Extract cover
    cover_el = soup.find('img', class_='summary_image')
    cover = cover_el.get('src', '') if cover_el else ''
    
    # Extract summary
    summary_el = soup.find('p', class_='description-summary')
    summary = summary_el.text.strip() if summary_el else ''
    
    # Extract views
    views = 0
    for item in soup.find_all('div', class_='post-content_item'):
        if 'View' in item.text or 'view' in item.text:
            views = _get_number(item.text) or 0
            break
    
    # Extract rating count
    rating_el = soup.find('span', attrs={'property': 'ratingCount'})
    rating_count = int(rating_el.text.strip()) if rating_el else 0
    
    # Extract release year
    released = 0
    for link in soup.find_all('a'):
        if 'post-status' in str(link.parent.get('class', [])):
            released = _get_number(link.text) or 0
            break
    
    # Extract genres
    genres = []
    for genre_link in soup.find_all('a', href=re.compile(r'/genre/')):
        genres.append({
            'name': genre_link.text.strip(),
            'url': genre_link.get('href', '')
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
            
            href = link.get('href', '')
            parts = href.strip('/').split('/')
            if len(parts) >= 2:
                ep_id = f"{parts[-2]}/{parts[-1]}"
                ep_id_encoded = base64.b64encode(ep_id.encode()).decode()
            else:
                continue
            
            ep_title = link.text.strip()
            ep_number = total_episodes - i
            
            date_el = ep_el.find('span', class_='chapter-release-date')
            ep_date = date_el.text.strip() if date_el else ''
            
            episodes.append({
                'id': ep_id_encoded,
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
        'views': views,
        'ratingCount': rating_count,
        'released': released,
        'genres': genres,
        'totalEpisodes': total_episodes,
        'episodes': episodes,
    }


async def get_streams(ep_id_encoded: str) -> dict:
    """
    Get video sources for an episode.
    Decrypts the hidden token and fetches actual stream URLs.
    """
    if not ep_id_encoded:
        return {'sources': [], 'dl_url': ''}
    
    if cloudscraper is None or BeautifulSoup is None:
        raise ImportError("cloudscraper and beautifulsoup4 are required")
    
    try:
        # Decode episode ID
        ep_id = base64.b64decode(ep_id_encoded).decode()
        parts = ep_id.split('/')
        series_id = parts[0]
        ep_num = parts[1]
    except Exception as e:
        log.error(f"Failed to decode episode ID: {e}")
        return {'sources': [], 'dl_url': ''}
    
    page_url = f"{BASE_URL}/watch/{series_id}/episode-{ep_num}"
    log.info(f"Fetching streams from {page_url}")
    
    try:
        scraper = cloudscraper.create_scraper()
        
        # Fetch page
        page_resp = scraper.get(page_url, timeout=30)
        page_html = page_resp.text
        
        soup = BeautifulSoup(page_html, 'html.parser')
        iframe = soup.find('iframe', class_='player_logic_item')
        
        if not iframe:
            log.error(f"No iframe found in page")
            return {'sources': [], 'dl_url': ''}
        
        iframe_src = iframe.get('src', '')
        if not iframe_src:
            log.error(f"No iframe src found")
            return {'sources': [], 'dl_url': ''}
        
        # Fetch iframe
        iframe_resp = scraper.get(iframe_src, timeout=30)
        iframe_html = iframe_resp.text
        
        iframe_soup = BeautifulSoup(iframe_html, 'html.parser')
        token_meta = iframe_soup.find('meta', attrs={'name': 'x-secure-token'})
        
        if not token_meta:
            log.error(f"No token meta found in iframe")
            return {'sources': [], 'dl_url': ''}
        
        secure_token = token_meta.get('content', '').replace('sha512-', '')
        
        # Decrypt using ROT13
        rotated_sha = _rot13(secure_token)
        decrypted_b64 = _rot13(base64.b64decode(rotated_sha).decode())
        decrypted_json = base64.b64decode(decrypted_b64).decode()
        
        decrypted_data = json.loads(decrypted_json)
        
        # Make API call
        api_url = decrypted_data.get('uri', 'https://hentaihaven.xxx/wp-content/plugins/player-logic/') + 'api.php'
        
        files = {
            'action': (None, 'zarat_get_data_player_ajax'),
            'a': (None, decrypted_data['en']),
            'b': (None, decrypted_data['iv']),
        }
        
        api_resp = scraper.post(api_url, files=files, timeout=30)
        api_response = api_resp.json()
        
        sources = api_response.get('data', {}).get('sources', [])
        thumbnail = api_response.get('data', {}).get('image', '')
        
        # Format sources
        formatted_sources = []
        for src in sources:
            formatted_sources.append({
                'label': src.get('label', ''),
                'url': src.get('src', ''),
                'type': src.get('type', ''),
            })
        
        dl_url = formatted_sources[0]['url'] if formatted_sources else ''
        
        return {
            'sources': formatted_sources,
            'thumbnail': thumbnail,
            'dl_url': dl_url,
        }
    
    except Exception as e:
        log.error(f"Failed to get streams: {e}")
        import traceback
        traceback.print_exc()
        return {'sources': [], 'dl_url': ''}
