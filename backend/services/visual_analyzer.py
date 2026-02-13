"""Visual Content Analyzer

Analyzes text to determine the best visual template based on content patterns.
This is the "brain" that routes content to the right visualization.

Based on Napkin AI's approach: detect content type → match to best template.
"""
import re
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Tuple


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
        """Napkin-style two-stage content analysis.
        
        Stage 1: Extract what's IN the content (themes, entities, relationships, sequences, dates)
        Stage 2: Pick visual types based on what was actually found
        
        This is smarter than asking "what type?" - we first understand the content structure.
        
        Returns: dict with visual_type, reasoning, and suggested_template
        """
        import httpx
        from config import settings
        
        # Stage 1: Extract content structure (what's actually in the text)
        # SIMPLIFIED extraction - focus on getting the main sections/themes right
        
        # ROBUST SECTION DETECTION - comprehensive patterns for any format
        detected_sections = []
        detection_method = None
        
        # All patterns to try - order matters (more specific first)
        patterns = [
            # Roman numerals: I. Title, II. Title
            (r'^[IVX]+\.\s*(.+?)(?:\n|$)', "roman-numeral"),
            # Bold numbered: **1. Title**
            (r'\*\*\d+\.\s*([^*\n]+)\*\*', "numbered-bold"),
            # Numbered at line start: 1. Title or 1. **Title**
            (r'^\d+\.\s*\*?\*?([^*\n]+)', "numbered-plain"),
            # Letter lists: A. Title, B. Title or a) Title, b) Title
            (r'^[A-Za-z][.)]\s*\*?\*?([^*\n]+)', "letter-list"),
            # Emoji bullets: 🔹 Title, ✅ Title, 📌 Title (common LLM patterns)
            (r'^[\U0001F300-\U0001F9FF\u2600-\u26FF\u2700-\u27BF]+\s*\*?\*?(.+?)(?:\n|$)', "emoji-bullet"),
            # Bold bullets: - **Title** or * **Title**
            (r'^[-*]\s*\*\*([^*\n]+)\*\*', "bullet-bold"),
            # Plain bullets with content: - Title (at least 10 chars)
            (r'^[-*•]\s+([A-Z][^*\n]{10,})', "bullet-plain"),
            # Markdown headers: ### Title or ## Title
            (r'^#{2,4}\s*(.+)$', "headers"),
        ]
        
        for pattern, method in patterns:
            detected_sections = re.findall(pattern, text, re.MULTILINE)
            if detected_sections and len(detected_sections) >= 2:
                detection_method = method
                break
            detected_sections = []  # Reset for next pattern
        
        # Clean up extracted sections
        detected_sections = [s.strip().rstrip('*').strip() for s in detected_sections if s.strip() and len(s.strip()) > 3]
        
        if detected_sections:
            print(f"[Visual Extraction] ✅ DETECTED {len(detected_sections)} SECTIONS via {detection_method}: {detected_sections[:6]}")
        else:
            print(f"[Visual Extraction] ⚠️ No structured sections found in text ({len(text)} chars)")
        
        # Keep reference for fallback
        numbered_sections = detected_sections
        
        extraction_prompt = f"""Extract the main themes and data from this content. Return ONLY valid JSON.

Content:
{text[:3000]}

Return JSON:
{{
  "title": "3-5 word title",
  "themes": ["point 1", "point 2", "point 3", "...all points found..."],
  "numbers": ["91.5", "14.2", "...numeric values found..."],
  "metrics": ["Revenue: $91.5B", "Margin: 14.2%", "...labeled metrics..."],
  "pros": ["positive 1", "positive 2", "..."],
  "cons": ["challenge 1", "challenge 2", "..."],
  "recommendations": ["action 1", "action 2", "..."]
}}

RULES:
- themes: Extract ALL main points from the content - NO LIMIT. If numbered sections exist (1. xxx, 2. yyy), use ALL of them.
- numbers: Extract key numeric values (revenue figures, percentages, counts, etc.)
- metrics: Extract labeled metrics as "Label: Value" pairs (e.g. "Revenue: $91.5B", "Growth: 8.2%")
- Each theme MUST be 4-6 words MAX (short noun phrases only!)
- NO citation markers like [1] or [2]
- NO incomplete sentences - every theme must be a complete phrase
- Return ONLY the JSON, nothing else"""

        try:
            import json
            import re
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Stage 1: Extract content structure using FAST model for speed
                # This runs in background after query - speed is critical
                response = await client.post(
                    f"{settings.ollama_base_url}/api/generate",
                    json={
                        "model": settings.ollama_fast_model,  # Use fast model (phi4-mini)
                        "prompt": extraction_prompt,
                        "stream": False,
                        "options": {"num_predict": 1200, "temperature": 0}  # More tokens for deeper extraction
                    }
                )
                result = response.json().get("response", "{}")
                print(f"[Visual Extraction] Raw LLM response ({len(result)} chars): {result[:500]}...")
                
                # Parse the structure extraction with robust JSON handling
                json_match = re.search(r'\{[\s\S]*\}', result)
                if not json_match:
                    raise ValueError("No JSON found in extraction response")
                
                json_str = json_match.group()
                # Fix common LLM JSON errors: trailing commas, single quotes
                json_str = re.sub(r',\s*}', '}', json_str)
                json_str = re.sub(r',\s*]', ']', json_str)
                
                try:
                    structure = json.loads(json_str)
                except json.JSONDecodeError:
                    # Try to extract at least themes from malformed JSON
                    themes_match = re.findall(r'"themes"\s*:\s*\[(.*?)\]', json_str, re.DOTALL)
                    if themes_match:
                        theme_items = re.findall(r'"([^"]+)"', themes_match[0])
                        structure = {"themes": theme_items}
                    else:
                        structure = {}
                
                # CRITICAL: If we detected numbered sections in the content, ALWAYS use them
                # They are more reliable than LLM extraction
                if numbered_sections and len(numbered_sections) >= 2:
                    print("[Visual Extraction] ✅ USING DETECTED SECTIONS (more reliable than LLM)")
                    structure["themes"] = numbered_sections[:8]
                    print(f"[Visual Extraction] Themes from sections: {structure['themes']}")
                    # Generate a better title from the content if LLM didn't provide one
                    if not structure.get("title") or len(structure.get("title", "")) < 5:
                        # Try to extract title from first line or use generic
                        first_line = text.split('\n')[0][:50].strip()
                        if first_line and not first_line.startswith('*'):
                            structure["title"] = first_line
                        else:
                            structure["title"] = "Key Themes"
                elif len(structure.get("themes", [])) < 3:
                    # LLM also failed - log this
                    print(f"[Visual Extraction] ⚠️ NO numbered sections AND LLM returned only {len(structure.get('themes', []))} themes")
                    # Last resort: extract first few sentences as themes
                    sentences = re.split(r'[.!?]\s+', text[:1000])
                    fallback_themes = [s.strip()[:60] for s in sentences[:4] if len(s.strip()) > 20]
                    if fallback_themes:
                        structure["themes"] = fallback_themes
                        print(f"[Visual Extraction] 🔄 Using sentence fallback: {fallback_themes}")
                
                # DEBUG: Show full extracted structure
                print("[Visual Extraction] PARSED STRUCTURE:")
                print(f"  themes ({len(structure.get('themes', []))}): {structure.get('themes', [])}")
                print(f"  tensions ({len(structure.get('tensions', []))}): {structure.get('tensions', [])}")
                print(f"  gaps ({len(structure.get('gaps', []))}): {structure.get('gaps', [])}")
                print(f"  relationships ({len(structure.get('relationships', []))}): {structure.get('relationships', [])}")
                print(f"  pros ({len(structure.get('pros', []))}): {structure.get('pros', [])}")
                print(f"  cons ({len(structure.get('cons', []))}): {structure.get('cons', [])}")
                print(f"  recommendations ({len(structure.get('recommendations', []))}): {structure.get('recommendations', [])}")
                
                # Stage 2: Pick visual type based on what we found
                themes = structure.get("themes", [])
                entities = structure.get("entities", [])
                relationships = structure.get("relationships", [])
                tensions = structure.get("tensions", [])
                gaps = structure.get("gaps", [])
                sequence = structure.get("sequence", [])
                dates_events = structure.get("dates_events", [])
                comparisons = structure.get("comparisons", [])
                numbers = structure.get("numbers", [])
                pros = structure.get("pros", [])
                cons = structure.get("cons", [])
                recommendations = structure.get("recommendations", [])
                components = structure.get("components", [])
                rankings = structure.get("rankings", [])
                
                # Decision logic based on extracted structure
                # Priority order matches template specificity (most specific first)
                visual_type = "THEMES"  # Default
                suggested_template = "key_takeaways"
                
                # === HIGH SPECIFICITY MATCHES ===
                # NEW: Tensions/gaps indicate narrative arc - use force field or gap analysis
                if len(tensions) >= 2 or (len(gaps) >= 2 and len(themes) >= 3):
                    visual_type = "TENSION_GAP"
                    suggested_template = "force_field"  # Shows opposing forces
                elif len(pros) >= 2 and len(cons) >= 2:
                    # Explicit pros/cons found
                    visual_type = "PROS_CONS"
                    suggested_template = "pros_cons"
                elif len(rankings) >= 3:
                    # Ranked/ordered items
                    visual_type = "RANKING"
                    suggested_template = "ranking"
                elif len(recommendations) >= 2:
                    # Action items / recommendations
                    visual_type = "RECOMMENDATIONS"
                    suggested_template = "recommendation_stack"
                elif len(dates_events) >= 3:
                    # Timeline with actual dated events
                    visual_type = "TIMELINE"
                    suggested_template = "timeline"
                elif len(comparisons) >= 1:
                    # Explicit A vs B comparisons
                    visual_type = "COMPARISON"
                    suggested_template = "side_by_side"
                elif len(sequence) >= 3:
                    # Process/progression with steps
                    visual_type = "PROGRESSION"
                    suggested_template = "horizontal_steps"
                elif len(components) >= 3:
                    # System/concept breakdown
                    visual_type = "ANATOMY"
                    suggested_template = "anatomy"
                elif len(relationships) >= 3:
                    # Multiple relationships - concept map
                    visual_type = "RELATIONSHIPS"
                    suggested_template = "concept_map"
                elif len(numbers) >= 3:
                    # Stats/metrics - key stats or distribution
                    visual_type = "STATS"
                    suggested_template = "key_stats"
                elif len(themes) >= 2:
                    # Themes found - choose template based on COUNT for visual quality
                    visual_type = "THEMES"
                    if len(themes) >= 7:
                        # 7+ themes: exec_summary (two-column) shows all, hub-spoke truncates
                        suggested_template = "exec_summary"
                    elif len(themes) >= 5:
                        # 5-6 themes: hub-spoke works well
                        suggested_template = "key_takeaways"
                    else:
                        # 2-4 themes: hub-spoke or MECE both work
                        suggested_template = "key_takeaways"
                elif len(entities) >= 3:
                    # Entities found - overview map
                    visual_type = "OVERVIEW"
                    suggested_template = "overview_map"
                
                # Phase 4: Detect multiple visual types for multi-visual generation
                secondary_types = []
                has_multiple = False
                
                # Check for secondary structures
                if visual_type != "THEMES" and len(themes) >= 2:
                    secondary_types.append("THEMES")
                if visual_type != "TIMELINE" and len(dates_events) >= 3:
                    secondary_types.append("TIMELINE")
                if visual_type != "RELATIONSHIPS" and len(relationships) >= 2:
                    secondary_types.append("RELATIONSHIPS")
                if visual_type != "PROGRESSION" and len(sequence) >= 3:
                    secondary_types.append("PROGRESSION")
                
                has_multiple = len(secondary_types) > 0
                
                print(f"[VisualAnalyzer] Napkin-style analysis: themes={len(themes)}, entities={len(entities)}, "
                      f"relationships={len(relationships)}, sequence={len(sequence)}, dates={len(dates_events)} "
                      f"-> {visual_type} ({suggested_template})" + 
                      (f" + {secondary_types}" if secondary_types else ""))
                
                # Use extracted title from LLM, fallback to generic
                extracted_title = structure.get("title", "")
                if not extracted_title or len(extracted_title) > 40:
                    # Fallback: create short title from first theme
                    extracted_title = themes[0][:30] if themes else "Key Insights"
                
                print(f"[VisualAnalyzer] Using title: {extracted_title}")
                
                return {
                    "visual_type": visual_type,
                    "suggested_template": suggested_template,
                    "key_items": themes[:5] or entities[:5] or sequence[:5],
                    "title": extracted_title,
                    "structure": structure,  # Include raw structure for debugging
                    "secondary_types": secondary_types,
                    "has_multiple_structures": has_multiple,
                }
                
        except Exception as e:
            print(f"[VisualAnalyzer] LLM analysis failed: {e}")
            import traceback
            traceback.print_exc()
        
        # Fallback - default to themes/mindmap for overview content
        return {
            "visual_type": "THEMES",
            "suggested_template": "key_takeaways",
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
    
    def _detect_metrics_fast(self, text: str) -> dict:
        """Fast regex detection of numeric/metric content (~1ms).
        
        Returns dict with:
          - is_metric_heavy: bool — True if content is primarily numbers-driven
          - metrics: list of {"label": str, "value": float, "display": str}
          - metric_type: "bar_chart" | "pie_chart" | "key_stats" | None
        """
        metrics = []
        
        # Pattern 1: "Label: $X.XB" or "Label: X%" or "Label: X,XXX" 
        # Handles: "Revenue: $91.5 billion", "Net Income: $9.1B", "Operating Margin: 14.2%"
        labeled_patterns = [
            # "Label: $91.5 billion/million/B/M"
            r'([A-Z][A-Za-z\s/&]+?)[\s:]+\$?([\d,.]+)\s*(billion|million|trillion|B|M|T|bn|mn)\b',
            # "Label: XX.X%" or "Label: XX%"
            r'([A-Z][A-Za-z\s/&]+?)[\s:]+\$?([\d,.]+)\s*%',
            # "Label: $X,XXX" or "Label: $X.X" (plain dollar amounts)
            r'([A-Z][A-Za-z\s/&]+?)[\s:]+\$([\d,.]+)(?:\s|$|,|\.|;)',
        ]
        
        for pattern in labeled_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                label = match[0].strip().rstrip(':- ')
                # Skip labels that are too long (likely sentences, not metric names)
                if len(label) > 40 or len(label) < 3:
                    continue
                try:
                    raw_val = match[1].replace(',', '')
                    value = float(raw_val)
                    # Build display string
                    if len(match) > 2 and match[2]:  # Has unit (billion/million/%)
                        unit = match[2].strip()
                        if unit in ('billion', 'B', 'bn'):
                            display = f"${raw_val}B"
                        elif unit in ('million', 'M', 'mn'):
                            display = f"${raw_val}M"
                        elif unit in ('trillion', 'T'):
                            display = f"${raw_val}T"
                        else:
                            display = f"{raw_val}%"
                    elif '$' in text[max(0, text.find(match[1])-5):text.find(match[1])]:
                        display = f"${raw_val}"
                    else:
                        display = raw_val
                    
                    # Deduplicate by label
                    if not any(m["label"].lower() == label.lower() for m in metrics):
                        metrics.append({"label": label, "value": value, "display": display})
                except (ValueError, IndexError):
                    continue
        
        # Pattern 2: Count raw number occurrences (fallback signal)
        raw_numbers = re.findall(r'[\$]?[\d,]+\.?\d*\s*(?:billion|million|%|B|M|bps|basis points)?', text)
        number_density = len(raw_numbers) / max(len(text.split()), 1)  # numbers per word
        
        is_metric_heavy = len(metrics) >= 3 or (len(metrics) >= 2 and number_density > 0.04)
        
        # Determine best chart type
        metric_type = None
        if is_metric_heavy:
            # Check if values look like percentages that sum to ~100 (pie chart)
            pct_metrics = [m for m in metrics if '%' in m.get("display", "")]
            if len(pct_metrics) >= 3:
                total = sum(m["value"] for m in pct_metrics)
                if 80 <= total <= 120:
                    metric_type = "distribution"  # pie chart
                else:
                    metric_type = "trend_chart"  # bar chart
            elif len(metrics) >= 3:
                metric_type = "trend_chart"  # bar chart for labeled values
            else:
                metric_type = "key_stats"  # hub-spoke with stat values
        
        return {
            "is_metric_heavy": is_metric_heavy,
            "metrics": metrics[:8],
            "metric_type": metric_type,
            "number_density": number_density,
        }
    
    async def pre_classify_fast(
        self, 
        notebook_id: str, 
        query: str, 
        answer: str
    ) -> None:
        """FAST pre-classification using regex extraction (~2ms).
        
        Called INLINE after RAG stream completes. Guarantees cache is ready
        before user can click "Create Visual".
        
        Uses regex extraction (instant) instead of LLM analysis (slow).
        Detects both theme-based AND metric-based content.
        """
        from services.visual_cache import visual_cache, VisualClassification
        from services.theme_extractor import (
            extract_themes_regex, extract_title_from_count, is_valid_extraction,
            extract_subpoints_for_themes
        )
        
        try:
            import time
            start = time.time()
            
            # Strip citation markers [1], [2], etc
            clean_answer = re.sub(r'\[\d+\]', '', answer)
            clean_answer = re.sub(r'[ \t]+', ' ', clean_answer)
            clean_answer = re.sub(r'\n\s*\n', '\n\n', clean_answer)
            
            # Detect metric/number-heavy content FIRST
            metric_result = self._detect_metrics_fast(clean_answer)
            
            # FAST regex extraction (~2ms)
            themes = extract_themes_regex(clean_answer)
            title = extract_title_from_count(clean_answer) or "Key Themes"
            
            # Extract subpoints for each theme (adds depth for mindmap/hub-spoke)
            subpoints = extract_subpoints_for_themes(clean_answer, themes)
            
            # Extract insight/summary sentence from last paragraph - keep SHORT for visual display
            insight = None
            last_para = clean_answer.split('\n\n')[-1] if '\n\n' in clean_answer else clean_answer[-500:]
            insight_match = re.search(r'((?:These|This|Overall|In summary|Together|Collectively)[^.]{20,}\.)', last_para, re.IGNORECASE)
            if insight_match:
                raw_insight = re.sub(r'\[\d+\]', '', insight_match.group(1)).strip()
                # Smart truncate to 60 chars (fits one line) at word boundary, add ellipsis
                if len(raw_insight) > 60:
                    insight = raw_insight[:60].rsplit(' ', 1)[0].rstrip('.,;:') + '...'
                else:
                    insight = raw_insight
            
            elapsed_ms = (time.time() - start) * 1000
            
            # METRIC-HEAVY CONTENT: route to chart/stats templates
            if metric_result["is_metric_heavy"]:
                metrics = metric_result["metrics"]
                metric_type = metric_result["metric_type"]
                
                # Build structure with extracted numbers for chart templates
                metric_labels = [m["label"] for m in metrics]
                metric_values = [m["value"] for m in metrics]
                metric_displays = [f'{m["label"]}: {m["display"]}' for m in metrics]
                
                # Use query as title hint
                query_lower = query.lower()
                if not title or title == "Key Themes":
                    # Derive title from query
                    title = query[:50] if len(query) <= 50 else query[:47] + "..."
                
                visual_type = "STATS"
                suggested_template = metric_type or "trend_chart"
                
                # Also keep themes as secondary option
                secondary = ["key_takeaways", "key_stats"]
                if metric_type != "trend_chart":
                    secondary.append("trend_chart")
                if metric_type != "distribution":
                    secondary.append("distribution")
                
                classification = VisualClassification(
                    query=query,
                    answer_preview=answer[:500],
                    visual_type=visual_type,
                    suggested_template=suggested_template,
                    key_items=metric_displays,
                    title=title,
                    structure={
                        "themes": metric_displays,  # Use metric displays as theme labels for hub-spoke fallback
                        "numbers": metric_values,
                        "dates_events": metric_labels,  # Labels for chart X-axis
                        "title": title,
                        "subpoints": subpoints,
                        "insight": insight,
                        "metrics": metrics,  # Raw metric data for chart builders
                    },
                    notebook_id=notebook_id,
                    secondary_types=secondary,
                    has_multiple_structures=True,
                )
                await visual_cache.set(classification)
                print(f"[VisualAnalyzer] ⚡ FAST pre-cache ({elapsed_ms:.0f}ms): METRICS detected — "
                      f"{len(metrics)} metrics, type={metric_type} -> {suggested_template}")
                return
            
            # THEME-BASED CONTENT: standard path
            if is_valid_extraction(themes):
                # Template selection based on ITEM COUNT for visual quality
                has_good_subpoints = subpoints and len(subpoints) >= 2 and any(len(subs) >= 2 for subs in subpoints.values())
                
                if len(themes) >= 7:
                    # 7+ themes: exec_summary (two-column) or mindmap shows all
                    suggested_template = "mindmap" if has_good_subpoints else "exec_summary"
                elif has_good_subpoints:
                    suggested_template = "mindmap"
                else:
                    suggested_template = "key_takeaways"
                
                # Cache the regex-extracted themes WITH subpoints and insight
                classification = VisualClassification(
                    query=query,
                    answer_preview=answer[:500],
                    visual_type="THEMES",
                    suggested_template=suggested_template,
                    key_items=themes,
                    title=title,
                    structure={
                        "themes": themes, 
                        "title": title,
                        "subpoints": subpoints,
                        "insight": insight,
                    },
                    notebook_id=notebook_id,
                    secondary_types=["key_takeaways", "ranking"] if suggested_template == "mindmap" else ["mindmap", "ranking"],
                    has_multiple_structures=bool(subpoints),
                )
                await visual_cache.set(classification)
                print(f"[VisualAnalyzer] ⚡ FAST pre-cache ({elapsed_ms:.0f}ms): {len(themes)} themes, {len(subpoints)} with subpoints -> {suggested_template}")
            else:
                print("[VisualAnalyzer] ⚠️ Regex extraction invalid, skipping cache")
            
        except Exception as e:
            print(f"[VisualAnalyzer] Fast pre-classification failed: {e}")

    async def pre_classify_for_cache(
        self, 
        notebook_id: str, 
        query: str, 
        answer: str
    ) -> None:
        """Pre-classify content and store in cache for instant visual generation.
        
        Called in background after RAG query completes. When user clicks 
        "Create Visual", the classification is already done.
        """
        from services.visual_cache import visual_cache, VisualClassification
        
        try:
            # CRITICAL: Strip citation markers [1], [2], etc BEFORE extraction
            # These leak into visual labels and look terrible
            clean_answer = re.sub(r'\[\d+\]', '', answer)
            # Collapse multiple spaces but PRESERVE newlines (needed for section detection)
            clean_answer = re.sub(r'[ \t]+', ' ', clean_answer)  # Only horizontal whitespace
            clean_answer = re.sub(r'\n\s*\n', '\n\n', clean_answer)  # Normalize paragraph breaks
            
            # DEBUG: Show what answer we're extracting from
            print(f"[VisualAnalyzer] PRE-CLASSIFY INPUT ({len(clean_answer)} chars):")
            print(f"  Query: {query}")
            print(f"  Answer preview: {clean_answer[:500]}...")
            
            # Run the Napkin-style analysis
            result = await self.analyze_with_llm(clean_answer)
            
            # Store in cache with multi-visual support
            classification = VisualClassification(
                query=query,
                answer_preview=answer[:500],
                visual_type=result.get("visual_type", "THEMES"),
                suggested_template=result.get("suggested_template", "key_takeaways"),
                key_items=result.get("key_items", []),
                title=result.get("title", ""),
                structure=result.get("structure", {}),
                notebook_id=notebook_id,
                secondary_types=result.get("secondary_types", []),
                has_multiple_structures=result.get("has_multiple_structures", False),
            )
            await visual_cache.set(classification)
            
            print(f"[VisualAnalyzer] Pre-cached classification for notebook {notebook_id}: "
                  f"{classification.visual_type} -> {classification.suggested_template}")
            
        except Exception as e:
            print(f"[VisualAnalyzer] Pre-classification failed (non-blocking): {e}")


# Singleton instance
visual_analyzer = VisualAnalyzer()
