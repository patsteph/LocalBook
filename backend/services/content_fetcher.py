"""
Universal Content Fetcher - Unified fetching for all source types

Fetches content from:
- RSS feeds (feedparser)
- Web pages (existing scraper or aiohttp)
- SEC EDGAR filings
- YouTube (search + transcripts)
- arXiv papers

All fetchers return a common CollectedItem format for the Collector to process.
"""
import asyncio
import aiohttp
import feedparser
import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from urllib.parse import urlencode, quote_plus


logger = logging.getLogger(__name__)


class FetchedItem(BaseModel):
    """Unified item format from any fetcher"""
    title: str
    url: Optional[str] = None
    content: str
    summary: str = ""
    source_name: str
    source_type: str  # rss, web, sec, youtube, arxiv
    source_url: str
    published_date: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    content_hash: str = ""
    
    def compute_hash(self) -> str:
        """Compute content hash for deduplication"""
        normalized = (self.title + self.content[:500]).lower().strip()
        self.content_hash = hashlib.sha256(normalized.encode()).hexdigest()[:16]
        return self.content_hash


class BaseFetcher(ABC):
    """Base class for all fetchers"""
    
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": "LocalBook/1.0 Research Assistant"}
            )
        return self._session
    
    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
    
    @abstractmethod
    async def fetch(self, source_config: Dict[str, Any], keywords: List[str]) -> List[FetchedItem]:
        """Fetch items from the source"""


class RSSFetcher(BaseFetcher):
    """Fetches items from RSS/Atom feeds"""
    
    async def fetch(
        self, 
        source_config: Dict[str, Any], 
        keywords: List[str]
    ) -> List[FetchedItem]:
        """
        Fetch from RSS feed.
        
        source_config:
            url: Feed URL
            name: Optional feed name
        """
        feed_url = source_config.get("url")
        if not feed_url:
            return []
        
        items = []
        
        try:
            # feedparser can handle URLs directly
            feed = feedparser.parse(feed_url)
            feed_name = source_config.get("name") or feed.feed.get("title", feed_url)
            
            for entry in feed.entries[:20]:  # Limit per feed
                title = entry.get("title", "")
                summary = entry.get("summary", entry.get("description", ""))
                link = entry.get("link", "")
                
                # Parse published date
                published = None
                if entry.get("published_parsed"):
                    try:
                        published = datetime(*entry.published_parsed[:6])
                    except:
                        pass
                
                # Filter by keywords if provided
                if keywords:
                    content_lower = f"{title} {summary}".lower()
                    if not any(kw.lower() in content_lower for kw in keywords):
                        continue
                
                item = FetchedItem(
                    title=title,
                    url=link,
                    content=summary,
                    summary=summary[:300] if summary else title,
                    source_name=feed_name,
                    source_type="rss",
                    source_url=feed_url,
                    published_date=published,
                    metadata={
                        "author": entry.get("author"),
                        "tags": [t.get("term") for t in entry.get("tags", [])]
                    }
                )
                item.compute_hash()
                items.append(item)
            
            logger.info(f"RSS fetcher: {len(items)} items from {feed_name}")
            
        except Exception as e:
            logger.error(f"RSS fetch error for {feed_url}: {e}")
        
        return items


