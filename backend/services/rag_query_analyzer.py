"""
RAG Query Analyzer — Query classification, entity extraction, temporal filtering.

Extracted from rag_engine.py Phase 4a. All functions are pure (no instance state).
RAGEngine delegates to these functions via thin wrapper methods.
"""
import re
from typing import Dict, List, Optional, Tuple


# ─── Query Classification ──────────────────────────────────────────────────────

def classify_query(question: str) -> str:
    """Classify query type for optimal prompt and model selection.
    
    Returns: 'factual', 'synthesis', or 'complex'
    """
    q_lower = question.lower()
    
    factual_patterns = [
        'how many', 'how much', 'what is the', 'what was the',
        'when did', 'when was', 'who is', 'who was', 'who did',
        'what date', 'what time', 'what number', 'what percentage',
        'list the', 'name the', 'count of', 'total of',
        'did chris', 'did christopher',
    ]
    for pattern in factual_patterns:
        if pattern in q_lower:
            return 'factual'
    
    complex_patterns = [
        'compare', 'contrast', 'analyze', 'explain why', 'explain how',
        'what are the differences', 'what are the similarities',
        'synthesize', 'evaluate', 'assess',
        'pros and cons', 'advantages and disadvantages',
        'step by step', 'walk me through', 'break down',
        'relationship between', 'implications', 'consequences',
        'argue', 'debate', 'critique', 'review'
    ]
    for pattern in complex_patterns:
        if pattern in q_lower:
            return 'complex'
    
    if len(question) > 100 or question.count('?') > 1:
        return 'complex'
    
    return 'synthesis'


def detect_response_format(question: str) -> str:
    """Detect the ideal response format from the query — pure regex, zero latency.

    Returns a short instruction string to append to the system prompt,
    or empty string for default paragraph style.
    """
    q_lower = question.lower()

    # LIST
    if re.search(r'\b(\d+|top|key|main|major|all)\s+(things?|items?|points?|reasons?|ways?|tips?|examples?|factors?|features?|benefits?|risks?|issues?|steps?|ideas?|recommendations?|priorities?|strengths?|weaknesses?|areas?)\b', q_lower):
        return "\nFORMAT: Respond using a numbered or bulleted markdown list. Each item should be concise (1-2 sentences). Place citations INLINE at the end of each item like [1], NOT grouped at the top."
    if re.search(r'\blist\s+(the|all|every|my|our|their)\b', q_lower):
        return "\nFORMAT: Respond using a numbered or bulleted markdown list. Each item should be concise (1-2 sentences). Place citations INLINE at the end of each item like [1], NOT grouped at the top."
    if re.search(r'\bwhat are (the |all )?(key|main|top|biggest|most)\b', q_lower):
        return "\nFORMAT: Respond using a numbered or bulleted markdown list. Each item should be concise (1-2 sentences). Place citations INLINE at the end of each item like [1], NOT grouped at the top."

    # CODE
    if re.search(r'\b(write|show|give|create|generate)\s+(me\s+)?(the\s+)?(code|script|function|implementation|snippet|class|method|query|sql|regex)\b', q_lower):
        return "\nFORMAT: Include code in fenced markdown code blocks (```language). Add brief explanations outside the code blocks."
    if re.search(r'\b(implement|code|program|script)\s+(a|an|the|this|that)\b', q_lower):
        return "\nFORMAT: Include code in fenced markdown code blocks (```language). Add brief explanations outside the code blocks."

    # TABLE
    if re.search(r'\b(table|comparison|matrix|grid)\b.*\b(of|for|showing|comparing)\b', q_lower):
        return "\nFORMAT: Use a markdown table for structured comparison. Add a brief summary below the table."

    # STEPS
    if re.search(r'\b(step.by.step|walk me through|how do i|how to|process for|guide to|instructions for)\b', q_lower):
        return "\nFORMAT: Respond with numbered steps. Each step should have a clear action and brief explanation."

    return ""


def should_auto_upgrade_to_think(question: str) -> bool:
    """Invisible auto-routing: detect if a 'fast' query should be upgraded to 'think' mode."""
    return classify_query(question) == 'complex'


# ─── Entity Extraction ──────────────────────────────────────────────────────────

