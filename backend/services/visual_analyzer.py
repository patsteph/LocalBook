"""Visual Content Analyzer

Analyzes text to determine the best visual template based on content patterns.
This is the "brain" that routes content to the right visualization.

Based on Napkin AI's approach: detect content type → match to best template.
"""
import re
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple


class ContentPattern(Enum):
    """Detected content patterns that map to visual types."""
    NUMBERS_STATS = "numbers_stats"
    STEPS_SEQUENCE = "steps_sequence"
    COMPARISON = "comparison"
    HIERARCHY = "hierarchy"
    TEMPORAL = "temporal"
    CATEGORIES = "categories"
    PROS_CONS = "pros_cons"
    RECOMMENDATIONS = "recommendations"
    RELATIONSHIPS = "relationships"
    RANKING = "ranking"
    LAYOUT_COLUMNS = "layout_columns"  # Explicit layout instructions
    STAGES_PHASES = "stages_phases"    # Numbered stages or phases
    MIND_MAP_REQUEST = "mind_map_request"  # User explicitly wants mind map
    PROCESS_FLOW = "process_flow"      # User wants process/workflow diagram
    CONCEPT_OVERVIEW = "concept_overview"  # User wants overview/summary visual


@dataclass
class ContentAnalysis:
    """Result of content analysis."""
    detected_patterns: List[ContentPattern]
    confidence_scores: Dict[ContentPattern, float]
    suggested_templates: List[str]
    entities: List[str]
    numbers: List[str]
    has_temporal_data: bool
    has_comparison: bool
    has_hierarchy: bool
    content_type: str  # "explainer", "persuasive", "analytical", "overview"