class WebPageFetcher(BaseFetcher):
    """Fetches and extracts content from web pages"""
    
    async def fetch(
        self, 
        source_config: Dict[str, Any], 
        keywords: List[str]
    ) -> List[FetchedItem]:
        """
        Fetch from a web page.
        
        source_config:
            url: Page URL
            name: Optional source name
            selector: Optional CSS selector for content
        """
        page_url = source_config.get("url")
        if not page_url:
            return []
        
        items = []
        session = await self._get_session()
        
        try:
            async with session.get(page_url) as response:
                if response.status != 200:
                    return []
                
                html = await response.text()
                
                # Basic extraction - find article-like content
                # In production, would use BeautifulSoup or existing web_scraper
                title = self._extract_title(html)
                content = self._extract_content(html)
                
                if title and content:
                    item = FetchedItem(
                        title=title,
                        url=page_url,
                        content=content,
                        summary=content[:300],
                        source_name=source_config.get("name", page_url),
                        source_type="web",
                        source_url=page_url,
                        published_date=datetime.utcnow(),
                        metadata={"scraped": True}
                    )
                    item.compute_hash()
                    items.append(item)
            
        except Exception as e:
            logger.error(f"Web fetch error for {page_url}: {e}")
        
        return items
    
    def _extract_title(self, html: str) -> str:
        """Extract title from HTML"""
        match = re.search(r'<title[^>]*>([^<]+)</title>', html, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return ""
    
    def _extract_content(self, html: str) -> str:
        """Extract main content from HTML (simplified)"""
        # Remove scripts and styles
        html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
        
        # Extract text from paragraphs
        paragraphs = re.findall(r'<p[^>]*>([^<]+)</p>', html, re.IGNORECASE)
        content = ' '.join(paragraphs)
        
        # Clean up
        content = re.sub(r'\s+', ' ', content).strip()
        return content[:5000]  # Limit content length


class SECFetcher(BaseFetcher):
    """Fetches SEC EDGAR filings using company name for precise results.
    
    Uses two strategies:
    1. EDGAR EFTS full-text search with quoted company name (precise)
    2. EDGAR company submissions API via CIK lookup (most reliable)
    
    Key: NEVER search EFTS by bare ticker (e.g. "COST" matches every
    filing containing the word "cost"). Always use quoted company name.
    """
    
    SEC_BASE_URL = "https://data.sec.gov"
    SEC_EFTS_URL = "https://efts.sec.gov/LATEST/search-index"
    SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
    
    def __init__(self):
        super().__init__()
        self._cik_cache: Dict[str, str] = {}  # ticker -> CIK
        self._name_cache: Dict[str, str] = {}  # ticker -> company name
    
    async def _resolve_cik(self, ticker: str) -> Optional[str]:
        """Resolve ticker to CIK using SEC company_tickers.json"""
        if ticker in self._cik_cache:
            return self._cik_cache[ticker]
        
        session = await self._get_session()
        try:
            async with session.get(
                self.SEC_TICKERS_URL,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "LocalBook Research Assistant research@localbook.app"
                }
            ) as response:
                if response.status != 200:
                    return None
                data = await response.json()
                
                # Format: {"0": {"cik_str": "320193", "ticker": "AAPL", "title": "Apple Inc."}, ...}
                ticker_upper = ticker.upper()
                for entry in data.values():
                    if entry.get("ticker", "").upper() == ticker_upper:
                        cik = str(entry["cik_str"])
                        self._cik_cache[ticker] = cik
                        self._name_cache[ticker] = entry.get("title", "")
                        return cik
        except Exception as e:
            logger.error(f"CIK resolution failed for {ticker}: {e}")
        
        return None
    
    async def _fetch_via_submissions(
        self, ticker: str, company_name: str, cik: str, filing_types: List[str]
    ) -> List[FetchedItem]:
        """Fetch filings via the EDGAR submissions API (most reliable)"""
        items = []
        session = await self._get_session()
        
        # Pad CIK to 10 digits
        cik_padded = cik.zfill(10)
        url = f"{self.SEC_BASE_URL}/submissions/CIK{cik_padded}.json"
        
        try:
            async with session.get(
                url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "LocalBook Research Assistant research@localbook.app"
                }
            ) as response:
                if response.status != 200:
                    return items
                
                data = await response.json()
                recent = data.get("filings", {}).get("recent", {})
                
                forms = recent.get("form", [])
                dates = recent.get("filingDate", [])
                accessions = recent.get("accessionNumber", [])
                primary_docs = recent.get("primaryDocument", [])
                descriptions = recent.get("primaryDocDescription", [])
                
                display_name = data.get("name", company_name)
                
                for i in range(min(len(forms), 50)):
                    form = forms[i]
                    if form not in filing_types:
                        continue
                    
                    # Only recent filings (last 2 years)
                    filing_date = dates[i] if i < len(dates) else ""
                    if filing_date and filing_date < "2023-01-01":
                        continue
                    
                    accession = accessions[i].replace("-", "") if i < len(accessions) else ""
                    primary_doc = primary_docs[i] if i < len(primary_docs) else ""
                    description = descriptions[i] if i < len(descriptions) else f"{form} filing"
                    
                    filing_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{primary_doc}" if accession and primary_doc else f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type={form}"
                    
                    item = FetchedItem(
                        title=f"{display_name} ({ticker}) - {form}: {description}",
                        url=filing_url,
                        content=f"{form} filing for {display_name} ({ticker}) filed on {filing_date}. {description}",
                        summary=f"{form} filing for {display_name} filed {filing_date}",
                        source_name=f"SEC EDGAR - {display_name}",
                        source_type="sec",
                        source_url=self.SEC_BASE_URL,
                        published_date=datetime.fromisoformat(filing_date) if filing_date else None,
                        metadata={
                            "ticker": ticker,
                            "company_name": display_name,
                            "filing_type": form,
                            "cik": cik,
                            "accession": accessions[i] if i < len(accessions) else ""
                        }
                    )
                    item.compute_hash()
                    items.append(item)
                    
                    if len(items) >= 10:  # Cap at 10 filings
                        break
                        
        except Exception as e:
            logger.error(f"SEC submissions API error for {ticker}: {e}")
        
        return items
    
    async def _fetch_via_efts(
        self, ticker: str, company_name: str, filing_types: List[str]
    ) -> List[FetchedItem]:
        """Fallback: EFTS full-text search using quoted company name"""
        items = []
        session = await self._get_session()
        
        # Use quoted company name for exact match - NEVER bare ticker
        search_term = f'"{company_name}"' if company_name else f'"{ticker}"'
        
        try:
            for filing_type in filing_types[:3]:
                search_url = (
                    f"{self.SEC_EFTS_URL}"
                    f"?q={quote_plus(search_term)}"
                    f"&dateRange=custom&startdt=2023-01-01"
                    f"&forms={filing_type}"
                )
                
                async with session.get(
                    search_url,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "LocalBook Research Assistant research@localbook.app"
                    }
                ) as response:
                    if response.status != 200:
                        continue
                    
                    data = await response.json()
                    hits = data.get("hits", {}).get("hits", [])
                    
                    for hit in hits[:3]:  # Limit per filing type
                        source = hit.get("_source", {})
                        display_names = source.get("display_names", ["Filing"])
                        
                        item = FetchedItem(
                            title=f"{company_name or ticker} {filing_type}: {display_names[0]}",
                            url=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={ticker}&type={filing_type}",
                            content=source.get("file_description") or f"{filing_type} filing for {company_name or ticker}",
                            summary=f"{filing_type} filing for {company_name or ticker}",
                            source_name=f"SEC EDGAR - {company_name or ticker}",
                            source_type="sec",
                            source_url=self.SEC_BASE_URL,
                            published_date=datetime.fromisoformat(source.get("file_date", datetime.utcnow().isoformat())[:10]),
                            metadata={
                                "ticker": ticker,
                                "company_name": company_name,
                                "filing_type": filing_type,
                                "cik": source.get("ciks", [None])[0]
                            }
                        )
                        item.compute_hash()
                        items.append(item)
        
        except Exception as e:
            logger.error(f"SEC EFTS search error for {company_name or ticker}: {e}")
        
        return items
    
    async def fetch(
        self, 
        source_config: Dict[str, Any], 
        keywords: List[str]
    ) -> List[FetchedItem]:
        """
        Fetch SEC filings for a company.
        
        source_config:
            ticker: Stock ticker (e.g., "COST")
            company_name: Company name (e.g., "Costco Wholesale") - used for precise search
            filing_types: List of filing types (e.g., ["10-K", "10-Q", "8-K"])
        """
        ticker = source_config.get("ticker")
        company_name = source_config.get("company_name", "")
        filing_types = source_config.get("filing_types", ["10-K", "10-Q", "8-K"])
        
        if not ticker and not company_name:
            return []
        
        # Strategy 1: Resolve CIK and use submissions API (most reliable)
        items = []
        if ticker:
            cik = await self._resolve_cik(ticker)
            if cik:
                # Use resolved company name if we don't have one
                if not company_name and ticker in self._name_cache:
                    company_name = self._name_cache[ticker]
                
                items = await self._fetch_via_submissions(ticker, company_name, cik, filing_types)
                if items:
                    logger.info(f"SEC fetcher: {len(items)} filings for {company_name} ({ticker}) via submissions API")
                    return items
        
        # Strategy 2: Fallback to EFTS with quoted company name
        items = await self._fetch_via_efts(ticker or "", company_name, filing_types)
        logger.info(f"SEC fetcher: {len(items)} filings for {company_name or ticker} via EFTS")
        
        return items


