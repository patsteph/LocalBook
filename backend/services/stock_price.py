"""
Stock Price Service - Fetch real-time stock quotes

Uses Yahoo Finance v8 API (no API key required).
Lightweight async implementation for the Collector Profile.
"""
import asyncio
import httpx
import logging
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Cache prices for 5 minutes to avoid hammering the API
_price_cache: Dict[str, Dict[str, Any]] = {}
CACHE_TTL_SECONDS = 300


@dataclass
class StockQuote:
    ticker: str
    price: float = 0.0
    change: float = 0.0
    change_percent: float = 0.0
    currency: str = "USD"
    market_state: str = "CLOSED"  # PRE, REGULAR, POST, CLOSED
    previous_close: float = 0.0
    open_price: float = 0.0
    day_high: float = 0.0
    day_low: float = 0.0
    volume: int = 0
    market_cap: Optional[str] = None
    fifty_two_week_high: float = 0.0
    fifty_two_week_low: float = 0.0
    name: str = ""
    exchange: str = ""
    fetched_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker,
            "price": self.price,
            "change": self.change,
            "change_percent": self.change_percent,
            "currency": self.currency,
            "market_state": self.market_state,
            "previous_close": self.previous_close,
            "open": self.open_price,
            "day_high": self.day_high,
            "day_low": self.day_low,
            "volume": self.volume,
            "market_cap": self.market_cap,
            "fifty_two_week_high": self.fifty_two_week_high,
            "fifty_two_week_low": self.fifty_two_week_low,
            "name": self.name,
            "exchange": self.exchange,
        }


def _get_us_market_state() -> str:
    """Determine US stock market state based on current ET time.
    More reliable than Yahoo's marketState field which can be stale."""
    try:
        import zoneinfo
        et = datetime.now(zoneinfo.ZoneInfo("America/New_York"))
    except Exception:
        # Fallback: assume UTC-5
        from datetime import timedelta
        et = datetime.now(timezone(timedelta(hours=-5)))

    weekday = et.weekday()  # 0=Mon, 6=Sun
    if weekday >= 5:
        return "CLOSED"

    hour, minute = et.hour, et.minute
    time_mins = hour * 60 + minute

    if time_mins < 4 * 60:        # Before 4:00 AM ET
        return "CLOSED"
    elif time_mins < 9 * 60 + 30:  # 4:00 AM – 9:29 AM ET
        return "PRE"
    elif time_mins < 16 * 60:      # 9:30 AM – 3:59 PM ET
        return "REGULAR"
    elif time_mins < 20 * 60:      # 4:00 PM – 7:59 PM ET
        return "POST"
    else:
        return "CLOSED"


def _format_market_cap(value: Optional[float]) -> Optional[str]:
    """Format market cap to human-readable string"""
    if not value:
        return None
    if value >= 1e12:
        return f"${value / 1e12:.2f}T"
    elif value >= 1e9:
        return f"${value / 1e9:.2f}B"
    elif value >= 1e6:
        return f"${value / 1e6:.2f}M"
    return f"${value:,.0f}"


# Common company name -> actual stock symbol for LLM-generated tickers
_TICKER_FIXES: Dict[str, str] = {
    "COSTCO": "COST", "WALMART": "WMT", "ALPHABET": "GOOGL", "GOOGLE": "GOOGL",
    "FACEBOOK": "META", "AMAZON": "AMZN", "APPLE": "AAPL", "MICROSOFT": "MSFT",
    "NVIDIA": "NVDA", "TESLA": "TSLA", "NETFLIX": "NFLX", "DISNEY": "DIS",
    "NIKE": "NKE", "STARBUCKS": "SBUX", "MCDONALDS": "MCD", "PEPSI": "PEP",
    "PEPSICO": "PEP", "COCA-COLA": "KO", "BOEING": "BA", "FORD": "F",
    "INTEL": "INTC", "CISCO": "CSCO", "ORACLE": "ORCL", "SALESFORCE": "CRM",
    "ADOBE": "ADBE", "IBM": "IBM", "AMD": "AMD", "JPMORGAN": "JPM",
    "BERKSHIRE": "BRK-B", "EXXON": "XOM", "CHEVRON": "CVX", "SHELL": "SHEL",
    "TARGET": "TGT", "KROGER": "KR", "PFIZER": "PFE", "MODERNA": "MRNA",
    "GOLDMAN": "GS", "MORGAN": "MS", "AIRBUS": "EADSY", "TOYOTA": "TM",
    "HONDA": "HMC", "GENERAL MOTORS": "GM", "WELLS FARGO": "WFC",
}


