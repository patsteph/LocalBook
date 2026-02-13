"""
Source Discovery Service - The Magic Brain

Takes user intent and automatically discovers relevant sources:
- Company intelligence (news pages, SEC filings, investor relations)
- Industry RSS feeds
- YouTube channels and keywords
- arXiv categories
- News sources
- Competitor analysis

Flow:
1. Analyze intent with LLM to extract entities, topics, company names
2. Use web search to find actual source URLs
3. Validate discovered sources (check if accessible, has RSS, etc.)
4. Return categorized sources for Curator validation and user review
"""
import asyncio
import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from enum import Enum
from pydantic import BaseModel, Field
import aiohttp

from services.ollama_client import ollama_client
from config import settings

logger = logging.getLogger(__name__)


class SourceType(str, Enum):
    RSS_FEED = "rss_feed"
    WEB_PAGE = "web_page"
    SEC_FILING = "sec_filing"
    YOUTUBE_CHANNEL = "youtube_channel"
    YOUTUBE_KEYWORD = "youtube_keyword"
    ARXIV_CATEGORY = "arxiv_category"
    NEWS_KEYWORD = "news_keyword"
    COMPANY_NEWS = "company_news"
    BLOG = "blog"
    PODCAST = "podcast"
    NEWSLETTER = "newsletter"
    COMMUNITY = "community"


class NotebookPurpose(str, Enum):
    """Classification of notebook purpose - drives discovery strategy"""
    COMPANY_RESEARCH = "company_research"      # Track a company (Costco, Tesla)
    TOPIC_RESEARCH = "topic_research"          # Broad topic (AI, Leadership)
    PRODUCT_RESEARCH = "product_research"      # Product/technology research
    SKILL_DEVELOPMENT = "skill_development"    # Learning a skill (Python, Public Speaking)
    PERSON_TRACKING = "person_tracking"        # Track a person (employee, public figure)
    INDUSTRY_MONITORING = "industry_monitoring" # Monitor an industry sector
    PROJECT_KNOWLEDGE = "project_knowledge"    # Project-specific knowledge base
    PERSONAL_INTERESTS = "personal_interests"  # Hobbies, interests


class DiscoveredSource(BaseModel):
    """A source discovered by the discovery engine"""
    id: str = Field(default_factory=lambda: f"src_{datetime.utcnow().timestamp()}")
    source_type: SourceType
    name: str
    url: Optional[str] = None
    description: str = ""
    confidence: float = 0.5  # 0-1 how confident we are this is relevant
    auto_approve: bool = False  # High confidence sources can be auto-approved
    metadata: Dict[str, Any] = Field(default_factory=dict)
    discovered_at: datetime = Field(default_factory=datetime.utcnow)
    
    # Validation status
    validated: bool = False
    validation_error: Optional[str] = None
    has_rss: bool = False
    rss_url: Optional[str] = None


class IntentAnalysis(BaseModel):
    """Analyzed intent from user input"""
    primary_topic: str
    notebook_purpose: str = "topic_research"  # NotebookPurpose value
    purpose_confidence: float = 0.8  # How confident we are in the purpose classification
    is_company_research: bool = False
    company_name: Optional[str] = None
    company_ticker: Optional[str] = None
    company_is_private: bool = False  # True if company has no public ticker
    needs_company_clarification: bool = False  # True if company lookup failed
    product_name: Optional[str] = None
    person_name: Optional[str] = None
    skill_name: Optional[str] = None
    industry: Optional[str] = None
    competitors: List[str] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list)
    geographic_focus: Optional[str] = None
    time_sensitivity: str = "normal"  # breaking, daily, weekly, archival
    research_depth: str = "standard"  # surface, standard, deep


class DiscoveryResult(BaseModel):
    """Complete discovery result"""
    intent_analysis: IntentAnalysis
    sources: List[DiscoveredSource] = Field(default_factory=list)
    discovery_time_ms: float = 0
    errors: List[str] = Field(default_factory=list)