class YouTubeFetcher(BaseFetcher):
    """Fetches YouTube videos based on keywords"""
    
    # YouTube RSS feed for search results (no API key needed)
    YOUTUBE_RSS_BASE = "https://www.youtube.com/feeds/videos.xml"
    
    async def fetch(
        self, 
        source_config: Dict[str, Any], 
        keywords: List[str]
    ) -> List[FetchedItem]:
        """
        Fetch YouTube videos.
        
        source_config:
            keyword: Search keyword
            channel_id: Optional channel ID to monitor
        """
        items = []
        keyword = source_config.get("keyword")
        channel_id = source_config.get("channel_id")
        
        if channel_id:
            # Fetch from channel RSS
            items.extend(await self._fetch_channel(channel_id, keywords))
        
        if keyword:
            # Use YouTube search RSS (limited but no API key needed)
            items.extend(await self._fetch_search(keyword, keywords))
        
        return items
    
    async def _fetch_channel(self, channel_id: str, keywords: List[str]) -> List[FetchedItem]:
        """Fetch videos from a channel's RSS feed"""
        items = []
        feed_url = f"{self.YOUTUBE_RSS_BASE}?channel_id={channel_id}"
        
        try:
            feed = feedparser.parse(feed_url)
            
            for entry in feed.entries[:10]:
                title = entry.get("title", "")
                
                # Filter by keywords if provided
                if keywords:
                    if not any(kw.lower() in title.lower() for kw in keywords):
                        continue
                
                item = FetchedItem(
                    title=title,
                    url=entry.get("link"),
                    content=entry.get("summary", ""),
                    summary=entry.get("summary", "")[:300],
                    source_name=feed.feed.get("title", "YouTube Channel"),
                    source_type="youtube",
                    source_url=feed_url,
                    published_date=datetime(*entry.published_parsed[:6]) if entry.get("published_parsed") else None,
                    metadata={
                        "channel_id": channel_id,
                        "video_id": entry.get("yt_videoid")
                    }
                )
                item.compute_hash()
                items.append(item)
                
        except Exception as e:
            logger.error(f"YouTube channel fetch error: {e}")
        
        return items
    
    async def _fetch_search(self, keyword: str, filter_keywords: List[str]) -> List[FetchedItem]:
        """
        Fetch YouTube search results.
        Note: YouTube doesn't have a public search RSS, so we use a workaround.
        In production, would use YouTube Data API.
        """
        items = []
        
        # For now, create a placeholder item indicating YouTube monitoring
        # Full implementation would require YouTube Data API
        item = FetchedItem(
            title=f"YouTube monitoring: {keyword}",
            url=f"https://www.youtube.com/results?search_query={quote_plus(keyword)}",
            content=f"Monitoring YouTube for videos about '{keyword}'",
            summary=f"YouTube search results for '{keyword}'",
            source_name="YouTube Search",
            source_type="youtube",
            source_url="https://www.youtube.com",
            metadata={
                "keyword": keyword,
                "search_type": "keyword_monitoring"
            }
        )
        item.compute_hash()
        items.append(item)
        
        return items


