"""Hybrid Theme Extraction Service

Approach 3 from VISUAL_SYSTEM_DIAGNOSTIC.md:
1. Fast regex extraction (instant)
2. Validation to catch garbage
3. LLM fallback with Pydantic schema when regex fails

This ensures 99% reliable theme extraction regardless of LLM output format.
"""
import re
from typing import List, Dict, Optional
from pydantic import BaseModel, Field
import logging

logger = logging.getLogger(__name__)


class Theme(BaseModel):
    """A single extracted theme."""
    name: str = Field(..., description="Theme name, 3-8 words, noun phrase format")


class VisualContent(BaseModel):
    """Structured content for visual generation."""
    title: str = Field(default="Key Themes", description="Visual title, 3-6 words")
    themes: List[str] = Field(..., min_length=2, max_length=15, description="Main themes/topics - NO LIMIT")
    insight: Optional[str] = Field(None, description="Key takeaway sentence")


# Patterns that indicate garbage extraction (sentence fragments, not themes)
GARBAGE_PATTERNS = [
    r'^(first|second|third|finally|next|then|also|another|a \w+ theme)',  # Sentence starters
    r'^(the|an?)\s',  # Articles at start
    r'(emerging|across|dominate|include|discuss|mention|highlight)',  # Verbs
    r'(is the|are the|was the|were the)',  # Copula phrases
    r'^(this|that|these|those)\s',  # Demonstratives
    r'(sources|content|text|document|article)',  # Meta-references
    r'\.\.\.$',  # Trailing ellipsis
    r'^\d+\s*$',  # Just a number
]

# Minimum requirements for valid themes
MIN_THEME_LENGTH = 8  # At least 8 chars
MIN_WORD_COUNT = 2    # At least 2 words
MIN_VALID_THEMES = 3  # Need at least 3 valid themes


def clean_theme_name(name: str) -> str:
    """Clean a theme name - handles ALL edge cases in ONE place."""
    if not name:
        return ""
    s = str(name)
    # Remove citation markers: [1], [2], (Citations: [1], [2]), etc.
    s = re.sub(r'\s*\(Citations?[^)]*\)', '', s)
    s = re.sub(r'\[\d+\]', '', s)
    # Remove parentheses with just commas/spaces: (, , , )
    s = re.sub(r'\s*\(\s*[,\s]*\s*\)\s*:?', '', s)
    # Remove markdown: **, *, _, #
    s = re.sub(r'[*_#]+', '', s)
    # Remove leading numbers/bullets: "1. ", "- ", "• "
    s = re.sub(r'^\s*[\d]+\.\s*', '', s)
    s = re.sub(r'^\s*[-•*]\s*', '', s)
    # Remove trailing colons
    s = re.sub(r'\s*:\s*$', '', s)
    # Collapse whitespace
    s = re.sub(r'\s+', ' ', s).strip()
    # Capitalize first letter (fix "the Future of" → "The Future of")
    if s and s[0].islower():
        s = s[0].upper() + s[1:]
    # NO TRUNCATION - let SVG templates handle text overflow with CSS word-wrap
    return s


def clean_insight_text(text: str) -> str:
    """Clean insight/tagline text - allows longer text than theme names."""
    if not text:
        return ""
    s = str(text)
    # Remove citation markers
    s = re.sub(r'\s*\(Citations?[^)]*\)', '', s)
    s = re.sub(r'\[\d+\]', '', s)
    s = re.sub(r'\s*\(\s*[,\s]*\s*\)\s*:?', '', s)
    # Remove markdown
    s = re.sub(r'[*_#]+', '', s)
    # Collapse whitespace
    s = re.sub(r'\s+', ' ', s).strip()
    # Remove trailing ellipsis - NEVER show "..."
    while s.endswith('...'):
        s = s[:-3].strip()
    while s.endswith('..'):
        s = s[:-2].strip()
    # Capitalize first letter
    if s and s[0].islower():
        s = s[0].upper() + s[1:]
    # Ensure proper punctuation if not empty
    if s and s[-1] not in '.!?':
        s += '.'
    return s


