"""Web scraping and search service"""
import asyncio
import re
from typing import List, Dict
from urllib.parse import urlparse, parse_qs
import trafilatura
import httpx
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound, VideoUnavailable
from api.settings import get_api_key

# Timeouts
SCRAPE_TIMEOUT = 30.0   # total timeout per URL
MAX_CONCURRENT = 5      # max parallel scrapes


class WebScraper:
    """Service for web search and scraping"""

    def __init__(self):
        pass

    async def search_web(self, query: str, max_results: int = 20, offset: int = 0, freshness: str = None) -> List[Dict]:
        """Search the web using Brave Search API with pagination via freshness filters"""
        brave_api_key = get_api_key("brave_api_key")

        if not brave_api_key:
            raise ValueError("Brave Search API key not configured. Please add it in Settings.")

        try:
            api_count = min(max_results, 20)
            
            params = {
                "q": query,
                "count": api_count,
            }
            
            if freshness:
                params["freshness"] = freshness
            
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    headers={
                        "Accept": "application/json",
                        "Accept-Encoding": "gzip",
                        "X-Subscription-Token": brave_api_key
                    },
                    params=params
                )

                if response.status_code != 200:
                    raise ValueError(f"Brave Search API returned status {response.status_code}")

                data = response.json()
                results = []

                for result in data.get("web", {}).get("results", []):
                    snippet = result.get("description", "")
                    # Estimate read time from snippet (snippets are ~10% of content)
                    words = len(snippet.split()) * 8
                    minutes = max(1, round(words / 238))
                    if minutes > 30:
                        minutes = 30
                    read_time = f"{minutes} min read" if minutes < 5 else f"~{5 * round(minutes / 5)} min read"
                    
                    results.append({
                        "title": result.get("title", ""),
                        "snippet": snippet,
                        "url": result.get("url", ""),
                        "read_time": read_time,
                    })

                return results
        except Exception as e:
            raise ValueError(f"Web search failed: {str(e)}")

    async def scrape_urls(self, urls: List[str]) -> List[Dict]:
        """Scrape content from URLs in parallel with timeout protection"""
        if not urls:
            return []
        
        # Create tasks with individual timeouts
        async def scrape_with_timeout(url: str) -> Dict:
            try:
                return await asyncio.wait_for(
                    self._scrape_single(url),
                    timeout=SCRAPE_TIMEOUT
                )
            except asyncio.TimeoutError:
                return {
                    "success": False,
                    "url": url,
                    "error": f"Timed out after {SCRAPE_TIMEOUT}s"
                }
            except Exception as e:
                return {
                    "success": False,
                    "url": url,
                    "error": str(e)
                }
        
        # Use semaphore to limit concurrency
        semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        
        async def bounded_scrape(url: str) -> Dict:
            async with semaphore:
                return await scrape_with_timeout(url)
        
        # Run all scrapes in parallel (bounded by semaphore)
        tasks = [bounded_scrape(url) for url in urls]
        results = await asyncio.gather(*tasks)
        
        return list(results)
    
    async def _scrape_single(self, url: str) -> Dict:
        """Scrape a single URL - routes to appropriate handler"""
        if self._is_youtube_url(url):
            return await self._scrape_youtube(url)
        elif self._is_arxiv_url(url):
            return await self._scrape_arxiv_pdf(url)
        else:
            return await self._scrape_web_page(url)

    def _is_arxiv_url(self, url: str) -> bool:
        """Check if URL is an arxiv.org paper link"""
        return bool(re.search(r'arxiv\.org/(abs|html|pdf)/', url))

    def _arxiv_to_pdf_url(self, url: str) -> str:
        """Convert any arxiv paper URL to its PDF download URL"""
        # arxiv.org/abs/2301.12345 → arxiv.org/pdf/2301.12345
        # arxiv.org/html/2301.12345v2 → arxiv.org/pdf/2301.12345v2
        # arxiv.org/pdf/2301.12345 → stays as-is
        pdf_url = re.sub(r'arxiv\.org/(abs|html)/', 'arxiv.org/pdf/', url)
        # Ensure .pdf extension for direct download
        if not pdf_url.endswith('.pdf'):
            pdf_url = pdf_url.rstrip('/') + '.pdf'
        return pdf_url

    async def _scrape_arxiv_pdf(self, url: str) -> Dict:
        """Download and extract text from an arxiv paper PDF"""
        pdf_url = self._arxiv_to_pdf_url(url)
        print(f"[WebScraper] arxiv detected: {url} → PDF: {pdf_url}")

        try:
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                response = await client.get(pdf_url)
                if response.status_code != 200:
                    print(f"[WebScraper] arxiv PDF download failed: HTTP {response.status_code}")
                    # Fall back to HTML scraping if PDF fails
                    return await self._scrape_web_page(url)

                pdf_bytes = response.content
                if len(pdf_bytes) < 1000:
                    return await self._scrape_web_page(url)

            # Extract text from PDF using document_processor
            from services.document_processor import document_processor
            text = await document_processor._extract_from_pdf(pdf_bytes)

            if not text or len(text.strip()) < 200:
                print(f"[WebScraper] arxiv PDF extraction yielded thin text, falling back to HTML")
                return await self._scrape_web_page(url)

            # Extract arxiv ID for title fetching
            arxiv_id_match = re.search(r'(\d{4}\.\d{4,5})(v\d+)?', url)
            arxiv_id = arxiv_id_match.group(0) if arxiv_id_match else None

            # Try to get title from first lines of PDF text
            title = f"arxiv:{arxiv_id}" if arxiv_id else url
            first_lines = text[:500].split('\n')
            for line in first_lines:
                clean = line.strip().strip('#').strip()
                if len(clean) > 15 and len(clean) < 200 and not clean.startswith('==='):
                    title = clean
                    break

            word_count = len(text.split())
            print(f"[WebScraper] arxiv PDF extracted: {word_count} words, title: {title[:80]}")

            return {
                "success": True,
                "url": url,
                "title": title,
                "author": None,
                "date": None,
                "text": text,
                "word_count": word_count,
                "char_count": len(text)
            }

        except Exception as e:
            print(f"[WebScraper] arxiv PDF extraction failed: {e}, falling back to HTML")
            return await self._scrape_web_page(url)

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

    async def _get_youtube_title(self, video_id: str) -> str:
        """Fetch YouTube video title using oEmbed API (no API key required)"""
        try:
            oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(oembed_url)
                if response.status_code == 200:
                    data = response.json()
                    return data.get("title", f"YouTube Video {video_id}")
        except Exception:
            pass
        return f"YouTube Video {video_id}"

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

            # Fetch actual video title using oEmbed API
            title = await self._get_youtube_title(video_id)

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
        except TranscriptsDisabled:
            return {
                "success": False,
                "url": url,
                "error": "Subtitles are disabled for this video"
            }
        except NoTranscriptFound:
            return {
                "success": False,
                "url": url,
                "error": "No transcript available for this video"
            }
        except VideoUnavailable:
            return {
                "success": False,
                "url": url,
                "error": "Video is unavailable or private"
            }
        except Exception as e:
            return {
                "success": False,
                "url": url,
                "error": str(e)
            }

    async def _scrape_web_page(self, url: str) -> Dict:
        """Scrape content from web page using trafilatura.
        
        Runs blocking trafilatura calls in thread pool to avoid blocking event loop.
        """
        try:
            loop = asyncio.get_event_loop()
            
            # Run blocking trafilatura.fetch_url in thread pool
            downloaded = await loop.run_in_executor(None, trafilatura.fetch_url, url)

            if not downloaded:
                return {
                    "success": False,
                    "url": url,
                    "error": "Failed to download page"
                }

            # Extract image references from HTML before trafilatura strips them
            image_refs = self._extract_image_references(downloaded, base_url=url)

            # Run blocking trafilatura.extract in thread pool
            def extract_content(html):
                return trafilatura.extract(
                    html,
                    include_comments=False,
                    include_tables=True,
                    no_fallback=False
                )
            
            text = await loop.run_in_executor(None, extract_content, downloaded)

            if not text:
                return {
                    "success": False,
                    "url": url,
                    "error": "Failed to extract text from page"
                }

            # Append image references (additive-only — original text untouched)
            if image_refs:
                text = text + image_refs

            # Run blocking metadata extraction in thread pool
            metadata = await loop.run_in_executor(None, trafilatura.extract_metadata, downloaded)

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
                "char_count": char_count,
                "html": downloaded,  # Raw HTML for optional background vision processing
            }
        except Exception as e:
            return {
                "success": False,
                "url": url,
                "error": str(e)
            }

    async def scrape_with_html(self, url: str) -> Dict:
        """Scrape a URL and also return the raw HTML for link extraction.
        
        Returns the same dict as _scrape_single but with an extra 'html' key.
        """
        if self._is_youtube_url(url):
            result = await self._scrape_youtube(url)
            result["html"] = None
            return result

        if self._is_arxiv_url(url):
            result = await self._scrape_arxiv_pdf(url)
            result["html"] = None
            return result

        try:
            loop = asyncio.get_event_loop()
            downloaded = await loop.run_in_executor(None, trafilatura.fetch_url, url)
            if not downloaded:
                return {"success": False, "url": url, "error": "Failed to download page", "html": None}

            def extract_content(html):
                return trafilatura.extract(html, include_comments=False, include_tables=True, no_fallback=False)

            # Extract image references before trafilatura strips them
            image_refs = self._extract_image_references(downloaded, base_url=url)

            text = await loop.run_in_executor(None, extract_content, downloaded)
            metadata = await loop.run_in_executor(None, trafilatura.extract_metadata, downloaded)

            title = metadata.title if metadata and metadata.title else url
            author = metadata.author if metadata and metadata.author else None
            date = metadata.date if metadata and metadata.date else None

            full_text = (text or "")
            if image_refs and full_text:
                full_text = full_text + image_refs

            return {
                "success": True,
                "url": url,
                "title": title,
                "author": author,
                "date": date,
                "text": full_text,
                "word_count": len(full_text.split()),
                "char_count": len(full_text),
                "html": downloaded,
            }
        except Exception as e:
            return {"success": False, "url": url, "error": str(e), "html": None}

    def is_index_page(self, url: str, html: str, extracted_text: str) -> bool:
        """Detect whether a page is an index/listing page rather than an article.
        
        Signals:
        - URL pattern (ends with /, contains /news/, /category/, /tag/, /topics/)
        - Low word-to-link ratio (many links, short text between them)
        - Title contains listing keywords
        - Many same-domain article-like links
        """
        from bs4 import BeautifulSoup
        from urllib.parse import urlparse

        parsed_url = urlparse(url)
        base_domain = parsed_url.netloc.lower().replace("www.", "")

        # URL pattern heuristics
        path = parsed_url.path.rstrip("/").lower()
        index_path_signals = ["/news", "/articles", "/category", "/categories",
                              "/tag", "/tags", "/topics", "/topic", "/archive",
                              "/latest", "/recent", "/blog", "/stories", "/feed"]
        url_looks_like_index = (
            parsed_url.path.endswith("/")
            or any(path.endswith(s) or s + "/" in path for s in index_path_signals)
        )

        # Parse HTML for links
        soup = BeautifulSoup(html, "lxml")
        all_links = soup.find_all("a", href=True)

        # Count internal article-like links
        article_links = []
        nav_keywords = {"home", "about", "contact", "login", "signup", "register",
                        "privacy", "terms", "cookie", "search", "faq", "help",
                        "subscribe", "newsletter", "sitemap", "careers", "advertise"}

        for a in all_links:
            href = a["href"]
            # Resolve relative URLs
            if href.startswith("/"):
                href = f"{parsed_url.scheme}://{parsed_url.netloc}{href}"
            if not href.startswith("http"):
                continue

            link_parsed = urlparse(href)
            link_domain = link_parsed.netloc.lower().replace("www.", "")
            link_path = link_parsed.path.lower().rstrip("/")

            # Must be same domain
            if link_domain != base_domain:
                continue
            # Skip navigation / utility links
            if any(nav in link_path for nav in nav_keywords):
                continue
            # Skip anchors, assets
            if link_path.endswith((".png", ".jpg", ".css", ".js", ".xml", ".pdf")):
                continue
            # Must have path depth > 1 segment (not just "/")
            segments = [s for s in link_path.split("/") if s]
            if len(segments) < 2:
                continue
            # Must not be the same as current URL path
            if link_path == path:
                continue

            anchor_text = a.get_text(strip=True)
            if anchor_text and len(anchor_text) > 10:
                article_links.append(href)

        unique_articles = list(dict.fromkeys(article_links))  # dedupe, preserve order
        text_word_count = len((extracted_text or "").split())

        # Decision: many article links + short/thin content = index page
        if len(unique_articles) >= 8 and url_looks_like_index:
            return True
        if len(unique_articles) >= 5 and text_word_count < 800:
            return True
        if len(unique_articles) >= 12:
            return True

        return False

    def extract_article_links(self, url: str, html: str, max_links: int = 10) -> list:
        """Extract article-like links from an index/listing page.
        
        Returns list of dicts: [{url, title}, ...]
        """
        from bs4 import BeautifulSoup
        from urllib.parse import urlparse

        parsed_url = urlparse(url)
        base_domain = parsed_url.netloc.lower().replace("www.", "")
        current_path = parsed_url.path.lower().rstrip("/")

        soup = BeautifulSoup(html, "lxml")
        all_links = soup.find_all("a", href=True)

        nav_keywords = {"home", "about", "contact", "login", "signup", "register",
                        "privacy", "terms", "cookie", "search", "faq", "help",
                        "subscribe", "newsletter", "sitemap", "careers", "advertise"}

        seen_urls = set()
        articles = []

        for a in all_links:
            href = a["href"]
            if href.startswith("/"):
                href = f"{parsed_url.scheme}://{parsed_url.netloc}{href}"
            if not href.startswith("http"):
                continue

            link_parsed = urlparse(href)
            link_domain = link_parsed.netloc.lower().replace("www.", "")
            link_path = link_parsed.path.lower().rstrip("/")

            if link_domain != base_domain:
                continue
            if any(nav in link_path for nav in nav_keywords):
                continue
            if link_path.endswith((".png", ".jpg", ".css", ".js", ".xml", ".pdf")):
                continue
            segments = [s for s in link_path.split("/") if s]
            if len(segments) < 2:
                continue
            if link_path == current_path:
                continue

            # Normalise
            clean_url = f"{link_parsed.scheme}://{link_parsed.netloc}{link_parsed.path}"
            if clean_url in seen_urls:
                continue
            seen_urls.add(clean_url)

            anchor_text = a.get_text(strip=True)
            if not anchor_text or len(anchor_text) < 10:
                continue

            articles.append({"url": clean_url, "title": anchor_text})

            if len(articles) >= max_links:
                break

        return articles


    # ── Image reference extraction ─────────────────────────────────────────────

    def _extract_image_references(self, html: str, base_url: str = "") -> str:
        """Extract image alt text and figure captions from HTML.

        Runs BEFORE trafilatura strips the HTML, preserving visual context
        that would otherwise be lost.  Returns a formatted text section
        to append to the scraped text, or "" if no meaningful images found.

        This is a lightweight, additive-only operation — no downloads,
        no vision model calls.  Never raises.
        """
        try:
            from bs4 import BeautifulSoup
            from urllib.parse import urljoin

            soup = BeautifulSoup(html, "html.parser")
            refs: List[str] = []
            seen_alts: set = set()

            # 1. <figure> with <figcaption> — richest signal
            for fig in soup.find_all("figure"):
                caption_tag = fig.find("figcaption")
                caption = caption_tag.get_text(strip=True) if caption_tag else ""
                img = fig.find("img")
                alt = img.get("alt", "").strip() if img else ""

                desc = caption or alt
                if not desc or len(desc) < 5:
                    continue
                desc_key = desc[:60].lower()
                if desc_key in seen_alts:
                    continue
                seen_alts.add(desc_key)
                # Also mark the alt text as seen so pass 2 skips the same <img>
                if alt and caption:
                    seen_alts.add(alt[:60].lower())
                label = "[FIGURE] " if caption else "[IMAGE] "
                refs.append(f"{label}{desc}")

            # 2. Standalone <img> with alt text (not already captured in figures)
            for img in soup.find_all("img"):
                alt = img.get("alt", "").strip()
                if not alt or len(alt) < 5:
                    continue
                # Skip common decorative alt text
                alt_lower = alt.lower()
                if alt_lower in ("image", "photo", "picture", "icon", "logo",
                                 "banner", "avatar", "thumbnail", "img"):
                    continue
                alt_key = alt[:60].lower()
                if alt_key in seen_alts:
                    continue
                seen_alts.add(alt_key)
                refs.append(f"[IMAGE] {alt}")

            if not refs:
                return ""

            # Cap at 20 references to avoid bloating the text
            refs = refs[:20]
            section = "\n\n=== IMAGE REFERENCES ===\n"
            section += "\n".join(refs)
            return section

        except Exception:
            # Never break scraping — if parsing fails, just skip images
            return ""


web_scraper = WebScraper()
