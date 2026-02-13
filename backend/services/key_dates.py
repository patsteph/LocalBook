"""
Key Dates Service - Discover upcoming important dates for a research subject

Uses LLM knowledge + SEC EDGAR API for earnings dates.
Provides quarterly earnings, annual meetings, product launches, etc.
"""
import asyncio
import httpx
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field, asdict

from services.ollama_client import ollama_client
from config import settings

logger = logging.getLogger(__name__)

# Cache key dates for 24 hours (they don't change often)
_dates_cache: Dict[str, Dict[str, Any]] = {}
CACHE_TTL_SECONDS = 86400


@dataclass
class KeyDate:
    date: str  # ISO date string or "TBD"
    event: str  # e.g., "Q1 2026 Earnings Report"
    category: str  # earnings, meeting, regulatory, product, conference
    importance: str  # high, medium, low
    source: str = "estimated"  # sec, estimated, llm

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


async def _fetch_sec_filings(cik: str) -> List[KeyDate]:
    """
    Fetch recent SEC filing dates from EDGAR API.
    CIK must be zero-padded to 10 digits.
    """
    if not cik:
        return []

    padded_cik = cik.zfill(10)
    url = f"https://data.sec.gov/submissions/CIK{padded_cik}.json"
    headers = {
        "User-Agent": "LocalBook Research Tool admin@localbook.app",
        "Accept": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                logger.warning(f"SEC EDGAR returned {resp.status_code} for CIK {cik}")
                return []

            data = resp.json()
            recent = data.get("filings", {}).get("recent", {})
            forms = recent.get("form", [])
            dates = recent.get("filingDate", [])
            descriptions = recent.get("primaryDocDescription", [])

            key_dates = []
            seen_quarters = set()

            for i, form_type in enumerate(forms[:50]):
                if form_type in ("10-Q", "10-K", "8-K"):
                    filing_date = dates[i] if i < len(dates) else None
                    desc = descriptions[i] if i < len(descriptions) else form_type

                    if not filing_date:
                        continue

                    # Deduplicate by quarter
                    quarter_key = f"{form_type}-{filing_date[:7]}"
                    if quarter_key in seen_quarters:
                        continue
                    seen_quarters.add(quarter_key)

                    category = "earnings" if form_type in ("10-Q", "10-K") else "regulatory"
                    importance = "high" if form_type == "10-K" else "medium"

                    key_dates.append(KeyDate(
                        date=filing_date,
                        event=f"{form_type} Filing: {desc}" if desc else f"{form_type} Filing",
                        category=category,
                        importance=importance,
                        source="sec",
                    ))

            return key_dates[:10]

    except Exception as e:
        logger.error(f"SEC EDGAR fetch failed for CIK {cik}: {e}")
        return []


async def _estimate_key_dates_llm(
    company_name: str,
    ticker: Optional[str] = None,
    industry: Optional[str] = None,
) -> List[KeyDate]:
    """
    Use LLM to estimate upcoming key dates for a subject.
    Context-aware: uses corporate prompts for companies, research prompts for topics.
    """
    now = datetime.utcnow()
    current_date = now.strftime("%B %d, %Y")

    # Detect if this is a company vs a research topic/skill
    is_company = bool(ticker)
    if not is_company:
        # Heuristic: common topic/research indicators
        name_lower = company_name.lower()
        topic_signals = [
            "research", "ai", "ml", "learning", "science", "engineering",
            "development", "programming", "design", "leadership", "management",
            "health", "finance", "crypto", "blockchain", "security", "data",
            "cloud", "devops", "marketing", "strategy", "innovation",
        ]
        if any(signal in name_lower for signal in topic_signals):
            is_company = False
        elif industry and not ticker:
            is_company = False  # Has industry context but no ticker → topic/industry monitoring

    if is_company:
        ticker_str = f" (ticker: {ticker})" if ticker else ""
        industry_str = f" in the {industry} industry" if industry else ""
        prompt = f"""Today is {current_date}. List 4-6 upcoming key dates ONLY for {company_name}{ticker_str}{industry_str}.

CRITICAL RULES:
- ONLY include events for {company_name} itself. Do NOT include events for competitors, parent companies, subsidiaries, or other companies.
- {company_name} is an independent company. Do NOT assume any parent-subsidiary relationships unless you are 100% certain.
- Each quarterly earnings report should appear EXACTLY ONCE. Do not list the same quarter twice.
- Only include FUTURE dates (after {current_date}).
- If you are unsure of an exact date, use "TBD" instead of guessing. Accuracy matters more than completeness.

Include where confident:
- Quarterly earnings report dates (based on the company's typical historical schedule)
- Annual shareholder meeting
- Major industry conferences the company typically attends

Respond with JSON array only:
[
  {{"date": "YYYY-MM-DD", "event": "description", "category": "earnings|meeting|conference|product|regulatory", "importance": "high|medium|low"}}
]

Respond with the JSON array only, no other text."""
    else:
        industry_str = f" in {industry}" if industry else ""
        prompt = f"""Today is {current_date}. List 4-6 upcoming key dates relevant to someone researching "{company_name}"{industry_str}.

CRITICAL RULES:
- "{company_name}" is a RESEARCH TOPIC, NOT a company. Do NOT generate earnings reports, shareholder meetings, or corporate events.
- Only include FUTURE dates (after {current_date}).
- If you are unsure of an exact date, use "TBD" instead of guessing.
- Focus on events that would matter to a researcher or practitioner in this field.

Include where confident:
- Major conferences and summits (e.g., NeurIPS, ICML, Google I/O, AWS re:Invent — whatever is relevant to this topic)
- Paper submission deadlines for top venues
- Notable product launches, version releases, or announcements expected
- Community events, hackathons, or workshops

Respond with JSON array only:
[
  {{"date": "YYYY-MM-DD", "event": "description", "category": "conference|deadline|release|community|research", "importance": "high|medium|low"}}
]

Respond with the JSON array only, no other text."""

    try:
        response = await ollama_client.generate(
            prompt=prompt,
            model=settings.ollama_fast_model,
            temperature=0.2,
        )

        text = response.get("response", "")
        json_start = text.find("[")
        json_end = text.rfind("]") + 1

        if json_start >= 0 and json_end > json_start:
            items = json.loads(text[json_start:json_end])
            dates = _validate_llm_dates(items, company_name, now)
            return dates

    except Exception as e:
        logger.error(f"LLM key dates estimation failed: {e}")

    return []


def _validate_llm_dates(items: list, company_name: str, now: datetime) -> List[KeyDate]:
    """Post-process and validate LLM-generated dates to catch hallucinations."""
    dates: List[KeyDate] = []
    seen_events: set = set()
    company_lower = company_name.lower()

    # Known competitor pairs — reject events mentioning the wrong company
    COMPETITOR_PAIRS = {
        "pepsi": ["coca-cola", "coke", "coca cola"],
        "pepsico": ["coca-cola", "coke", "coca cola"],
        "coca-cola": ["pepsi", "pepsico"],
        "coke": ["pepsi", "pepsico"],
        "google": ["microsoft", "apple"],
        "microsoft": ["google", "apple"],
        "apple": ["google", "microsoft"],
        "ford": ["gm", "general motors"],
        "general motors": ["ford"],
        "boeing": ["airbus"],
        "airbus": ["boeing"],
    }

    competitors = set()
    for key, rivals in COMPETITOR_PAIRS.items():
        if key in company_lower:
            competitors.update(rivals)

    for item in items[:8]:
        try:
            event_text = item.get("event", "")
            date_str = item.get("date", "TBD")

            # Reject events mentioning competitors
            event_lower = event_text.lower()
            if any(comp in event_lower for comp in competitors):
                logger.warning(f"[KeyDates] Rejected competitor event: {event_text}")
                continue

            # Reject events with wrong parent/subsidiary claims
            if "parent company" in event_lower or "subsidiary" in event_lower:
                logger.warning(f"[KeyDates] Rejected relationship claim: {event_text}")
                continue

            # Validate date format
            if date_str != "TBD":
                try:
                    parsed = datetime.strptime(date_str, "%Y-%m-%d")
                    # Reject dates in the past
                    if parsed < now - timedelta(days=1):
                        logger.warning(f"[KeyDates] Rejected past date: {date_str} for {event_text}")
                        continue
                    # Reject dates more than 18 months out (likely hallucinated)
                    if parsed > now + timedelta(days=548):
                        logger.warning(f"[KeyDates] Rejected far-future date: {date_str}")
                        continue
                except ValueError:
                    date_str = "TBD"

            # Deduplicate: normalize event to catch "Q1 Earnings" appearing twice
            dedup_key = _event_dedup_key(event_text, item.get("category", ""))
            if dedup_key in seen_events:
                logger.warning(f"[KeyDates] Rejected duplicate event: {event_text}")
                continue
            seen_events.add(dedup_key)

            dates.append(KeyDate(
                date=date_str,
                event=event_text,
                category=item.get("category", "other"),
                importance=item.get("importance", "medium"),
                source="estimated",
            ))
        except Exception:
            continue

    return dates


def _event_dedup_key(event: str, category: str) -> str:
    """Generate a dedup key to catch duplicate events like 'Q1 Earnings' listed twice."""
    e = event.lower()
    # Normalize quarterly earnings: extract quarter identifier
    q_match = re.search(r'q([1-4])', e)
    if q_match and category == "earnings":
        return f"earnings-q{q_match.group(1)}"
    # For annual events, just use category
    if "annual" in e or "shareholder" in e:
        return f"annual-{category}"
    # For others, use first 40 chars normalized
    return re.sub(r'[^a-z0-9]', '', e[:40])


async def get_key_dates(
    company_name: str,
    ticker: Optional[str] = None,
    cik: Optional[str] = None,
    industry: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Get key upcoming dates for a company.
    Combines SEC filing data with LLM estimates.
    Returns sorted list of upcoming dates.
    """
    cache_key = f"{company_name}:{ticker or ''}:{cik or ''}"
    cached = _dates_cache.get(cache_key)
    if cached and (datetime.utcnow().timestamp() - cached.get("fetched_at", 0)) < CACHE_TTL_SECONDS:
        return cached.get("dates", [])

    # Fetch SEC filings and LLM estimates in parallel
    llm_task = _estimate_key_dates_llm(company_name, ticker, industry)

    if cik:
        sec_dates, llm_dates = await asyncio.gather(
            _fetch_sec_filings(cik),
            llm_task,
            return_exceptions=True,
        )
    else:
        sec_dates = []
        llm_dates = await llm_task

    # Handle exceptions
    if isinstance(sec_dates, Exception):
        logger.error(f"SEC dates failed: {sec_dates}")
        sec_dates = []
    if isinstance(llm_dates, Exception):
        logger.error(f"LLM dates failed: {llm_dates}")
        llm_dates = []

    # Merge and deduplicate (prefer SEC data over LLM estimates)
    all_dates: List[KeyDate] = []
    sec_events = set()

    for d in sec_dates:
        all_dates.append(d)
        sec_events.add(d.event[:30].lower())

    for d in llm_dates:
        # Don't duplicate SEC-sourced events
        if not any(sec_ev in d.event[:30].lower() for sec_ev in sec_events):
            all_dates.append(d)

    # Sort by date (TBD dates go last)
    now_str = datetime.utcnow().strftime("%Y-%m-%d")

    def sort_key(d: KeyDate):
        if d.date == "TBD":
            return "9999-12-31"
        return d.date

    all_dates.sort(key=sort_key)

    # Filter to only future dates (keep past SEC filings for context, last 2)
    future_dates = [d for d in all_dates if d.date >= now_str or d.date == "TBD"]
    past_dates = [d for d in all_dates if d.date < now_str and d.date != "TBD"][-2:]

    result = [d.to_dict() for d in (past_dates + future_dates)[:12]]

    # Cache
    _dates_cache[cache_key] = {
        "dates": result,
        "fetched_at": datetime.utcnow().timestamp(),
    }

    return result