class VisualAnalyzer:
    """Analyzes content to determine optimal visualization."""
    
    def __init__(self):
        # Pattern detection regexes
        self.patterns = {
            ContentPattern.NUMBERS_STATS: [
                r'\d+%',  # Percentages
                r'\$[\d,]+',  # Dollar amounts
                r'\b\d+[KMB]\b',  # K/M/B numbers
                r'\b\d{1,3}(,\d{3})+\b',  # Large numbers with commas
                r'increased by|decreased by|grew|declined',
            ],
            ContentPattern.STEPS_SEQUENCE: [
                r'(?:first|second|third|then|next|finally|step \d)',
                r'(?:^|\n)\s*\d+\.\s',  # Numbered lists
                r'(?:before|after|during)',
                r'process|procedure|workflow|flow',
            ],
            ContentPattern.COMPARISON: [
                r'(?:vs\.?|versus|compared to|unlike|whereas)',
                r'(?:better|worse|more|less|higher|lower) than',
                r'(?:similar|different|same|opposite)',
                r'(?:advantage|disadvantage)',
            ],
            ContentPattern.HIERARCHY: [
                r'(?:includes?|contains?|consists? of)',
                r'(?:types? of|kinds? of|categories? of)',
                r'(?:parent|child|sub-|under)',
                r'(?:level|tier|layer)',
            ],
            ContentPattern.TEMPORAL: [
                r'\b(?:19|20)\d{2}\b',  # Years
                r'(?:january|february|march|april|may|june|july|august|september|october|november|december)',
                r'(?:Q[1-4]|quarter)',
                r'(?:yesterday|today|tomorrow|last (?:week|month|year))',
                r'(?:timeline|history|evolution|over time)',
            ],
            ContentPattern.CATEGORIES: [
                r'(?:group|category|type|class|segment)',
                r'(?:divided into|classified as|sorted by)',
                r'(?:breakdown|distribution|composition)',
            ],
            ContentPattern.PROS_CONS: [
                r'(?:pros?|cons?|advantages?|disadvantages?)',
                r'(?:benefits?|drawbacks?|strengths?|weaknesses?)',
                r'(?:for and against|trade-?offs?)',
            ],
            ContentPattern.RECOMMENDATIONS: [
                r'(?:recommend|suggest|advise|propose)',
                r'(?:should|must|need to|ought to)',
                r'(?:action items?|next steps?|to-?do)',
                r'(?:priority|prioritize)',
            ],
            ContentPattern.RELATIONSHIPS: [
                r'(?:relates? to|connected to|linked to)',
                r'(?:causes?|effects?|impacts?|influences?)',
                r'(?:depends? on|leads? to|results? in)',
            ],
            ContentPattern.RANKING: [
                r'(?:top \d+|best|worst|most|least)',
                r'(?:ranked?|rating|score)',
                r'(?:#\d+|\b(?:1st|2nd|3rd|[4-9]th|10th)\b)',
            ],
            ContentPattern.LAYOUT_COLUMNS: [
                r'side[- ]by[- ]side',
                r'columns?',
                r'horizontal',
                r'left[- ]to[- ]right',
                r'in a row',
                r'equal size',
            ],
            ContentPattern.STAGES_PHASES: [
                r'(?:\d+|five|four|three|six|seven)\s+(?:stages?|phases?|steps?|levels?)',
                r'stage\s+(?:one|two|three|four|five|\d)',
                r'phase\s+(?:one|two|three|four|five|\d)',
                r'progression',
                r'evolution',
            ],
            ContentPattern.MIND_MAP_REQUEST: [
                r'mind\s*map',
                r'brain\s*storm',
                r'concept\s*map',
                r'idea\s*map',
                r'visual\s*summary',
                r'visual\s*overview',
            ],
            ContentPattern.PROCESS_FLOW: [
                r'process\s*flow',
                r'work\s*flow',
                r'flow\s*chart',
                r'flow\s*diagram',
                r'decision\s*tree',
                r'how\s+(?:to|it|they)\s+work',
            ],
            ContentPattern.CONCEPT_OVERVIEW: [
                r'overview',
                r'summary\s*(?:visual|diagram|chart)?',
                r'main\s+(?:concepts?|ideas?|points?)',
                r'key\s+(?:concepts?|ideas?|points?|takeaways?)',
                r'big\s+picture',
            ],
        }
        
        # Template mapping based on patterns - prioritize simple, clean visualizations
        self.pattern_to_templates = {
            ContentPattern.NUMBERS_STATS: ["key_stats", "distribution", "trend_chart"],
            ContentPattern.STEPS_SEQUENCE: ["horizontal_steps", "timeline", "cycle_loop"],  # horizontal_steps first for simple flows
            ContentPattern.COMPARISON: ["side_by_side", "quadrant", "pros_cons"],
            ContentPattern.HIERARCHY: ["overview_map", "anatomy", "concept_map"],
            ContentPattern.TEMPORAL: ["timeline", "trend_chart", "horizontal_steps"],
            ContentPattern.CATEGORIES: ["distribution", "overview_map", "ranking"],
            ContentPattern.PROS_CONS: ["pros_cons", "side_by_side", "force_field"],
            ContentPattern.RECOMMENDATIONS: ["key_takeaways", "recommendation_stack", "call_to_action"],
            ContentPattern.RELATIONSHIPS: ["concept_map", "overview_map", "causal_loop"],
            ContentPattern.RANKING: ["ranking", "funnel", "spectrum"],
            ContentPattern.LAYOUT_COLUMNS: ["side_by_side", "horizontal_steps", "stages_progression"],
            ContentPattern.STAGES_PHASES: ["stages_progression", "side_by_side", "horizontal_steps"],
            ContentPattern.MIND_MAP_REQUEST: ["mindmap", "concept_map", "overview_map"],
            ContentPattern.PROCESS_FLOW: ["horizontal_steps", "decision_tree", "cycle_loop"],
            ContentPattern.CONCEPT_OVERVIEW: ["overview_map", "mindmap", "concept_map"],
        }
    
    def analyze(self, text: str) -> ContentAnalysis:
        """Analyze text content and determine best visualization approach."""
        text_lower = text.lower()
        
        # Detect patterns and calculate confidence
        detected = []
        scores = {}
        
        for pattern, regexes in self.patterns.items():
            match_count = 0
            for regex in regexes:
                matches = re.findall(regex, text_lower, re.IGNORECASE | re.MULTILINE)
                match_count += len(matches)
            
            # Normalize score (0-1) based on text length
            text_words = len(text.split())
            confidence = min(1.0, match_count / max(1, text_words / 50))
            
            if confidence > 0.1:
                detected.append(pattern)
                scores[pattern] = confidence
        
        # Sort by confidence
        detected.sort(key=lambda p: scores.get(p, 0), reverse=True)
        
        # PRIORITY: If explicit visual requests detected, move them to front
        # User's explicit requests should override content-based selection
        explicit_request_patterns = [
            ContentPattern.LAYOUT_COLUMNS, 
            ContentPattern.STAGES_PHASES,
            ContentPattern.MIND_MAP_REQUEST,
            ContentPattern.PROCESS_FLOW,
            ContentPattern.CONCEPT_OVERVIEW,
        ]
        for explicit_pattern in explicit_request_patterns:
            if explicit_pattern in detected:
                detected.remove(explicit_pattern)
                detected.insert(0, explicit_pattern)
                # Boost confidence for explicit requests
                scores[explicit_pattern] = max(scores.get(explicit_pattern, 0), 0.9)
        
        # Get suggested templates based on top patterns
        suggested = []
        seen = set()
        for pattern in detected[:3]:  # Top 3 patterns
            for template in self.pattern_to_templates.get(pattern, []):
                if template not in seen:
                    suggested.append(template)
                    seen.add(template)
                if len(suggested) >= 5:
                    break
        
        # Extract entities (simple approach - capitalized words)
        entities = list(set(re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', text)))[:10]
        
        # Extract numbers
        numbers = list(set(re.findall(r'\b\d+(?:\.\d+)?%?|\$[\d,]+(?:\.\d+)?', text)))[:10]
        
        # Determine content type
        content_type = self._determine_content_type(detected, scores)
        
        return ContentAnalysis(
            detected_patterns=detected,
            confidence_scores=scores,
            suggested_templates=suggested[:5],
            entities=entities,
            numbers=numbers,
            has_temporal_data=ContentPattern.TEMPORAL in detected,
            has_comparison=ContentPattern.COMPARISON in detected,
            has_hierarchy=ContentPattern.HIERARCHY in detected,
            content_type=content_type,
        )
    
    def _determine_content_type(
        self, 
        patterns: List[ContentPattern], 
        scores: Dict[ContentPattern, float]
    ) -> str:
        """Determine the overall content type based on detected patterns."""
        
        explainer_patterns = {ContentPattern.STEPS_SEQUENCE, ContentPattern.HIERARCHY, ContentPattern.RELATIONSHIPS}
        persuasive_patterns = {ContentPattern.RECOMMENDATIONS, ContentPattern.PROS_CONS}
        analytical_patterns = {ContentPattern.NUMBERS_STATS, ContentPattern.COMPARISON, ContentPattern.RANKING}
        
        type_scores = {
            "explainer": sum(scores.get(p, 0) for p in explainer_patterns),
            "persuasive": sum(scores.get(p, 0) for p in persuasive_patterns),
            "analytical": sum(scores.get(p, 0) for p in analytical_patterns),
            "overview": 0.3,  # Default fallback
        }
        
        return max(type_scores, key=type_scores.get)
    
    def get_best_template(self, text: str) -> Tuple[str, float]:
        """Get the single best template for the content.
        
        Returns: (template_name, confidence)
        """
        analysis = self.analyze(text)
        
        if analysis.suggested_templates:
            best = analysis.suggested_templates[0]
            # Calculate combined confidence from top patterns
            top_confidence = max(analysis.confidence_scores.values()) if analysis.confidence_scores else 0.3
            return best, top_confidence
        
        # Fallback
        return "concept_map", 0.3
    
    async def analyze_with_llm(self, text: str) -> dict:
        """Use LLM to semantically understand content and suggest the best visualization.
        
        This is for when regex patterns don't detect enough - we use AI to "understand"
        what the user is describing and suggest the most appropriate visual.
        
        Returns: dict with visual_type, reasoning, and suggested_template
        """
        import httpx
        from config import settings
        
        prompt = f"""Analyze this content and determine the BEST way to visualize it.

Content:
{text[:1500]}

What is this content describing? Choose the BEST visualization type:

1. PROGRESSION - Sequential stages, phases, evolution, journey (use horizontal flowchart)
2. HIERARCHY - Categories, types, parent-child relationships (use mindmap)
3. PROCESS - How something works, steps to complete (use flowchart)
4. COMPARISON - Differences between things, pros/cons (use side-by-side or quadrant)
5. TIMELINE - Events over time, history (use timeline)
6. DISTRIBUTION - Parts of a whole, percentages (use pie chart)
7. RELATIONSHIPS - How things connect, cause/effect (use concept map)
8. LIST - Simple enumeration of items (use mindmap with items as branches)

Respond with ONLY a JSON object:
{{"visual_type": "PROGRESSION|HIERARCHY|PROCESS|COMPARISON|TIMELINE|DISTRIBUTION|RELATIONSHIPS|LIST", "key_items": ["item1", "item2", ...], "title": "suggested title"}}"""

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{settings.ollama_base_url}/api/generate",
                    json={
                        "model": settings.ollama_model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"num_predict": 200, "temperature": 0}
                    }
                )
                result = response.json().get("response", "{}")
                
                # Parse JSON from response
                import json
                import re
                json_match = re.search(r'\{[^}]+\}', result, re.DOTALL)
                if json_match:
                    parsed = json.loads(json_match.group())
                    
                    # Map visual_type to template
                    type_to_template = {
                        "PROGRESSION": "stages_progression",
                        "HIERARCHY": "mindmap", 
                        "PROCESS": "horizontal_steps",
                        "COMPARISON": "side_by_side",
                        "TIMELINE": "timeline",
                        "DISTRIBUTION": "pie",
                        "RELATIONSHIPS": "concept_map",
                        "LIST": "mindmap",
                    }
                    
                    visual_type = parsed.get("visual_type", "HIERARCHY")
                    return {
                        "visual_type": visual_type,
                        "suggested_template": type_to_template.get(visual_type, "mindmap"),
                        "key_items": parsed.get("key_items", []),
                        "title": parsed.get("title", ""),
                    }
        except Exception as e:
            print(f"[VisualAnalyzer] LLM analysis failed: {e}")
        
        # Fallback
        return {
            "visual_type": "HIERARCHY",
            "suggested_template": "mindmap",
            "key_items": [],
            "title": "",
        }
    
    def get_template_recommendations(self, text: str, max_count: int = 3) -> List[Tuple[str, float, str]]:
        """Get ranked template recommendations with explanations.
        
        Returns: List of (template_name, confidence, reason)
        """
        analysis = self.analyze(text)
        
        recommendations = []
        for template in analysis.suggested_templates[:max_count]:
            # Find which pattern(s) led to this recommendation
            reasons = []
            for pattern, templates in self.pattern_to_templates.items():
                if template in templates and pattern in analysis.detected_patterns:
                    confidence = analysis.confidence_scores.get(pattern, 0)
                    reasons.append((pattern.value.replace("_", " "), confidence))
            
            if reasons:
                best_reason = max(reasons, key=lambda x: x[1])
                recommendations.append((
                    template,
                    best_reason[1],
                    f"Content contains {best_reason[0]}"
                ))
        
        return recommendations


# Singleton instance
visual_analyzer = VisualAnalyzer()
