"""Template Scoring System

Scores all visual templates based on extracted content structure.
Replaces hardcoded keyword matching with content-driven selection.
"""
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass


@dataclass
class TemplateScore:
    """Score result for a template."""
    template_id: str
    score: float
    pattern: str  # For diversity filtering
    reasons: List[str]  # Why this template scored well


# Minimum requirements for a template to render properly (not just score well)
# Format: {template_id: {field: min_count, ...}}
MINIMUM_VIABLE: Dict[str, Dict[str, int]] = {
    "pros_cons": {"pros": 2, "cons": 2},
    "force_field": {"themes": 2},  # Can work with just themes as forces
    "side_by_side": {"themes": 2},
    "ranking": {"themes": 2},  # Themes can be ranked
    "funnel": {"themes": 3},
    "recommendation_stack": {"themes": 2},
    "horizontal_steps": {"themes": 2},  # Themes as steps
    "process_flow": {"themes": 2},
    "cycle_loop": {"themes": 3},
    "stages_progression": {"themes": 2},
    "timeline": {"dates_events": 2},
    "mindmap": {"themes": 2},
    "concept_map": {"themes": 2},
    "overview_map": {"themes": 2},
    "overview": {"themes": 2},
    "anatomy": {"themes": 2},
    "mece": {"themes": 3},
    "key_takeaways": {"themes": 1},  # Most forgiving - works with 1+ theme
    "key_stats": {"themes": 1},
    "quadrant": {"themes": 4},
    "heatmap": {"themes": 2},
    "stakeholder_map": {"themes": 2},
    "system_architecture": {"themes": 2},
    "decision_tree": {"themes": 2},
    "argument": {"themes": 2},
    "trend_chart": {"numbers": 2},
    "distribution": {"themes": 2},
    "exec_summary": {"themes": 2},
    "call_to_action": {"themes": 1},
    "scope": {"themes": 2},
    "spectrum": {"themes": 2},
}

# Universal fallback templates that work with minimal data
UNIVERSAL_FALLBACKS = ["key_takeaways", "ranking", "mindmap"]

# Quick lookup for template patterns
TEMPLATE_PATTERNS: Dict[str, str] = {
    "key_takeaways": "hub",
    "key_stats": "hub",
    "overview": "hub",
    "overview_map": "hub",
    "anatomy": "hub",
    "ranking": "flow_v",
    "funnel": "flow_v",
    "recommendation_stack": "flow_v",
    "mindmap": "mindmap",
    "concept_map": "mindmap",
    "horizontal_steps": "flow_h",
    "process_flow": "flow_h",
    "stages_progression": "flow_h",
    "timeline": "timeline",
    "pros_cons": "two_col",
    "force_field": "two_col",
    "side_by_side": "two_col",
    "scope": "two_col",
    "quadrant": "grid",
    "heatmap": "grid",
    "cycle_loop": "cycle",
    "system_architecture": "hierarchy",
    "decision_tree": "hierarchy",
    "argument": "hierarchy",
    "mece": "hierarchy",
}


def _get_template_pattern(template_id: str) -> str:
    """Get the pattern group for a template."""
    return TEMPLATE_PATTERNS.get(template_id, "unknown")