def extract_entities(question: str) -> List[str]:
    """Extract named entities from query for better matching.
    
    Lightweight entity extraction without spaCy dependency.
    Focuses on names, proper nouns, and domain-specific terms.
    """
    entities = []
    
    # Capitalized words/phrases
    cap_pattern = r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b'
    cap_matches = re.findall(cap_pattern, question)
    entities.extend(cap_matches)
    
    # First + last name patterns
    name_pattern = r'\b([A-Z][a-z]+)\s+([A-Z][a-z]+)\b'
    name_matches = re.findall(name_pattern, question)
    for first, last in name_matches:
        entities.append(f"{first} {last}")
    
    # Quoted phrases
    quoted = re.findall(r'"([^"]+)"', question)
    entities.extend(quoted)
    quoted_single = re.findall(r"'([^']+)'", question)
    entities.extend(quoted_single)
    
    # Deduplicate while preserving order
    seen = set()
    unique_entities = []
    for e in entities:
        e_lower = e.lower()
        if e_lower not in seen and len(e) > 1:
            seen.add(e_lower)
            unique_entities.append(e)
    
    return unique_entities


def boost_entity_matches(results: List[Dict], entities: List[str]) -> List[Dict]:
    """Boost results containing extracted entities."""
    if not entities:
        return results
    
    def entity_score(result):
        text = result.get('text', '').lower()
        score = 0
        for entity in entities:
            if entity.lower() in text:
                score += 2
        return score
    
    scored = [(entity_score(r), i, r) for i, r in enumerate(results)]
    scored.sort(key=lambda x: (-x[0], x[1]))
    
    boosted = [r for _, _, r in scored]
    
    top_scores = [s for s, _, _ in scored[:5]]
    if any(s > 0 for s in top_scores):
        print(f"[RAG] Entity boost applied for {entities}: top scores = {top_scores}")
    
    return boosted


# ─── Temporal Filtering ─────────────────────────────────────────────────────────

def extract_temporal_filter(question: str) -> Optional[Dict]:
    """Extract temporal references from query for filtering."""
    q_lower = question.lower()
    
    temporal_info = {
        'quarters': [],
        'years': [],
        'fiscal_years': []
    }
    
    quarter_patterns = [
        r'\bq\s*([1-4])\b',
        r'\bquarter\s*([1-4])\b',
        r'\b(first|second|third|fourth)\s+quarter\b'
    ]
    quarter_map = {'first': '1', 'second': '2', 'third': '3', 'fourth': '4'}
    
    for pattern in quarter_patterns:
        matches = re.findall(pattern, q_lower)
        for match in matches:
            if match in quarter_map:
                temporal_info['quarters'].append(quarter_map[match])
            elif match.isdigit():
                temporal_info['quarters'].append(match)
    
    year_matches = re.findall(r'\b(20[2-3][0-9])\b', question)
    temporal_info['years'] = list(set(year_matches))
    
    fy_matches = re.findall(r'\bfy\s*(\d{4}|\d{2})\b', q_lower)
    for fy in fy_matches:
        if len(fy) == 2:
            fy = '20' + fy
        temporal_info['fiscal_years'].append(fy)
    
    if not any(temporal_info.values()):
        return None
    
    return temporal_info


def boost_temporal_relevance(results: List[Dict], temporal_filter: Dict) -> List[Dict]:
    """Boost results that match temporal criteria."""
    if not temporal_filter:
        return results
    
    patterns = []
    for q in temporal_filter.get('quarters', []):
        patterns.extend([f'q{q}', f'q {q}', f'quarter {q}'])
    for y in temporal_filter.get('years', []):
        patterns.append(y)
    for fy in temporal_filter.get('fiscal_years', []):
        patterns.extend([f'fy {fy}', f'fy{fy}', fy])
    
    if not patterns:
        return results
    
    def temporal_score(result):
        text = result.get('text', '').lower()
        source_id = result.get('source_id', '').lower()
        filename = result.get('filename', '').lower()
        searchable = f"{text} {source_id} {filename}"
        
        score = 0
        for pattern in patterns:
            if pattern.lower() in searchable:
                score += 1
        return score
    
    scored = [(temporal_score(r), i, r) for i, r in enumerate(results)]
    scored.sort(key=lambda x: (-x[0], x[1]))
    
    boosted = [r for _, _, r in scored]
    
    top_scores = [s for s, _, _ in scored[:5]]
    if any(s > 0 for s in top_scores):
        print(f"[RAG] Temporal boost applied: top scores = {top_scores}")
    else:
        print(f"[RAG] WARNING: Temporal filter {patterns} found no matching documents")
    
    return boosted


