"""Content Date Extractor — determines WHEN a document's content is from.

When a user bulk-uploads years of performance reviews, coaching feedback,
or other dated materials, ingestion timestamps are meaningless for showing
progression. This module extracts the actual content date from:

1. Filename patterns (FY23, 2024-Q1, H2_2025, Review_2023, etc.)
2. Early content text (date headers, fiscal year references, date ranges)

Returns an ISO date string representing the approximate content date,
or None if no date can be inferred.
"""

import re
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# ============================================================================
# Fiscal year / quarter / half-year → approximate ISO date mapping
# ============================================================================

# FY end dates: FY23 → fiscal year ending ~June 2023
# We place the date at the END of the period so sorting shows progression
_CURRENT_YEAR = datetime.utcnow().year

def _fy_to_date(fy_year: int) -> str:
    """Convert fiscal year number to approximate end-of-FY date."""
    # Normalize 2-digit to 4-digit year
    if fy_year < 100:
        fy_year = 2000 + fy_year if fy_year < 50 else 1900 + fy_year
    return f"{fy_year}-06-30"


def _quarter_to_date(year: int, quarter: int) -> str:
    """Convert year + quarter to end-of-quarter date."""
    if year < 100:
        year = 2000 + year if year < 50 else 1900 + year
    end_months = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}
    return f"{year}-{end_months.get(quarter, '06-30')}"


def _half_to_date(year: int, half: int) -> str:
    """Convert year + half to end-of-half date."""
    if year < 100:
        year = 2000 + year if year < 50 else 1900 + year
    return f"{year}-06-30" if half == 1 else f"{year}-12-31"


def _month_name_to_num(name: str) -> int:
    """Convert month name/abbreviation to number."""
    months = {
        'jan': 1, 'january': 1, 'feb': 2, 'february': 2,
        'mar': 3, 'march': 3, 'apr': 4, 'april': 4,
        'may': 5, 'jun': 6, 'june': 6,
        'jul': 7, 'july': 7, 'aug': 8, 'august': 8,
        'sep': 9, 'sept': 9, 'september': 9,
        'oct': 10, 'october': 10, 'nov': 11, 'november': 11,
        'dec': 12, 'december': 12,
    }
    return months.get(name.lower(), 0)


# ============================================================================
# Filename date extraction
# ============================================================================

# Ordered by specificity — most specific patterns first
_FILENAME_PATTERNS = [
    # Q4 FY2023, Q1 FY24, Q2-FY2025, Q3_FY25 (quarter + fiscal year combined)
    (r'[Qq]([1-4])[_\-\s]*(?:FY|fy)[_\-\s]?(\d{2,4})', 'quarter_fy'),
    # FY23, FY2023, FY-23, FY_2023
    (r'(?:FY|fy)[_\-\s]?(\d{2,4})', 'fy'),
    # Q1-2024, Q1_2024, Q1 2024, 2024-Q1, 2024_Q1
    (r'[Qq]([1-4])[_\-\s]?(\d{2,4})', 'quarter_qy'),
    (r'(\d{2,4})[_\-\s]?[Qq]([1-4])', 'quarter_yq'),
    # H1-2025, H2_2025, H1 2025, 2025-H1, 2025_H2
    (r'[Hh]([12])[_\-\s]?(\d{2,4})', 'half_hy'),
    (r'(\d{2,4})[_\-\s]?[Hh]([12])', 'half_yh'),
    # Month Year: January_2024, Jan-2024, jan2024
    (r'((?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*)[_\-\s]?(\d{2,4})', 'month_year'),
    # Year-Month: 2024-January, 2024_Jan
    (r'(\d{4})[_\-\s]?((?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*)', 'year_month'),
    # ISO-ish: 2024-01-15, 20240115
    (r'(\d{4})[_\-](\d{2})[_\-](\d{2})', 'iso_date'),
    (r'(\d{4})(\d{2})(\d{2})', 'iso_compact'),
    # US date: 01-15-2024, 01/15/2024
    (r'(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})', 'us_date'),
    # Plain year: _2023_, -2024-, (2023), Review2024
    (r'(?:^|[_\-\s(])(\d{4})(?:[_\-\s).]|$)', 'plain_year'),
]


