"""Web scraping and search service"""
import re
from typing import List, Dict
from urllib.parse import urlparse, parse_qs
import trafilatura
import httpx
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound
from api.settings import get_api_key

class WebScraper:
    """Service for web search and scraping"""

    async def search_web(self, query: str, max_results: int = 20, offset: int = 0, freshness: str = None) -> List[Dict]:
        """Search the web using Brave Search API with pagination via freshness filters"""
        brave_api_key = get_api_key("brave_api_key")

        if not brave_api_key:
            raise ValueError("Brave Search API key not configured. Please add it in Settings.")

        try:
            # Brave API max count is 20, offset max is 9 on free tier
            # Use freshness filter as a workaround for more diverse results
            api_count = min(max_results, 20)
            
            params = {
                "q": query,
                "count": api_count,
            }
            
            # Add freshness filter if specified (pd=past day, pw=past week, pm=past month, py=past year)
            if freshness:
                params["freshness"] = freshness
            
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    headers={
                        "Accept": "application/json",
                        "Accept-Encoding": "gzip",
                        "X-Subscription-Token": brave_api_key
                    },
                    params=params,
                    timeout=10.0
                )

                if response.status_code != 200:
                    raise ValueError(f"Brave Search API returned status {response.status_code}: {response.text}")

                data = response.json()
                results = []

                for result in data.get("web", {}).get("results", []):
                    results.append({
                        "title": result.get("title", ""),
                        "snippet": result.get("description", ""),
                        "url": result.get("url", "")
                    })

                return results
        except Exception as e:
            raise ValueError(f"Web search failed: {str(e)}")

    async def scrape_urls(self, urls: List[str]) -> List[Dict]:
        """Scrape content from URLs (including YouTube)"""
        results = []

        for url in urls:
            try:
                # Check if it's a YouTube URL
                if self._is_youtube_url(url):
                    result = await self._scrape_youtube(url)
                else:
                    result = await self._scrape_web_page(url)

                results.append(result)
            except Exception as e:
                results.append({
                    "success": False,
                    "url": url,
                    "error": str(e)
                })

        return results

    def _is_youtube_url(self, url: str) -> bool:
        """Check if URL is a YouTube video"""
        youtube_regex = r'(youtube\.com|youtu\.be)'
        return bool(re.search(youtube_regex, url))

    def _extract_youtube_id(self, url: str) -> str:
        """Extract video ID from YouTube URL"""
        # Handle youtu.be short URLs
        if 'youtu.be' in url:
            return url.split('/')[-1].split('?')[0]

        # Handle youtube.com URLs
        parsed = urlparse(url)
        if parsed.hostname in ('www.youtube.com', 'youtube.com'):
            if parsed.path == '/watch':
                return parse_qs(parsed.query).get('v', [None])[0]
            elif parsed.path.startswith('/embed/'):
                return parsed.path.split('/')[2]
            elif parsed.path.startswith('/v/'):
                return parsed.path.split('/')[2]

        return None

    async def _scrape_youtube(self, url: str) -> Dict:
        """Scrape YouTube video transcript"""
        video_id = self._extract_youtube_id(url)

        if not video_id:
            return {
                "success": False,
                "url": url,
                "error": "Could not extract video ID from URL"
            }

        try:
            # Get transcript - newer API uses fetch() method
            ytt_api = YouTubeTranscriptApi()
            transcript_list = ytt_api.fetch(video_id)
            transcript_text = " ".join([entry.text for entry in transcript_list])

            # Get video title (we'd need youtube-dl or similar for full metadata)
            # For now, use a simple title
            title = f"YouTube Video {video_id}"

            word_count = len(transcript_text.split())
            char_count = len(transcript_text)

            return {
                "success": True,
                "url": url,
                "title": title,
                "author": None,
                "date": None,
                "text": transcript_text,
                "word_count": word_count,
                "char_count": char_count
            }
        except (TranscriptsDisabled, NoTranscriptFound) as e:
            return {
                "success": False,
                "url": url,
                "error": f"Transcript not available: {str(e)}"
            }
        except Exception as e:
            return {
                "success": False,
                "url": url,
                "error": str(e)
            }

    async def _scrape_web_page(self, url: str) -> Dict:
        """Scrape content from web page using trafilatura"""
        try:
            downloaded = trafilatura.fetch_url(url)

            if not downloaded:
                return {
                    "success": False,
                    "url": url,
                    "error": "Failed to download page"
                }

            # Extract text content
            text = trafilatura.extract(
                downloaded,
                include_comments=False,
                include_tables=True,
                no_fallback=False
            )

            if not text:
                return {
                    "success": False,
                    "url": url,
                    "error": "Failed to extract text from page"
                }

            # Extract metadata
            metadata = trafilatura.extract_metadata(downloaded)

            title = metadata.title if metadata and metadata.title else url
            author = metadata.author if metadata and metadata.author else None
            date = metadata.date if metadata and metadata.date else None

            word_count = len(text.split())
            char_count = len(text)

            return {
                "success": True,
                "url": url,
                "title": title,
                "author": author,
                "date": date,
                "text": text,
                "word_count": word_count,
                "char_count": char_count
            }
        except Exception as e:
            return {
                "success": False,
                "url": url,
                "error": str(e)
            }

web_scraper = WebScraper()
