"""Web scraping and search service"""
import asyncio
import logging
import re
from typing import List, Dict, Optional
from urllib.parse import urlparse, parse_qs
import trafilatura
import httpx
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound, VideoUnavailable
from api.settings import get_api_key

logger = logging.getLogger(__name__)

# Quiet trafilatura's per-URL parse-failure noise (it logs ERROR/WARNING like
# "parsed tree length: 1" / "empty HTML tree" when a scraped URL returns non-HTML
# — a PDF/redirect/empty page). We handle failed scrapes via our own fallback, so
# these are benign and just clutter the log during collection. (2026-06-30)
logging.getLogger("trafilatura").setLevel(logging.CRITICAL)

# Timeouts
SCRAPE_TIMEOUT = 120.0  # total timeout per URL (documents can be large)
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
                    url = result.get("url", "")
                    
                    # Don't show read_time for video URLs — it's meaningless
                    is_video = any(d in url for d in ("youtube.com/watch", "youtu.be/", "vimeo.com/"))
                    read_time = ""
                    if not is_video:
                        # Estimate read time from snippet (snippets are ~10% of content)
                        words = len(snippet.split()) * 8
                        minutes = max(1, round(words / 238))
                        if minutes > 30:
                            minutes = 30
                        read_time = f"{minutes} min read" if minutes < 5 else f"~{5 * round(minutes / 5)} min read"
                    
                    results.append({
                        "title": result.get("title", ""),
                        "snippet": snippet,
                        "url": url,
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
    
    async def _scrape_single(self, url: str, extension_fallback: bool = False) -> Dict:
        """Scrape a single URL - routes to appropriate handler.
        
        Set extension_fallback=True to enable Phase 3 browser extension
        fallback for URLs that Playwright+httpx can't handle.
        """
        if self._is_youtube_url(url):
            return await self._scrape_youtube(url)
        elif self._is_arxiv_url(url):
            return await self._scrape_arxiv_pdf(url)
        elif self._is_document_url(url):
            return await self._scrape_remote_document(url)
        elif self._get_google_export_url(url):
            return await self._scrape_remote_document(self._get_google_export_url(url), original_url=url)
        else:
            return await self._scrape_web_page(url, extension_fallback=extension_fallback)

    def _is_document_url(self, url: str) -> bool:
        """Check if URL points to a downloadable document (PDF, PPTX, DOCX, etc.)"""
        url_lower = url.lower().split('?')[0].split('#')[0]
        return any(url_lower.endswith(ext) for ext in (
            '.pdf', '.pptx', '.docx', '.xlsx', '.doc', '.ppt', '.xls',
        ))

    def _get_google_export_url(self, url: str) -> str:
        """Convert Google Docs/Slides/Sheets URL to export URL. Returns '' if not a Google doc."""
        m = re.search(r'docs\.google\.com/document/d/([a-zA-Z0-9_-]+)', url)
        if m:
            return f"https://docs.google.com/document/d/{m.group(1)}/export?format=txt"
        m = re.search(r'docs\.google\.com/presentation/d/([a-zA-Z0-9_-]+)', url)
        if m:
            return f"https://docs.google.com/presentation/d/{m.group(1)}/export/pptx"
        m = re.search(r'docs\.google\.com/spreadsheets/d/([a-zA-Z0-9_-]+)', url)
        if m:
            return f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv"
        return ""

    async def _scrape_remote_document(self, url: str, original_url: str = None) -> Dict:
        """Download and extract text from a remote document (PDF, PPTX, DOCX, etc.)"""
        display_url = original_url or url
        try:
            async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                response = await client.get(url, headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "*/*",
                })
                if response.status_code != 200:
                    return {"success": False, "url": display_url,
                            "error": f"Failed to download file (HTTP {response.status_code})"}
                content_bytes = response.content

            if len(content_bytes) < 100:
                return {"success": False, "url": display_url, "error": "Downloaded file is empty"}

            # Determine filename for type detection
            url_lower = url.lower().split('?')[0].split('#')[0]
            if url_lower.endswith('.pptx') or '/export/pptx' in url_lower:
                filename = "document.pptx"
            elif url_lower.endswith('.docx'):
                filename = "document.docx"
            elif url_lower.endswith('.xlsx') or 'format=csv' in url.lower():
                filename = "document.csv" if 'format=csv' in url.lower() else "document.xlsx"
            elif url_lower.endswith('.pdf') or b'%PDF' in content_bytes[:10]:
                filename = "document.pdf"
            else:
                # Try to detect from content
                if b'%PDF' in content_bytes[:10]:
                    filename = "document.pdf"
                elif b'PK' in content_bytes[:4]:
                    filename = "document.pptx"  # ZIP-based (could be docx/pptx/xlsx)
                else:
                    # Treat as plain text
                    filename = "document.txt"

            from services.document_processor import document_processor
            text = await document_processor._extract_text(content_bytes, filename)

            if not text or len(text.strip()) < 50:
                return {"success": False, "url": display_url,
                        "error": "Could not extract text from downloaded file"}

            # Try to extract title from first lines
            title = display_url
            first_lines = text[:500].split('\n')
            for line in first_lines:
                clean = line.strip().strip('#').strip()
                if 15 < len(clean) < 200 and not clean.startswith('==='):
                    title = clean
                    break

            word_count = len(text.split())
            print(f"[WebScraper] Remote doc extracted: {word_count} words from {display_url}")

            return {
                "success": True,
                "url": display_url,
                "title": title,
                "author": None,
                "date": None,
                "text": text,
                "word_count": word_count,
                "char_count": len(text),
            }
        except Exception as e:
            print(f"[WebScraper] Remote document extraction failed: {e}")
            return {"success": False, "url": display_url, "error": str(e)}

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
            elif parsed.path.startswith('/shorts/'):
                return parsed.path.split('/')[2]
            elif parsed.path.startswith('/live/'):
                return parsed.path.split('/')[2]

        return None

    async def _get_youtube_title(self, video_id: str) -> str:
        """Fetch YouTube video title using oEmbed API (no API key required).
        
        Retries once with a longer timeout if the first attempt fails.
        """
        oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
        for timeout in (5.0, 10.0):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.get(oembed_url)
                    if response.status_code == 200:
                        data = response.json()
                        return data.get("title", f"YouTube Video {video_id}")
            except Exception:
                continue
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

    async def _scrape_web_page(self, url: str, extension_fallback: bool = False) -> Dict:
        """Scrape content from web page using Playwright + trafilatura.
        
        Phase 1 — FETCH: Playwright (real Chromium) with httpx fallback.
        Phase 2 — EXTRACT: trafilatura parses the HTML into clean text.
        Phase 3 — EXTENSION FALLBACK: If Phase 1 or 2 fail and extension_fallback
                  is True, queue the URL for the LocalBook browser extension to
                  scrape in the user's real browser (bypasses bot protection, etc.).
        """
        try:
            # ── Phase 1: FETCH HTML ──────────────────────────────────────
            downloaded = await self._fetch_html(url)

            if not downloaded:
                if extension_fallback:
                    return await self._try_extension_fallback(url, "both Playwright and httpx failed to download")
                return {
                    "success": False,
                    "url": url,
                    "error": "Failed to download page (both Playwright and httpx failed)"
                }

            # ── Phase 2: EXTRACT content ─────────────────────────────────
            loop = asyncio.get_event_loop()
            # Extract image references from HTML before trafilatura strips them.
            # Offloaded to a thread (2026-06-30 perf fix): this BeautifulSoup parse
            # of the full HTML ran ON the event loop and caused multi-second loop
            # stalls while a collection scraped 10-15 pages back-to-back.
            image_refs = await loop.run_in_executor(
                None, self._extract_image_references, downloaded, url
            )

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
                if extension_fallback:
                    return await self._try_extension_fallback(url, "trafilatura extracted no text from HTML")
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

            # If Playwright+trafilatura returned something but it's very shallow,
            # try the extension as well — it often gets more from JS-heavy pages.
            if extension_fallback and char_count < 500:
                logger.info(f"[Scraper] Phase 2 result shallow ({char_count} chars) for {url} — trying extension fallback")
                ext_result = await self._try_extension_fallback(url, f"shallow result ({char_count} chars)")
                if ext_result.get("success") and len(ext_result.get("text", "")) > char_count:
                    return ext_result

            return {
                "success": True,
                "url": url,
                "title": title,
                "author": author,
                "date": date,
                "text": text,
                "word_count": word_count,
                "char_count": char_count,
                "html": downloaded,
            }
        except Exception as e:
            return {
                "success": False,
                "url": url,
                "error": str(e)
            }

    async def _try_extension_fallback(self, url: str, reason: str) -> Dict:
        """Phase 3: Ask the browser extension to scrape a URL.
        
        Opens the URL in the user's default browser and waits for the
        extension to extract content and post it back.  Returns a standard
        scrape result dict.  If the extension is not available or times out,
        returns a failure dict — no worse than the original failure.
        """
        try:
            from services.browser_scrape_queue import browser_scrape_queue
            logger.info(f"[Scraper] Phase 3 extension fallback for {url} (reason: {reason})")
            print(f"🌐 Extension fallback: opening {url} in browser (reason: {reason})")

            result = await browser_scrape_queue.request_scrape(url, open_browser=True)
            if result and result.get("success"):
                logger.info(f"[Scraper] Extension fallback SUCCESS for {url}: {result.get('char_count', 0)} chars")
                return result

            logger.info(f"[Scraper] Extension fallback returned no usable content for {url}")
        except Exception as ext_err:
            logger.warning(f"[Scraper] Extension fallback error for {url}: {ext_err}")

        return {
            "success": False,
            "url": url,
            "error": f"All scrape methods failed ({reason}). Extension fallback unavailable or timed out."
        }

    async def _fetch_html(self, url: str) -> Optional[str]:
        """Fetch raw HTML from a URL. Tries Playwright first, falls back to httpx.
        
        Returns HTML string or None on failure.
        """
        # Strategy 1: Playwright (real Chromium — bypasses Cloudflare)
        try:
            from playwright.async_api import async_playwright
            
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/120.0.0.0 Safari/537.36"
                )
                page = await context.new_page()
                await page.goto(url, wait_until="networkidle", timeout=30000)
                html = await page.content()
                await browser.close()
                
            if html and len(html) > 200:
                return html
        except Exception as e:
            print(f"[WebScraper] Playwright failed for {url}: {e}")

        # Strategy 2: httpx with browser headers
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Referer": "https://www.google.com/",
                "DNT": "1",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            }
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.get(url, headers=headers)
            if response.status_code == 200 and response.text:
                return response.text
        except Exception as e:
            print(f"[WebScraper] httpx fallback also failed for {url}: {e}")

        return None

    async def scrape_with_html(self, url: str, extension_fallback: bool = False) -> Dict:
        """Scrape a URL and also return the raw HTML for link extraction.
        
        Returns the same dict as _scrape_single but with an extra 'html' key.
        Set extension_fallback=True to enable Phase 3 browser extension fallback.
        """
        if self._is_youtube_url(url):
            result = await self._scrape_youtube(url)
            result["html"] = None
            return result

        if self._is_arxiv_url(url):
            result = await self._scrape_arxiv_pdf(url)
            result["html"] = None
            return result

        if self._is_document_url(url):
            result = await self._scrape_remote_document(url)
            result["html"] = None
            return result

        google_export = self._get_google_export_url(url)
        if google_export:
            result = await self._scrape_remote_document(google_export, original_url=url)
            result["html"] = None
            return result

        # Use the same Playwright+httpx fetch pipeline as _scrape_web_page
        result = await self._scrape_web_page(url, extension_fallback=extension_fallback)
        if "html" not in result:
            result["html"] = None
        return result

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

        # If current page is root (/), single-segment paths like /article-slug are articles
        current_segments = [s for s in path.split("/") if s]
        min_segments = 1 if len(current_segments) == 0 else 2

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
            # Must have enough path depth (relaxed for root pages)
            segments = [s for s in link_path.split("/") if s]
            if len(segments) < min_segments:
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
        # Blog detection: very thin content + several descriptive links = index/blog
        if len(unique_articles) >= 3 and text_word_count < 300:
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

        # If current page is root (/), single-segment paths are articles
        current_segments = [s for s in current_path.split("/") if s]
        min_segments = 1 if len(current_segments) == 0 else 2

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
            if len(segments) < min_segments:
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


    # ─── Subscription Resolution ──────────────────────────────────────
    
    async def resolve_subscription_target(self, url: str) -> Dict:
        """Resolve a URL to its underlying subscription feed.
        
        Returns a dict with:
            source_type: 'youtube_channel' | 'rss_feed' | 'feed_page' | 'web_page'
            feed_url: The RSS/Atom feed URL (if discovered)
            channel_id: YouTube channel ID (if applicable)
            channel_name: Human-readable channel/source name
            immediate_url: URL to scrape immediately for instant value
            default_schedule: Suggested schedule frequency
        """
        parsed = urlparse(url)
        domain = (parsed.hostname or "").lower().replace("www.", "")
        
        result = {
            "source_type": "web_page",
            "feed_url": None,
            "channel_id": None,
            "channel_name": None,
            "immediate_url": url,
            "default_schedule": "weekly",
        }
        
        # ── YouTube: video, channel, or playlist ──
        if domain in ("youtube.com", "youtu.be", "m.youtube.com"):
            return await self._resolve_youtube_subscription(url, parsed, result)
        
        # ── Substack ──
        if domain.endswith("substack.com"):
            pub = domain.split(".substack.com")[0]
            result["source_type"] = "rss_feed"
            result["feed_url"] = f"https://{pub}.substack.com/feed"
            result["channel_name"] = f"Substack: {pub}"
            result["default_schedule"] = "daily"
            return result
        
        # ── Generic: try RSS autodiscovery from HTML ──
        try:
            scraped = await self.scrape_with_html(url)
            raw_html = scraped.get("html", "")
            
            # Cache the scraped data so caller doesn't need to re-fetch
            result["_scraped"] = scraped
            
            if raw_html:
                # Check for RSS/Atom <link> tags
                feed_url = self._discover_rss_from_html(raw_html, url)
                if feed_url:
                    result["source_type"] = "rss_feed"
                    result["feed_url"] = feed_url
                    result["channel_name"] = scraped.get("title", domain)
                    result["default_schedule"] = "daily"
                    return result
                
                # Check if it's an index/feed page
                is_index = self.is_index_page(url, raw_html, scraped.get("text", ""))
                if is_index:
                    result["source_type"] = "feed_page"
                    result["channel_name"] = scraped.get("title", domain)
                    result["default_schedule"] = "weekly"
                    return result
            
            # Fallback: regular web page
            result["channel_name"] = scraped.get("title", domain)
        except Exception as e:
            print(f"[WebScraper] Subscription resolution failed for {url}: {e}")
            result["channel_name"] = domain
        
        return result
    
    async def _resolve_youtube_subscription(self, url: str, parsed, result: Dict) -> Dict:
        """Resolve YouTube URLs to channel RSS feeds.
        
        Returns result dict with:
          - source_type, feed_url, channel_id, channel_name, immediate_url, default_schedule
          - playlist_videos: list of {"video_id", "title", "url"} if a playlist was detected
          - playlist_id: the playlist ID if detected
        """
        path = parsed.path or ""
        query_params = parse_qs(parsed.query or "")
        
        channel_id = None
        channel_name = None
        immediate_url = url
        playlist_id = query_params.get("list", [None])[0]  # &list=PLxxxxxx
        playlist_videos = []
        
        print(f"[YT-Resolve] url={url}  path={path}  playlist_id={playlist_id}  host={parsed.hostname}")
        
        # ── Channel URL: /@handle, /c/name, /channel/ID ──
        if path.startswith("/@") or path.startswith("/c/") or path.startswith("/channel/"):
            print(f"[YT-Resolve] Detected: CHANNEL URL")
            channel_id = await self._resolve_youtube_channel_id(url)
            parts = path.split("/")
            channel_name = parts[1] if len(parts) > 1 else "YouTube Channel"
            if channel_name.startswith("@"):
                channel_name = channel_name[1:]
            immediate_url = url
            print(f"[YT-Resolve] channel_id={channel_id}  channel_name={channel_name}")
        
        # ── Video URL: extract channel + check for playlist param ──
        elif "/watch" in path or path.startswith("/shorts/") or "youtu.be" in (parsed.hostname or ""):
            video_id = self._extract_youtube_id(url)
            print(f"[YT-Resolve] Detected: VIDEO URL  video_id={video_id}")
            if video_id:
                immediate_url = url
                lookup_url = url
                if "youtu.be" in (parsed.hostname or ""):
                    lookup_url = f"https://www.youtube.com/watch?v={video_id}"
                # Get channel name from oEmbed
                try:
                    oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        resp = await client.get(oembed_url)
                        if resp.status_code == 200:
                            channel_name = resp.json().get("author_name")
                            print(f"[YT-Resolve] oEmbed channel_name={channel_name}")
                except Exception as _oe_err:
                    print(f"[YT-Resolve] oEmbed FAILED for {video_id}: {_oe_err}")
                channel_id = await self._get_channel_from_video(lookup_url)
                print(f"[YT-Resolve] channel_id={channel_id}")
                
                # If video is part of a playlist, extract all playlist videos
                if playlist_id:
                    print(f"[YT-Resolve] Video has playlist param: {playlist_id}")
                    playlist_url = f"https://www.youtube.com/playlist?list={playlist_id}"
                    playlist_videos = await self._extract_playlist_video_ids(playlist_url)
                    print(f"[YT-Resolve] Playlist extraction: {len(playlist_videos)} videos")
                else:
                    print(f"[YT-Resolve] No &list= param in URL — playlist extraction skipped")
        
        # ── Playlist URL: /playlist?list=PLxxxxxx ──
        elif "/playlist" in path:
            print(f"[YT-Resolve] Detected: PLAYLIST URL")
            if not playlist_id:
                playlist_id = query_params.get("list", [None])[0]
            if playlist_id:
                playlist_url = f"https://www.youtube.com/playlist?list={playlist_id}"
                playlist_videos = await self._extract_playlist_video_ids(playlist_url)
                print(f"[YT-Resolve] Playlist extraction: {len(playlist_videos)} videos")
            else:
                print(f"[YT-Resolve] Playlist URL but no list= param found!")
            channel_id = await self._resolve_youtube_channel_id(url)
            immediate_url = url
            print(f"[YT-Resolve] channel_id={channel_id}")
        else:
            print(f"[YT-Resolve] Detected: UNKNOWN YouTube URL pattern")
        
        if channel_id:
            result["source_type"] = "youtube_channel"
            result["feed_url"] = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
            result["channel_id"] = channel_id
            result["channel_name"] = channel_name or f"YouTube Channel ({channel_id[:8]}...)"
            result["immediate_url"] = immediate_url
            result["default_schedule"] = "weekly"
            print(f"[YT-Resolve] RESULT: youtube_channel  feed={result['feed_url']}")
        else:
            # Couldn't resolve channel — treat as single video
            result["source_type"] = "web_page"
            result["channel_name"] = "YouTube Video"
            result["immediate_url"] = immediate_url
            result["default_schedule"] = "manual"
            print(f"[YT-Resolve] RESULT: web_page (no channel resolved)")
        
        # Attach playlist data if found
        if playlist_videos:
            result["playlist_videos"] = playlist_videos
            result["playlist_id"] = playlist_id
            print(f"[YT-Resolve] Attached {len(playlist_videos)} playlist_videos to result")
        else:
            print(f"[YT-Resolve] No playlist_videos to attach")
        
        return result
    
    async def _resolve_youtube_channel_id(self, url: str) -> Optional[str]:
        """Resolve a YouTube channel/page URL to its channel ID by fetching the page HTML."""
        try:
            html = await self._fetch_html(url)
            if html:
                # Look for channel ID in meta tags or canonical links
                # Pattern: "channel_id":"UC..." or /channel/UC...
                match = re.search(r'"(?:externalId|channelId)"\s*:\s*"(UC[a-zA-Z0-9_-]+)"', html)
                if match:
                    return match.group(1)
                match = re.search(r'/channel/(UC[a-zA-Z0-9_-]+)', html)
                if match:
                    return match.group(1)
        except Exception as e:
            print(f"[WebScraper] Could not resolve YouTube channel ID: {e}")
        return None
    
    async def _get_channel_from_video(self, video_url: str) -> Optional[str]:
        """Extract channel ID from a YouTube video URL.

        Strategy (ordered by reliability):
        1. oEmbed API -> get author_url (channel handle) -> fetch channel page -> extract ID
        2. Direct HTML scrape of video page -> regex for channelId in JSON
        """
        video_id = self._extract_youtube_id(video_url)

        # Strategy 1: oEmbed -> channel handle -> channel page -> channel ID
        if video_id:
            try:
                oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(oembed_url)
                    if resp.status_code == 200:
                        data = resp.json()
                        author_url = data.get("author_url", "")
                        if author_url:
                            channel_id = await self._resolve_youtube_channel_id(author_url)
                            if channel_id:
                                print(f"[WebScraper] Resolved channel via oEmbed: {author_url} -> {channel_id}")
                                return channel_id
            except Exception as e:
                print(f"[WebScraper] oEmbed channel resolution failed: {e}")

        # Strategy 2: Direct HTML scrape fallback
        try:
            html = await self._fetch_html(video_url)
            if html:
                match = re.search(r'"channelId"\s*:\s*"(UC[a-zA-Z0-9_-]+)"', html)
                if match:
                    return match.group(1)
                match = re.search(r'/channel/(UC[a-zA-Z0-9_-]+)', html)
                if match:
                    return match.group(1)
        except Exception as e:
            print(f"[WebScraper] Could not get channel from video HTML: {e}")
        return None
    
    async def _extract_playlist_video_ids(self, playlist_url: str, max_videos: int = 20) -> List[Dict]:
        """Extract video IDs and titles from a YouTube playlist page.
        
        Returns list of {"video_id": str, "title": str, "url": str}.
        Uses HTML scraping — no API key required.
        """
        import json as _json
        videos = []
        try:
            print(f"[Playlist] Fetching HTML for {playlist_url}")
            html = await self._fetch_html(playlist_url)
            if not html:
                print(f"[Playlist] _fetch_html returned None/empty for {playlist_url}")
                return videos
            print(f"[Playlist] Got {len(html):,} chars of HTML")
            
            # Strategy 1: Parse ytInitialData JSON blob (re.DOTALL for multi-line)
            match = re.search(r'var\s+ytInitialData\s*=\s*', html)
            if match:
                # Find the JSON object start and use bracket matching
                json_start = match.end()
                json_str = self._extract_json_object(html, json_start)
                if json_str:
                    print(f"[Playlist] Found ytInitialData JSON ({len(json_str):,} chars)")
                    try:
                        data = _json.loads(json_str)
                        top_keys = list(data.keys())[:5]
                        print(f"[Playlist] Top-level keys: {top_keys}")
                        
                        # Navigate to playlist video list
                        browse = data.get("contents", {}).get("twoColumnBrowseResultsRenderer", {})
                        tabs = browse.get("tabs", [])
                        print(f"[Playlist] twoColumnBrowseResultsRenderer has {len(tabs)} tabs")
                        
                        if tabs:
                            tab_content = tabs[0].get("tabRenderer", {}).get("content", {})
                            section_contents = (tab_content
                                .get("sectionListRenderer", {})
                                .get("contents", []))
                            print(f"[Playlist] sectionListRenderer has {len(section_contents)} sections")
                            
                            if section_contents:
                                item_section = section_contents[0].get("itemSectionRenderer", {})
                                isr_contents = item_section.get("contents", [])
                                print(f"[Playlist] itemSectionRenderer has {len(isr_contents)} items")
                                
                                if isr_contents:
                                    playlist_renderer = isr_contents[0].get("playlistVideoListRenderer", {})
                                    pl_contents = playlist_renderer.get("contents", [])
                                    print(f"[Playlist] playlistVideoListRenderer has {len(pl_contents)} videos")
                                    
                                    for item in pl_contents[:max_videos]:
                                        renderer = item.get("playlistVideoRenderer", {})
                                        vid = renderer.get("videoId")
                                        title_obj = renderer.get("title", {})
                                        title = (title_obj.get("runs", [{}])[0].get("text")
                                                or title_obj.get("simpleText", ""))
                                        if vid:
                                            videos.append({
                                                "video_id": vid,
                                                "title": title or f"Video {vid}",
                                                "url": f"https://www.youtube.com/watch?v={vid}",
                                            })
                                    print(f"[Playlist] Strategy 1 (JSON nav): {len(videos)} videos")
                                else:
                                    print(f"[Playlist] itemSectionRenderer.contents is empty")
                                    # Dump first-level keys for debugging
                                    if section_contents:
                                        s0_keys = list(section_contents[0].keys())
                                        print(f"[Playlist] section[0] keys: {s0_keys}")
                    except _json.JSONDecodeError as e:
                        print(f"[Playlist] JSON decode FAILED: {e}")
                else:
                    print(f"[Playlist] Could not extract JSON object from ytInitialData")
            else:
                print(f"[Playlist] No ytInitialData found in HTML")
            
            # Strategy 2: Regex fallback — find all videoId values in the HTML
            if not videos:
                print(f"[Playlist] Strategy 1 failed, trying regex fallback")
                seen = set()
                for vid_match in re.finditer(r'"videoId"\s*:\s*"([a-zA-Z0-9_-]{11})"', html):
                    vid = vid_match.group(1)
                    if vid not in seen:
                        seen.add(vid)
                        videos.append({
                            "video_id": vid,
                            "title": f"Video {vid}",
                            "url": f"https://www.youtube.com/watch?v={vid}",
                        })
                        if len(videos) >= max_videos:
                            break
                print(f"[Playlist] Strategy 2 (regex): {len(videos)} videos")
            
            print(f"[Playlist] RESULT: {len(videos)} videos from {playlist_url}")
        except Exception as e:
            import traceback
            print(f"[Playlist] EXCEPTION: {e}")
            traceback.print_exc()
        return videos

    @staticmethod
    def _extract_json_object(text: str, start: int) -> Optional[str]:
        """Extract a complete JSON object from text starting at position start.
        
        Uses bracket counting instead of regex — handles nested objects correctly.
        """
        # Find the opening brace
        idx = text.find('{', start)
        if idx == -1:
            return None
        depth = 0
        in_string = False
        escape = False
        for i in range(idx, min(idx + 5_000_000, len(text))):
            c = text[i]
            if escape:
                escape = False
                continue
            if c == '\\':
                if in_string:
                    escape = True
                continue
            if c == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    return text[idx:i + 1]
        return None

    def _discover_rss_from_html(self, html: str, base_url: str) -> Optional[str]:
        """Discover RSS/Atom feed URL from HTML <link> tags."""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            
            # Look for <link rel="alternate" type="application/rss+xml"> or atom+xml
            for link_tag in soup.find_all("link", rel="alternate"):
                link_type = (link_tag.get("type") or "").lower()
                if "rss" in link_type or "atom" in link_type:
                    href = link_tag.get("href", "")
                    if href:
                        # Resolve relative URLs
                        if href.startswith("/"):
                            parsed_base = urlparse(base_url)
                            href = f"{parsed_base.scheme}://{parsed_base.netloc}{href}"
                        return href
        except Exception as _e:
            print(f"[WebScraper] RSS discovery failed for {base_url}: {_e}")
        return None


web_scraper = WebScraper()