def _extract_from_filename(filename: str) -> Optional[str]:
    """Extract content date from filename patterns."""
    # Strip path and extension
    name = filename.rsplit('/', 1)[-1]  # Remove path
    name_no_ext = name.rsplit('.', 1)[0] if '.' in name else name

    for pattern, ptype in _FILENAME_PATTERNS:
        m = re.search(pattern, name_no_ext, re.IGNORECASE)
        if not m:
            continue

        try:
            if ptype == 'quarter_fy':
                return _quarter_to_date(int(m.group(2)), int(m.group(1)))
            elif ptype == 'fy':
                return _fy_to_date(int(m.group(1)))
            elif ptype == 'quarter_qy':
                return _quarter_to_date(int(m.group(2)), int(m.group(1)))
            elif ptype == 'quarter_yq':
                return _quarter_to_date(int(m.group(1)), int(m.group(2)))
            elif ptype == 'half_hy':
                return _half_to_date(int(m.group(2)), int(m.group(1)))
            elif ptype == 'half_yh':
                return _half_to_date(int(m.group(1)), int(m.group(2)))
            elif ptype == 'month_year':
                month = _month_name_to_num(m.group(1))
                year = int(m.group(2))
                if year < 100:
                    year = 2000 + year if year < 50 else 1900 + year
                if month:
                    return f"{year}-{month:02d}-28"
            elif ptype == 'year_month':
                year = int(m.group(1))
                month = _month_name_to_num(m.group(2))
                if month:
                    return f"{year}-{month:02d}-28"
            elif ptype in ('iso_date', 'iso_compact'):
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if 1990 <= y <= _CURRENT_YEAR + 2 and 1 <= mo <= 12 and 1 <= d <= 31:
                    return f"{y}-{mo:02d}-{d:02d}"
            elif ptype == 'us_date':
                mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if 1990 <= y <= _CURRENT_YEAR + 2 and 1 <= mo <= 12 and 1 <= d <= 31:
                    return f"{y}-{mo:02d}-{d:02d}"
            elif ptype == 'plain_year':
                y = int(m.group(1))
                if 1990 <= y <= _CURRENT_YEAR + 2:
                    return f"{y}-06-30"
        except (ValueError, IndexError):
            continue

    return None


# ============================================================================
# Content text date extraction (first ~500 chars)
# ============================================================================

_CONTENT_PATTERNS = [
    # "Fiscal Year 2023", "fiscal year 2024"
    (r'(?:fiscal\s+year|FY)\s*[:\-]?\s*(\d{2,4})', 'fy'),
    # "Period: Q1 2024", "Quarter 2, 2024"
    (r'(?:quarter|Q)\s*[:\-]?\s*([1-4])\s*[,\-\s]+(\d{2,4})', 'quarter'),
    (r'(\d{2,4})\s*[,\-\s]+(?:quarter|Q)\s*[:\-]?\s*([1-4])', 'quarter_rev'),
    # "H1 2025", "First Half 2025", "Second Half 2024"
    (r'(?:first\s+half|H1)\s*[:\-]?\s*(\d{2,4})', 'h1'),
    (r'(?:second\s+half|H2)\s*[:\-]?\s*(\d{2,4})', 'h2'),
    # "Date: January 15, 2024", "Date: 01/15/2024"
    (r'(?:date|dated|as\s+of|prepared|effective|review\s+date|period\s+ending)[:\s]+(\w+\s+\d{1,2},?\s+\d{4})', 'date_header'),
    (r'(?:date|dated|as\s+of|prepared|effective|review\s+date|period\s+ending)[:\s]+(\d{1,2}[/\-]\d{1,2}[/\-]\d{4})', 'date_header_numeric'),
    # "Performance Review 2023-2024", "Annual Review 2024"
    (r'(?:performance|annual|mid.?year|year.?end|semi.?annual)\s+(?:review|evaluation|assessment|appraisal)\s+(?:for\s+)?(\d{4})', 'review_year'),
    # "January 2024 - June 2024" (date range — use end date)
    (r'((?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*)\s+(\d{4})\s*[\-–—to]+\s*((?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*)\s+(\d{4})', 'date_range'),
    # "2023-2024" year range (use end year)
    (r'(\d{4})\s*[\-–—]\s*(\d{4})', 'year_range'),
    # Standalone "Month Year" near the top
    (r'^.{0,50}((?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*)\s+(\d{4})', 'early_month_year'),
]