# ─── Source Diversity ────────────────────────────────────────────────────────────

def ensure_source_diversity(results: List[Dict], min_sources: int = 2) -> List[Dict]:
    """Ensure results come from multiple sources when possible."""
    if len(results) <= min_sources:
        return results
    
    by_source = {}
    for r in results:
        source_id = r.get('source_id', 'unknown')
        if source_id not in by_source:
            by_source[source_id] = []
        by_source[source_id].append(r)
    
    if len(by_source) <= 1:
        return results
    
    diverse_results = []
    source_iterators = {k: iter(v) for k, v in by_source.items()}
    
    while len(diverse_results) < len(results):
        added_this_round = False
        for source_id in list(source_iterators.keys()):
            try:
                result = next(source_iterators[source_id])
                diverse_results.append(result)
                added_this_round = True
            except StopIteration:
                del source_iterators[source_id]
        
        if not added_this_round:
            break
    
    return diverse_results


# ─── Query Expansion ────────────────────────────────────────────────────────────

def expand_query(question: str) -> str:
    """Query expansion with synonyms and related terms."""
    expansions = {
        'demo': 'demo demonstration "record count"',
        'demos': 'demos demonstrations "record count"',
        'trial': 'trial pilot',
        'trials': 'trials pilots',
        'q1': 'q1 "q 1" "quarter 1" "first quarter" "Q 1 FY"',
        'q2': 'q2 "q 2" "quarter 2" "second quarter" "Q 2 FY"',
        'q3': 'q3 "q 3" "quarter 3" "third quarter" "Q 3 FY"',
        'q4': 'q4 "q 4" "quarter 4" "fourth quarter" "Q 4 FY"',
        'fy': 'fy "fiscal year"',
        'fy2026': 'fy2026 "fy 2026" "FY 2026"',
        'fy2025': 'fy2025 "fy 2025" "FY 2025"',
        'revenue': 'revenue sales income',
        'customer': 'customer client account',
        'customers': 'customers clients accounts',
        'meeting': 'meeting call conversation',
        'meetings': 'meetings calls conversations',
    }
    
    name_expansions = {
        'chris': 'chris christopher',
        'mike': 'mike michael',
        'dan': 'dan daniel',
        'bill': 'bill william',
        'bob': 'bob robert',
        'jim': 'jim james',
        'tom': 'tom thomas',
        'steve': 'steve stephen steven',
        'pat': 'pat patrick patricia',
        'jen': 'jen jennifer',
        'liz': 'liz elizabeth',
        'alex': 'alex alexander alexandra',
        'matt': 'matt matthew',
        'nick': 'nick nicholas',
        'sam': 'sam samuel samantha',
        'joe': 'joe joseph',
        'will': 'will william',
    }
    
    expanded = question
    q_lower = question.lower()
    
    for term, expansion in expansions.items():
        if term in q_lower and expansion not in q_lower:
            expanded = f"{expanded} {expansion}"
    
    for nick, full in name_expansions.items():
        if nick in q_lower.split():
            expanded = f"{expanded} {full}"
    
    return expanded


def build_search_query(analysis: Dict, original_question: str) -> str:
    """Build an optimized search query from LLM analysis."""
    parts = [original_question]
    
    for term in analysis.get("search_terms", []):
        if term.lower() not in original_question.lower():
            parts.append(term)
    
    for entity in analysis.get("entities", []):
        if entity.lower() not in original_question.lower():
            parts.append(entity)
    
    for period in analysis.get("time_periods", []):
        parts.append(period)
    
    if analysis.get("key_metric"):
        metric = analysis["key_metric"].lower()
        parts.append(metric)
        parts.append("record count")
    
    return " ".join(parts)


def fallback_query_analysis(question: str) -> Dict:
    """Fallback query analysis when LLM is unavailable."""
    q_lower = question.lower()
    
    time_periods = []
    quarter_match = re.search(r'q([1-4])\s*(?:fy)?\s*(\d{4})?', q_lower)
    if quarter_match:
        q_num = quarter_match.group(1)
        year = quarter_match.group(2) or "2026"
        time_periods.append(f"Q {q_num} FY {year}")
    
    entities = re.findall(r'\b([A-Z][a-z]+)\b', question)
    
    search_terms = list(set(question.lower().split()))
    
    return {
        "search_terms": search_terms,
        "entities": entities,
        "time_periods": time_periods,
        "data_type": "count" if any(w in q_lower for w in ["how many", "count", "number"]) else "explanation",
        "key_metric": None
    }