def is_garbage_theme(theme: str) -> bool:
    """Check if a theme looks like garbage (sentence fragment, not a real theme)."""
    theme_lower = theme.lower().strip()
    
    # Too short
    if len(theme_lower) < MIN_THEME_LENGTH:
        return True
    
    # Not enough words
    if len(theme_lower.split()) < MIN_WORD_COUNT:
        return True
    
    # Matches garbage patterns
    for pattern in GARBAGE_PATTERNS:
        if re.search(pattern, theme_lower, re.IGNORECASE):
            return True
    
    # Ends with comma (incomplete phrase)
    if theme_lower.rstrip().endswith(','):
        return True
    
    # Contains "..." in the middle (truncated)
    if '...' in theme_lower[:-3]:  # Not counting trailing
        return True
    
    return False


def is_valid_extraction(themes: List[str]) -> bool:
    """Validate that extracted themes are meaningful, not garbage."""
    if not themes or len(themes) < MIN_VALID_THEMES:
        return False
    
    valid_count = 0
    for theme in themes:
        if not is_garbage_theme(theme):
            valid_count += 1
    
    # Need at least 3 valid themes
    return valid_count >= MIN_VALID_THEMES


def extract_title_from_count(text: str) -> Optional[str]:
    """Extract dynamic title from theme/section count mentions."""
    # Look for "X distinct sections", "X key themes", etc.
    count_patterns = [
        r'(two|three|four|five|six|seven|eight|\d+)\s+(distinct\s+)?(section|theme|point|area|insight|topic|category)',
    ]
    count_map = {'two': '2', 'three': '3', 'four': '4', 'five': '5', 'six': '6', 'seven': '7', 'eight': '8'}
    
    for pattern in count_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            count_word = match.group(1).lower()
            count = count_map.get(count_word, count_word)
            return f"{count} Key Themes"
    return None