def _extract_from_content(text: str) -> Optional[str]:
    """Extract content date from early text of a document."""
    # Only look at the first 800 characters
    snippet = text[:800].strip()
    if not snippet:
        return None

    for pattern, ptype in _CONTENT_PATTERNS:
        m = re.search(pattern, snippet, re.IGNORECASE)
        if not m:
            continue

        try:
            if ptype == 'fy':
                return _fy_to_date(int(m.group(1)))
            elif ptype == 'quarter':
                return _quarter_to_date(int(m.group(2)), int(m.group(1)))
            elif ptype == 'quarter_rev':
                return _quarter_to_date(int(m.group(1)), int(m.group(2)))
            elif ptype == 'h1':
                return _half_to_date(int(m.group(1)), 1)
            elif ptype == 'h2':
                return _half_to_date(int(m.group(1)), 2)
            elif ptype == 'date_header':
                try:
                    from dateutil.parser import parse as dateparse
                    dt = dateparse(m.group(1))
                    return dt.strftime("%Y-%m-%d")
                except Exception:
                    pass
            elif ptype == 'date_header_numeric':
                parts = re.split(r'[/\-]', m.group(1))
                if len(parts) == 3:
                    mo, d, y = int(parts[0]), int(parts[1]), int(parts[2])
                    if 1990 <= y <= _CURRENT_YEAR + 2:
                        return f"{y}-{mo:02d}-{d:02d}"
            elif ptype == 'review_year':
                y = int(m.group(1))
                if 1990 <= y <= _CURRENT_YEAR + 2:
                    return f"{y}-12-31"
            elif ptype == 'date_range':
                # Use the END date of the range
                end_month = _month_name_to_num(m.group(3))
                end_year = int(m.group(4))
                if end_month and 1990 <= end_year <= _CURRENT_YEAR + 2:
                    return f"{end_year}-{end_month:02d}-28"
            elif ptype == 'year_range':
                end_year = int(m.group(2))
                if 1990 <= end_year <= _CURRENT_YEAR + 2:
                    return f"{end_year}-06-30"
            elif ptype == 'early_month_year':
                month = _month_name_to_num(m.group(1))
                year = int(m.group(2))
                if month and 1990 <= year <= _CURRENT_YEAR + 2:
                    return f"{year}-{month:02d}-28"
        except (ValueError, IndexError):
            continue

    return None


# ============================================================================
# Public API
# ============================================================================

def extract_content_date(filename: str, text: str = "") -> Optional[str]:
    """Extract the content date from a document's filename and/or text.
    
    Tries filename first (most reliable), then falls back to content scan.
    Returns an ISO date string (YYYY-MM-DD) or None.
    
    Args:
        filename: The document filename (e.g., "FY23_Review_Chris.pdf")
        text: Optional extracted text content for content-based detection
    
    Returns:
        ISO date string or None if no date can be determined
    """
    # Try filename first — it's the most intentional signal
    date = _extract_from_filename(filename)
    if date:
        logger.info(f"[ContentDate] Extracted '{date}' from filename: {filename}")
        return date

    # Fall back to content scan
    if text:
        date = _extract_from_content(text)
        if date:
            logger.info(f"[ContentDate] Extracted '{date}' from content of: {filename}")
            return date

    return None