# Template requirements: what structure fields boost each template's score
# Format: {template_id: [(field, min_count, score_boost, pattern_group), ...]}
TEMPLATE_REQUIREMENTS: Dict[str, List[Tuple[str, int, int, str]]] = {
    # === PROS/CONS & COMPARISON ===
    "pros_cons": [
        ("pros", 2, 25, "two_col"),
        ("cons", 2, 25, "two_col"),
    ],
    "force_field": [
        ("tensions", 2, 22, "two_col"),
        ("pros", 2, 15, "two_col"),
        ("cons", 2, 15, "two_col"),
        ("gaps", 2, 12, "two_col"),
    ],
    "side_by_side": [
        ("comparisons", 1, 20, "two_col"),
        ("themes", 2, 8, "two_col"),
    ],
    
    # === RANKING & ORDERED ===
    "ranking": [
        ("rankings", 3, 25, "flow_v"),
        ("themes", 3, 10, "flow_v"),  # Themes can be ranked
    ],
    "funnel": [
        ("sequence", 3, 18, "flow_v"),
        ("numbers", 2, 12, "flow_v"),
    ],
    "recommendation_stack": [
        ("recommendations", 2, 25, "flow_v"),
        ("themes", 2, 8, "flow_v"),
    ],
    
    # === SEQUENTIAL / PROCESS ===
    "horizontal_steps": [
        ("sequence", 3, 22, "flow_h"),
        ("themes", 3, 8, "flow_h"),
    ],
    "process_flow": [
        ("sequence", 3, 20, "flow_h"),
        ("comparisons", 1, 8, "flow_h"),  # Decision points
    ],
    "cycle_loop": [
        ("sequence", 3, 18, "cycle"),
        ("relationships", 2, 10, "cycle"),
    ],
    "stages_progression": [
        ("sequence", 3, 20, "flow_h"),
        ("themes", 3, 8, "flow_h"),
    ],
    
    # === TEMPORAL ===
    "timeline": [
        ("dates_events", 3, 28, "timeline"),
        ("sequence", 2, 8, "timeline"),
    ],
    
    # === HIERARCHICAL / MINDMAP ===
    "mindmap": [
        ("themes", 2, 15, "mindmap"),
        ("subpoints", 1, 18, "mindmap"),  # Key differentiator: needs subpoints
        ("relationships", 2, 8, "mindmap"),
    ],
    "concept_map": [
        ("themes", 2, 12, "mindmap"),
        ("relationships", 3, 18, "mindmap"),
        ("subpoints", 1, 10, "mindmap"),
    ],
    "overview_map": [
        ("themes", 3, 15, "hub"),
        ("entities", 3, 12, "hub"),
    ],
    "overview": [
        ("themes", 2, 12, "hub"),
        ("entities", 2, 10, "hub"),
    ],
    "anatomy": [
        ("components", 3, 20, "hub"),
        ("themes", 2, 10, "hub"),
    ],
    "mece": [
        ("themes", 4, 15, "hierarchy"),
        ("components", 3, 12, "hierarchy"),
    ],
    
    # === HUB-SPOKE (simple themes, no subpoints needed) ===
    "key_takeaways": [
        ("themes", 2, 18, "hub"),
        ("insight", 1, 8, "hub"),  # Has a summary insight
    ],
    "key_stats": [
        ("numbers", 3, 22, "hub"),
        ("themes", 2, 8, "hub"),
    ],
    
    # === GRID / MATRIX ===
    "quadrant": [
        ("comparisons", 2, 18, "grid"),
        ("themes", 4, 12, "grid"),
    ],
    "heatmap": [
        ("themes", 4, 12, "grid"),
        ("numbers", 2, 10, "grid"),
    ],
    "stakeholder_map": [
        ("entities", 3, 18, "grid"),
        ("relationships", 2, 10, "grid"),
    ],
    
    # === SYSTEM / ARCHITECTURE ===
    "system_architecture": [
        ("components", 3, 20, "hierarchy"),
        ("relationships", 2, 12, "hierarchy"),
    ],
    "decision_tree": [
        ("comparisons", 2, 18, "hierarchy"),
        ("sequence", 2, 10, "hierarchy"),
    ],
    "argument": [
        ("themes", 2, 12, "hierarchy"),
        ("recommendations", 1, 10, "hierarchy"),
    ],
    
    # === CHARTS ===
    "trend_chart": [
        ("dates_events", 2, 15, "chart"),
        ("numbers", 3, 20, "chart"),
    ],
    "distribution": [
        ("numbers", 3, 18, "chart"),
        ("themes", 2, 8, "chart"),
    ],
    
    # === PERSUASION ===
    "exec_summary": [
        ("themes", 2, 12, "flow_h"),
        ("recommendations", 1, 15, "flow_h"),
        ("insight", 1, 10, "flow_h"),
    ],
    "call_to_action": [
        ("recommendations", 1, 20, "flow_h"),
        ("sequence", 2, 10, "flow_h"),
    ],
    "scope": [
        ("themes", 2, 10, "two_col"),
        ("comparisons", 1, 12, "two_col"),
    ],
    "spectrum": [
        ("rankings", 2, 15, "flow_h"),
        ("comparisons", 1, 12, "flow_h"),
    ],
}


def _count_structure_field(structure: Dict, field: str) -> int:
    """Count items in a structure field, handling nested subpoints."""
    if field == "subpoints":
        # Special handling: count if any theme has subpoints
        themes = structure.get("themes", [])
        subpoints = structure.get("subpoints", {})
        if isinstance(subpoints, dict) and subpoints:
            return sum(len(v) for v in subpoints.values() if isinstance(v, list))
        # Also check if themes themselves have nested structure
        for theme in themes:
            if isinstance(theme, dict) and theme.get("subpoints"):
                return 1  # Has subpoints
        return 0
    elif field == "insight":
        # Boolean-ish: does it have an insight/tagline?
        return 1 if structure.get("insight") or structure.get("tagline") else 0
    else:
        items = structure.get(field, [])
        if isinstance(items, list):
            return len(items)
        elif isinstance(items, dict):
            return len(items)
        return 0


