"""Structured LLM Service using Pydantic AI

Provides type-safe, validated LLM outputs for features like:
- Quiz generation
- Visual summaries (Mermaid diagrams)
- Timeline extraction
- Document comparison
- Writing assistance

Uses Ollama as the backend with automatic retry on validation failures.
Uses professional-grade templates from output_templates.py for quality.
"""
import asyncio
import re
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
import httpx

from config import settings
from services.output_templates import VISUAL_TEMPLATES, build_visual_prompt
from services.svg_templates import build_svg_visual, COLOR_THEMES as SVG_COLOR_THEMES


# =============================================================================
# Output Models for Structured Generation
# =============================================================================

class QuizQuestion(BaseModel):
    """A single quiz question with answer and explanation."""
    question: str = Field(description="The question text")
    answer: str = Field(description="The correct answer")
    explanation: str = Field(description="Why this answer is correct")
    difficulty: str = Field(default="medium", description="easy, medium, or hard")
    question_type: str = Field(default="multiple_choice", description="multiple_choice or true_false")
    options: Optional[List[str]] = Field(default=None, description="Answer options - required for multiple_choice, ['True', 'False'] for true_false")
    source_reference: Optional[str] = Field(default=None, description="Name of the source document this question is from")


class QuizOutput(BaseModel):
    """Complete quiz output with multiple questions."""
    questions: List[QuizQuestion] = Field(description="List of quiz questions")
    topic: str = Field(description="The main topic of the quiz")
    source_summary: str = Field(description="Brief summary of source material used")


class MermaidDiagram(BaseModel):
    """Mermaid diagram output."""
    diagram_type: str = Field(description="flowchart, mindmap, timeline, sequenceDiagram, etc.")
    code: str = Field(description="Valid Mermaid diagram code")
    title: str = Field(description="Title for the diagram")
    description: str = Field(description="Brief description of what the diagram shows")


class VisualSummary(BaseModel):
    """Visual summary with one or more diagrams."""
    diagrams: List[MermaidDiagram] = Field(description="List of Mermaid diagrams")
    key_points: List[str] = Field(description="Key points summarized from the content")


class TimelineEvent(BaseModel):
    """A single event for timeline visualization."""
    date: str = Field(description="Date or time period (e.g., '2024-01-15', 'Q1 2024', 'Early 1900s')")
    title: str = Field(description="Short title for the event")
    description: str = Field(description="Description of what happened")
    importance: str = Field(default="medium", description="low, medium, or high")
    source_reference: Optional[str] = Field(default=None, description="Reference to source document")


class TimelineOutput(BaseModel):
    """Timeline extraction output."""
    events: List[TimelineEvent] = Field(description="List of events in chronological order")
    time_span: str = Field(description="Overall time span covered (e.g., '2020-2024')")
    context: str = Field(description="Brief context about the timeline")


class DocumentComparison(BaseModel):
    """Comparison between two documents."""
    similarities: List[str] = Field(description="Key similarities between documents")
    differences: List[str] = Field(description="Key differences between documents")
    unique_to_first: List[str] = Field(description="Points unique to first document")
    unique_to_second: List[str] = Field(description="Points unique to second document")
    synthesis: str = Field(description="Synthesized understanding combining both documents")


class WritingAssistance(BaseModel):
    """Writing assistance output."""
    content: str = Field(description="The generated or improved content")
    format_used: str = Field(description="The format applied (e.g., 'academic', 'blog', 'email')")
    suggestions: List[str] = Field(default_factory=list, description="Additional suggestions for improvement")
    word_count: int = Field(description="Approximate word count of the content")


# =============================================================================
# Structured LLM Service
# =============================================================================