class ArXivFetcher(BaseFetcher):
    """Fetches papers from arXiv"""
    
    ARXIV_API_BASE = "http://export.arxiv.org/api/query"
    ARXIV_RSS_BASE = "http://export.arxiv.org/rss"
    
    async def fetch(
        self, 
        source_config: Dict[str, Any], 
        keywords: List[str]
    ) -> List[FetchedItem]:
        """
        Fetch arXiv papers.
        
        source_config:
            category: arXiv category (e.g., "cs.AI", "cs.LG")
            query: Optional search query
        """
        category = source_config.get("category")
        query = source_config.get("query")
        items = []
        
        if category:
            items.extend(await self._fetch_category(category, keywords))
        
        if query:
            items.extend(await self._fetch_search(query, keywords))
        
        return items
    
    async def _fetch_category(self, category: str, keywords: List[str]) -> List[FetchedItem]:
        """Fetch recent papers from a category via RSS"""
        items = []
        feed_url = f"{self.ARXIV_RSS_BASE}/{category}"
        
        try:
            feed = feedparser.parse(feed_url)
            
            for entry in feed.entries[:15]:
                title = entry.get("title", "").replace("\n", " ")
                summary = entry.get("summary", "").replace("\n", " ")
                
                # Filter by keywords if provided
                if keywords:
                    content_lower = f"{title} {summary}".lower()
                    if not any(kw.lower() in content_lower for kw in keywords):
                        continue
                
                # Extract arXiv ID
                arxiv_id = ""
                link = entry.get("link", "")
                if "abs/" in link:
                    arxiv_id = link.split("abs/")[-1]
                
                item = FetchedItem(
                    title=title,
                    url=link,
                    content=summary,
                    summary=summary[:300],
                    source_name=f"arXiv {category}",
                    source_type="arxiv",
                    source_url=feed_url,
                    published_date=datetime(*entry.updated_parsed[:6]) if entry.get("updated_parsed") else None,
                    metadata={
                        "category": category,
                        "arxiv_id": arxiv_id,
                        "authors": [a.get("name") for a in entry.get("authors", [])]
                    }
                )
                item.compute_hash()
                items.append(item)
            
            logger.info(f"arXiv fetcher: {len(items)} papers from {category}")
                
        except Exception as e:
            logger.error(f"arXiv category fetch error: {e}")
        
        return items
    
    async def _fetch_search(self, query: str, keywords: List[str]) -> List[FetchedItem]:
        """Search arXiv API — no secondary keyword filter since the query ensures relevance."""
        items = []
        session = await self._get_session()
        
        params = {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": 10,
            "sortBy": "submittedDate",
            "sortOrder": "descending"
        }
        
        try:
            url = f"{self.ARXIV_API_BASE}?{urlencode(params)}"
            
            async with session.get(url) as response:
                if response.status != 200:
                    return items
                
                xml_text = await response.text()
                root = ET.fromstring(xml_text)
                
                # arXiv uses Atom namespace
                ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
                
                for entry in root.findall("atom:entry", ns):
                    title_elem = entry.find("atom:title", ns)
                    summary_elem = entry.find("atom:summary", ns)
                    link_elem = entry.find("atom:id", ns)
                    
                    title = title_elem.text.strip() if title_elem is not None else ""
                    summary = summary_elem.text.strip() if summary_elem is not None else ""
                    link = link_elem.text if link_elem is not None else ""
                    
                    # No secondary keyword filter — the arXiv search query
                    # already targets specific content
                    
                    item = FetchedItem(
                        title=title,
                        url=link,
                        content=summary,
                        summary=summary[:300],
                        source_name="arXiv Search",
                        source_type="arxiv",
                        source_url=self.ARXIV_API_BASE,
                        metadata={"query": query}
                    )
                    item.compute_hash()
                    items.append(item)
            
            logger.info(f"arXiv search: {len(items)} papers for '{query}'")
                
        except Exception as e:
            logger.error(f"arXiv search error: {e}")
        
        return items