def score_template(template_id: str, structure: Dict) -> TemplateScore:
    """Score a single template against the extracted structure."""
    requirements = TEMPLATE_REQUIREMENTS.get(template_id)
    if not requirements:
        return TemplateScore(template_id, 0.0, "unknown", [])
    
    total_score = 0.0
    reasons = []
    pattern = "hub"  # Default pattern
    
    for field, min_count, boost, pat in requirements:
        count = _count_structure_field(structure, field)
        pattern = pat  # Use last pattern (they should all be the same for a template)
        
        if count >= min_count:
            total_score += boost
            reasons.append(f"{field}={count}")
        elif count > 0:
            # Partial credit: 50% if has some but not enough
            partial = boost * 0.5 * (count / min_count)
            total_score += partial
            if partial > 2:
                reasons.append(f"{field}={count} (partial)")
    
    return TemplateScore(template_id, total_score, pattern, reasons)


def rank_templates(structure: Dict) -> List[TemplateScore]:
    """
    Rank all templates by fitness score for the given structure.
    
    Returns sorted list of TemplateScore, highest score first.
    """
    scores = []
    
    for template_id in TEMPLATE_REQUIREMENTS:
        score = score_template(template_id, structure)
        if score.score > 0:
            scores.append(score)
    
    # Sort by score descending
    scores.sort(key=lambda x: x.score, reverse=True)
    
    return scores


def can_render(template_id: str, structure: Dict) -> bool:
    """
    Check if structure has minimum data for this template to render properly.
    Prevents selecting templates that will produce empty/broken visuals.
    """
    requirements = MINIMUM_VIABLE.get(template_id, {"themes": 1})
    
    for field, min_count in requirements.items():
        count = _count_structure_field(structure, field)
        if count < min_count:
            return False
    return True


def select_primary_and_alternatives(
    structure: Dict,
    max_alternatives: int = 3
) -> Tuple[Optional[str], List[str]]:
    """
    Select primary template and alternatives based on structure scoring.
    
    Returns:
        (primary_template_id, [alternative_template_ids])
        
    Ensures pattern diversity in alternatives.
    Guarantees a working fallback if scoring fails.
    """
    ranked = rank_templates(structure)
    
    # Filter to only templates that can actually render with this structure
    viable_ranked = [r for r in ranked if can_render(r.template_id, structure)]
    
    if not viable_ranked:
        # Nothing scored well enough to render - use universal fallbacks
        print(f"[TemplateScorer] ⚠️ No viable templates, using universal fallbacks")
        # Find first fallback that can render
        for fallback_id in UNIVERSAL_FALLBACKS:
            if can_render(fallback_id, structure):
                remaining = [f for f in UNIVERSAL_FALLBACKS if f != fallback_id and can_render(f, structure)]
                return (fallback_id, remaining[:max_alternatives])
        # Absolute last resort: key_takeaways with whatever themes we have
        return ("key_takeaways", [])
    
    # Primary is the highest scoring VIABLE template
    primary = viable_ranked[0]
    
    # Alternatives: pick from viable templates with different patterns for diversity
    alternatives = []
    seen_patterns = {primary.pattern}
    
    for candidate in viable_ranked[1:]:
        if len(alternatives) >= max_alternatives:
            break
        
        # Skip if we already have this pattern
        if candidate.pattern in seen_patterns:
            continue
        
        # Skip if score is too low (less than 25% of primary)
        if candidate.score < primary.score * 0.25:
            continue
        
        alternatives.append(candidate.template_id)
        seen_patterns.add(candidate.pattern)
    
    # If we don't have enough diverse alternatives, try adding from fallbacks
    # But NEVER add same-pattern templates - better to have fewer than duplicates
    if len(alternatives) < max_alternatives:
        # Add from universal fallbacks if they're viable and different pattern
        for fallback_id in UNIVERSAL_FALLBACKS:
            if len(alternatives) >= max_alternatives:
                break
            if fallback_id == primary.template_id or fallback_id in alternatives:
                continue
            if can_render(fallback_id, structure):
                # Check pattern is different
                fallback_pattern = _get_template_pattern(fallback_id)
                if fallback_pattern not in seen_patterns:
                    alternatives.append(fallback_id)
                    seen_patterns.add(fallback_pattern)
    
    print(f"[TemplateScorer] Primary: {primary.template_id} (score={primary.score:.1f}, {primary.reasons})")
    print(f"[TemplateScorer] Alternatives: {alternatives}")
    
    return (primary.template_id, alternatives)