class StructuredLLMService:
    """Service for generating structured outputs from LLM using Pydantic models."""
    
    def __init__(self):
        self.base_url = settings.ollama_base_url
        self.model = settings.ollama_model
        self.max_retries = 3
    
    async def _call_ollama_json(self, system_prompt: str, user_prompt: str, temperature: float = 0.7, timeout_seconds: float = 60.0) -> Dict[str, Any]:
        """Call Ollama with JSON mode enabled."""
        timeout = httpx.Timeout(timeout_seconds)
        
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": f"{system_prompt}\n\nUser request:\n{user_prompt}",
                    "stream": False,
                    "format": "json",
                    "options": {
                        "temperature": temperature,
                        "num_predict": 2000,
                    }
                }
            )
            result = response.json()
            
            import json
            try:
                return json.loads(result.get("response", "{}"))
            except json.JSONDecodeError:
                return {}
    
    async def generate_quiz(
        self, 
        content: str, 
        num_questions: int = 5,
        difficulty: str = "medium",
        question_types: Optional[List[str]] = None
    ) -> QuizOutput:
        """Generate a professional-quality quiz from content with structured output."""
        
        question_types = question_types or ["multiple_choice", "true_false"]
        
        system_prompt = f"""You are an expert instructional designer creating assessment questions for mastery learning.

Create exactly {num_questions} high-quality questions based on the provided content.

Output a valid JSON object with this structure:
{{
    "questions": [
        {{
            "question": "clear, unambiguous question text",
            "answer": "the correct answer (must match one of the options exactly)",
            "explanation": "why this is correct, referencing the source material",
            "difficulty": "{difficulty}",
            "question_type": "multiple_choice or true_false",
            "options": ["array of 4 options for multiple_choice, or ['True', 'False'] for true_false"],
            "source_reference": "name of source document this question comes from"
        }}
    ],
    "topic": "main topic being tested",
    "source_summary": "brief summary of source material used"
}}

QUESTION QUALITY REQUIREMENTS:
1. Test UNDERSTANDING, not just recall of trivial facts
2. Each question should assess a meaningful concept
3. For multiple_choice:
   - All 4 options must be plausible (no obviously wrong answers)
   - Distractors should represent common misconceptions
   - Options should be similar in length and structure
   - Avoid "all of the above" or "none of the above"
4. For true_false:
   - Statement must be unambiguously true or false
   - False statements should be plausibly incorrect
5. Questions should span different topics from the source material
6. Explanations should teach, not just state the answer

DIFFICULTY GUIDELINES:
- easy: Basic recall and comprehension
- medium: Application and analysis
- hard: Synthesis and evaluation across concepts"""

        for attempt in range(self.max_retries):
            try:
                result = await self._call_ollama_json(system_prompt, f"Content:\n{content[:8000]}")
                return QuizOutput(**result)
            except Exception as e:
                if attempt == self.max_retries - 1:
                    # Return minimal valid output on final failure
                    return QuizOutput(
                        questions=[],
                        topic="Quiz generation failed",
                        source_summary=str(e)
                    )
                await asyncio.sleep(1)
        
        return QuizOutput(questions=[], topic="Failed", source_summary="Max retries exceeded")
    
    # Color theme palettes - synced with frontend VisualToolbar.tsx and svg_templates.py
    COLOR_THEMES = {
        "auto": ["#ef4444", "#f97316", "#eab308", "#22c55e", "#3b82f6", "#8b5cf6"],
        "vibrant": ["#ef4444", "#f97316", "#eab308", "#22c55e", "#3b82f6", "#8b5cf6"],
        "ocean": ["#0ea5e9", "#06b6d4", "#14b8a6", "#0d9488", "#0891b2", "#0284c7"],
        "sunset": ["#f97316", "#fb923c", "#fbbf24", "#f59e0b", "#dc2626", "#facc15"],
        "forest": ["#22c55e", "#16a34a", "#15803d", "#84cc16", "#65a30d", "#10b981"],
        "monochrome": ["#1f2937", "#374151", "#4b5563", "#6b7280", "#9ca3af", "#d1d5db"],
        "pastel": ["#fecaca", "#fed7aa", "#fef08a", "#bbf7d0", "#bfdbfe", "#ddd6fe"],
    }
    
    # =========================================================================
    # DIAGRAM BUILDERS - Guaranteed valid Mermaid from structured data
    # =========================================================================
    
    def _build_mindmap(self, data: dict, colors: List[str]) -> str:
        """Build valid mindmap from structured data."""
        root = data.get("root", "Topic")
        branches = data.get("branches", [])
        
        lines = ["mindmap", f"  root(({root}))"]
        for i, branch in enumerate(branches[:4]):  # Max 4 branches
            if isinstance(branch, dict):
                name = branch.get("name", f"Branch{i+1}")
                leaves = branch.get("leaves", [])
            else:
                name = str(branch)
                leaves = []
            lines.append(f"    {name}")
            for leaf in leaves[:3]:  # Max 3 leaves per branch
                lines.append(f"      {leaf}")
        return "\n".join(lines)
    
    def _build_flowchart(self, data: dict, colors: List[str]) -> str:
        """Build valid flowchart from structured data."""
        steps = data.get("steps", data.get("nodes", []))
        direction = data.get("direction", "LR")
        
        lines = [f"flowchart {direction}"]
        node_ids = []
        
        for i, step in enumerate(steps[:6]):  # Max 6 steps
            node_id = chr(65 + i)  # A, B, C, D, E, F
            label = step if isinstance(step, str) else step.get("label", f"Step {i+1}")
            # Clean label - remove special chars that break Mermaid
            label = re.sub(r'["\'\[\]{}()]', '', label)[:40]
            lines.append(f"  {node_id}[{label}]")
            node_ids.append(node_id)
        
        # Add arrows between consecutive nodes
        for i in range(len(node_ids) - 1):
            lines.append(f"  {node_ids[i]} --> {node_ids[i+1]}")
        
        # Add colors
        if len(node_ids) > 0:
            lines.append(f"  style {node_ids[0]} fill:{colors[0]}")
        if len(node_ids) > 1:
            lines.append(f"  style {node_ids[-1]} fill:{colors[1]}")
        
        return "\n".join(lines)
    
    def _build_timeline(self, data: dict, colors: List[str]) -> str:
        """Build valid timeline from structured data."""
        title = data.get("title", "Timeline")
        events = data.get("events", data.get("sections", []))
        
        lines = ["timeline", f"  title {title}"]
        for i, event in enumerate(events[:5]):  # Max 5 events
            if isinstance(event, dict):
                period = event.get("period", f"Phase {i+1}")
                desc = event.get("description", event.get("event", "Event"))
            else:
                period = f"Phase {i+1}"
                desc = str(event)
            lines.append(f"  section {period}")
            lines.append(f"    {desc}")
        return "\n".join(lines)
    
    def _build_quadrant(self, data: dict, colors: List[str]) -> str:
        """Build valid quadrant chart from structured data."""
        title = data.get("title", "Analysis Matrix")
        x_axis = data.get("x_axis", "Low Impact --> High Impact")
        y_axis = data.get("y_axis", "Low Effort --> High Effort")
        items = data.get("items", [])
        
        lines = [
            "quadrantChart",
            f"    title {title}",
            f"    x-axis {x_axis}",
            f"    y-axis {y_axis}",
            "    quadrant-1 Do First",
            "    quadrant-2 Schedule",
            "    quadrant-3 Delegate",
            "    quadrant-4 Eliminate"
        ]
        
        # Place items in quadrants based on position or distribute evenly
        for i, item in enumerate(items[:8]):
            name = item if isinstance(item, str) else item.get("name", f"Item {i+1}")
            # Clean name for Mermaid
            name = re.sub(r'[:\[\]{}()]', '', name)[:30]
            # Distribute across quadrants
            x = 0.2 + (i % 4) * 0.2
            y = 0.3 + (i // 4) * 0.4
            lines.append(f"    {name}: [{x:.2f}, {y:.2f}]")
        
        return "\n".join(lines)
    
    def _build_pie(self, data: dict, colors: List[str]) -> str:
        """Build valid pie chart from structured data."""
        title = data.get("title", "Distribution")
        segments = data.get("segments", [])
        
        lines = ["pie showData", f"    title {title}"]
        
        # If no segments provided, create even distribution
        if not segments:
            segments = [{"name": "Segment", "value": 100}]
        
        total = sum(s.get("value", 1) if isinstance(s, dict) else 1 for s in segments)
        
        for i, seg in enumerate(segments[:7]):  # Max 7 segments
            if isinstance(seg, dict):
                name = seg.get("name", f"Item {i+1}")
                value = seg.get("value", 1)
            else:
                name = str(seg)
                value = 100 // len(segments)  # Even distribution
            # Clean name
            name = re.sub(r'[:\[\]{}()]', '', str(name))[:25]
            lines.append(f'    "{name}" : {value}')
        
        return "\n".join(lines)
    
    def _build_xychart(self, data: dict, colors: List[str]) -> str:
        """Build valid xy chart from structured data."""
        title = data.get("title", "Trend")
        x_labels = data.get("x_labels", ["Q1", "Q2", "Q3", "Q4"])
        values = data.get("values", [10, 20, 30, 40])
        
        # Ensure we have matching labels and values
        if len(values) < len(x_labels):
            values = values + [values[-1] if values else 0] * (len(x_labels) - len(values))
        
        x_str = str(x_labels[:8]).replace("'", '"')
        y_str = str(values[:8])
        
        lines = [
            "xychart-beta",
            f'    title "{title}"',
            f"    x-axis {x_str}",
            '    y-axis "Value"',
            f"    bar {y_str}"
        ]
        
        return "\n".join(lines)
    
    def _build_pros_cons(self, data: dict, colors: List[str]) -> str:
        """Build valid pros/cons flowchart from structured data."""
        pros = data.get("pros", [])
        cons = data.get("cons", [])
        
        lines = ["flowchart TB"]  # TB for better text display
        
        # Pros subgraph - use longer labels
        lines.append("    subgraph Pros[✅ Advantages]")
        lines.append("    direction TB")
        for i, pro in enumerate(pros[:4]):
            pro_clean = self._clean_label(str(pro), max_len=60)
            lines.append(f"        P{i+1}[\"{pro_clean}\"]")
        lines.append("    end")
        
        # Cons subgraph
        lines.append("    subgraph Cons[❌ Challenges]")
        lines.append("    direction TB")
        for i, con in enumerate(cons[:4]):
            con_clean = self._clean_label(str(con), max_len=60)
            lines.append(f"        C{i+1}[\"{con_clean}\"]")
        lines.append("    end")
        
        # Style
        lines.append(f"    style Pros fill:{colors[1]},color:#fff")
        lines.append(f"    style Cons fill:#ef4444,color:#fff")
        
        return "\n".join(lines)

    def build_from_structure(
        self,
        structure: dict,
        diagram_type: str,
        colors: List[str],
        title: str = "",
        template_id: str = ""
    ) -> VisualSummary:
        """Build diagram directly from pre-classified structure data.
        
        NO LLM CALL - uses data already extracted during pre-classification.
        This is the robust approach: pre-classification extracts data once,
        builders produce guaranteed-valid Mermaid.
        
        Args:
            structure: Pre-extracted content structure
            diagram_type: Mermaid diagram type (mindmap, flowchart, etc.)
            colors: Color palette
            title: Diagram title
            template_id: Specific template ID for differentiated output
        """
        # Route to template-specific builder if we have a template_id
        if template_id:
            return self._build_for_template(structure, template_id, colors, title)
        
        # Fallback: Map mermaid type to generic builder
        # NOTE: mindmap type is converted to flowchart because Mermaid mindmap text rendering is broken
        if diagram_type == "mindmap":
            themes = structure.get("themes", [])
            entities = structure.get("entities", [])
            
            # Build as flowchart (mindmap text rendering is broken in Mermaid)
            theme_list = themes[:4] if themes else entities[:4] if entities else [title or "Key Points"]
            lines = ["flowchart TB"]
            lines.append(f'    ROOT["{title or "Overview"}"]')
            
            for i, theme in enumerate(theme_list):
                theme_id = f"T{i+1}"
                theme_clean = self._clean_label(str(theme), max_len=50)
                lines.append(f'    {theme_id}["{theme_clean}"]')
                lines.append(f"    ROOT --> {theme_id}")
                # Add entities as sub-nodes if available
                if themes and i < len(entities):
                    ent_id = f"E{i+1}"
                    ent_clean = self._clean_label(str(entities[i]), max_len=40)
                    lines.append(f'    {ent_id}["{ent_clean}"]')
                    lines.append(f"    {theme_id} --> {ent_id}")
            
            lines.append(f"    style ROOT fill:{colors[0]},color:#fff")
            code = "\n".join(lines)
            
        elif diagram_type == "flowchart":
            sequence = structure.get("sequence", [])
            themes = structure.get("themes", [])
            relationships = structure.get("relationships", [])
            
            # Priority: sequence (actual steps) > relationships > themes (as concept flow)
            if sequence:
                steps = sequence[:6]
            elif relationships:
                # Extract unique nodes from relationships
                nodes = []
                for rel in relationships[:4]:
                    if isinstance(rel, list) and len(rel) >= 2:
                        if rel[0] not in nodes: nodes.append(rel[0])
                        if rel[-1] not in nodes: nodes.append(rel[-1])
                steps = nodes[:6] if nodes else themes[:6]
            else:
                # Use themes but frame as a conceptual flow
                steps = [f"Understand {themes[0]}" if themes else "Start"]
                steps += themes[1:5] if len(themes) > 1 else []
                steps.append("Apply Knowledge" if themes else "End")
            
            data = {"steps": steps or ["Start", "Process", "End"]}
            code = self._build_flowchart(data, colors)
            
        elif diagram_type == "timeline":
            dates_events = structure.get("dates_events", [])
            sequence = structure.get("sequence", [])
            themes = structure.get("themes", [])
            
            events = []
            # Priority: dates_events > sequence > themes (as evolution phases)
            source_items = dates_events or sequence or themes
            
            for i, item in enumerate(source_items[:5]):
                if isinstance(item, str) and ":" in item:
                    parts = item.split(":", 1)
                    events.append({"period": parts[0].strip(), "description": parts[1].strip()})
                elif dates_events or sequence:
                    events.append({"period": f"Phase {i+1}", "description": str(item)})
                else:
                    # Themes as evolution: "Early" -> "Current" -> "Future"
                    phase_names = ["Foundation", "Development", "Expansion", "Maturity", "Future"]
                    events.append({"period": phase_names[i] if i < len(phase_names) else f"Phase {i+1}", 
                                   "description": str(item)})
            
            data = {"title": title or "Timeline", "events": events or [{"period": "Start", "description": "Beginning"}]}
            code = self._build_timeline(data, colors)
            
        elif diagram_type == "quadrantChart":
            themes = structure.get("themes", [])
            entities = structure.get("entities", [])
            comparisons = structure.get("comparisons", [])
            
            # Use themes or entities as items to plot
            items = themes[:8] if themes else entities[:8]
            data = {
                "title": title or "Analysis Matrix",
                "items": items or ["Item 1", "Item 2", "Item 3", "Item 4"]
            }
            code = self._build_quadrant(data, colors)
            
        elif diagram_type == "pie":
            themes = structure.get("themes", [])
            numbers = structure.get("numbers", [])
            entities = structure.get("entities", [])
            
            # Create segments from themes or entities
            segments = []
            items = themes[:7] if themes else entities[:7]
            for i, item in enumerate(items):
                # Try to extract numbers if available
                value = 100 // len(items) if items else 100
                if i < len(numbers):
                    # Try to extract numeric value from number string
                    num_str = re.sub(r'[^\d.]', '', str(numbers[i]))
                    if num_str:
                        try:
                            value = int(float(num_str))
                        except:
                            pass
                segments.append({"name": item, "value": value})
            
            data = {"title": title or "Distribution", "segments": segments}
            code = self._build_pie(data, colors)
            
        elif diagram_type == "xychart-beta":
            dates_events = structure.get("dates_events", [])
            numbers = structure.get("numbers", [])
            themes = structure.get("themes", [])
            
            # Extract labels and values
            x_labels = []
            values = []
            
            if dates_events:
                for de in dates_events[:8]:
                    if ":" in de:
                        parts = de.split(":", 1)
                        x_labels.append(parts[0].strip()[:10])
                        # Try to extract number from description
                        num = re.search(r'\d+', parts[1])
                        values.append(int(num.group()) if num else (len(values) + 1) * 10)
                    else:
                        x_labels.append(str(de)[:10])
                        values.append((len(values) + 1) * 10)
            else:
                x_labels = themes[:6] if themes else ["Q1", "Q2", "Q3", "Q4"]
                values = list(range(10, 10 + len(x_labels) * 10, 10))
            
            data = {"title": title or "Trend", "x_labels": x_labels, "values": values}
            code = self._build_xychart(data, colors)
            
        else:
            # Default to flowchart (mindmap text rendering is broken)
            themes = structure.get("themes", [])
            lines = ["flowchart TB"]
            lines.append(f'    ROOT["{title or "Overview"}"]')
            for i, theme in enumerate(themes[:4]):
                theme_id = f"T{i+1}"
                lines.append(f'    {theme_id}["{self._clean_label(str(theme), max_len=50)}"]')
                lines.append(f"    ROOT --> {theme_id}")
            lines.append(f"    style ROOT fill:{colors[0]},color:#fff")
            code = "\n".join(lines)
        
        description = f"Visual summary of {title}" if title else "AI-generated diagram"
        
        return VisualSummary(
            diagrams=[MermaidDiagram(
                diagram_type=diagram_type,
                code=code,
                title=title or "Visual Summary",
                description=description
            )],
            key_points=structure.get("themes", [])[:3]
        )
    
    def build_svg_from_structure(
        self,
        structure: dict,
        template_id: str,
        color_theme: str = "auto",
        title: str = "",
        dark_mode: bool = True
    ) -> dict:
        """Build SVG visual from pre-classified structure data.
        
        This replaces Mermaid generation with pure SVG for reliable rendering.
        
        Args:
            structure: Pre-extracted content structure
            template_id: Template ID for visual type
            color_theme: Color theme name
            title: Diagram title
            dark_mode: Whether to use dark mode colors
            
        Returns:
            dict with 'svg', 'title', 'description', 'template_id'
        """
        colors = SVG_COLOR_THEMES.get(color_theme, SVG_COLOR_THEMES["auto"])
        
        svg_code = build_svg_visual(
            template_id=template_id,
            structure=structure,
            colors=colors,
            title=title,
            dark_mode=dark_mode
        )
        
        description = f"{template_id.replace('_', ' ').title()} visualization"
        
        return {
            "svg": svg_code,
            "title": title or template_id.replace('_', ' ').title(),
            "description": description,
            "template_id": template_id,
            "render_type": "svg"
        }
    
    def _build_for_template(
        self,
        structure: dict,
        template_id: str,
        colors: List[str],
        title: str = ""
    ) -> VisualSummary:
        """Build diagram for a specific template ID with differentiated output.
        
        Each of the 25 templates gets its own builder logic for unique visuals.
        """
        # Extract all possible data from structure
        themes = structure.get("themes", [])
        entities = structure.get("entities", [])
        relationships = structure.get("relationships", [])
        sequence = structure.get("sequence", [])
        dates_events = structure.get("dates_events", [])
        comparisons = structure.get("comparisons", [])
        numbers = structure.get("numbers", [])
        pros = structure.get("pros", [])
        cons = structure.get("cons", [])
        recommendations = structure.get("recommendations", [])
        components = structure.get("components", [])
        rankings = structure.get("rankings", [])
        
        code = ""
        diagram_type = "flowchart"
        
        # =========== CONTEXT TEMPLATES ===========
        
        if template_id == "key_stats":
            # Flowchart with numbers/stats (mindmap text rendering is broken)
            diagram_type = "flowchart"
            lines = ["flowchart TB"]
            lines.append(f'    ROOT["{title or "Key Metrics"}"]')
            stats = numbers[:5] if numbers else themes[:5]
            for i, stat in enumerate(stats):
                stat_clean = re.sub(r'[:\[\]{}()]', '', str(stat))[:40]
                stat_id = f"S{i+1}"
                lines.append(f'    {stat_id}["{stat_clean}"]')
                lines.append(f"    ROOT --> {stat_id}")
                # Add entity context if available
                if i < len(entities):
                    ent_id = f"E{i+1}"
                    lines.append(f'    {ent_id}["{entities[i][:25]}"]')
                    lines.append(f"    {stat_id} --> {ent_id}")
            lines.append(f"    style ROOT fill:{colors[0]},color:#fff")
            code = "\n".join(lines)
            
        elif template_id == "exec_summary":
            # Flowchart: Situation → Findings → Recommendation
            diagram_type = "flowchart"
            situation = themes[0] if themes else "Current State"
            findings = themes[1:4] if len(themes) > 1 else ["Key Finding"]
            rec = recommendations[0] if recommendations else "Take Action"
            
            lines = ["flowchart LR"]
            lines.append("    subgraph Situation")
            lines.append(f"        S[{self._clean_label(situation)}]")
            lines.append("    end")
            lines.append("    subgraph Findings")
            for i, f in enumerate(findings[:3]):
                lines.append(f"        F{i+1}[{self._clean_label(f)}]")
            lines.append("    end")
            lines.append("    subgraph Action")
            lines.append(f"        R[{self._clean_label(rec)}]")
            lines.append("    end")
            lines.append("    S --> F1")
            if len(findings) > 1: lines.append("    S --> F2")
            if len(findings) > 2: lines.append("    S --> F3")
            lines.append("    F1 --> R")
            lines.append(f"    style R fill:{colors[1]},color:#fff")
            code = "\n".join(lines)
            
        elif template_id == "timeline":
            diagram_type = "timeline"
            code = self._build_timeline({"title": title, "events": dates_events or sequence}, colors)
            
        elif template_id == "overview_map":
            # Flowchart overview (mindmap text rendering is broken)
            diagram_type = "flowchart"
            lines = ["flowchart TB"]
            lines.append(f'    ROOT["{title or "Overview"}"]')
            # Group entities by themes if possible
            for i, theme in enumerate(themes[:4]):
                theme_id = f"T{i+1}"
                lines.append(f'    {theme_id}["{self._clean_label(theme, max_len=45)}"]')
                lines.append(f"    ROOT --> {theme_id}")
                # Add related entities
                start = i * 2
                for j, ent in enumerate(entities[start:start+2]):
                    ent_id = f"E{i}_{j}"
                    lines.append(f'    {ent_id}["{self._clean_label(ent, max_len=35)}"]')
                    lines.append(f"    {theme_id} --> {ent_id}")
            lines.append(f"    style ROOT fill:{colors[0]},color:#fff")
            code = "\n".join(lines)
            
        # =========== MECHANISM TEMPLATES ===========
        
        elif template_id == "horizontal_steps":
            diagram_type = "flowchart"
            steps = sequence[:6] if sequence else themes[:6]
            lines = ["flowchart LR"]
            gradient_colors = ["#6366f1", "#8b5cf6", "#a855f7", "#d946ef", "#ec4899", "#f43f5e"]
            for i, step in enumerate(steps):
                node = chr(65 + i)
                lines.append(f'    {node}["{i+1}. {self._clean_label(step)}"]')
                if i > 0:
                    lines.append(f"    {chr(64+i)} --> {node}")
            for i in range(min(len(steps), 6)):
                lines.append(f"    style {chr(65+i)} fill:{gradient_colors[i]},color:#fff")
            code = "\n".join(lines)
            
        elif template_id == "process_flow":
            diagram_type = "flowchart"
            steps = sequence[:5] if sequence else themes[:5]
            lines = ["flowchart TD", "    A[Start]"]
            for i, step in enumerate(steps):
                node = chr(66 + i)
                lines.append(f"    {node}[{self._clean_label(step)}]")
            lines.append(f"    {chr(65+len(steps)+1)}[End]")
            # Connect nodes
            lines.append("    A --> B")
            for i in range(len(steps)-1):
                lines.append(f"    {chr(66+i)} --> {chr(67+i)}")
            lines.append(f"    {chr(65+len(steps))} --> {chr(66+len(steps))}")
            lines.append(f"    style A fill:{colors[0]}")
            lines.append(f"    style {chr(66+len(steps))} fill:{colors[1]}")
            code = "\n".join(lines)
            
        elif template_id == "system_architecture":
            diagram_type = "flowchart"
            comps = components[:6] if components else entities[:6]
            lines = ["flowchart TB"]
            if len(comps) >= 4:
                lines.append("    subgraph Layer1[Top Layer]")
                lines.append(f"        A[{self._clean_label(comps[0])}]")
                lines.append(f"        B[{self._clean_label(comps[1])}]")
                lines.append("    end")
                lines.append("    subgraph Layer2[Core Layer]")
                lines.append(f"        C[{self._clean_label(comps[2])}]")
                lines.append(f"        D[{self._clean_label(comps[3])}]")
                lines.append("    end")
                lines.append("    A --> C")
                lines.append("    B --> D")
            else:
                for i, comp in enumerate(comps):
                    lines.append(f"    {chr(65+i)}[{self._clean_label(comp)}]")
                for i in range(len(comps)-1):
                    lines.append(f"    {chr(65+i)} --> {chr(66+i)}")
            code = "\n".join(lines)
            
        elif template_id == "cycle_loop":
            diagram_type = "flowchart"
            steps = sequence[:4] if sequence else themes[:4] if themes else ["Plan", "Do", "Check", "Act"]
            lines = ["flowchart LR"]
            for i, step in enumerate(steps):
                lines.append(f"    {chr(65+i)}[{self._clean_label(step)}]")
            for i in range(len(steps)-1):
                lines.append(f"    {chr(65+i)} --> {chr(66+i)}")
            lines.append(f"    {chr(64+len(steps))} --> A")  # Loop back
            code = "\n".join(lines)
            
        elif template_id == "anatomy":
            # Flowchart breakdown (mindmap text rendering is broken)
            diagram_type = "flowchart"
            parts = components[:5] if components else themes[:5]
            lines = ["flowchart TB"]
            lines.append(f'    ROOT["{title or "Breakdown"}"]')
            for i, part in enumerate(parts):
                part_id = f"P{i+1}"
                lines.append(f'    {part_id}["{self._clean_label(part, max_len=45)}"]')
                lines.append(f"    ROOT --> {part_id}")
            lines.append(f"    style ROOT fill:{colors[0]},color:#fff")
            code = "\n".join(lines)
            
        elif template_id == "decision_tree":
            diagram_type = "flowchart"
            decisions = comparisons[:3] if comparisons else [[t, "?", ""] for t in themes[:3]]
            lines = ["flowchart TD"]
            lines.append(f"    A{{{self._clean_label(themes[0] if themes else 'Main Question')}?}}")
            lines.append("    A -->|Option 1| B[Path A]")
            lines.append("    A -->|Option 2| C[Path B]")
            if len(decisions) > 1:
                lines.append(f"    B --> D{{{self._clean_label(str(decisions[1][0]))}?}}")
                lines.append("    D -->|Yes| E[Outcome 1]")
                lines.append("    D -->|No| F[Outcome 2]")
            lines.append(f"    style A fill:{colors[0]}")
            code = "\n".join(lines)
            
        # =========== ANALYSIS TEMPLATES ===========
        
        elif template_id == "side_by_side":
            diagram_type = "flowchart"
            if comparisons:
                item_a = comparisons[0][0] if comparisons[0] else "Option A"
                item_b = comparisons[0][-1] if comparisons[0] else "Option B"
            else:
                item_a = entities[0] if entities else "Option A"
                item_b = entities[1] if len(entities) > 1 else "Option B"
            
            lines = ["flowchart LR"]
            lines.append(f"    subgraph OptionA[{self._clean_label(item_a)}]")
            for i, t in enumerate(themes[:3]):
                lines.append(f"        A{i+1}[{self._clean_label(t)}]")
            lines.append("    end")
            lines.append(f"    subgraph OptionB[{self._clean_label(item_b)}]")
            for i, t in enumerate(themes[3:6] if len(themes) > 3 else themes[:3]):
                lines.append(f"        B{i+1}[{self._clean_label(t)}]")
            lines.append("    end")
            lines.append(f"    style OptionA fill:{colors[0]}")
            lines.append(f"    style OptionB fill:{colors[1]}")
            code = "\n".join(lines)
            
        elif template_id == "quadrant":
            diagram_type = "quadrantChart"
            code = self._build_quadrant({"title": title, "items": themes or entities}, colors)
            
        elif template_id == "pros_cons":
            diagram_type = "flowchart"
            p_list = pros[:4] if pros else themes[:2]
            c_list = cons[:4] if cons else themes[2:4]
            code = self._build_pros_cons({"pros": p_list, "cons": c_list}, colors)
            
        elif template_id == "ranking":
            diagram_type = "flowchart"
            ranked = rankings[:5] if rankings else themes[:5]
            medals = ["🥇", "🥈", "🥉", "4.", "5."]
            lines = ["flowchart TD", "    subgraph Rankings[📊 Rankings]"]
            for i, item in enumerate(ranked):
                item_clean = self._clean_label(str(item))
                lines.append(f"        R{i+1}[{medals[i]} {item_clean}]")
            lines.append("    end")
            for i in range(len(ranked)-1):
                lines.append(f"    R{i+1} --> R{i+2}")
            lines.append(f"    style R1 fill:{colors[1]},color:#fff")
            code = "\n".join(lines)
            
        elif template_id == "spectrum":
            diagram_type = "flowchart"
            items = themes[:5] if themes else ["Low", "Medium-Low", "Medium", "Medium-High", "High"]
            lines = ["flowchart LR"]
            for i, item in enumerate(items):
                lines.append(f"    {chr(65+i)}[{self._clean_label(item)}]")
            for i in range(len(items)-1):
                lines.append(f"    {chr(65+i)} --- {chr(66+i)}")
            mid = len(items) // 2
            lines.append(f"    style {chr(65+mid)} fill:#f9f,stroke:#333")
            code = "\n".join(lines)
            
        # =========== PATTERN TEMPLATES ===========
        
        elif template_id == "trend_chart":
            diagram_type = "xychart-beta"
            code = self._build_xychart({"title": title, "x_labels": themes[:6], "values": list(range(10, 70, 10))}, colors)
            
        elif template_id == "distribution":
            diagram_type = "pie"
            items = themes[:6] if themes else entities[:6]
            code = self._build_pie({"title": title, "segments": items}, colors)
            
        elif template_id == "funnel":
            diagram_type = "flowchart"
            stages = sequence[:5] if sequence else themes[:5]
            nums = numbers[:5] if numbers else [str(1000 // (i+1)) for i in range(5)]
            lines = ["flowchart TD"]
            gradient = ["#e0e0ff", "#c0c0ff", "#a0a0ff", "#8080ff", "#90EE90"]
            for i, stage in enumerate(stages):
                num = nums[i] if i < len(nums) else ""
                lines.append(f"    {chr(65+i)}[{self._clean_label(stage)}: {num}]")
            for i in range(len(stages)-1):
                lines.append(f"    {chr(65+i)} --> {chr(66+i)}")
            for i in range(min(len(stages), 5)):
                lines.append(f"    style {chr(65+i)} fill:{gradient[i]}")
            code = "\n".join(lines)
            
        elif template_id == "heatmap":
            diagram_type = "flowchart"
            items = themes[:6] if themes else ["Item 1", "Item 2", "Item 3"]
            heat_colors = ["#ff6b6b", "#ffd93d", "#6bcb77"]
            lines = ["flowchart TB", "    subgraph Matrix[Intensity Map]"]
            for i, item in enumerate(items):
                color = heat_colors[i % 3]
                lines.append(f"        M{i+1}[{self._clean_label(item)}]")
            lines.append("    end")
            for i in range(len(items)):
                lines.append(f"    style M{i+1} fill:{heat_colors[i % 3]}")
            code = "\n".join(lines)
            
        # =========== PERSUADE TEMPLATES ===========
        
        elif template_id == "recommendation_stack":
            diagram_type = "flowchart"
            recs = recommendations[:4] if recommendations else themes[:4]
            priorities = ["🔴 HIGH", "🟡 MEDIUM", "🟢 LOW", "⚪ OPTIONAL"]
            lines = ["flowchart TD", "    subgraph Priority[📋 Recommended Actions]"]
            for i, rec in enumerate(recs):
                prio = priorities[i] if i < len(priorities) else priorities[-1]
                lines.append(f"        R{i+1}[{prio}: {self._clean_label(rec)}]")
            lines.append("    end")
            for i in range(len(recs)-1):
                lines.append(f"    R{i+1} --> R{i+2}")
            code = "\n".join(lines)
            
        elif template_id == "key_takeaways":
            # Use flowchart instead of mindmap (mindmap text rendering is broken)
            diagram_type = "flowchart"
            points = themes[:5] if themes else ["Key Point"]
            lines = ["flowchart TB"]
            lines.append(f'    ROOT["{title or "Key Takeaways"}"]')
            for i, point in enumerate(points):
                node_id = f"P{i+1}"
                lines.append(f'    {node_id}["{self._clean_label(point, max_len=50)}"]')
                lines.append(f"    ROOT --> {node_id}")
            # Style the root
            lines.append(f"    style ROOT fill:{colors[0]},color:#fff,stroke-width:2px")
            code = "\n".join(lines)
            
        elif template_id == "concept_map":
            # Use flowchart instead of mindmap (mindmap text rendering is broken)
            diagram_type = "flowchart"
            lines = ["flowchart TB"]
            lines.append(f'    ROOT["{title or "Concepts"}"]')
            for i, theme in enumerate(themes[:4]):
                theme_id = f"T{i+1}"
                lines.append(f'    {theme_id}["{self._clean_label(theme, max_len=45)}"]')
                lines.append(f"    ROOT --> {theme_id}")
                # Add related entities as sub-nodes
                if i < len(entities):
                    ent_id = f"E{i+1}"
                    lines.append(f'    {ent_id}["{self._clean_label(entities[i], max_len=40)}"]')
                    lines.append(f"    {theme_id} --> {ent_id}")
            lines.append(f"    style ROOT fill:{colors[0]},color:#fff")
            code = "\n".join(lines)
            
        elif template_id == "call_to_action":
            diagram_type = "flowchart"
            action = recommendations[0] if recommendations else themes[0] if themes else "Take Action"
            lines = ["flowchart LR"]
            lines.append("    A[Current State]")
            lines.append("    B{Ready?}")
            lines.append(f"    C[✅ {self._clean_label(action)}]")
            lines.append("    D[Prepare]")
            lines.append("    E[🎯 Success!]")
            lines.append("    A --> B")
            lines.append("    B -->|Yes| C")
            lines.append("    B -->|No| D")
            lines.append("    D --> B")
            lines.append("    C --> E")
            lines.append(f"    style C fill:{colors[1]},color:#fff")
            lines.append("    style E fill:#FFD700")
            code = "\n".join(lines)
            
        else:
            # Fallback to flowchart for unknown templates (mindmap text rendering is broken)
            diagram_type = "flowchart"
            lines = ["flowchart TB"]
            lines.append(f'    ROOT["{title or "Overview"}"]')
            for i, theme in enumerate(themes[:4]):
                theme_id = f"T{i+1}"
                lines.append(f'    {theme_id}["{self._clean_label(theme, max_len=50)}"]')
                lines.append(f"    ROOT --> {theme_id}")
            lines.append(f"    style ROOT fill:{colors[0]},color:#fff")
            code = "\n".join(lines)
        
        description = f"{template_id.replace('_', ' ').title()} visualization"
        
        return VisualSummary(
            diagrams=[MermaidDiagram(
                diagram_type=diagram_type,
                code=code,
                title=title or template_id.replace('_', ' ').title(),
                description=description
            )],
            key_points=themes[:3]
        )
    
    def _clean_label(self, text: str, max_len: int = 50) -> str:
        """Clean text for use in Mermaid labels."""
        if not text:
            return "Item"
        cleaned = re.sub(r'[:\[\]{}()"\'<>]', '', str(text))
        if len(cleaned) > max_len:
            return cleaned[:max_len-3] + "..."
        return cleaned

    async def _generate_visual_fast(
        self,
        content: str,
        diagram_type: str,
        colors: List[str],
        pre_classified_structure: dict = None  # NEW: Accept pre-classified data
    ) -> VisualSummary:
        """FAST visual generation - uses pre-classified structure if available.
        
        If pre_classified_structure is provided, builds diagram directly (no LLM call).
        Otherwise, falls back to LLM extraction.
        """
        # FAST PATH: Use pre-classified structure directly
        if pre_classified_structure and pre_classified_structure.get("themes"):
            print(f"[StructuredLLM] Using pre-classified structure for {diagram_type}")
            title = pre_classified_structure.get("themes", ["Overview"])[0] if pre_classified_structure.get("themes") else "Overview"
            return self.build_from_structure(pre_classified_structure, diagram_type, colors, title)
        
        # FALLBACK: Extract structure via LLM (only if no pre-classification)
        import httpx
        from config import settings
        
        prompt = f"""Topic: {content[:300]}

Return JSON with themes, sequence, and dates:
{{"themes": ["theme1", "theme2", "theme3"], "sequence": ["step1", "step2"], "dates_events": [], "title": "Short Title", "description": "One sentence"}}

Extract the main themes/concepts from the topic."""

        try:
            timeout = httpx.Timeout(90.0)  # 90 second timeout for LLM generation
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{settings.ollama_base_url}/api/generate",
                    json={
                        "model": settings.ollama_fast_model,  # phi4-mini - FAST
                        "prompt": prompt,
                        "stream": False,
                        "format": "json",
                        "options": {
                            "temperature": 0.3,
                            "num_predict": 500,  # Small output
                        }
                    }
                )
                result = response.json()
                raw_response = result.get("response", "{}")
                print(f"[StructuredLLM] LLM fallback response: {raw_response[:200]}...")
                
                import json
                parsed = json.loads(raw_response)
                
                # Use diagram builders with LLM-extracted structure
                title = parsed.get("title", parsed.get("themes", ["Overview"])[0] if parsed.get("themes") else "Overview")
                return self.build_from_structure(parsed, diagram_type, colors, title)
        except Exception as e:
            import traceback
            print(f"[StructuredLLM] Fast visual failed: {e}")
            traceback.print_exc()
            return VisualSummary(diagrams=[], key_points=[])
    
    async def generate_visual_summary(
        self, 
        content: str,
        diagram_types: Optional[List[str]] = None,
        color_theme: str = "auto",
        use_fast_model: bool = True  # Use fast model by default for speed
    ) -> VisualSummary:
        """Generate visual summary with Mermaid diagrams.
        
        SPEED OPTIMIZATION: Uses phi4-mini (fast model) with minimal prompt.
        Target: <15 seconds for single visual.
        """
        
        diagram_types = diagram_types or ["mindmap"]
        diagram_type = diagram_types[0]  # Focus on ONE type for speed
        
        # Get color palette for the selected theme
        colors = self.COLOR_THEMES.get(color_theme, self.COLOR_THEMES["auto"])
        
        # SPEED PATH: Minimal prompt for fast generation
        # Napkin.ai approach: simple, direct prompt with just what's needed
        if use_fast_model:
            return await self._generate_visual_fast(content, diagram_type, colors)
        
        # Legacy path: Full prompt (slower but more detailed)
        diagram_guidelines = []
        for dtype in diagram_types:
            if dtype in VISUAL_TEMPLATES:
                template = VISUAL_TEMPLATES[dtype]
                diagram_guidelines.append(f"""
### {template['name']} ({dtype})
{template['system_prompt']}

Example syntax:
```mermaid
{template['example']}
```
""")
        
        system_prompt = f"""You are a professional visualization expert creating BEAUTIFUL, publication-quality diagrams like Napkin.ai.

Output a valid JSON object with this structure:
{{
    "diagrams": [
        {{
            "diagram_type": "one of {diagram_types}",
            "code": "valid mermaid code - MUST be syntactically correct",
            "title": "descriptive diagram title",
            "description": "what this diagram reveals about the content"
        }}
    ],
    "key_points": ["list", "of", "key", "insights", "from the content"]
}}

DIAGRAM GUIDELINES:
{chr(10).join(diagram_guidelines) if diagram_guidelines else "- Mindmap, Flowchart, Timeline diagram types supported"}

⚠️ CRITICAL - DATA INTEGRITY RULES:
1. NEVER invent, fabricate, or add data that is NOT in the provided content
2. Do NOT add fake dates, years, percentages, or numbers unless explicitly provided
3. Do NOT add extra categories, stages, or branches beyond what is described
4. Use ONLY the exact labels, names, and descriptions from the content
5. If content says "5 stages" or "6 processes" - create EXACTLY that many items
6. NEVER use placeholder labels like "Key Process 1", "Item A", "Stage 1" - use ACTUAL content words
7. Keep diagrams SIMPLE: 3-8 nodes maximum for clarity

⚠️ CRITICAL - LAYOUT INSTRUCTIONS:
- If user requests "side by side columns" or "horizontal" - use flowchart LR (left to right)
- If user requests "vertical" - use flowchart TB (top to bottom)
- For N items side-by-side: flowchart LR with A --> B --> C --> D --> E pattern

⚠️ CRITICAL - DIAGRAM TYPE SELECTION:
- quadrantChart: For 2x2 matrices, comparisons on 2 dimensions, priority grids - NEVER use flowchart for these!
- flowchart: For processes, steps, sequences, progressions
- mindmap: For hierarchies, categories, concept overviews
- timeline: For chronological events
- pie: For proportions/percentages

📊 QUADRANTCHART SYNTAX (for 2x2 matrices - MUST USE THIS FORMAT):
"code": "quadrantChart\\n    title Matrix Title\\n    x-axis Low Dimension1 --> High Dimension1\\n    y-axis Low Dimension2 --> High Dimension2\\n    quadrant-1 Top Right Label\\n    quadrant-2 Top Left Label\\n    quadrant-3 Bottom Left Label\\n    quadrant-4 Bottom Right Label\\n    Item A: [0.8, 0.9]\\n    Item B: [0.3, 0.7]\\n    Item C: [0.6, 0.2]\\n    Item D: [0.2, 0.4]"
- Coordinates are [x, y] from 0.0 to 1.0
- quadrant-1 is TOP RIGHT (high x, high y)
- quadrant-2 is TOP LEFT (low x, high y)
- quadrant-3 is BOTTOM LEFT (low x, low y)
- quadrant-4 is BOTTOM RIGHT (high x, low y)

🎨 NAPKIN.AI-STYLE VISUAL DESIGN (REQUIRED):
- ALWAYS add colorful styling to EVERY node using distinct, vibrant colors
- Use this EXACT color palette (theme: {color_theme}):
  * Color 1: fill:{colors[0]},color:#fff
  * Color 2: fill:{colors[1]},color:#fff  
  * Color 3: fill:{colors[2]},color:#fff
  * Color 4: fill:{colors[3]},color:#fff
  * Color 5: fill:{colors[4]},color:#fff
  * Color 6: fill:{colors[5]},color:#fff
- Cycle through these colors for each node
- Each node should have a DIFFERENT color for visual appeal
- Use rounded corners with border-radius where possible
- Keep labels SHORT (2-4 words max) but descriptive

QUALITY REQUIREMENTS:
- Diagrams must be readable at a glance (not overcrowded)
- Use clear, concise labels (2-4 words per node)
- Capture ONLY the structure described, nothing more
- Make it VISUALLY APPEALING - this is crucial

CRITICAL CODE FORMAT:
- The "code" field must contain ONLY raw Mermaid code - NO markdown fences
- Each statement MUST be on its own line with proper newlines (\\n)
- EXAMPLE for 5 horizontal stages (COPY THIS PATTERN):
  "code": "flowchart LR\\n    A[AI Assistant] --> B[Co-Pilot]\\n    B --> C[Modular Tasks]\\n    C --> D[Autonomous Systems]\\n    D --> E[Full Replacement]\\n    style A fill:#3b82f6,color:#fff,stroke:#2563eb,stroke-width:2px\\n    style B fill:#22c55e,color:#fff,stroke:#16a34a,stroke-width:2px\\n    style C fill:#f59e0b,color:#000,stroke:#d97706,stroke-width:2px\\n    style D fill:#8b5cf6,color:#fff,stroke:#7c3aed,stroke-width:2px\\n    style E fill:#ec4899,color:#fff,stroke:#db2777,stroke-width:2px"
- NEVER put the entire diagram on a single line
- ALWAYS include style statements for EVERY node"""

        def clean_mermaid_code(code: str) -> str:
            """Clean mermaid code from LLM output."""
            if not code:
                return code
            # Remove markdown fences
            code = code.strip()
            code = re.sub(r'^```mermaid\s*', '', code, flags=re.IGNORECASE)
            code = re.sub(r'^```\s*', '', code)
            code = re.sub(r'```\s*$', '', code)
            
            # Ensure proper newlines if code is single-line
            if '\n' not in code and len(code) > 50:
                # Add newlines after diagram declarations
                code = re.sub(r'(flowchart\s+(?:LR|RL|TB|TD|BT))\s+', r'\1\n    ', code, flags=re.IGNORECASE)
                code = re.sub(r'(mindmap)\s+', r'\1\n    ', code, flags=re.IGNORECASE)
                code = re.sub(r'(timeline)\s+', r'\1\n    ', code, flags=re.IGNORECASE)
                code = re.sub(r'(quadrantChart)\s+', r'\1\n    ', code, flags=re.IGNORECASE)
                # Add newlines before style statements
                code = re.sub(r'\s+(style\s+)', r'\n    \1', code, flags=re.IGNORECASE)
                # Add newlines for quadrantChart elements
                code = re.sub(r'\s+(x-axis\s+)', r'\n    \1', code, flags=re.IGNORECASE)
                code = re.sub(r'\s+(y-axis\s+)', r'\n    \1', code, flags=re.IGNORECASE)
                code = re.sub(r'\s+(quadrant-\d\s+)', r'\n    \1', code, flags=re.IGNORECASE)
            
            # Fix malformed style statements (style with multiple IDs like "style 3,4,6")
            # These are invalid - remove them entirely as they cause parse errors
            code = re.sub(r'^\s*style\s+[\d,\s]+.*$', '', code, flags=re.MULTILINE | re.IGNORECASE)
            
            # Fix style statements with invalid characters in node IDs
            # Valid: style A fill:#fff   Invalid: style 3,4 fill:#fff
            code = re.sub(r'^\s*style\s+[^A-Za-z_].*$', '', code, flags=re.MULTILINE)
            
            # Remove empty lines that may have been created
            code = re.sub(r'\n\s*\n', '\n', code)
            
            return code.strip()
        
        async def validate_and_repair_mermaid(code: str, diagram_type: str) -> str:
            """Validate Mermaid code and attempt repair if invalid.
            Based on Microsoft GenAIScript's auto-repair pattern.
            """
            # Basic syntax validation
            errors = []
            lines = code.split('\n')
            
            # Check for common issues
            if not lines or not lines[0].strip():
                errors.append("Empty diagram code")
            
            first_line = lines[0].strip().lower() if lines else ""
            valid_starts = ['flowchart', 'mindmap', 'timeline', 'pie', 'quadrantchart', 'graph']
            if not any(first_line.startswith(s) for s in valid_starts):
                errors.append(f"Invalid diagram start: '{first_line[:20]}...'")
            
            # Check for placeholder labels that should be replaced
            placeholder_patterns = ['Key Process', 'Item A', 'Item B', 'Stage 1', 'Step 1', 'Node 1']
            for pattern in placeholder_patterns:
                if pattern in code:
                    errors.append(f"Contains placeholder label: '{pattern}'")
            
            if errors:
                # Attempt repair by asking LLM to fix
                repair_prompt = f"""Fix these errors in the Mermaid diagram:
Errors: {', '.join(errors)}

Original code:
{code}

Return ONLY the fixed Mermaid code, no explanation."""
                try:
                    repair_result = await self._call_ollama_json(
                        "You fix Mermaid diagram syntax errors. Return JSON: {\"fixed_code\": \"...\"}",
                        repair_prompt
                    )
                    if repair_result.get("fixed_code"):
                        return clean_mermaid_code(repair_result["fixed_code"])
                except:
                    pass  # Return original if repair fails
            
            return code
        
        # Visual generation: single attempt with shorter timeout for speed
        for attempt in range(1):  # Only 1 attempt for visuals - fail fast
            try:
                result = await self._call_ollama_json(system_prompt, f"Content:\n{content[:8000]}", timeout_seconds=45.0)
                # Clean and validate the diagram codes
                if result.get("diagrams"):
                    for diagram in result["diagrams"]:
                        if "code" in diagram:
                            diagram["code"] = clean_mermaid_code(diagram["code"])
                            # Validate and repair if needed
                            diagram["code"] = await validate_and_repair_mermaid(
                                diagram["code"], 
                                diagram.get("diagram_type", "flowchart")
                            )
                return VisualSummary(**result)
            except Exception as e:
                if attempt == self.max_retries - 1:
                    return VisualSummary(
                        diagrams=[],
                        key_points=[f"Visual summary generation failed: {str(e)}"]
                    )
                await asyncio.sleep(1)
        
        return VisualSummary(diagrams=[], key_points=["Failed to generate"])
    
    async def extract_timeline(self, content: str) -> TimelineOutput:
        """Extract timeline events from content."""
        
        system_prompt = """You are a timeline extractor. Identify dates and events from the content.

Output a valid JSON object with this structure:
{
    "events": [
        {
            "date": "date or time period",
            "title": "short title",
            "description": "what happened",
            "importance": "low/medium/high",
            "source_reference": "optional source reference"
        }
    ],
    "time_span": "overall time period",
    "context": "brief context"
}

Rules:
- Extract all dates, years, and time periods mentioned
- Order events chronologically
- Include approximate dates if exact dates unknown
- Rate importance based on significance in the content"""

        for attempt in range(self.max_retries):
            try:
                result = await self._call_ollama_json(system_prompt, f"Content:\n{content[:8000]}")
                return TimelineOutput(**result)
            except Exception as e:
                if attempt == self.max_retries - 1:
                    return TimelineOutput(
                        events=[],
                        time_span="Unknown",
                        context=f"Timeline extraction failed: {str(e)}"
                    )
                await asyncio.sleep(1)
        
        return TimelineOutput(events=[], time_span="Unknown", context="Failed")
    
    async def compare_documents(self, doc1_content: str, doc2_content: str) -> DocumentComparison:
        """Compare two documents and identify similarities/differences."""
        
        system_prompt = """You are a document comparison expert. Analyze two documents and compare them.

Output a valid JSON object with this structure:
{
    "similarities": ["list of similarities"],
    "differences": ["list of differences"],
    "unique_to_first": ["points only in first doc"],
    "unique_to_second": ["points only in second doc"],
    "synthesis": "synthesized understanding combining both"
}

Rules:
- Be specific about what's similar and different
- Note contradictions if any
- Provide a useful synthesis that combines insights from both"""

        combined_prompt = f"DOCUMENT 1:\n{doc1_content[:4000]}\n\nDOCUMENT 2:\n{doc2_content[:4000]}"
        
        for attempt in range(self.max_retries):
            try:
                result = await self._call_ollama_json(system_prompt, combined_prompt)
                return DocumentComparison(**result)
            except Exception as e:
                if attempt == self.max_retries - 1:
                    return DocumentComparison(
                        similarities=[],
                        differences=[],
                        unique_to_first=[],
                        unique_to_second=[],
                        synthesis=f"Comparison failed: {str(e)}"
                    )
                await asyncio.sleep(1)
        
        return DocumentComparison(
            similarities=[], differences=[], unique_to_first=[],
            unique_to_second=[], synthesis="Failed"
        )
    
    async def assist_writing(
        self, 
        content: str, 
        task: str = "improve",
        format_style: str = "professional"
    ) -> WritingAssistance:
        """Assist with writing tasks."""
        
        system_prompt = f"""You are a writing assistant. Your task is to {task} the provided content.
        
Use this format style: {format_style}

Output a valid JSON object with this structure:
{{
    "content": "the improved/generated content",
    "format_used": "{format_style}",
    "suggestions": ["additional improvement suggestions"],
    "word_count": number
}}

Available format styles:
- professional: Clear, formal, suitable for business
- academic: Scholarly, with proper structure and citations style
- casual: Friendly, conversational
- technical: Precise, detailed, for technical audiences
- blog: Engaging, readable, with good flow
- email: Concise, clear, action-oriented"""

        for attempt in range(self.max_retries):
            try:
                result = await self._call_ollama_json(system_prompt, f"Content to work with:\n{content[:6000]}")
                return WritingAssistance(**result)
            except Exception as e:
                if attempt == self.max_retries - 1:
                    return WritingAssistance(
                        content=content,
                        format_used="none",
                        suggestions=[f"Writing assistance failed: {str(e)}"],
                        word_count=len(content.split())
                    )
                await asyncio.sleep(1)
        
        return WritingAssistance(
            content=content, format_used="none", suggestions=[], word_count=0
        )


# Singleton instance
structured_llm = StructuredLLMService()