class GoogleNewsFetcher(BaseFetcher):
    """Fetches from Google News RSS"""
    
    GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
    
    async def fetch(
        self, 
        source_config: Dict[str, Any], 
        keywords: List[str]
    ) -> List[FetchedItem]:
        """
        Fetch Google News results.
        
        source_config:
            keyword: Search keyword
            
        Note: We do NOT apply secondary keyword filtering here because the
        Google News search query itself already ensures relevance. Applying
        substring-based keyword filters on top kills good results (e.g., a
        multi-word smart query like "transformer scaling laws 2026" won't
        match as a substring in most article titles).
        """
        keyword = source_config.get("keyword")
        if not keyword:
            return []
        
        items = []
        feed_url = f"{self.GOOGLE_NEWS_RSS}?q={quote_plus(keyword)}&hl=en-US&gl=US&ceid=US:en"
        
        try:
            feed = feedparser.parse(feed_url)
            
            for entry in feed.entries[:15]:
                title = entry.get("title", "")
                link = entry.get("link", "")
                
                # Google News wraps links - extract actual URL
                actual_url = link
                if "url=" in link:
                    actual_url = link.split("url=")[-1].split("&")[0]
                
                # No secondary keyword filter — the search query ensures relevance
                
                item = FetchedItem(
                    title=title,
                    url=actual_url,
                    content=entry.get("summary", ""),
                    summary=entry.get("summary", "")[:300],
                    source_name=entry.get("source", {}).get("title", "Google News"),
                    source_type="news",
                    source_url=feed_url,
                    published_date=datetime(*entry.published_parsed[:6]) if entry.get("published_parsed") else None,
                    metadata={
                        "keyword": keyword,
                        "source": "google_news"
                    }
                )
                item.compute_hash()
                items.append(item)
            
            logger.info(f"Google News fetcher: {len(items)} articles for '{keyword}'")
                
        except Exception as e:
            logger.error(f"Google News fetch error: {e}")
        
        return items