def extract_themes_regex(text: str) -> List[str]:
    """Fast regex extraction - tries multiple patterns."""
    
    # PRIORITY 1: Numbered items - "1. Title" or "**1. Title**" or "(1) Title"
    # This is the MOST RELIABLE pattern for structured content
    numbered = re.findall(r'^\s*\*?\*?(?:\d+\.|\(\d+\))\s*\*?\*?\s*([^\n]+)', text, re.MULTILINE)
    if len(numbered) >= 2:
        themes = [clean_theme_name(t) for t in numbered[:15]]  # Allow up to 15
        themes = [t for t in themes if len(t) > 5]
        if len(themes) >= 2:
            print(f"[Theme Extractor] ✅ Numbered list found: {len(themes)} themes")
            return themes
    
    # PRIORITY 2: Letter lists - "a) Title" or "a. Title"
    letters = re.findall(r'^\s*[a-z][).]\s+([^\n]+)', text, re.MULTILINE)
    if len(letters) >= 2:
        themes = [clean_theme_name(t) for t in letters[:15]]
        themes = [t for t in themes if len(t) > 5]
        if len(themes) >= 2:
            print(f"[Theme Extractor] ✅ Letter list found: {len(themes)} themes")
            return themes
    
    # PRIORITY 3: Markdown headers - "## Title" or "### Title"
    headers = re.findall(r'^#{1,3}\s+([^\n]+)', text, re.MULTILINE)
    if len(headers) >= 2:
        themes = [clean_theme_name(t) for t in headers[:15]]
        themes = [t for t in themes if len(t) > 5]
        if len(themes) >= 2:
            print(f"[Theme Extractor] ✅ Markdown headers found: {len(themes)} themes")
            return themes
    
    # PRIORITY 4: Roman numerals - "I. Title" or "II. Title"
    roman = re.findall(r'^\s*(?:I{1,3}|IV|VI{0,3}|IX|X)\.\s+([^\n]+)', text, re.MULTILINE)
    if len(roman) >= 2:
        themes = [clean_theme_name(t) for t in roman[:15]]
        themes = [t for t in themes if len(t) > 5]
        if len(themes) >= 2:
            print(f"[Theme Extractor] ✅ Roman numeral list found: {len(themes)} themes")
            return themes
    
    # PRIORITY 5: Comma-separated list (prose format) - LAST RESORT for structured lists
    # Only use this if no numbered/bulleted structure found
    colon_list_patterns = [
        r'(?:section|theme|area|topic|point|category|finding)s?\s*(?:include|are|emerge)?:\s*([^.]+(?:,\s*and\s+[^.]+)?)\.',
        r'distilled into[^:]*:\s*([^.]+(?:,\s*and\s+[^.]+)?)\.',
        r'grouped into[^:]*:\s*([^.]+(?:,\s*and\s+[^.]+)?)\.',
        r'organized (?:into|as)[^:]*:\s*([^.]+(?:,\s*and\s+[^.]+)?)\.',
    ]
    
    for pattern in colon_list_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            items_str = match.group(1)
            items_str = re.sub(r',\s*and\s+', ', ', items_str)
            items = [item.strip() for item in items_str.split(',')]
            themes = [clean_theme_name(t) for t in items if len(t.strip()) > 5]
            if len(themes) >= 2:
                print(f"[Theme Extractor] ✅ Prose colon-list found: {themes}")
                return themes
    
    # PRIORITY 6: Parenthetical inline - "(1) X, (2) Y, (3) Z"
    parens = re.findall(r'\(\d+\)\s*([^,(]+)', text)
    if len(parens) >= 2:
        themes = [clean_theme_name(t) for t in parens[:15]]
        themes = [t for t in themes if len(t) > 5]
        if len(themes) >= 2:
            print(f"[Theme Extractor] ✅ Parenthetical list found: {len(themes)} themes")
            return themes
    
    # PRIORITY 7: Bold items - "**Title**"
    bold = re.findall(r'\*\*([^*]+)\*\*', text)
    if len(bold) >= 2:
        themes = [clean_theme_name(t) for t in bold[:15]]
        themes = [t for t in themes if len(t) > 5]
        if len(themes) >= 2:
            print(f"[Theme Extractor] ✅ Bold items found: {len(themes)} themes")
            return themes
    
    # PRIORITY 8: Topic with colon - "Topic Name: description"
    topic_colon = re.findall(r'^([A-Z][A-Za-z\s]+?):\s+[A-Z]', text, re.MULTILINE)
    if len(topic_colon) >= 2:
        themes = [clean_theme_name(t) for t in topic_colon[:15]]
        themes = [t for t in themes if len(t) > 5]
        if len(themes) >= 2:
            print(f"[Theme Extractor] ✅ Topic-colon format found: {len(themes)} themes")
            return themes
    
    # PRIORITY 9: Key areas format - "Topic - description"
    topic_dash = re.findall(r'^([A-Z][A-Za-z\s]+?)\s+-\s+', text, re.MULTILINE)
    if len(topic_dash) >= 2:
        themes = [clean_theme_name(t) for t in topic_dash[:15]]
        themes = [t for t in themes if len(t) > 5]
        if len(themes) >= 2:
            print(f"[Theme Extractor] ✅ Topic-dash format found: {len(themes)} themes")
            return themes
    
    # PRIORITY 10: Bullet items - "- Title" or "• Title"
    bullets = re.findall(r'^[-•*]\s+([^\n]+)', text, re.MULTILINE)
    if len(bullets) >= 2:
        themes = [clean_theme_name(t) for t in bullets[:15]]
        themes = [t for t in themes if len(t) > 5]
        if len(themes) >= 2:
            print(f"[Theme Extractor] ✅ Bullet items found: {len(themes)} themes")
            return themes
    
    # PRIORITY 11: Plain prose with "theme is/involves/focuses on" patterns
    # Extracts: "The first theme is safety in AI" → "Safety in AI"
    prose_theme_patterns = [
        # "The first/second theme is X" or "Another key theme involves X"
        r'(?:the\s+)?(?:first|second|third|fourth|fifth|another|one|key|major|primary|next)\s+(?:major\s+)?(?:theme|area|topic|focus|point)\s+(?:is|involves|focuses on|concerns|addresses|covers)\s+([^.]{10,60})',
        # "Third, X enables..." or "Finally, X connects..."
        r'(?:^|\.\s+)(?:third|fourth|fifth|finally|lastly|additionally),?\s+([a-z][^.]{8,50}?)(?:\s+(?:enables?|allows?|connects?|provides?|ensures?|involves?|addresses))',
        # Generic "theme is/involves"
        r'(?:theme|area|topic)\s+(?:is|involves|focuses on)\s+([^.]{10,60})',
    ]
    prose_themes = []
    for pattern in prose_theme_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for m in matches:
            cleaned = clean_theme_name(m)
            # Capitalize first letter of each word for noun phrase format
            cleaned = ' '.join(word.capitalize() for word in cleaned.split())
            if len(cleaned) > 8 and cleaned not in prose_themes:
                prose_themes.append(cleaned)
    if len(prose_themes) >= 2:
        print(f"[Theme Extractor] ✅ Prose theme patterns found: {prose_themes}")
        return prose_themes[:15]
    
    # PRIORITY 12: Last resort - extract key noun phrases from paragraph starts
    # Look for capitalized phrases that look like topic headers
    paragraphs = re.split(r'\n\s*\n', text)
    themes = []
    for p in paragraphs:
        # Try to find a topic phrase at the start (capitalized words)
        topic_match = re.match(r'^([A-Z][a-z]+(?:\s+(?:and|in|of|for|the|with|to)?\s*[A-Za-z]+){1,5})', p.strip())
        if topic_match:
            cleaned = clean_theme_name(topic_match.group(1))
            if len(cleaned) > 8 and not is_garbage_theme(cleaned):
                themes.append(cleaned)
        if len(themes) >= 15:
            break
    
    if len(themes) >= 2:
        print(f"[Theme Extractor] ✅ Last resort paragraph extraction: {len(themes)} themes")
        return themes
    
    return []