def generate_query_variants(question: str) -> List[str]:
    """Generate variant queries to improve retrieval on retry."""
    variants = [question]
    
    q_lower = question.lower()
    
    expansions = {
        'q1': 'first quarter Q1',
        'q2': 'second quarter Q2',
        'q3': 'third quarter Q3',
        'q4': 'fourth quarter Q4',
        'fy': 'fiscal year FY',
    }
    for abbrev, full in expansions.items():
        if abbrev in q_lower:
            expanded = re.sub(rf'\b{abbrev}\b', full, question, flags=re.IGNORECASE)
            if expanded != question:
                variants.append(expanded)
                break
    
    if any(word in q_lower for word in ['how many', 'how much', 'total', 'count']):
        variants.append(f"{question} total count number")
    
    entities = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', question)
    if entities:
        entity_focused = f"{entities[0]} {question}"
        variants.append(entity_focused)
    
    return variants[:3]


# ─── Quality Verification ────────────────────────────────────────────────────────

def verify_retrieval_quality(results: List[Dict], analysis: Dict) -> Tuple[bool, str]:
    """Verify that retrieved chunks actually contain relevant data.
    
    Returns: (is_good, reason)
    """
    if not results:
        return False, "No results retrieved"
    
    entities = analysis.get("entities", [])
    time_periods = analysis.get("time_periods", [])
    key_metric = analysis.get("key_metric", "")
    
    combined_text = " ".join(r.get("text", "") for r in results[:4]).lower()
    
    entity_found = False
    for entity in entities:
        if entity.lower() in combined_text:
            entity_found = True
            break
    
    time_found = False
    for period in time_periods:
        period_lower = period.lower()
        if period_lower in combined_text:
            time_found = True
            break
        q_match = re.search(r'q\s*(\d)', period_lower)
        y_match = re.search(r'20\d{2}', period_lower)
        if q_match and y_match:
            quarter = q_match.group(1)
            year = y_match.group(0)
            if f"q {quarter}" in combined_text and year in combined_text:
                time_found = True
                break
            if f"q{quarter}" in combined_text and year in combined_text:
                time_found = True
                break
    
    metric_found = True
    if key_metric:
        metric_lower = key_metric.lower()
        metric_found = (
            metric_lower in combined_text or
            metric_lower.rstrip('s') in combined_text or
            (metric_lower + 's') in combined_text
        )
    
    if entities and not entity_found:
        return False, f"Entity '{entities[0]}' not found in top results"
    if time_periods and not time_found:
        return False, f"Time period '{time_periods[0]}' not found in top results"
    if key_metric and not metric_found:
        return False, f"Metric '{key_metric}' not found in top results"
    
    return True, "Retrieval looks good"


def check_answer_quality(question: str, answer: str, query_type: str) -> Tuple[bool, str]:
    """Lightweight quality check for answers - no LLM call, just heuristics."""
    if not answer or len(answer.strip()) < 15:
        return False, "Answer too short"
    
    failure_phrases = [
        "i cannot find", "not in the sources", "no information",
        "unable to find", "don't have", "doesn't contain",
        "not mentioned", "no data", "cannot determine"
    ]
    answer_lower = answer.lower()
    for phrase in failure_phrases:
        if phrase in answer_lower:
            return False, f"Answer indicates failure: '{phrase}'"
    
    if query_type == 'factual':
        has_number = bool(re.search(r'\d+', answer))
        if not has_number:
            return False, "Factual query but no number in answer"
        if re.search(r'\bX\s+(number|demos?|seedings?|activities?|total)\b', answer, re.IGNORECASE):
            return False, "Answer contains 'X' placeholder instead of actual number"
    
    if '[N]' in answer or '[Summary]' in answer:
        return False, "Answer contains placeholder artifacts"
    
    if "note to user" in answer_lower or "replace 'x'" in answer_lower:
        return False, "Answer contains meta-commentary instead of actual data"
    
    return True, "Answer looks good"