class UnifiedFetcher:
    """
    Unified interface for all fetchers.
    Routes to appropriate fetcher based on source type.
    """
    
    def __init__(self):
        self.rss_fetcher = RSSFetcher()
        self.web_fetcher = WebPageFetcher()
        self.sec_fetcher = SECFetcher()
        self.youtube_fetcher = YouTubeFetcher()
        self.arxiv_fetcher = ArXivFetcher()
        self.news_fetcher = GoogleNewsFetcher()
    
    async def close(self):
        """Close all fetcher sessions"""
        await asyncio.gather(
            self.rss_fetcher.close(),
            self.web_fetcher.close(),
            self.sec_fetcher.close(),
            self.youtube_fetcher.close(),
            self.arxiv_fetcher.close(),
            self.news_fetcher.close(),
            return_exceptions=True
        )
    
    async def fetch_all(
        self,
        sources: Dict[str, Any],
        keywords: List[str]
    ) -> List[FetchedItem]:
        """
        Fetch from all configured sources.
        
        Args:
            sources: Dict with source configurations:
                {
                    "rss_feeds": ["url1", "url2"],
                    "web_pages": ["url1", "url2"],
                    "sec_tickers": ["PEP", "KO"],
                    "youtube_keywords": ["keyword1"],
                    "arxiv_categories": ["cs.AI"],
                    "news_keywords": ["keyword1"]
                }
            keywords: Keywords to filter results
            
        Returns:
            List of FetchedItem from all sources
        """
        all_items: List[FetchedItem] = []
        tasks = []
        
        # RSS feeds
        for feed_url in sources.get("rss_feeds", []):
            tasks.append(self.rss_fetcher.fetch({"url": feed_url}, keywords))
        
        # Web pages
        for page_url in sources.get("web_pages", []):
            tasks.append(self.web_fetcher.fetch({"url": page_url}, keywords))
        
        # SEC tickers (supports both legacy string format and new dict format)
        for sec_entry in sources.get("sec_tickers", []):
            if isinstance(sec_entry, str):
                # Legacy format: just ticker string
                sec_config = {"ticker": sec_entry, "filing_types": ["10-K", "10-Q", "8-K"]}
            else:
                # New format: dict with ticker, company_name, optional filing_types
                sec_config = {
                    "ticker": sec_entry.get("ticker"),
                    "company_name": sec_entry.get("company_name"),
                    "filing_types": sec_entry.get("filing_types", ["10-K", "10-Q", "8-K"])
                }
            tasks.append(self.sec_fetcher.fetch(sec_config, keywords))
        
        # YouTube keywords
        for keyword in sources.get("youtube_keywords", []):
            tasks.append(self.youtube_fetcher.fetch({"keyword": keyword}, keywords))
        
        # arXiv categories (browse recent papers, filter by keywords)
        for category in sources.get("arxiv_categories", []):
            tasks.append(self.arxiv_fetcher.fetch({"category": category}, keywords))
        
        # arXiv direct search queries (targeted paper search, no secondary filter)
        for query in sources.get("arxiv_queries", []):
            tasks.append(self.arxiv_fetcher.fetch({"query": query}, keywords))
        
        # News keywords
        for keyword in sources.get("news_keywords", []):
            tasks.append(self.news_fetcher.fetch({"keyword": keyword}, keywords))
        
        if not tasks:
            return all_items
        
        # Execute all fetches in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if isinstance(result, list):
                all_items.extend(result)
            elif isinstance(result, Exception):
                logger.error(f"Fetch task error: {result}")
        
        # Deduplicate by content hash
        seen_hashes = set()
        unique_items = []
        for item in all_items:
            if item.content_hash not in seen_hashes:
                seen_hashes.add(item.content_hash)
                unique_items.append(item)
        
        logger.info(f"UnifiedFetcher: {len(unique_items)} unique items from {len(tasks)} sources")
        return unique_items


# Singleton instance
unified_fetcher = UnifiedFetcher()