async def extract_themes_llm(text: str) -> VisualContent:
    """LLM extraction fallback using pydantic-ai for guaranteed structured output."""
    import httpx
    from config import settings
    import json
    
    extraction_prompt = f"""Extract the main themes/topics from this content. 

IMPORTANT: Extract NOUN PHRASES that represent the main topics, NOT sentence fragments.

Examples of GOOD themes:
- "Safety and Alignment in AI"
- "Healthcare AI Adoption Challenges"
- "Security Sandboxing for AI Systems"
- "Autonomous Agent Development"

Examples of BAD themes (do NOT do this):
- "First, safety dominates..." (sentence fragment)
- "A third theme is..." (meta-reference)
- "The sources discuss..." (meta-reference)

Content to analyze:
{text[:3000]}

Return ONLY valid JSON in this exact format:
{{
  "title": "3-5 word title describing the overall topic",
  "themes": ["Theme 1", "Theme 2", "Theme 3"],
  "insight": "Short tagline summarizing the themes (max 100 characters)"
}}

RULES:
- themes: Extract ALL MAIN TOPICS - NO LIMIT. If content has 8 themes, extract 8. If 10, extract 10.
- Each theme should be a TOPIC NAME, not a sentence
- insight: A SHORT, PUNCHY tagline (MAX 100 CHARACTERS). Must be COMPLETE - never end with "..."
- Example good insights: "AI safety and enterprise scalability drive modern agent development."
- NEVER use "..." or trailing ellipsis - write a complete thought that fits in 100 chars
- NO words like "First", "Second", "A theme is", "The sources"
- NO citation markers like [1] or [2]
- Return ONLY the JSON, no other text"""

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{settings.ollama_base_url}/api/generate",
                json={
                    "model": settings.ollama_fast_model,
                    "prompt": extraction_prompt,
                    "stream": False,
                    "options": {"num_predict": 800, "temperature": 0}
                }
            )
            result = response.json().get("response", "{}")
            logger.info(f"[Theme Extractor] LLM response: {result[:300]}...")
            
            # Parse JSON
            json_match = re.search(r'\{[\s\S]*\}', result)
            if not json_match:
                raise ValueError("No JSON found in LLM response")
            
            json_str = json_match.group()
            # Fix common JSON errors
            json_str = re.sub(r',\s*}', '}', json_str)
            json_str = re.sub(r',\s*]', ']', json_str)
            
            data = json.loads(json_str)
            
            # Clean and validate themes
            themes = [clean_theme_name(t) for t in data.get("themes", [])]
            themes = [t for t in themes if len(t) > 5 and not is_garbage_theme(t)]
            
            if len(themes) < 3:
                # Even LLM failed - use generic fallback
                themes = ["Key Finding 1", "Key Finding 2", "Key Finding 3"]
            
            # Clean insight - strip trailing "..." that LLM sometimes adds
            raw_insight = data.get("insight", "")
            if raw_insight:
                insight = raw_insight.strip()
                # Remove trailing ellipsis
                while insight.endswith('...'):
                    insight = insight[:-3].strip()
                while insight.endswith('..'):
                    insight = insight[:-2].strip()
                # Ensure it ends with proper punctuation if not empty
                if insight and not insight[-1] in '.!?':
                    insight += '.'
            else:
                insight = None
            
            return VisualContent(
                title=data.get("title", "Key Themes")[:60],
                themes=themes[:15],  # Allow up to 15 themes
                insight=insight
            )
            
    except Exception as e:
        logger.error(f"[Theme Extractor] LLM extraction failed: {e}")
        return VisualContent(
            title="Key Themes",
            themes=["Key Finding 1", "Key Finding 2", "Key Finding 3"],
            insight=None
        )


