"""article_extractor — split newsletter bodies into individual articles.

Phase 1 of Tier 2 (2026-06-09). See `READFIRST/CORRESPONDENT_TIER2_DESIGN.md`
for the spec.

Layered heuristics, applied in order; first one that produces ≥2 articles wins.
Lossless single-article fallback if everything else returns 1.

Caller pattern:
    articles = extract_articles(parsed.html_body, parsed.text_body, fallback_title=parsed.subject)
    for article in articles:
        await article_store.create(source_id=..., notebook_id=..., **article._asdict())

No LLM hops. Pure structural parsing — runs in milliseconds.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ExtractedArticle:
    """Result of splitting a newsletter body. Position is 0-based.

    `body_text_offset` (P1C.2, 2026-06-10) — character offset of this
    article's body within the parent newsletter's flattened text body.
    Used by SourceNotesViewer to scroll exactly to the article boundary
    when a user clicks an article card in chat. -1 means "unknown" (the
    splitter couldn't determine — fall back to proportional scroll)."""
    position: int
    title: str
    body_text: str
    body_html: Optional[str] = None
    body_text_offset: int = -1


# ─────────────────────────────────────────────────────────────────────────────
# HTML-based heuristics (preferred when html_body is present — more reliable
# than text-only because newsletter templates have structural markup).
# ─────────────────────────────────────────────────────────────────────────────

# Maximum reasonable number of articles. Beyond this we're probably
# mis-parsing a navigation list as articles.
_MAX_ARTICLES = 20
# Below this character count, we treat a "section" as too small to be an
# article (probably a divider line, footer text, etc).
_MIN_ARTICLE_CHARS = 120


def _strip_html_tags(html: str) -> str:
    """Convert HTML to plain text. Quick + dirty; keeps line breaks for
    paragraphs, drops everything else. Falls back to html_to_clean_text
    when BeautifulSoup is available."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        # Drop script/style entirely — never content
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        # Convert <br> and block elements to newlines
        for br in soup.find_all("br"):
            br.replace_with("\n")
        text = soup.get_text(separator="\n")
        # Collapse runs of blank lines
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
    except Exception:
        # Fallback: regex strip. Loses some structure but works.
        text = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()


# Q1 (2026-06-10) — common newsletter template strings that often
# appear at the top of an article segment and were getting picked as
# titles. Lowercase for case-insensitive membership check.
_TITLE_BLACKLIST = frozenset(s.lower() for s in [
    "view in browser", "view online", "view in your browser",
    "open in browser", "read in browser", "view email",
    "view this email in your browser", "having trouble viewing",
    "click here", "click to view", "click here to read",
    "tap here", "tap to view",
    "sign up", "sign in", "log in", "subscribe", "unsubscribe",
    "manage subscription", "manage preferences", "preferences",
    "share this", "share with friends", "forward to a friend",
    "follow us", "follow us on", "follow on",
    "settings", "options",
    "email us", "contact us", "reply",
    "advertisement", "sponsored", "promoted",
    "read more", "continue reading", "learn more",
    "this email was sent to", "you received this email",
    "go to website", "visit website", "website",
])

_URL_PATTERN = re.compile(
    r"""^\s*(?:
        https?://\S+
        | www\.\S+
        | mailto:\S+
        | [a-z0-9][-a-z0-9.]*\.[a-z]{2,}(?:/\S*)?
    )\s*$""",
    re.IGNORECASE | re.VERBOSE,
)


def _looks_like_title(text: str) -> bool:
    """Q1 (2026-06-10) — gate for substantive title candidates.

    Rejects:
      - URLs / domain-only strings ('example.com', 'https://...')
      - Common newsletter template strings ('View Online', 'Sign Up')
      - Strings under 4 chars or under 40% alpha (mostly emoji/punctuation)
    """
    s = (text or "").strip()
    if not s or len(s) < 4 or len(s) > 300:
        return False
    if _URL_PATTERN.match(s):
        return False
    if s.lower() in _TITLE_BLACKLIST:
        return False
    # Reject single-word UI fragments like "Subscribe" even when not
    # exact match (e.g. "Subscribe now" → false)
    low = s.lower()
    for needle in ("view online", "view in browser", "click here",
                   "sign up", "subscribe", "unsubscribe",
                   "share this", "follow us", "manage preferences"):
        if low.startswith(needle):
            return False
    alpha = sum(1 for c in s if c.isalpha())
    if alpha / len(s) < 0.4:
        return False
    return True


def _extract_title_from_segment(text: str, html: Optional[str] = None) -> str:
    """Pull a best-effort title from the start of an article segment.

    Q1 (2026-06-10) — was returning URLs and template noise ('View
    Online', 'Sign Up') as titles for ~90% of articles. Now walks the
    first few candidates and picks the first that passes
    `_looks_like_title`. Falls back to first non-empty line only if
    everything fails.
    """
    if html:
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            # Walk every H1/H2/H3 — the first substantive one wins
            for heading in soup.find_all(["h1", "h2", "h3"]):
                t = heading.get_text(strip=True)
                if t and _looks_like_title(t):
                    return t[:200]
            # Also try title-ish elements with weight (a.title-link etc)
            for a in soup.find_all("a", limit=8):
                t = a.get_text(strip=True)
                if t and len(t) >= 8 and _looks_like_title(t):
                    return t[:200]
        except Exception:
            pass
    lines = [ln.strip() for ln in (text or "").split("\n") if ln.strip()]
    # Walk first ~15 lines looking for a substantive one
    for line in lines[:15]:
        if _looks_like_title(line):
            return line[:200]
    # Last resort: first non-empty line, even if poor quality. Better
    # to have SOMETHING than blank, and the calling code prefers the
    # newsletter subject when this returns "(untitled)".
    if lines:
        return lines[0][:200]
    return "(untitled)"


def _split_by_hr(html: str) -> List[str]:
    """Heuristic 1 — split on <hr/> rules. Simplest signal; many newsletter
    templates use HR between sections."""
    # Match <hr> with any attributes
    parts = re.split(r"<hr\b[^>]*/?>", html, flags=re.I)
    return [p for p in parts if p and len(_strip_html_tags(p)) >= _MIN_ARTICLE_CHARS]


def _split_by_headers(html: str) -> List[str]:
    """Heuristic 2 — split on H1/H2/H3 boundaries. Each header marks the
    start of an article."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        headers = soup.find_all(["h1", "h2", "h3"])
        if len(headers) < 2:
            return []

        segments: List[str] = []
        for i, header in enumerate(headers):
            # Collect the header + everything until the next header (or end).
            collected: List[str] = [str(header)]
            for sibling in header.find_next_siblings():
                if sibling.name in ("h1", "h2", "h3"):
                    break
                collected.append(str(sibling))
            segments.append("".join(collected))

        segments = [s for s in segments if len(_strip_html_tags(s)) >= _MIN_ARTICLE_CHARS]
        return segments
    except Exception as e:
        logger.debug(f"[article_extractor] header-split failed: {e}")
        return []


def _split_by_repeated_table_blocks(html: str) -> List[str]:
    """Heuristic 3 — Substack/Beehiiv use repeating <table> blocks per
    article. If we find ≥3 top-level <table> elements with similar
    structure (rough character-count proxy), treat each as an article."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        # Walk top-level tables only
        tables = [t for t in soup.find_all("table", recursive=True) if t.parent and t.parent.name == "body"]
        if len(tables) < 3:
            return []

        # Check sizes — if all tables are within 50% of each other in
        # character count, they're likely templated.
        sizes = [len(str(t)) for t in tables]
        if not sizes:
            return []
        median = sorted(sizes)[len(sizes) // 2]
        if median == 0:
            return []
        ratios = [s / median for s in sizes]
        if not all(0.5 <= r <= 1.5 for r in ratios):
            return []

        segments = [str(t) for t in tables]
        segments = [s for s in segments if len(_strip_html_tags(s)) >= _MIN_ARTICLE_CHARS]
        return segments
    except Exception as e:
        logger.debug(f"[article_extractor] table-block split failed: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Text-based heuristics (fallback when HTML is absent / parsing fails)
# ─────────────────────────────────────────────────────────────────────────────


def _split_by_text_separators(text: str) -> List[str]:
    """Heuristic 4 — plain-text separators. Common in dev newsletters:
       ─────, ===, * * *, ---, etc. Three or more of the same character
       on a line, optionally surrounded by whitespace."""
    pattern = r"\n\s*[\-\=\*_─━—]{3,}\s*\n"
    parts = re.split(pattern, text)
    parts = [p.strip() for p in parts if len(p.strip()) >= _MIN_ARTICLE_CHARS]
    return parts


def _split_by_text_headers(text: str) -> List[str]:
    """Heuristic 5 — markdown-style heading lines. ## or ### at start of
    a line marks a new section. Less reliable than HTML headers."""
    lines = text.split("\n")
    headers_idx = [i for i, ln in enumerate(lines) if re.match(r"^\s*#{1,3}\s+\S", ln)]
    if len(headers_idx) < 2:
        return []
    segments = []
    for i, start in enumerate(headers_idx):
        end = headers_idx[i + 1] if i + 1 < len(headers_idx) else len(lines)
        seg = "\n".join(lines[start:end]).strip()
        if len(seg) >= _MIN_ARTICLE_CHARS:
            segments.append(seg)
    return segments


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────


def extract_articles(
    html_body: Optional[str],
    text_body: Optional[str],
    *,
    fallback_title: str = "(untitled newsletter)",
) -> List[ExtractedArticle]:
    """Split a newsletter body into individual articles.

    Tries HTML heuristics first (more reliable), falls back to text
    heuristics, finally falls back to single-article (lossless).

    Returns at least one ExtractedArticle. Position is 0-based.
    """
    html = (html_body or "").strip()
    text = (text_body or "").strip()

    if not html and not text:
        return []

    # Try HTML heuristics in order
    segments: List[str] = []
    use_html = bool(html)
    if html:
        for heuristic_name, splitter in (
            ("hr", _split_by_hr),
            ("headers", _split_by_headers),
            ("table-blocks", _split_by_repeated_table_blocks),
        ):
            candidate = splitter(html)
            if 2 <= len(candidate) <= _MAX_ARTICLES:
                logger.debug(f"[article_extractor] HTML heuristic '{heuristic_name}' → {len(candidate)} articles")
                segments = candidate
                break

    # Fall through to text heuristics
    if not segments and text:
        use_html = False
        for heuristic_name, splitter in (
            ("separators", _split_by_text_separators),
            ("headers", _split_by_text_headers),
        ):
            candidate = splitter(text)
            if 2 <= len(candidate) <= _MAX_ARTICLES:
                logger.debug(f"[article_extractor] text heuristic '{heuristic_name}' → {len(candidate)} articles")
                segments = candidate
                break

    # Final fallback — entire body is one article
    if not segments:
        if html:
            segments = [html]
            use_html = True
        else:
            segments = [text]

    # P1C.2 — compute the flattened parent text once so we can locate
    # each article body's character offset within it. The viewer uses
    # that offset to scroll precisely instead of guessing proportionally.
    parent_flat_text = _strip_html_tags(html) if html else text

    # Build the ExtractedArticle list
    out: List[ExtractedArticle] = []
    search_from = 0  # advance through parent text so duplicate substrings don't collide
    for pos, seg in enumerate(segments):
        if use_html:
            seg_text = _strip_html_tags(seg)
            seg_html = seg
            title = _extract_title_from_segment(seg_text, html=seg_html)
        else:
            seg_text = seg.strip()
            seg_html = None
            title = _extract_title_from_segment(seg_text)
        if not seg_text or len(seg_text) < _MIN_ARTICLE_CHARS:
            continue
        if pos == 0 and (not title or title == "(untitled)"):
            title = fallback_title[:200]

        # Find this article's body in the parent text starting from where
        # the previous article ended. Use a probe — first 80 non-space
        # chars of the article body — to dodge text-cleanup mismatches.
        probe = re.sub(r"\s+", " ", seg_text[:200]).strip()[:80]
        offset = -1
        if probe and parent_flat_text:
            flat_window = parent_flat_text[search_from:]
            normalized_flat = re.sub(r"\s+", " ", flat_window)
            found = normalized_flat.find(probe)
            if found >= 0:
                # Map back to original parent_flat_text index — close-enough
                # since whitespace collapse is monotonic.
                offset = search_from + found
                search_from = offset + len(seg_text) // 2  # advance halfway through

        out.append(ExtractedArticle(
            position=pos,
            title=title,
            body_text=seg_text,
            body_html=seg_html,
            body_text_offset=offset,
        ))

    # Ensure we always return ≥1 article
    if not out and (html or text):
        full_text = _strip_html_tags(html) if html else text
        out.append(ExtractedArticle(
            position=0,
            title=fallback_title[:200],
            body_text=full_text,
            body_html=html or None,
        ))
    return out