class SourceDiscoveryService:
    """
    The magic that turns user intent into a comprehensive source list.
    Uses LLM for intent analysis and web search for source discovery.
    """
    
    # Common RSS feed patterns for major news sources
    KNOWN_RSS_PATTERNS = {
        "google_news": "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en",
        "google_news_geo": "https://news.google.com/rss/search?q={query}&hl=en-{geo}&gl={geo}&ceid={geo}:en",
        "reddit": "https://www.reddit.com/r/{subreddit}/.rss",
        "yahoo_finance": "https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US",
        "seeking_alpha": "https://seekingalpha.com/api/sa/combined/{ticker}.xml",
        "arxiv": "http://export.arxiv.org/rss/{category}",
        "substack": "https://{publication}.substack.com/feed",
        "youtube_channel": "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}",
    }
    
    # SEC EDGAR base URL
    SEC_EDGAR_BASE = "https://www.sec.gov/cgi-bin/browse-edgar"
    
    # Configurable thresholds
    PURPOSE_CONFIDENCE_THRESHOLD = 0.7
    WEB_SEARCH_TIMEOUT = 15.0  # seconds
    
    # Geographic region mappings
    GEO_MAPPINGS = {
        "us": "US", "usa": "US", "united states": "US",
        "uk": "GB", "united kingdom": "GB", "britain": "GB",
        "canada": "CA", "eu": "EU", "europe": "EU",
        "australia": "AU", "germany": "DE", "france": "FR",
        "japan": "JP", "china": "CN", "india": "IN",
    }
    
    # Time sensitivity to freshness mapping for Brave Search
    FRESHNESS_MAPPINGS = {
        "breaking": "pd",  # past day
        "daily": "pw",     # past week
        "weekly": "pm",    # past month
        "normal": None,    # no filter
        "archival": None,  # no filter
    }
    
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": "LocalBook/1.0 Research Assistant"}
            )
        return self._session
    
    async def close(self):
        """Close the session"""
        if self._session and not self._session.closed:
            await self._session.close()
    
    async def discover_sources(
        self,
        intent: str,
        focus_areas: List[str],
        subject: str = "",
        existing_memory_context: Optional[str] = None,
        override_purpose: Optional[str] = None,
        company_details: Optional[Dict[str, Any]] = None,
        existing_source_urls: Optional[List[str]] = None
    ) -> DiscoveryResult:
        """
        Main entry point: discover sources from intent.
        
        Args:
            intent: User's stated intent for the notebook
            focus_areas: List of focus topics
            existing_memory_context: Optional context from user's memory
            existing_source_urls: URLs from existing notebook sources for seed-based discovery
            
        Returns:
            DiscoveryResult with analyzed intent and discovered sources
        """
        start_time = datetime.utcnow()
        result = DiscoveryResult(
            intent_analysis=IntentAnalysis(primary_topic=intent)
        )
        
        try:
            # Step 1: Analyze intent with LLM
            print(f"[SOURCE_DISCOVERY] Analyzing intent: {intent[:80]}... subject={subject}")
            result.intent_analysis = await self._analyze_intent(intent, focus_areas, subject=subject)
            
            # Apply override if user clarified the purpose
            if override_purpose:
                print(f"[SOURCE_DISCOVERY] Using user-specified purpose: {override_purpose}")
                result.intent_analysis.notebook_purpose = override_purpose
                result.intent_analysis.purpose_confidence = 1.0  # User confirmed
                # Set is_company_research flag if applicable
                if override_purpose == "company_research":
                    result.intent_analysis.is_company_research = True
            
            # Apply user-provided company details if available
            if company_details:
                print(f"[SOURCE_DISCOVERY] Using user-provided company details: {company_details}")
                result.intent_analysis.company_name = company_details.get("name")
                result.intent_analysis.company_ticker = company_details.get("ticker")
                result.intent_analysis.industry = company_details.get("industry")
                result.intent_analysis.is_company_research = True
                result.intent_analysis.needs_company_clarification = False
                # If no ticker provided, assume private company
                result.intent_analysis.company_is_private = not company_details.get("ticker")
            
            purpose = result.intent_analysis.notebook_purpose
            print(f"[SOURCE_DISCOVERY] Intent analysis: purpose={purpose}, topic={result.intent_analysis.primary_topic}")
            
            # Step 2: Discover sources based on notebook purpose
            sources = []
            
            # Dynamic discovery based on purpose type
            if purpose == "company_research" or result.intent_analysis.is_company_research:
                if result.intent_analysis.company_name:
                    print(f"[SOURCE_DISCOVERY] Company research: {result.intent_analysis.company_name}")
                    company_sources = await self._discover_company_sources(result.intent_analysis)
                    sources.extend(company_sources)
            
            elif purpose == "topic_research":
                print(f"[SOURCE_DISCOVERY] Topic research: {result.intent_analysis.primary_topic}")
                topic_sources = await self._discover_dynamic_sources(
                    result.intent_analysis,
                    search_queries=[
                        f"best {result.intent_analysis.primary_topic} blogs RSS feeds",
                        f"{result.intent_analysis.primary_topic} news sources",
                        f"top {result.intent_analysis.primary_topic} newsletters",
                    ]
                )
                sources.extend(topic_sources)
            
            elif purpose == "product_research":
                product = result.intent_analysis.product_name or result.intent_analysis.primary_topic
                print(f"[SOURCE_DISCOVERY] Product research: {product}")
                product_sources = await self._discover_dynamic_sources(
                    result.intent_analysis,
                    search_queries=[
                        f"{product} official documentation",
                        f"{product} blog release notes",
                        f"{product} tutorials guides",
                        f"{product} community forum",
                    ]
                )
                sources.extend(product_sources)
            
            elif purpose == "skill_development":
                skill = result.intent_analysis.skill_name or result.intent_analysis.primary_topic
                print(f"[SOURCE_DISCOVERY] Skill development: {skill}")
                # Enhanced skill discovery with courses, docs, and GitHub
                skill_sources = await self._discover_skill_sources(result.intent_analysis)
                sources.extend(skill_sources)
            
            elif purpose == "person_tracking":
                person = result.intent_analysis.person_name or result.intent_analysis.primary_topic
                print(f"[SOURCE_DISCOVERY] Person tracking: {person}")
                # Enhanced person tracking with social, blog, and podcast
                person_sources = await self._discover_person_sources(result.intent_analysis)
                sources.extend(person_sources)
            
            elif purpose == "industry_monitoring":
                industry = result.intent_analysis.industry or result.intent_analysis.primary_topic
                print(f"[SOURCE_DISCOVERY] Industry monitoring: {industry}")
                industry_sources = await self._discover_dynamic_sources(
                    result.intent_analysis,
                    search_queries=[
                        f"{industry} industry news RSS",
                        f"{industry} market research reports",
                        f"{industry} trade publications",
                        f"{industry} analyst reports",
                    ]
                )
                sources.extend(industry_sources)
            
            else:
                # Default: topic-based discovery
                print(f"[SOURCE_DISCOVERY] General discovery for: {result.intent_analysis.primary_topic}")
                topic_sources = await self._discover_topic_sources(result.intent_analysis)
                sources.extend(topic_sources)
            
            # Always add news and YouTube sources
            news_sources = await self._discover_news_sources(result.intent_analysis)
            sources.extend(news_sources)
            
            youtube_sources = await self._discover_youtube_sources(result.intent_analysis)
            sources.extend(youtube_sources)
            
            # Add Reddit communities for relevant topics
            reddit_sources = await self._discover_reddit_sources(result.intent_analysis)
            sources.extend(reddit_sources)
            
            # Add podcasts for topic/skill/person tracking
            if purpose in ["topic_research", "skill_development", "person_tracking", "industry_monitoring"]:
                podcast_sources = await self._discover_podcast_sources(result.intent_analysis)
                sources.extend(podcast_sources)
            
            # Add newsletters for topic/industry research
            if purpose in ["topic_research", "industry_monitoring", "product_research"]:
                newsletter_sources = await self._discover_newsletter_sources(result.intent_analysis)
                sources.extend(newsletter_sources)
            
            # Add arXiv for research-oriented notebooks
            if result.intent_analysis.research_depth == "deep" or purpose == "topic_research":
                arxiv_sources = await self._discover_arxiv_sources(result.intent_analysis)
                sources.extend(arxiv_sources)
            
            # Seed-based discovery: use existing notebook sources to find more from proven channels
            if existing_source_urls:
                seed_sources = await self._discover_from_seed_urls(
                    existing_source_urls, result.intent_analysis
                )
                sources.extend(seed_sources)
            
            print(f"[SOURCE_DISCOVERY] Total sources before validation: {len(sources)}")
            
            # Step 3: Validate discovered sources (check accessibility)
            # Skip validation if it's causing issues - just mark as unvalidated
            try:
                validated_sources = await self._validate_sources(sources)
                result.sources = validated_sources
            except Exception as val_err:
                print(f"[SOURCE_DISCOVERY] Validation failed, using unvalidated sources: {val_err}")
                result.sources = sources  # Use unvalidated sources
            
        except Exception as e:
            import traceback
            logger.error(f"Source discovery error: {e}")
            print(f"[SOURCE_DISCOVERY] ERROR: {e}")
            traceback.print_exc()
            result.errors.append(str(e))
        
        end_time = datetime.utcnow()
        result.discovery_time_ms = (end_time - start_time).total_seconds() * 1000
        
        print(f"[SOURCE_DISCOVERY] Complete: {len(result.sources)} sources in {result.discovery_time_ms:.0f}ms")
        return result
    
    async def _lookup_company_info(self, company_name: str) -> Dict[str, Any]:
        """
        Use web search to look up company info (ticker, industry).
        Returns dict with lookup_success, is_private, needs_clarification flags.
        """
        from services.web_scraper import web_scraper
        
        result = {
            "lookup_success": False,
            "is_private": False,
            "needs_clarification": False,
            "company_name": company_name,
            "ticker": None,
            "industry": None
        }
        
        try:
            # Search for company stock ticker and industry
            query = f"{company_name} stock ticker symbol industry"
            print(f"[SOURCE_DISCOVERY] Looking up company info: {query}")
            
            search_results = await web_scraper.search_web(query, max_results=5)
            
            if not search_results:
                print("[SOURCE_DISCOVERY] No search results - needs clarification")
                result["needs_clarification"] = True
                return result
            
            # Combine snippets for LLM analysis
            snippets = "\n".join([f"- {r['title']}: {r['snippet']}" for r in search_results[:3]])
            
            # Use LLM to extract structured info from search results
            extract_prompt = f"""From these search results, extract company information.

Search results for "{company_name}":
{snippets}

Respond with JSON only:
{{
    "company_name": "official company name or null if not a real company",
    "ticker": "stock ticker or null if private/not found",
    "industry": "industry/sector or null",
    "is_private": true/false (true if company exists but has no public stock),
    "is_real_company": true/false (false if search results don't match a real company)
}}"""
            
            response = await ollama_client.generate(
                prompt=extract_prompt,
                system="Extract company information from search results. Respond only with JSON.",
                model=settings.ollama_fast_model,
                temperature=0.1,
                timeout=15.0
            )
            
            text = response.get("response", "")
            json_start = text.find("{")
            json_end = text.rfind("}") + 1
            
            if json_start >= 0 and json_end > json_start:
                data = json.loads(text[json_start:json_end])
                print(f"[SOURCE_DISCOVERY] Company lookup result: {data}")
                
                # Check if this is a real company
                if not data.get("is_real_company", True):
                    print(f"[SOURCE_DISCOVERY] '{company_name}' doesn't appear to be a real company")
                    result["needs_clarification"] = True
                    return result
                
                result["lookup_success"] = True
                result["company_name"] = data.get("company_name", company_name)
                result["ticker"] = data.get("ticker")
                result["industry"] = data.get("industry")
                result["is_private"] = data.get("is_private", False) or not data.get("ticker")
                return result
                
        except Exception as e:
            print(f"[SOURCE_DISCOVERY] Company lookup failed: {e}")
            result["needs_clarification"] = True
        
        return result

    async def _discover_dynamic_sources(
        self,
        analysis: IntentAnalysis,
        search_queries: List[str]
    ) -> List[DiscoveredSource]:
        """
        Universal dynamic source discovery using web search.
        
        Searches for sources based on the provided queries, then uses LLM
        to extract and categorize relevant sources from search results.
        """
        from services.web_scraper import web_scraper
        
        sources = []
        all_results = []
        
        try:
            # Run searches in parallel for speed with timeout
            print(f"[SOURCE_DISCOVERY] Running {len(search_queries)} dynamic searches...")
            
            async def search_with_timeout(query: str):
                """Wrap search with timeout"""
                try:
                    return await asyncio.wait_for(
                        web_scraper.search_web(query, max_results=5),
                        timeout=self.WEB_SEARCH_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    print(f"[SOURCE_DISCOVERY] Search timed out: {query[:50]}...")
                    return []
            
            search_tasks = [search_with_timeout(query) for query in search_queries]
            search_results = await asyncio.gather(*search_tasks, return_exceptions=True)
            
            for i, results in enumerate(search_results):
                if isinstance(results, Exception):
                    print(f"[SOURCE_DISCOVERY] Search {i} failed: {results}")
                    continue
                if results:
                    all_results.extend(results[:5])
            
            if not all_results:
                print("[SOURCE_DISCOVERY] No search results - using fallback sources")
                return self._get_fallback_sources(analysis)
            
            # Deduplicate by URL
            seen_urls = set()
            unique_results = []
            for r in all_results:
                url = r.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    unique_results.append(r)
            
            print(f"[SOURCE_DISCOVERY] Found {len(unique_results)} unique results, analyzing...")
            
            # Use LLM to categorize and rank sources
            results_text = "\n".join([
                f"- {r['title']}: {r['url']} - {r['snippet'][:100]}"
                for r in unique_results[:15]
            ])
            
            categorize_prompt = f"""Analyze these search results for "{analysis.primary_topic}" research.
Select the TOP 8 most valuable sources for ongoing research and categorization.

Search Results:
{results_text}

For each selected source, respond with JSON array:
[
    {{
        "name": "source name",
        "url": "source url",
        "type": "rss_feed|blog|newsletter|community|web_page|podcast",
        "description": "why this is valuable",
        "confidence": 0.5-1.0
    }}
]

Prioritize:
1. Official sources (docs, blogs)
2. High-quality publications
3. Active communities
4. RSS-capable sites

Respond ONLY with the JSON array."""

            response = await ollama_client.generate(
                prompt=categorize_prompt,
                system="You are a research source curator. Respond only with a JSON array.",
                model=settings.ollama_fast_model,
                temperature=0.2,
                timeout=20.0
            )
            
            text = response.get("response", "")
            json_start = text.find("[")
            json_end = text.rfind("]") + 1
            
            if json_start >= 0 and json_end > json_start:
                source_data = json.loads(text[json_start:json_end])
                
                for item in source_data:
                    source_type_str = item.get("type", "web_page")
                    try:
                        source_type = SourceType(source_type_str)
                    except ValueError:
                        source_type = SourceType.WEB_PAGE
                    
                    sources.append(DiscoveredSource(
                        source_type=source_type,
                        name=item.get("name", "Unknown"),
                        url=item.get("url"),
                        description=item.get("description", ""),
                        confidence=float(item.get("confidence", 0.6)),
                        auto_approve=float(item.get("confidence", 0.6)) >= 0.8,
                        metadata={"discovered_via": "dynamic_search"}
                    ))
                
                print(f"[SOURCE_DISCOVERY] Dynamic discovery found {len(sources)} sources")
            else:
                print("[SOURCE_DISCOVERY] Could not parse LLM response for dynamic sources")
                # Fallback: add top results as web pages
                for r in unique_results[:5]:
                    sources.append(DiscoveredSource(
                        source_type=SourceType.WEB_PAGE,
                        name=r.get("title", "Unknown"),
                        url=r.get("url"),
                        description=r.get("snippet", ""),
                        confidence=0.5,
                        metadata={"discovered_via": "dynamic_search_fallback"}
                    ))
                    
        except Exception as e:
            print(f"[SOURCE_DISCOVERY] Dynamic discovery error: {e}")
            import traceback
            traceback.print_exc()
            # Return fallback sources on error
            return self._get_fallback_sources(analysis)
        
        return sources
    
    def _get_fallback_sources(self, analysis: IntentAnalysis) -> List[DiscoveredSource]:
        """
        Return fallback sources when web search is unavailable.
        Provides basic sources that work without dynamic discovery.
        """
        sources = []
        topic = analysis.primary_topic
        keywords = analysis.keywords[:3] if analysis.keywords else [topic]
        
        # Google News RSS - works for any topic
        for kw in keywords[:2]:
            sources.append(DiscoveredSource(
                source_type=SourceType.NEWS_KEYWORD,
                name=f"Google News: {kw}",
                url=self.KNOWN_RSS_PATTERNS["google_news"].format(query=kw.replace(" ", "+")),
                description=f"News articles about {kw}",
                confidence=0.7,
                has_rss=True,
                rss_url=self.KNOWN_RSS_PATTERNS["google_news"].format(query=kw.replace(" ", "+")),
                metadata={"keyword": kw, "fallback": True}
            ))
        
        # YouTube keyword search
        sources.append(DiscoveredSource(
            source_type=SourceType.YOUTUBE_KEYWORD,
            name=f"YouTube: {topic}",
            description=f"YouTube videos about {topic}",
            confidence=0.6,
            metadata={"keyword": topic, "fallback": True}
        ))
        
        # arXiv for technical/research topics
        if any(kw.lower() in topic.lower() for kw in ["ai", "machine learning", "research", "science", "computer", "data"]):
            sources.append(DiscoveredSource(
                source_type=SourceType.ARXIV_CATEGORY,
                name="arXiv: cs.AI",
                url="http://export.arxiv.org/rss/cs.AI",
                description="Latest AI research papers from arXiv",
                confidence=0.65,
                has_rss=True,
                rss_url="http://export.arxiv.org/rss/cs.AI",
                metadata={"category": "cs.AI", "fallback": True}
            ))
        
        print(f"[SOURCE_DISCOVERY] Returning {len(sources)} fallback sources")
        return sources

    async def _analyze_intent(
        self, 
        intent: str, 
        focus_areas: List[str],
        subject: str = ""
    ) -> IntentAnalysis:
        """Use LLM to deeply analyze the user's intent"""
        
        focus_str = ", ".join(focus_areas) if focus_areas else "general"
        
        # Build subject context for the prompt
        subject_context = ""
        if subject:
            subject_context = f"""
IMPORTANT - PRIMARY RESEARCH SUBJECT: "{subject}"
The user has explicitly identified "{subject}" as the main subject of this research.
- Use "{subject}" as the primary_topic and company_name (if it's a company).
- Do NOT extract other companies, products, or people mentioned in the intent description as the primary subject.
- Other entities mentioned in the intent are just context, NOT the research target.
- Keywords should combine "{subject}" with the focus areas (e.g., "{subject} {focus_areas[0] if focus_areas else 'news'}").
"""
        
        prompt = f"""Analyze this research intent and classify it for source discovery.
{subject_context}
Intent: {intent}
Focus Areas: {focus_str}

NOTEBOOK PURPOSE TYPES:
- company_research: Track a specific company (e.g., Costco, Tesla, Apple)
- topic_research: Broad topic like AI, Leadership, Renewable Energy
- product_research: Specific product or technology (e.g., iPhone, Kubernetes, React)
- skill_development: Learning a skill (e.g., Python programming, Public Speaking)
- person_tracking: Following a person, coaching a team member, personal development, team management, 1:1 prep
- industry_monitoring: Monitor an industry sector (e.g., Fintech, Healthcare)
- project_knowledge: Project-specific knowledge base
- personal_interests: Hobbies and interests (e.g., Photography, Cooking)

Respond with JSON only:
{{
    "primary_topic": "main research topic",
    "notebook_purpose": "one of the purpose types above",
    "purpose_confidence": 0.0-1.0 (how confident you are in the purpose classification),
    "is_company_research": true/false,
    "company_name": "company name if applicable, else null",
    "company_ticker": "stock ticker if known, else null",
    "product_name": "product/technology name if applicable, else null",
    "person_name": "person name if applicable, else null",
    "skill_name": "skill being learned if applicable, else null",
    "industry": "industry/sector if applicable",
    "competitors": ["list", "of", "competitors"] or [],
    "keywords": ["key", "search", "terms"],
    "geographic_focus": "region/country if applicable, else null",
    "time_sensitivity": "breaking|daily|weekly|archival",
    "research_depth": "surface|standard|deep"
}}

Confidence guide:
- 0.9+: Very clear intent (e.g., "Track Tesla stock" → company_research)
- 0.7-0.9: Reasonably clear (e.g., "AI trends" → topic_research)
- 0.5-0.7: Ambiguous, could be multiple purposes
- <0.5: Very unclear, needs user clarification

Examples:
- "Track Costco" → company_research, company_name: Costco
- "AI Research" → topic_research, keywords: [AI, machine learning, deep learning]
- "Learn Python" → skill_development, skill_name: Python
- "Follow Elon Musk" → person_tracking, person_name: Elon Musk
- "Team coaching notes" → person_tracking
- "Personal development for Jane" → person_tracking, person_name: Jane
- "1:1 prep for my team" → person_tracking
- "React development" → product_research, product_name: React"""

        try:
            print("[SOURCE_DISCOVERY] Calling LLM for intent analysis...")
            response = await ollama_client.generate(
                prompt=prompt,
                system="You are an intent analysis system. Respond only with valid JSON.",
                model=settings.ollama_fast_model,
                temperature=0.3,
                timeout=30.0
            )
            
            text = response.get("response", "")
            print(f"[SOURCE_DISCOVERY] LLM response: {text[:200]}...")
            
            json_start = text.find("{")
            json_end = text.rfind("}") + 1
            
            if json_start >= 0 and json_end > json_start:
                data = json.loads(text[json_start:json_end])
                analysis = IntentAnalysis(**data)
                
                # Hard override: if subject was explicitly provided, force it as primary
                if subject:
                    analysis.primary_topic = subject
                    # If company research detected, ensure company_name matches subject
                    if analysis.is_company_research:
                        analysis.company_name = subject
                    # Ensure keywords include subject + focus_area combos
                    if focus_areas:
                        combined = [f"{subject} {fa}" for fa in focus_areas if subject.lower() not in fa.lower()]
                        combined.append(subject)
                        # Merge with any LLM-generated keywords, but subject combos first
                        existing = [k for k in analysis.keywords if subject.lower() in k.lower()]
                        analysis.keywords = list(dict.fromkeys(combined + existing))[:8]
                
                # If company research detected but missing ticker, look it up via web search
                if analysis.is_company_research and analysis.company_name and not analysis.company_ticker:
                    company_info = await self._lookup_company_info(analysis.company_name)
                    
                    if company_info.get("needs_clarification"):
                        # Company lookup failed - flag for user clarification
                        analysis.needs_company_clarification = True
                        print(f"[SOURCE_DISCOVERY] Company '{analysis.company_name}' needs user clarification")
                    elif company_info.get("lookup_success"):
                        analysis.company_ticker = company_info.get("ticker")
                        analysis.company_is_private = company_info.get("is_private", False)
                        if not analysis.industry:
                            analysis.industry = company_info.get("industry")
                        # Update company name if we found the official name
                        if company_info.get("company_name"):
                            analysis.company_name = company_info.get("company_name")
                
                return analysis
            else:
                print("[SOURCE_DISCOVERY] No valid JSON in LLM response, using fallback")
        except Exception as e:
            print(f"[SOURCE_DISCOVERY] LLM intent analysis failed: {e}, using fallback")
            logger.error(f"Intent analysis failed: {e}")
        
        # Fallback: Use subject if provided, else extract potential company name
        words = intent.split()
        potential_company = subject if subject else None
        if not potential_company:
            for word in words:
                # Skip common words, look for proper nouns
                if word[0].isupper() and len(word) > 2 and word.lower() not in ["the", "and", "for", "research", "analysis", "track", "monitor"]:
                    potential_company = word
                    break
        
        # Build keywords from subject + focus areas
        if subject and focus_areas:
            fallback_keywords = [f"{subject} {fa}" for fa in focus_areas if subject.lower() not in fa.lower()]
            fallback_keywords.append(subject)
        elif focus_areas:
            fallback_keywords = focus_areas
        else:
            fallback_keywords = [w for w in words if len(w) > 3][:5]
        
        fallback = IntentAnalysis(
            primary_topic=subject or intent,
            keywords=fallback_keywords
        )
        
        # Try web lookup for potential company
        if potential_company:
            print(f"[SOURCE_DISCOVERY] Fallback: trying web lookup for '{potential_company}'")
            company_info = await self._lookup_company_info(potential_company)
            if company_info.get("lookup_success"):
                fallback.is_company_research = True
                fallback.company_name = company_info.get("company_name", potential_company)
                fallback.company_ticker = company_info.get("ticker")
                fallback.company_is_private = company_info.get("is_private", False)
                fallback.industry = company_info.get("industry")
            elif company_info.get("needs_clarification"):
                # Set flag so frontend can ask user for clarification
                fallback.is_company_research = True
                fallback.company_name = potential_company
                fallback.needs_company_clarification = True
        
        print(f"[SOURCE_DISCOVERY] Using fallback analysis: {fallback.model_dump()}")
        return fallback
    
    async def _discover_company_sources(
        self, 
        analysis: IntentAnalysis
    ) -> List[DiscoveredSource]:
        """Discover sources for company research"""
        sources = []
        company = analysis.company_name
        ticker = analysis.company_ticker
        is_private = analysis.company_is_private
        
        if not company:
            return sources
        
        # 1. Company news page (discovered via LLM + web search simulation)
        sources.append(DiscoveredSource(
            source_type=SourceType.COMPANY_NEWS,
            name=f"{company} Official News",
            url=f"https://www.{company.lower().replace(' ', '')}.com/news",
            description=f"Official news and press releases from {company}",
            confidence=0.9,
            auto_approve=True,
            metadata={"company": company, "official": True}
        ))
        
        # 2. For private companies, add Crunchbase and TechCrunch
        if is_private:
            sources.append(DiscoveredSource(
                source_type=SourceType.WEB_PAGE,
                name=f"Crunchbase - {company}",
                url=f"https://www.crunchbase.com/organization/{company.lower().replace(' ', '-')}",
                description=f"Crunchbase profile for {company} - funding, employees, news",
                confidence=0.8,
                metadata={"company": company, "source": "crunchbase", "is_private": True}
            ))
            sources.append(DiscoveredSource(
                source_type=SourceType.NEWS_KEYWORD,
                name=f"TechCrunch - {company}",
                description=f"TechCrunch coverage of {company}",
                confidence=0.75,
                metadata={"keyword": company, "source": "techcrunch"}
            ))
        
        # 3. SEC filings only for public companies with ticker
        if ticker and not is_private:
            sources.append(DiscoveredSource(
                source_type=SourceType.SEC_FILING,
                name=f"{ticker} SEC Filings",
                url=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={ticker}&type=&dateb=&owner=include&count=40",
                description=f"SEC filings (10-K, 10-Q, 8-K) for {company}",
                confidence=1.0,
                auto_approve=True,
                metadata={"ticker": ticker, "company_name": company, "filing_types": ["10-K", "10-Q", "8-K"]}
            ))
            
            # Yahoo Finance RSS
            sources.append(DiscoveredSource(
                source_type=SourceType.RSS_FEED,
                name=f"Yahoo Finance - {ticker}",
                url=self.KNOWN_RSS_PATTERNS["yahoo_finance"].format(ticker=ticker),
                description=f"Yahoo Finance news for {ticker}",
                confidence=0.85,
                auto_approve=True,
                has_rss=True,
                rss_url=self.KNOWN_RSS_PATTERNS["yahoo_finance"].format(ticker=ticker),
                metadata={"ticker": ticker, "source": "yahoo_finance"}
            ))
            
            # Seeking Alpha (if they have RSS)
            sources.append(DiscoveredSource(
                source_type=SourceType.RSS_FEED,
                name=f"Seeking Alpha - {ticker}",
                url=f"https://seekingalpha.com/symbol/{ticker}",
                description=f"Seeking Alpha analysis for {ticker}",
                confidence=0.75,
                metadata={"ticker": ticker, "source": "seeking_alpha"}
            ))
        
        # 4. Investor/Annual Report — valuable for financials, investor relations, sustainability
        financial_keywords = {"financials", "financial", "investor", "investor relations",
                              "sustainability", "esg", "annual report", "earnings",
                              "revenue", "10-k", "quarterly"}
        focus_lower = {k.lower() for k in analysis.keywords}
        if focus_lower & financial_keywords or ticker:
            # Build the most likely investor relations / annual report URL
            company_slug = company.lower().replace(' ', '').replace(',', '').replace('.', '')
            sources.append(DiscoveredSource(
                source_type=SourceType.WEB_PAGE,
                name=f"{company} Annual/Investor Report",
                url=f"https://www.{company_slug}.com/investor-relations",
                description=f"Annual report and investor presentations for {company}. "
                            f"Published yearly — contains financials, strategy, ESG, and outlook.",
                confidence=0.85,
                auto_approve=True,
                metadata={
                    "company": company,
                    "type": "investor_report",
                    "frequency": "annual",
                    "content_types": ["annual_report", "investor_presentation", "proxy_statement"],
                }
            ))

        # 5. Competitors
        for competitor in analysis.competitors[:3]:
            sources.append(DiscoveredSource(
                source_type=SourceType.NEWS_KEYWORD,
                name=f"Competitor: {competitor}",
                description=f"News about competitor {competitor}",
                confidence=0.7,
                metadata={"competitor": competitor, "type": "competitor_tracking"}
            ))
        
        return sources
    
    async def _discover_topic_sources(
        self, 
        analysis: IntentAnalysis
    ) -> List[DiscoveredSource]:
        """Discover RSS feeds for topics/industry"""
        sources = []
        
        # Use LLM to suggest RSS feeds for the industry
        if analysis.industry:
            industry_feeds = await self._get_industry_feeds(analysis.industry)
            sources.extend(industry_feeds)
        
        # Determine geographic region for news
        geo = "US"  # default
        if analysis.geographic_focus:
            geo_lower = analysis.geographic_focus.lower()
            geo = self.GEO_MAPPINGS.get(geo_lower, "US")
        
        # Add Google News RSS for main keywords (with geo targeting)
        for keyword in analysis.keywords[:3]:
            news_url = self.KNOWN_RSS_PATTERNS["google_news_geo"].format(
                query=keyword.replace(" ", "+"),
                geo=geo
            ) if geo != "US" else self.KNOWN_RSS_PATTERNS["google_news"].format(
                query=keyword.replace(" ", "+")
            )
            
            sources.append(DiscoveredSource(
                source_type=SourceType.RSS_FEED,
                name=f"Google News: {keyword}" + (f" ({geo})" if geo != "US" else ""),
                url=news_url,
                description=f"Google News RSS for '{keyword}'" + (f" focused on {analysis.geographic_focus}" if analysis.geographic_focus else ""),
                confidence=0.8,
                has_rss=True,
                rss_url=news_url,
                metadata={"keyword": keyword, "source": "google_news", "geo": geo}
            ))
        
        return sources
    
    async def _discover_skill_sources(
        self, 
        analysis: IntentAnalysis
    ) -> List[DiscoveredSource]:
        """Enhanced skill development source discovery with courses, docs, GitHub"""
        from services.web_scraper import web_scraper
        sources = []
        skill = analysis.skill_name or analysis.primary_topic
        
        # 1. Official documentation
        sources.append(DiscoveredSource(
            source_type=SourceType.WEB_PAGE,
            name=f"{skill} Official Documentation",
            url=f"https://www.google.com/search?q={skill.replace(' ', '+')}+official+documentation",
            description=f"Official documentation for {skill}",
            confidence=0.85,
            metadata={"skill": skill, "type": "documentation"}
        ))
        
        # 2. Search for courses on major platforms
        try:
            query = f"{skill} online course free tutorial"
            results = await web_scraper.search_web(query, max_results=5)
            
            if results:
                # Extract course platforms
                course_platforms = ["coursera", "udemy", "edx", "codecademy", "pluralsight", "linkedin.com/learning", "freecodecamp"]
                for r in results:
                    url = r.get("url", "").lower()
                    for platform in course_platforms:
                        if platform in url:
                            sources.append(DiscoveredSource(
                                source_type=SourceType.WEB_PAGE,
                                name=r.get("title", f"{skill} Course"),
                                url=r.get("url"),
                                description=r.get("snippet", f"Course about {skill}"),
                                confidence=0.75,
                                metadata={"skill": skill, "platform": platform, "type": "course"}
                            ))
                            break
        except Exception as e:
            print(f"[SOURCE_DISCOVERY] Course discovery error: {e}")
        
        # 3. GitHub repositories (for technical skills)
        tech_keywords = ["programming", "code", "software", "development", "framework", "library", "api", "python", "javascript", "java", "react", "node"]
        if any(kw in skill.lower() for kw in tech_keywords):
            sources.append(DiscoveredSource(
                source_type=SourceType.WEB_PAGE,
                name=f"GitHub: {skill}",
                url=f"https://github.com/topics/{skill.lower().replace(' ', '-')}",
                description=f"GitHub repositories and projects for {skill}",
                confidence=0.7,
                metadata={"skill": skill, "type": "github", "platform": "github"}
            ))
            
            # Awesome lists
            sources.append(DiscoveredSource(
                source_type=SourceType.WEB_PAGE,
                name=f"Awesome {skill}",
                url=f"https://github.com/search?q=awesome+{skill.replace(' ', '+')}&type=repositories",
                description=f"Curated list of {skill} resources",
                confidence=0.65,
                metadata={"skill": skill, "type": "awesome_list"}
            ))
        
        # 4. Dynamic discovery for tutorials and guides
        skill_sources = await self._discover_dynamic_sources(
            analysis,
            search_queries=[
                f"learn {skill} tutorials beginners",
                f"{skill} best practices guide",
                f"{skill} cheat sheet reference",
            ]
        )
        sources.extend(skill_sources)
        
        return sources
    
    async def _discover_person_sources(
        self, 
        analysis: IntentAnalysis
    ) -> List[DiscoveredSource]:
        """Enhanced person tracking with social, blog, and podcast discovery"""
        from services.web_scraper import web_scraper
        sources = []
        person = analysis.person_name or analysis.primary_topic
        
        # 1. Search for person's official presence
        try:
            query = f"{person} official website blog"
            results = await web_scraper.search_web(query, max_results=5)
            
            if results:
                # Look for official site, blog, or personal page
                for r in results:
                    url = r.get("url", "").lower()
                    title = r.get("title", "").lower()
                    
                    # Check if it looks like an official/personal site
                    if any(x in url or x in title for x in [person.lower().split()[0], "official", "blog", ".me", ".io"]):
                        sources.append(DiscoveredSource(
                            source_type=SourceType.BLOG,
                            name=r.get("title", f"{person}'s Site"),
                            url=r.get("url"),
                            description=r.get("snippet", f"Official presence of {person}"),
                            confidence=0.8,
                            metadata={"person": person, "type": "official_site"}
                        ))
                        break
        except Exception as e:
            print(f"[SOURCE_DISCOVERY] Person site discovery error: {e}")
        
        # 2. Social media profiles
        social_platforms = [
            ("Twitter/X", f"https://twitter.com/search?q={person.replace(' ', '%20')}&f=user", "twitter"),
            ("LinkedIn", f"https://www.linkedin.com/search/results/people/?keywords={person.replace(' ', '%20')}", "linkedin"),
        ]
        
        for platform_name, url, platform_key in social_platforms:
            sources.append(DiscoveredSource(
                source_type=SourceType.WEB_PAGE,
                name=f"{person} on {platform_name}",
                url=url,
                description=f"Search for {person}'s {platform_name} profile",
                confidence=0.6,
                metadata={"person": person, "platform": platform_key}
            ))
        
        # 3. Podcast appearances
        try:
            query = f"{person} podcast interview guest"
            results = await web_scraper.search_web(query, max_results=5)
            
            if results:
                snippets = "\n".join([f"- {r['title']}: {r['snippet']}" for r in results[:5]])
                
                extract_prompt = f"""From these search results, extract podcast appearances for "{person}".

{snippets}

Respond with JSON array only (max 3 podcasts):
[
    {{"podcast_name": "Podcast Name", "episode_url": "URL if found", "description": "what was discussed"}}
]"""
                
                response = await ollama_client.generate(
                    prompt=extract_prompt,
                    system="Extract podcast appearances. Respond only with JSON array.",
                    model=settings.ollama_fast_model,
                    temperature=0.1,
                    timeout=15.0
                )
                
                text = response.get("response", "")
                json_start = text.find("[")
                json_end = text.rfind("]") + 1
                
                if json_start >= 0 and json_end > json_start:
                    podcasts = json.loads(text[json_start:json_end])
                    for p in podcasts[:3]:
                        sources.append(DiscoveredSource(
                            source_type=SourceType.PODCAST,
                            name=f"{person} on {p.get('podcast_name', 'Podcast')}",
                            url=p.get("episode_url"),
                            description=p.get("description", f"Podcast interview with {person}"),
                            confidence=0.65,
                            metadata={"person": person, "podcast": p.get("podcast_name"), "type": "interview"}
                        ))
        except Exception as e:
            print(f"[SOURCE_DISCOVERY] Person podcast discovery error: {e}")
        
        # 4. News and mentions
        sources.append(DiscoveredSource(
            source_type=SourceType.NEWS_KEYWORD,
            name=f"News: {person}",
            description=f"News mentions of {person}",
            confidence=0.7,
            metadata={"keyword": person, "type": "person_news"}
        ))
        
        return sources
    
    async def _get_industry_feeds(self, industry: str) -> List[DiscoveredSource]:
        """Use LLM to suggest industry-specific RSS feeds"""
        prompt = f"""Suggest 3-5 RSS feeds for the {industry} industry.
Focus on reputable trade publications and news sources.

Respond with JSON array only:
[
    {{"name": "Publication Name", "url": "https://example.com/rss", "description": "brief description"}}
]

Only include feeds you're confident exist. Better to suggest fewer high-quality feeds."""

        try:
            response = await ollama_client.generate(
                prompt=prompt,
                system="You are a research assistant. Respond only with valid JSON array.",
                model=settings.ollama_fast_model,
                temperature=0.3
            )
            
            text = response.get("response", "")
            json_start = text.find("[")
            json_end = text.rfind("]") + 1
            
            if json_start >= 0 and json_end > json_start:
                feeds = json.loads(text[json_start:json_end])
                return [
                    DiscoveredSource(
                        source_type=SourceType.RSS_FEED,
                        name=f.get("name", "Unknown"),
                        url=f.get("url"),
                        description=f.get("description", ""),
                        confidence=0.6,  # LLM suggestions get medium confidence
                        metadata={"industry": industry, "llm_suggested": True}
                    )
                    for f in feeds
                ]
        except Exception as e:
            logger.error(f"Industry feed discovery failed: {e}")
        
        return []
    
    async def _discover_news_sources(
        self, 
        analysis: IntentAnalysis
    ) -> List[DiscoveredSource]:
        """Discover news sources based on keywords with time sensitivity"""
        from services.web_scraper import web_scraper
        sources = []
        
        # Get freshness filter based on time_sensitivity
        freshness = self.FRESHNESS_MAPPINGS.get(analysis.time_sensitivity, None)
        
        # Determine geographic region
        geo = "US"
        if analysis.geographic_focus:
            geo_lower = analysis.geographic_focus.lower()
            geo = self.GEO_MAPPINGS.get(geo_lower, "US")
        
        # For breaking/daily news, search for latest sources
        if analysis.time_sensitivity in ["breaking", "daily"]:
            try:
                query = f"{analysis.primary_topic} latest news"
                results = await web_scraper.search_web(query, max_results=5, freshness=freshness)
                
                for r in results[:3]:
                    sources.append(DiscoveredSource(
                        source_type=SourceType.WEB_PAGE,
                        name=r.get("title", "News Article"),
                        url=r.get("url"),
                        description=r.get("snippet", f"Recent news about {analysis.primary_topic}"),
                        confidence=0.7,
                        metadata={
                            "topic": analysis.primary_topic, 
                            "freshness": freshness,
                            "time_sensitivity": analysis.time_sensitivity
                        }
                    ))
            except Exception as e:
                print(f"[SOURCE_DISCOVERY] Time-sensitive news discovery error: {e}")
        
        # Fallback Reddit mapping for topics without dynamic discovery
        subreddit_map = {
            "technology": "technology",
            "artificial intelligence": "MachineLearning",
            "ai": "MachineLearning",
            "stocks": "stocks",
            "investing": "investing",
            "cryptocurrency": "CryptoCurrency",
            "business": "business",
            "marketing": "marketing",
            "startups": "startups",
        }
        
        topic_lower = analysis.primary_topic.lower()
        for key, subreddit in subreddit_map.items():
            if key in topic_lower:
                sources.append(DiscoveredSource(
                    source_type=SourceType.COMMUNITY,
                    name=f"Reddit r/{subreddit}",
                    url=f"https://www.reddit.com/r/{subreddit}/",
                    description=f"Reddit discussions in r/{subreddit}",
                    confidence=0.65,
                    has_rss=True,
                    rss_url=self.KNOWN_RSS_PATTERNS["reddit"].format(subreddit=subreddit),
                    metadata={"subreddit": subreddit, "source": "reddit", "geo": geo}
                ))
                break
        
        return sources
    
    async def _discover_youtube_sources(
        self, 
        analysis: IntentAnalysis
    ) -> List[DiscoveredSource]:
        """Discover YouTube channels and search keywords dynamically"""
        from services.web_scraper import web_scraper
        sources = []
        topic = analysis.primary_topic
        
        # Add search keywords for YouTube monitoring
        for keyword in analysis.keywords[:2]:
            sources.append(DiscoveredSource(
                source_type=SourceType.YOUTUBE_KEYWORD,
                name=f"YouTube: {keyword}",
                description=f"Monitor YouTube for videos about '{keyword}'",
                confidence=0.6,
                metadata={"keyword": keyword, "platform": "youtube"}
            ))
        
        # If company research, add company name as YouTube keyword
        if analysis.is_company_research and analysis.company_name:
            sources.append(DiscoveredSource(
                source_type=SourceType.YOUTUBE_KEYWORD,
                name=f"YouTube: {analysis.company_name}",
                description=f"Monitor YouTube for videos about {analysis.company_name}",
                confidence=0.7,
                metadata={"keyword": analysis.company_name, "platform": "youtube", "type": "company"}
            ))
        
        # Discover specific YouTube channels via web search
        try:
            query = f"best YouTube channels for {topic}"
            results = await web_scraper.search_web(query, max_results=5)
            
            if results:
                # Extract YouTube channel URLs
                for r in results:
                    url = r.get("url", "")
                    if "youtube.com/c/" in url or "youtube.com/channel/" in url or "youtube.com/@" in url:
                        sources.append(DiscoveredSource(
                            source_type=SourceType.YOUTUBE_CHANNEL,
                            name=r.get("title", "YouTube Channel"),
                            url=url,
                            description=r.get("snippet", f"YouTube channel about {topic}"),
                            confidence=0.7,
                            metadata={"topic": topic, "source": "channel_discovery"}
                        ))
                
                # If no channels found in URLs, use LLM to extract channel names
                if not any(s.source_type == SourceType.YOUTUBE_CHANNEL for s in sources):
                    snippets = "\n".join([f"- {r['title']}: {r['snippet']}" for r in results[:5]])
                    
                    extract_prompt = f"""From these search results, extract YouTube channel information for "{topic}".

{snippets}

Respond with JSON array only (max 3 channels):
[
    {{"name": "Channel Name", "url": "youtube channel URL if found", "description": "what they cover"}}
]"""
                    
                    response = await ollama_client.generate(
                        prompt=extract_prompt,
                        system="Extract YouTube channel information. Respond only with JSON array.",
                        model=settings.ollama_fast_model,
                        temperature=0.1,
                        timeout=15.0
                    )
                    
                    text = response.get("response", "")
                    json_start = text.find("[")
                    json_end = text.rfind("]") + 1
                    
                    if json_start >= 0 and json_end > json_start:
                        channels = json.loads(text[json_start:json_end])
                        for c in channels[:3]:
                            sources.append(DiscoveredSource(
                                source_type=SourceType.YOUTUBE_CHANNEL,
                                name=c.get("name", "YouTube Channel"),
                                url=c.get("url"),
                                description=c.get("description", f"YouTube channel about {topic}"),
                                confidence=0.6,
                                metadata={"topic": topic, "source": "llm_suggestion"}
                            ))
                            
        except Exception as e:
            print(f"[SOURCE_DISCOVERY] YouTube channel discovery error: {e}")
        
        return sources
    
    async def _discover_arxiv_sources(
        self, 
        analysis: IntentAnalysis
    ) -> List[DiscoveredSource]:
        """Discover arXiv categories for academic research"""
        sources = []
        
        # Map topics to arXiv categories
        arxiv_category_map = {
            "machine learning": "cs.LG",
            "artificial intelligence": "cs.AI",
            "natural language": "cs.CL",
            "computer vision": "cs.CV",
            "robotics": "cs.RO",
            "physics": "physics",
            "mathematics": "math",
            "economics": "econ",
            "finance": "q-fin",
            "biology": "q-bio",
        }
        
        topic_lower = analysis.primary_topic.lower()
        for key, category in arxiv_category_map.items():
            if key in topic_lower:
                sources.append(DiscoveredSource(
                    source_type=SourceType.ARXIV_CATEGORY,
                    name=f"arXiv {category}",
                    url=self.KNOWN_RSS_PATTERNS["arxiv"].format(category=category),
                    description=f"Latest papers in arXiv category {category}",
                    confidence=0.8,
                    has_rss=True,
                    rss_url=self.KNOWN_RSS_PATTERNS["arxiv"].format(category=category),
                    metadata={"category": category, "source": "arxiv"}
                ))
                break
        
        return sources
    
    async def _discover_from_seed_urls(
        self,
        existing_urls: List[str],
        analysis: IntentAnalysis
    ) -> List[DiscoveredSource]:
        """
        Use existing notebook source URLs to discover additional sources.
        Extracts domains, channels, and authors from proven-valuable URLs.
        """
        from urllib.parse import urlparse
        import re
        
        sources = []
        domain_counts: Dict[str, int] = {}
        substack_pubs = set()
        medium_authors = set()
        youtube_channels = set()
        
        skip_domains = {
            "twitter.com", "x.com", "facebook.com", "linkedin.com",
            "reddit.com", "github.com", "google.com", "t.co",
            "bit.ly", "docs.google.com", "en.wikipedia.org",
        }
        
        for url in existing_urls:
            try:
                parsed = urlparse(url)
                domain = parsed.netloc.lower().replace("www.", "")
                
                if domain in skip_domains:
                    continue
                
                # Substack publications
                match = re.search(r'https?://([^.]+)\.substack\.com', url)
                if match:
                    substack_pubs.add(match.group(1))
                    continue
                
                # YouTube channels
                if "youtube.com" in domain:
                    if "/@" in parsed.path or "/c/" in parsed.path or "/channel/" in parsed.path:
                        parts = parsed.path.split("/")
                        if len(parts) > 1 and parts[1]:
                            youtube_channels.add(parts[1])
                    continue
                
                # Medium authors
                if "medium.com" in domain:
                    parts = parsed.path.strip("/").split("/")
                    if parts and parts[0].startswith("@"):
                        medium_authors.add(parts[0])
                    elif parts and parts[0] not in ("", "p", "s", "tag"):
                        medium_authors.add(parts[0])
                    continue
                
                domain_counts[domain] = domain_counts.get(domain, 0) + 1
            except Exception:
                continue
        
        topic = analysis.primary_topic
        
        # Frequent domains → site-scoped news searches
        frequent = sorted(domain_counts.items(), key=lambda x: -x[1])
        for domain, count in frequent[:6]:
            if count >= 2:
                sources.append(DiscoveredSource(
                    source_type=SourceType.NEWS_KEYWORD,
                    name=f"{domain} (from your sources)",
                    url=f"https://{domain}",
                    description=f"You have {count} sources from {domain} — searching for more {topic} content there",
                    confidence=0.85,
                    metadata={"seed_domain": domain, "count": count, "source": "seed_discovery"}
                ))
        
        # Substack publications → RSS feeds
        for pub in list(substack_pubs)[:3]:
            rss_url = f"https://{pub}.substack.com/feed"
            sources.append(DiscoveredSource(
                source_type=SourceType.NEWSLETTER,
                name=f"{pub} (Substack)",
                url=f"https://{pub}.substack.com",
                description=f"Substack newsletter from your existing sources",
                confidence=0.9,
                has_rss=True,
                rss_url=rss_url,
                metadata={"substack": pub, "source": "seed_discovery"}
            ))
        
        # Medium authors → news keyword searches
        for author in list(medium_authors)[:3]:
            sources.append(DiscoveredSource(
                source_type=SourceType.BLOG,
                name=f"Medium: {author}",
                url=f"https://medium.com/{author}",
                description=f"Medium author from your existing sources",
                confidence=0.8,
                metadata={"medium_author": author, "source": "seed_discovery"}
            ))
        
        # YouTube channels → channel sources
        for channel in list(youtube_channels)[:3]:
            sources.append(DiscoveredSource(
                source_type=SourceType.YOUTUBE_CHANNEL,
                name=f"YouTube: {channel}",
                url=f"https://youtube.com/{channel}",
                description=f"YouTube channel from your existing sources",
                confidence=0.8,
                metadata={"youtube_channel": channel, "source": "seed_discovery"}
            ))
        
        if sources:
            print(f"[SOURCE_DISCOVERY] Seed-based discovery: {len(sources)} sources from {len(existing_urls)} existing URLs")
        
        return sources

    async def _discover_reddit_sources(
        self, 
        analysis: IntentAnalysis
    ) -> List[DiscoveredSource]:
        """Discover Reddit communities dynamically using web search"""
        from services.web_scraper import web_scraper
        sources = []
        topic = analysis.primary_topic
        
        try:
            # Search for relevant subreddits
            query = f"reddit best subreddit for {topic}"
            results = await web_scraper.search_web(query, max_results=5)
            
            # Extract subreddit names from results
            subreddits_found = set()
            for r in results:
                url = r.get("url", "")
                # Extract subreddit from reddit URLs
                import re
                match = re.search(r'reddit\.com/r/(\w+)', url)
                if match:
                    subreddits_found.add(match.group(1))
            
            # Add discovered subreddits
            for subreddit in list(subreddits_found)[:3]:
                sources.append(DiscoveredSource(
                    source_type=SourceType.COMMUNITY,
                    name=f"Reddit r/{subreddit}",
                    url=f"https://www.reddit.com/r/{subreddit}/",
                    description=f"Reddit community discussions in r/{subreddit}",
                    confidence=0.7,
                    has_rss=True,
                    rss_url=self.KNOWN_RSS_PATTERNS["reddit"].format(subreddit=subreddit),
                    metadata={"subreddit": subreddit, "source": "reddit", "discovered": True}
                ))
                
        except Exception as e:
            print(f"[SOURCE_DISCOVERY] Reddit discovery error: {e}")
        
        return sources
    
    async def _discover_podcast_sources(
        self, 
        analysis: IntentAnalysis
    ) -> List[DiscoveredSource]:
        """Discover podcasts dynamically using web search"""
        from services.web_scraper import web_scraper
        sources = []
        topic = analysis.primary_topic
        
        try:
            # Search for relevant podcasts
            query = f"best {topic} podcasts RSS feed"
            results = await web_scraper.search_web(query, max_results=5)
            
            if results:
                # Use LLM to extract podcast info from search results
                snippets = "\n".join([f"- {r['title']}: {r['snippet']}" for r in results[:5]])
                
                extract_prompt = f"""From these search results, extract podcast information for "{topic}".

{snippets}

Respond with JSON array only (max 3 podcasts):
[
    {{"name": "Podcast Name", "url": "podcast website or RSS feed URL", "description": "brief description"}}
]"""
                
                response = await ollama_client.generate(
                    prompt=extract_prompt,
                    system="Extract podcast information from search results. Respond only with JSON array.",
                    model=settings.ollama_fast_model,
                    temperature=0.1,
                    timeout=15.0
                )
                
                text = response.get("response", "")
                json_start = text.find("[")
                json_end = text.rfind("]") + 1
                
                if json_start >= 0 and json_end > json_start:
                    podcasts = json.loads(text[json_start:json_end])
                    for p in podcasts[:3]:
                        sources.append(DiscoveredSource(
                            source_type=SourceType.PODCAST,
                            name=p.get("name", "Unknown Podcast"),
                            url=p.get("url"),
                            description=p.get("description", f"Podcast about {topic}"),
                            confidence=0.65,
                            metadata={"topic": topic, "source": "podcast_discovery"}
                        ))
                        
        except Exception as e:
            print(f"[SOURCE_DISCOVERY] Podcast discovery error: {e}")
        
        return sources
    
    async def _discover_newsletter_sources(
        self, 
        analysis: IntentAnalysis
    ) -> List[DiscoveredSource]:
        """Discover newsletters (especially Substack) dynamically"""
        from services.web_scraper import web_scraper
        sources = []
        topic = analysis.primary_topic
        
        try:
            # Search for relevant newsletters/Substacks
            query = f"best {topic} newsletter substack"
            results = await web_scraper.search_web(query, max_results=5)
            
            if results:
                # Extract Substack URLs directly
                for r in results:
                    url = r.get("url", "")
                    if "substack.com" in url:
                        import re
                        match = re.search(r'https?://([^.]+)\.substack\.com', url)
                        if match:
                            publication = match.group(1)
                            sources.append(DiscoveredSource(
                                source_type=SourceType.NEWSLETTER,
                                name=r.get("title", f"{publication} Newsletter"),
                                url=url,
                                description=r.get("snippet", f"Substack newsletter about {topic}"),
                                confidence=0.7,
                                has_rss=True,
                                rss_url=self.KNOWN_RSS_PATTERNS["substack"].format(publication=publication),
                                metadata={"publication": publication, "source": "substack"}
                            ))
                
                # If no Substacks found, use LLM to suggest newsletters
                if not sources:
                    snippets = "\n".join([f"- {r['title']}: {r['snippet']}" for r in results[:5]])
                    
                    extract_prompt = f"""From these search results, extract newsletter information for "{topic}".

{snippets}

Respond with JSON array only (max 2 newsletters):
[
    {{"name": "Newsletter Name", "url": "newsletter website", "description": "brief description"}}
]"""
                    
                    response = await ollama_client.generate(
                        prompt=extract_prompt,
                        system="Extract newsletter information. Respond only with JSON array.",
                        model=settings.ollama_fast_model,
                        temperature=0.1,
                        timeout=15.0
                    )
                    
                    text = response.get("response", "")
                    json_start = text.find("[")
                    json_end = text.rfind("]") + 1
                    
                    if json_start >= 0 and json_end > json_start:
                        newsletters = json.loads(text[json_start:json_end])
                        for n in newsletters[:2]:
                            sources.append(DiscoveredSource(
                                source_type=SourceType.NEWSLETTER,
                                name=n.get("name", "Newsletter"),
                                url=n.get("url"),
                                description=n.get("description", f"Newsletter about {topic}"),
                                confidence=0.6,
                                metadata={"topic": topic, "source": "newsletter_discovery"}
                            ))
                            
        except Exception as e:
            print(f"[SOURCE_DISCOVERY] Newsletter discovery error: {e}")
        
        return sources
    
    async def _validate_sources(
        self, 
        sources: List[DiscoveredSource]
    ) -> List[DiscoveredSource]:
        """Validate that discovered sources are accessible - with fast timeout"""
        
        # Skip validation entirely for now - it's slow and not critical
        # Just mark all sources as unvalidated but still return them
        print(f"[SOURCE_DISCOVERY] Skipping URL validation for {len(sources)} sources (fast path)")
        for source in sources:
            if not source.url:
                source.validated = True  # Keywords don't need URL validation
            else:
                source.validated = False  # Will validate on first use
                source.validation_error = None
        return sources
        
        # NOTE: Full validation below is disabled for speed
        # Re-enable if needed by uncommenting
        """
        session = await self._get_session()
        
        async def validate_one(source: DiscoveredSource) -> DiscoveredSource:
            if not source.url:
                source.validated = True  # Keywords don't need URL validation
                return source
            
            try:
                async with session.head(source.url, allow_redirects=True) as resp:
                    source.validated = resp.status < 400
                    if not source.validated:
                        source.validation_error = f"HTTP {resp.status}"
            except asyncio.TimeoutError:
                source.validation_error = "Timeout"
                source.validated = False
            except Exception as e:
                source.validation_error = str(e)[:100]
                source.validated = False
            
            return source
        
        # Validate in parallel (max 10 concurrent)
        semaphore = asyncio.Semaphore(10)
        
        async def validate_with_semaphore(source):
            async with semaphore:
                return await validate_one(source)
        
        validated = await asyncio.gather(
            *[validate_with_semaphore(s) for s in sources],
            return_exceptions=True
        )
        
        # Filter out exceptions and return validated sources
        return [s for s in validated if isinstance(s, DiscoveredSource)]
        """
    
    async def enrich_with_ticker_lookup(
        self, 
        company_name: str
    ) -> Optional[str]:
        """Look up stock ticker for a company name"""
        # Use LLM to get ticker (more reliable than API for common companies)
        prompt = f"""What is the stock ticker symbol for {company_name}?
If it's a public company, respond with just the ticker (e.g., AAPL).
If not public or unknown, respond with NULL."""

        try:
            response = await ollama_client.generate(
                prompt=prompt,
                model=settings.ollama_fast_model,
                temperature=0.1
            )
            
            ticker = response.get("response", "").strip().upper()
            if ticker and ticker != "NULL" and len(ticker) <= 5:
                return ticker
        except Exception as e:
            logger.error(f"Ticker lookup failed: {e}")
        
        return None


# Singleton instance
source_discovery = SourceDiscoveryService()