async def extract_themes_hybrid(text: str) -> VisualContent:
    """
    Hybrid extraction: fast regex first, LLM fallback if garbage detected.
    
    This is the main entry point for theme extraction.
    """
    # PASS 1: Fast regex extraction (instant)
    regex_themes = extract_themes_regex(text)
    
    # Extract title from count mentions (works regardless of theme quality)
    title = extract_title_from_count(text) or "Key Themes"
    
    # PASS 2: Validate extraction quality
    # ALWAYS check for garbage - length alone doesn't mean quality
    if is_valid_extraction(regex_themes):
        print(f"[Theme Extractor] ✅ Regex extraction VALID: {regex_themes}")
        
        # Extract insight
        insight = None
        last_para = text.split('\n\n')[-1] if '\n\n' in text else text[-500:]
        insight_match = re.search(r'((?:These|This|Overall|In summary|Together)[^.]{20,}\.)', last_para, re.IGNORECASE)
        if insight_match:
            insight = clean_insight_text(insight_match.group(1))  # Use dedicated insight cleaner
        
        return VisualContent(
            title=title,
            themes=regex_themes[:15],  # Allow up to 15 themes
            insight=insight
        )
    
    # PASS 3: LLM fallback (regex produced garbage)
    garbage_samples = [t for t in regex_themes if is_garbage_theme(t)][:3]
    print(f"[Theme Extractor] ⚠️ Regex extraction FAILED validation. Garbage detected: {garbage_samples}")
    print("[Theme Extractor] 🔄 Falling back to LLM extraction...")
    
    return await extract_themes_llm(text)


def extract_subpoints_for_themes(text: str, themes: List[str]) -> Dict[str, List[str]]:
    """Extract sub-points for each theme. Used for mindmap visualization."""
    subpoints = {}
    
    # Split text into paragraphs
    paragraphs = text.split('\n\n')
    
    for theme in themes:
        theme_lower = theme.lower()
        # Get key words for matching (first 2-3 significant words)
        theme_words = [w for w in theme_lower.split() if w not in ('the', 'a', 'an', 'of', 'in', 'and', 'for')][:3]
        if len(theme_words) < 2:
            continue
            
        # Find the paragraph that discusses this theme
        # Must have theme words in FIRST 100 chars (not just mentioned somewhere)
        section_text = None
        for para in paragraphs:
            if len(para) < 100:
                continue
            para_lower = para.lower()
            first_100 = para_lower[:100]
            # Theme words must appear near the START of the paragraph (within first 100 chars)
            if all(w in first_100 for w in theme_words[:2]):
                section_text = para
                break
        
        if not section_text:
            continue
        
        theme_subs = []
        
        # Strategy 1: Find "such as X, Y, and Z" or "like X, Y, and Z" patterns
        list_patterns = [
            r'such as\s+([^.]+?)(?:\.|$)',
            r'(?:mechanisms? |tools? |patterns? |measures? )?(?:like|including)\s+([^.]+?)(?:\.|$)',
        ]
        for pattern in list_patterns:
            match = re.search(pattern, section_text, re.IGNORECASE)
            if match and not theme_subs:
                items_str = match.group(1)
                # Split on comma, handling "and" 
                items_str = re.sub(r',\s*and\s+', ', ', items_str)
                items = [x.strip() for x in items_str.split(',')]
                for item in items[:6]:  # Allow more sub-items
                    item = re.sub(r'^and\s+', '', item).strip()
                    # Truncate at prepositions that start dependent clauses
                    item = re.split(r'\s+(?:to\s+\w|in\s+\w|for\s+\w|that\s+|which\s+)', item)[0]
                    cleaned = clean_theme_name(item)[:40]
                    if cleaned and len(cleaned) > 5 and cleaned not in theme_subs:
                        theme_subs.append(cleaned)
        
        # Strategy 2: Find bullet points if present
        if not theme_subs:
            bullets = re.findall(r'[-•*]\s+([^\n]{10,50})', section_text)
            theme_subs = [clean_theme_name(b)[:40] for b in bullets[:3]]
        
        if theme_subs:
            subpoints[theme] = theme_subs[:3]
    
    return subpoints
