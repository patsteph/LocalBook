"""
Company Profiler - Extract company intelligence from intent

Given a company name, discovers:
- Stock ticker symbol
- Competitors
- Official news/press release pages
- SEC CIK number
- Industry classification
- Key executives (for social monitoring)
"""
import asyncio
import httpx
import json
import logging
import re
import urllib.parse
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from datetime import datetime

from services.ollama_client import ollama_client
from config import settings

logger = logging.getLogger(__name__)


class CompanyProfile(BaseModel):
    """Comprehensive company profile for source discovery"""
    name: str
    ticker: Optional[str] = None
    cik: Optional[str] = None  # SEC CIK number
    industry: Optional[str] = None
    sector: Optional[str] = None
    competitors: List[str] = Field(default_factory=list)
    
    # URLs discovered
    official_website: Optional[str] = None
    news_page: Optional[str] = None
    investor_relations: Optional[str] = None
    
    # Social/monitoring
    key_people: List[str] = Field(default_factory=list)
    
    # Metadata
    confidence: float = 0.5
    profiled_at: datetime = Field(default_factory=datetime.utcnow)


class CompanyProfiler:
    """
    Profiles companies to enable intelligent source discovery.
    Uses LLM for entity extraction and knowledge lookup.
    """
    
    # Known company tickers (for fast lookup)
    KNOWN_TICKERS = {
        "apple": "AAPL",
        "microsoft": "MSFT",
        "google": "GOOGL",
        "alphabet": "GOOGL",
        "amazon": "AMZN",
        "meta": "META",
        "facebook": "META",
        "tesla": "TSLA",
        "nvidia": "NVDA",
        "pepsi": "PEP",
        "pepsico": "PEP",
        "coca-cola": "KO",
        "coke": "KO",
        "costco": "COST",
        "costco wholesale": "COST",
        "walmart": "WMT",
        "target": "TGT",
        "kroger": "KR",
        "netflix": "NFLX",
        "disney": "DIS",
        "nike": "NKE",
        "starbucks": "SBUX",
        "mcdonald's": "MCD",
        "mcdonalds": "MCD",
        "intel": "INTC",
        "amd": "AMD",
        "salesforce": "CRM",
        "adobe": "ADBE",
        "oracle": "ORCL",
        "ibm": "IBM",
        "cisco": "CSCO",
        "boeing": "BA",
        "airbus": "EADSY",
        "ford": "F",
        "general motors": "GM",
        "gm": "GM",
        "toyota": "TM",
        "honda": "HMC",
        "jpmorgan": "JPM",
        "jp morgan": "JPM",
        "bank of america": "BAC",
        "wells fargo": "WFC",
        "goldman sachs": "GS",
        "morgan stanley": "MS",
        "berkshire hathaway": "BRK.B",
        "johnson & johnson": "JNJ",
        "pfizer": "PFE",
        "moderna": "MRNA",
        "exxon": "XOM",
        "exxonmobil": "XOM",
        "chevron": "CVX",
        "shell": "SHEL",
        "bp": "BP",
    }
    
    # Known competitor mappings
    KNOWN_COMPETITORS = {
        "PEP": ["KO", "KDP", "MNST"],  # Pepsi vs Coke, Dr Pepper, Monster
        "KO": ["PEP", "KDP", "MNST"],
        "AAPL": ["MSFT", "GOOGL", "SSNLF"],  # Apple vs Microsoft, Google, Samsung
        "MSFT": ["AAPL", "GOOGL", "AMZN"],
        "GOOGL": ["MSFT", "AAPL", "META"],
        "META": ["GOOGL", "SNAP", "PINS"],
        "AMZN": ["WMT", "TGT", "SHOP"],
        "TSLA": ["F", "GM", "RIVN"],
        "NFLX": ["DIS", "WBD", "PARA"],
        "NKE": ["ADDYY", "UAA", "LULU"],
        "SBUX": ["DNKN", "MCD"],
    }
    
    async def profile_company(self, company_name: str) -> CompanyProfile:
        """
        Build a comprehensive profile for a company.
        Combines fast lookups with LLM enrichment.
        """
        profile = CompanyProfile(name=company_name)
        
        # Step 1: Fast ticker lookup
        ticker = self._fast_ticker_lookup(company_name)
        if ticker:
            profile.ticker = ticker
            profile.confidence = 0.9
        else:
            # LLM lookup for unknown companies
            ticker = await self._llm_ticker_lookup(company_name)
            if ticker:
                profile.ticker = ticker
                profile.confidence = 0.7
        
        # Step 2: Get competitors
        if profile.ticker and profile.ticker in self.KNOWN_COMPETITORS:
            profile.competitors = self.KNOWN_COMPETITORS[profile.ticker]
        else:
            profile.competitors = await self._discover_competitors(company_name, profile.ticker)
        
        # Step 3: Get industry/sector and URLs via LLM
        enrichment = await self._enrich_profile(company_name, profile.ticker)
        profile.industry = enrichment.get("industry")
        profile.sector = enrichment.get("sector")
        profile.official_website = enrichment.get("website")
        profile.news_page = enrichment.get("news_page")
        profile.investor_relations = enrichment.get("investor_relations")
        profile.key_people = enrichment.get("key_people", [])
        
        logger.info(f"Profiled company: {company_name} -> {profile.ticker}")
        return profile
    
    def _fast_ticker_lookup(self, company_name: str) -> Optional[str]:
        """Fast lookup in known tickers dictionary — handles common name variations"""
        normalized = company_name.lower().strip()
        
        # Exact match
        if normalized in self.KNOWN_TICKERS:
            return self.KNOWN_TICKERS[normalized]
        
        # Strip common suffixes: Inc, Corp, Co, Ltd, LLC, Group, Holdings, etc.
        stripped = re.sub(
            r'[,.]?\s*\b(inc|incorporated|corp|corporation|co|company|ltd|limited|'
            r'llc|plc|group|holdings|enterprises|international|worldwide|global|'
            r'the|& co)\b\.?', '', normalized, flags=re.IGNORECASE
        ).strip().rstrip(',').strip()
        if stripped and stripped in self.KNOWN_TICKERS:
            return self.KNOWN_TICKERS[stripped]
        
        # Try substring match — check if any known key is contained in the name
        for key, ticker in self.KNOWN_TICKERS.items():
            if key in normalized or normalized in key:
                return ticker
        
        return None
    
    async def _llm_ticker_lookup(self, company_name: str) -> Optional[str]:
        """Use LLM to look up ticker for unknown companies, then validate via Yahoo Finance"""
        prompt = f"""What is the US stock ticker symbol for "{company_name}"?

If it's a publicly traded company, respond with just the ticker symbol (e.g., AAPL).
If it's not public or you're not sure, respond with: PRIVATE

Respond with just the ticker or PRIVATE, nothing else."""

        try:
            response = await ollama_client.generate(
                prompt=prompt,
                model=settings.ollama_fast_model,
                temperature=0.1
            )
            
            ticker = response.get("response", "").strip().upper()
            if ticker and ticker != "PRIVATE" and len(ticker) <= 6 and ticker.isalpha():
                # Validate: quick check that Yahoo Finance recognizes this ticker
                # and the returned company name contains the subject
                validated = await self._validate_ticker(ticker, company_name)
                if validated:
                    return ticker
                logger.warning(f"LLM ticker '{ticker}' failed validation for '{company_name}'")
        except Exception as e:
            logger.error(f"LLM ticker lookup failed: {e}")
        
        return None

    async def _validate_ticker(self, ticker: str, company_name: str) -> bool:
        """Validate a ticker symbol against Yahoo Finance to catch LLM hallucinations."""
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        params = {"range": "1d", "interval": "1d"}
        headers = {"User-Agent": "Mozilla/5.0 LocalBook/1.0"}

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url, params=params, headers=headers)
                if resp.status_code != 200:
                    return False
                data = resp.json()
                result = data.get("chart", {}).get("result", [])
                if not result:
                    return False
                meta = result[0].get("meta", {})
                # Check that the exchange is a US exchange (not a random foreign stock)
                exchange = meta.get("exchangeName", "").upper()
                us_exchanges = {"NMS", "NYQ", "NGM", "NCM", "BTS", "PCX", "ASE", "NYSE", "NASDAQ"}
                if exchange not in us_exchanges:
                    logger.warning(f"Ticker {ticker} is on {exchange}, not a US exchange")
                    return False
                # Sanity check: does the returned name relate to the company?
                name = (meta.get("shortName", "") or meta.get("longName", "")).lower()
                company_lower = company_name.lower()
                # Check if any significant word from company name appears in the stock name
                words = [w for w in company_lower.split() if len(w) > 2]
                if words and not any(w in name for w in words):
                    logger.warning(f"Ticker {ticker} name '{name}' doesn't match '{company_name}'")
                    return False
                return True
        except Exception as e:
            logger.warning(f"Ticker validation failed for {ticker}: {e}")
            return True  # On network error, accept the ticker (benefit of the doubt)
    
    async def _discover_competitors(
        self, 
        company_name: str, 
        ticker: Optional[str]
    ) -> List[str]:
        """Use LLM to discover competitors"""
        prompt = f"""List the top 3-5 main competitors for {company_name}.

Respond with JSON array only, using stock tickers if public:
["TICKER1", "TICKER2", "TICKER3"]

If competitors are private companies, use their names instead.
Only include direct competitors in the same industry."""

        try:
            response = await ollama_client.generate(
                prompt=prompt,
                model=settings.ollama_fast_model,
                temperature=0.3
            )
            
            text = response.get("response", "")
            json_start = text.find("[")
            json_end = text.rfind("]") + 1
            
            if json_start >= 0 and json_end > json_start:
                competitors = json.loads(text[json_start:json_end])
                return competitors[:5]
        except Exception as e:
            logger.error(f"Competitor discovery failed: {e}")
        
        return []
    
    async def _validate_url(self, url: str) -> bool:
        """Check if a URL resolves (2xx/3xx) via HEAD request."""
        if not url:
            return False
        try:
            async with httpx.AsyncClient(timeout=6.0, follow_redirects=True) as client:
                resp = await client.head(url, headers={"User-Agent": "Mozilla/5.0 LocalBook/1.0"})
                return resp.status_code < 400
        except Exception:
            return False

    async def _find_investor_relations_url(
        self, company_name: str, website: Optional[str]
    ) -> Optional[str]:
        """Discover the real investor relations URL by trying common patterns + web search."""
        # Extract domain root from website if available
        domain = None
        if website:
            parsed = urllib.parse.urlparse(website)
            domain = parsed.netloc or parsed.path.split("/")[0]
            domain = domain.removeprefix("www.")

        # Common IR URL patterns (ordered by popularity)
        candidates = []
        if domain:
            candidates = [
                f"https://investors.{domain}",
                f"https://investor.{domain}",
                f"https://ir.{domain}",
                f"https://www.{domain}/investors",
                f"https://www.{domain}/investor-relations",
                f"https://www.{domain}/investor",
            ]

        # Test candidates in parallel
        if candidates:
            results = await asyncio.gather(
                *[self._validate_url(url) for url in candidates],
                return_exceptions=True,
            )
            for url, ok in zip(candidates, results):
                if ok is True:
                    logger.info(f"Found IR URL via pattern: {url}")
                    return url

        # Fallback: web search for the real IR page
        try:
            from services.web_scraper import web_scraper
            query = f"{company_name} investor relations site"
            search_results = await web_scraper.search_web(query, max_results=3)
            for sr in search_results:
                url = sr.get("url", "")
                if any(kw in url.lower() for kw in ["investor", "ir."]):
                    if await self._validate_url(url):
                        logger.info(f"Found IR URL via web search: {url}")
                        return url
        except Exception as e:
            logger.warning(f"IR web search fallback failed: {e}")

        return None

    async def _enrich_profile(
        self, 
        company_name: str, 
        ticker: Optional[str]
    ) -> Dict[str, Any]:
        """Use LLM to enrich profile with industry, URLs, etc."""
        ticker_str = f" (ticker: {ticker})" if ticker else ""
        
        prompt = f"""Provide information about {company_name}{ticker_str}.

Respond with JSON only:
{{
    "industry": "specific industry (e.g., Beverages, Cloud Computing)",
    "sector": "broad sector (e.g., Consumer Staples, Technology)",
    "website": "official website URL",
    "news_page": "URL to company news/press releases page if known, else null",
    "investor_relations": "URL to investor relations page if public, else null",
    "key_people": ["CEO name", "other key executive"] (max 3 people)
}}

Be accurate - only include URLs you're confident about."""

        enrichment = {}
        try:
            response = await ollama_client.generate(
                prompt=prompt,
                model=settings.ollama_fast_model,
                temperature=0.3
            )
            
            text = response.get("response", "")
            json_start = text.find("{")
            json_end = text.rfind("}") + 1
            
            if json_start >= 0 and json_end > json_start:
                enrichment = json.loads(text[json_start:json_end])
        except Exception as e:
            logger.error(f"Profile enrichment failed: {e}")

        # Validate website URL
        website = enrichment.get("website")
        if website and not await self._validate_url(website):
            logger.warning(f"LLM website URL failed validation: {website}")
            enrichment["website"] = None

        # Validate and fix investor relations URL
        ir_url = enrichment.get("investor_relations")
        if ir_url and await self._validate_url(ir_url):
            pass  # LLM got it right
        else:
            if ir_url:
                logger.warning(f"LLM IR URL failed validation: {ir_url}")
            # Discover the real IR URL
            real_ir = await self._find_investor_relations_url(
                company_name, enrichment.get("website")
            )
            if real_ir:
                enrichment["investor_relations"] = real_ir

        return enrichment
    
    async def extract_company_from_intent(self, intent: str) -> Optional[str]:
        """
        Extract company name from a research intent.
        Returns None if intent is not company-focused.
        """
        prompt = f"""Analyze this research intent and determine if it's focused on a specific company.

Intent: "{intent}"

If this is about researching a specific company, respond with just the company name.
If this is general topic research (not company-specific), respond with: NONE

Examples:
- "Track Pepsi's competitive strategy" → Pepsi
- "Monitor Apple's product launches" → Apple
- "AI trends in enterprise software" → NONE
- "Coca-Cola market share analysis" → Coca-Cola

Respond with just the company name or NONE, nothing else."""

        try:
            response = await ollama_client.generate(
                prompt=prompt,
                model=settings.ollama_fast_model,
                temperature=0.1
            )
            
            result = response.get("response", "").strip()
            if result and result.upper() != "NONE":
                return result
        except Exception as e:
            logger.error(f"Company extraction failed: {e}")
        
        return None


# Singleton instance
company_profiler = CompanyProfiler()