def _resolve_ticker(ticker: str) -> Optional[str]:
    """Resolve a company name to a stock symbol using local lookup."""
    upper = ticker.upper().strip()
    # Direct match
    if upper in _TICKER_FIXES:
        resolved = _TICKER_FIXES[upper]
        logger.info(f"Resolved ticker '{ticker}' -> '{resolved}' via local map")
        return resolved
    # Try without common suffixes
    for suffix in (" INC", " CORP", " CO", " LTD", " LLC", " WHOLESALE", " CORPORATION"):
        stripped = upper.replace(suffix, "").strip()
        if stripped in _TICKER_FIXES:
            resolved = _TICKER_FIXES[stripped]
            logger.info(f"Resolved ticker '{ticker}' -> '{resolved}' via local map (stripped suffix)")
            return resolved
    return None


# Cache resolved tickers so we don't keep searching
_ticker_map: Dict[str, Optional[str]] = {}


async def get_stock_quote(ticker: str) -> Optional[StockQuote]:
    """
    Fetch a real-time stock quote for a ticker symbol.
    Uses Yahoo Finance v8 quote API.
    Returns None if the ticker is invalid or the request fails.
    """
    if not ticker:
        return None

    ticker = ticker.upper().strip()

    # Check if we've previously resolved this ticker to a different symbol
    if ticker in _ticker_map:
        resolved = _ticker_map[ticker]
        if resolved is None:
            return None  # Previously failed to resolve
        ticker = resolved

    # Check cache
    cached = _price_cache.get(ticker)
    if cached and (time.time() - cached.get("fetched_at", 0)) < CACHE_TTL_SECONDS:
        return cached.get("quote")

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    params = {
        "range": "1d",
        "interval": "1m",
        "includePrePost": "true",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) LocalBook/1.0"
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params=params, headers=headers)
            if resp.status_code != 200:
                logger.warning(f"Yahoo Finance returned {resp.status_code} for {ticker}")
                # Try to resolve the ticker if it looks like a bad symbol
                original_ticker = ticker
                resolved = _resolve_ticker(ticker)
                if resolved and resolved != ticker:
                    _ticker_map[original_ticker] = resolved
                    ticker = resolved
                    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
                    resp = await client.get(url, params=params, headers=headers)
                    if resp.status_code != 200:
                        _ticker_map[original_ticker] = None
                        return None
                else:
                    _ticker_map[original_ticker] = None
                    return None

            data = resp.json()
            result = data.get("chart", {}).get("result", [])
            if not result:
                return None

            meta = result[0].get("meta", {})
            indicators = result[0].get("indicators", {}).get("quote", [{}])
            quote_data = indicators[0] if indicators else {}

            current_price = meta.get("regularMarketPrice", 0)
            previous_close = meta.get("previousClose", meta.get("chartPreviousClose", 0))
            change = current_price - previous_close if previous_close else 0
            change_pct = (change / previous_close * 100) if previous_close else 0

            # Get high/low from today's data
            highs = [h for h in (quote_data.get("high") or []) if h is not None]
            lows = [l for l in (quote_data.get("low") or []) if l is not None]
            volumes = [v for v in (quote_data.get("volume") or []) if v is not None]

            quote = StockQuote(
                ticker=ticker,
                price=round(current_price, 2),
                change=round(change, 2),
                change_percent=round(change_pct, 2),
                currency=meta.get("currency", "USD"),
                market_state=_get_us_market_state(),
                previous_close=round(previous_close, 2),
                open_price=round(meta.get("regularMarketOpen", 0) or 0, 2) if meta.get("regularMarketOpen") else 0,
                day_high=round(max(highs), 2) if highs else 0,
                day_low=round(min(lows), 2) if lows else 0,
                volume=sum(volumes) if volumes else 0,
                fifty_two_week_high=round(meta.get("fiftyTwoWeekHigh", 0) or 0, 2),
                fifty_two_week_low=round(meta.get("fiftyTwoWeekLow", 0) or 0, 2),
                name=meta.get("shortName", meta.get("longName", ticker)),
                exchange=meta.get("exchangeName", ""),
            )

            # Cache it
            _price_cache[ticker] = {"quote": quote, "fetched_at": time.time()}
            logger.info(f"Stock quote for {ticker}: ${quote.price} ({quote.change_percent:+.2f}%)")
            return quote

    except httpx.TimeoutException:
        logger.warning(f"Timeout fetching stock quote for {ticker}")
    except Exception as e:
        logger.error(f"Error fetching stock quote for {ticker}: {e}")

    return None


async def get_multiple_quotes(tickers: list[str]) -> Dict[str, Optional[StockQuote]]:
    """Fetch quotes for multiple tickers concurrently"""
    tasks = [get_stock_quote(t) for t in tickers]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return {
        ticker: (result if isinstance(result, StockQuote) else None)
        for ticker, result in zip(tickers, results)
    }
