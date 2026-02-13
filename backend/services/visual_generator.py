"""Visual Generator Service

Enhanced visual generation using the Essential 8 templates.
Produces high-quality Mermaid diagrams with smart template-specific prompts.

Phase 2: Key Stats, Process Flow, Comparison, Timeline, 
         Pros/Cons, Key Takeaways, Concept Map, Anatomy
"""
import asyncio
import json
import re
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
import httpx

from config import settings
from services.visual_router import visual_router, VisualTemplate, VISUAL_TEMPLATES
from services.visual_analyzer import visual_analyzer


@dataclass
class GeneratedVisual:
    """Result of visual generation."""
    success: bool
    template_id: str
    template_name: str
    mermaid_code: str
    title: str
    description: str
    key_points: List[str]
    alternatives: List[Dict[str, str]]
    error: Optional[str] = None


# Template-specific system prompts for high-quality output
TEMPLATE_PROMPTS = {
    
    "key_stats": """You are creating a KEY STATS visualization to highlight important metrics.

TASK: Extract 4-6 key statistics from the content and create a mindmap showing them.

OUTPUT FORMAT (JSON):
{
    "mermaid": "mindmap\\n  root((Key Metrics))\\n    Category 1\\n      Stat: Value\\n    Category 2\\n      Stat: Value",
    "title": "Key Metrics Overview",
    "description": "Summary of the most important statistics",
    "key_points": ["Insight 1", "Insight 2", "Insight 3"]
}

RULES:
- Group stats into 2-4 logical categories
- Include the actual numbers/percentages
- Format: "Metric: Value" for each stat
- Highlight the most impactful number""",

    "process_flow": """You are creating a PROCESS FLOW diagram to show sequential steps.

TASK: Identify the main process/workflow and create a flowchart showing each step.

OUTPUT FORMAT (JSON):
{
    "mermaid": "flowchart TD\\n    A[Start] --> B[Step 1]\\n    B --> C{Decision?}\\n    C -->|Yes| D[Action A]\\n    C -->|No| E[Action B]\\n    D --> F[End]\\n    E --> F",
    "title": "Process Flow Title",
    "description": "Description of the process",
    "key_points": ["Key step 1", "Key decision point", "Important outcome"]
}

RULES:
- Maximum 8-12 steps for readability
- Use decision diamonds {text} for branching
- Use [text] for regular steps
- Label all branches with descriptive text
- Clear start and end points""",

    "side_by_side": """You are creating a COMPARISON diagram to show differences between options.

TASK: Create a side-by-side comparison of the main options/alternatives.

OUTPUT FORMAT (JSON):
{
    "mermaid": "flowchart LR\\n    subgraph Option_A[Option A]\\n        A1[Feature 1: High]\\n        A2[Feature 2: Low]\\n    end\\n    subgraph Option_B[Option B]\\n        B1[Feature 1: Low]\\n        B2[Feature 2: High]\\n    end",
    "title": "Comparison: A vs B",
    "description": "Side-by-side comparison of options",
    "key_points": ["Key difference 1", "Key difference 2", "Recommendation"]
}

RULES:
- Compare equivalent features across options
- Use consistent formatting for both sides
- Highlight key differentiators
- Maximum 5-6 features per option""",

    "timeline": """You are creating a TIMELINE diagram to show chronological progression.

TASK: Extract key events/milestones and arrange them chronologically.

OUTPUT FORMAT (JSON):
{
    "mermaid": "timeline\\n    title Timeline Title\\n    section Period 1\\n        Date 1 : Event description\\n        Date 2 : Event description\\n    section Period 2\\n        Date 3 : Milestone",
    "title": "Timeline: Subject",
    "description": "Chronological view of events",
    "key_points": ["Major milestone 1", "Turning point", "Current state"]
}

RULES:
- Maximum 8-10 events for readability
- Group into 2-4 logical periods/phases
- Include dates or time references
- Highlight key turning points""",

    "pros_cons": """You are creating a PROS/CONS diagram for balanced evaluation.

TASK: Identify advantages and disadvantages and present them side by side.

OUTPUT FORMAT (JSON):
{
    "mermaid": "flowchart LR\\n    subgraph Pros[✅ Pros]\\n        P1[Benefit 1]\\n        P2[Benefit 2]\\n        P3[Benefit 3]\\n    end\\n    subgraph Cons[❌ Cons]\\n        C1[Drawback 1]\\n        C2[Drawback 2]\\n    end",
    "title": "Pros & Cons: Subject",
    "description": "Balanced evaluation of advantages and disadvantages",
    "key_points": ["Main benefit", "Main concern", "Bottom line"]
}

RULES:
- Balance both sides (similar number of items)
- Keep descriptions concise (3-5 words each)
- Maximum 4-5 items per side
- Use ✅ and ❌ emoji prefixes""",

    "key_takeaways": """You are creating a KEY TAKEAWAYS diagram for memorable insights.

TASK: Distill the content into 3-5 key takeaways that are memorable and actionable.

OUTPUT FORMAT (JSON):
{
    "mermaid": "mindmap\\n  root((Key Takeaways))\\n    💡 Insight 1\\n      Supporting detail\\n    💡 Insight 2\\n      Supporting detail\\n    💡 Insight 3\\n      Supporting detail",
    "title": "Key Takeaways",
    "description": "The most important insights to remember",
    "key_points": ["Takeaway 1", "Takeaway 2", "Takeaway 3"]
}

RULES:
- Exactly 3-5 takeaways
- Each takeaway should be actionable or memorable
- Include one supporting detail per takeaway
- Use 💡 emoji prefix for main points""",

    "concept_map": """You are creating a CONCEPT MAP to show relationships between ideas.

TASK: Identify the central concept and map how related ideas connect to it.

OUTPUT FORMAT (JSON):
{
    "mermaid": "mindmap\\n  root((Central Concept))\\n    Related Idea A\\n      Detail 1\\n      Detail 2\\n    Related Idea B\\n      Detail 3\\n    Related Idea C\\n      Detail 4\\n        Sub-detail",
    "title": "Concept Map: Subject",
    "description": "How key concepts relate to each other",
    "key_points": ["Main concept", "Key relationship", "Important connection"]
}

RULES:
- One clear central concept
- 3-5 main branches (related ideas)
- Maximum 3 levels deep
- Show meaningful relationships""",

    "anatomy": """You are creating an ANATOMY/BREAKDOWN diagram to show parts of a whole.

TASK: Break down the subject into its constituent parts and components.

OUTPUT FORMAT (JSON):
{
    "mermaid": "mindmap\\n  root((Subject))\\n    Component A\\n      Sub-part 1\\n      Sub-part 2\\n    Component B\\n      Sub-part 3\\n    Component C\\n      Sub-part 4\\n      Sub-part 5",
    "title": "Anatomy of Subject",
    "description": "Breaking down the components",
    "key_points": ["Main component", "Key part", "Important element"]
}

RULES:
- 3-5 main components
- 2-4 sub-parts per component
- Show hierarchical structure
- Use consistent level of detail""",

    # === PHASE 3: ANALYSIS TEMPLATES ===
    
    "quadrant": """You are creating a QUADRANT CHART for two-dimensional analysis.

TASK: Place items on a 2x2 matrix based on two key dimensions from the content.

OUTPUT FORMAT (JSON):
{
    "mermaid": "quadrantChart\\n    title Analysis Matrix\\n    x-axis Low Dimension1 --> High Dimension1\\n    y-axis Low Dimension2 --> High Dimension2\\n    quadrant-1 High Both\\n    quadrant-2 High Y Only\\n    quadrant-3 Low Both\\n    quadrant-4 High X Only\\n    Item A: [0.8, 0.9]\\n    Item B: [0.3, 0.7]\\n    Item C: [0.6, 0.2]",
    "title": "Quadrant Analysis: Subject",
    "description": "Two-dimensional comparison of items",
    "key_points": ["Top performers", "Key insight", "Action item"]
}

RULES:
- Choose two meaningful, distinct dimensions for axes
- Place 4-8 items with accurate coordinates [0-1, 0-1]
- Label all four quadrants with descriptive names
- Coordinates should reflect actual position on both dimensions""",

    "trend_chart": """You are creating a TREND CHART to show change over time.

TASK: Extract time-series data and visualize the trend.

OUTPUT FORMAT (JSON):
{
    "mermaid": "xychart-beta\\n    title \\"Trend Title\\"\\n    x-axis [Period1, Period2, Period3, Period4]\\n    y-axis \\"Metric\\" 0 --> 100\\n    bar [25, 40, 55, 75]\\n    line [25, 40, 55, 75]",
    "title": "Trend: Subject Over Time",
    "description": "How the metric changed over time",
    "key_points": ["Starting point", "Key change", "Current state"]
}

RULES:
- Use actual numbers from the content
- 4-8 time periods for clarity
- Include both bar and line for visual impact
- Set y-axis range appropriately for the data""",

    "funnel": """You are creating a FUNNEL diagram to show conversion or attrition.

TASK: Show progressive stages with decreasing quantities.

OUTPUT FORMAT (JSON):
{
    "mermaid": "flowchart TD\\n    A[Stage 1: 10,000] --> B[Stage 2: 2,500]\\n    B --> C[Stage 3: 500]\\n    C --> D[Stage 4: 100]\\n    D --> E[Final: 25]\\n    style A fill:#e0e0ff\\n    style E fill:#90EE90",
    "title": "Funnel: Process Name",
    "description": "Conversion through stages",
    "key_points": ["Total input", "Key drop-off point", "Final conversion rate"]
}

RULES:
- Show 4-6 stages maximum
- Include actual numbers at each stage
- Calculate and mention conversion rates
- Style first stage light blue, last stage green""",

    "ranking": """You are creating a RANKING/LEADERBOARD visualization.

TASK: Order items by score, performance, or importance.

OUTPUT FORMAT (JSON):
{
    "mermaid": "flowchart TD\\n    subgraph Rankings\\n        R1[🥇 #1: Top Item - Score: 95]\\n        R2[🥈 #2: Second - Score: 87]\\n        R3[🥉 #3: Third - Score: 82]\\n        R4[#4: Fourth - Score: 75]\\n        R5[#5: Fifth - Score: 68]\\n    end\\n    R1 --> R2 --> R3 --> R4 --> R5",
    "title": "Rankings: Category",
    "description": "Ordered comparison by performance",
    "key_points": ["Leader", "Notable performer", "Key gap"]
}

RULES:
- Rank 5-10 items maximum
- Include scores/metrics when available
- Use medal emojis for top 3
- Show clear descending order""",

    "distribution": """You are creating a PIE CHART to show composition/distribution.

TASK: Show how parts make up a whole with percentages.

OUTPUT FORMAT (JSON):
{
    "mermaid": "pie showData\\n    title Distribution Title\\n    \\"Category A\\" : 35\\n    \\"Category B\\" : 28\\n    \\"Category C\\" : 20\\n    \\"Category D\\" : 12\\n    \\"Others\\" : 5",
    "title": "Distribution: Subject",
    "description": "Breakdown of composition",
    "key_points": ["Largest segment", "Notable finding", "Insight"]
}

RULES:
- Maximum 5-7 segments for readability
- Values should sum to 100 (or close)
- Group small items into "Others" if needed
- Order by size (largest first)""",

    "spectrum": """You are creating a SPECTRUM/SCALE visualization.

TASK: Position items along a continuum between two extremes.

OUTPUT FORMAT (JSON):
{
    "mermaid": "flowchart LR\\n    L[Low End] --- P1[Position 1] --- P2[Position 2] --- P3[Position 3] --- H[High End]\\n    style P2 fill:#f9f,stroke:#333",
    "title": "Spectrum: Dimension",
    "description": "Positioning items on a scale",
    "key_points": ["Low end example", "Middle ground", "High end example"]
}

RULES:
- Clear, contrasting endpoints
- 3-5 positions along the spectrum
- Highlight the most relevant position
- Use meaningful dimension labels""",

    "heatmap": """You are creating a HEATMAP-style visualization to show intensity patterns.

TASK: Show intensity or concentration across categories using a matrix.

OUTPUT FORMAT (JSON):
{
    "mermaid": "flowchart TB\\n    subgraph Matrix[Intensity Map]\\n        subgraph Row1[Category A]\\n            A1[High]\\n            A2[Medium]\\n            A3[Low]\\n        end\\n        subgraph Row2[Category B]\\n            B1[Low]\\n            B2[High]\\n            B3[Medium]\\n        end\\n    end\\n    style A1 fill:#ff6b6b\\n    style B2 fill:#ff6b6b\\n    style A2 fill:#ffd93d\\n    style B3 fill:#ffd93d\\n    style A3 fill:#6bcb77\\n    style B1 fill:#6bcb77",
    "title": "Intensity Map: Subject",
    "description": "Pattern of intensity across categories",
    "key_points": ["Hotspot", "Cool zone", "Pattern insight"]
}

RULES:
- Use color coding: red=high, yellow=medium, green=low
- 2-4 rows and 2-4 columns
- Label rows and columns clearly
- Highlight notable patterns""",

    # === PHASE 4: PERSUASION TEMPLATES ===
    
    "exec_summary": """You are creating an EXECUTIVE SUMMARY visualization.

TASK: Distill content into Situation → Findings → Recommendation flow.

OUTPUT FORMAT (JSON):
{
    "mermaid": "flowchart LR\\n    subgraph Situation\\n        S[Current State]\\n    end\\n    subgraph Findings\\n        F1[Finding 1]\\n        F2[Finding 2]\\n        F3[Finding 3]\\n    end\\n    subgraph Action\\n        R[Recommendation]\\n    end\\n    S --> F1 & F2 & F3\\n    F1 & F2 & F3 --> R",
    "title": "Executive Summary: Topic",
    "description": "High-level overview for decision makers",
    "key_points": ["Situation", "Key finding", "Recommended action"]
}

RULES:
- One clear situation statement
- 2-4 key findings
- One actionable recommendation
- Flow should be left-to-right for readability""",

    "recommendation_stack": """You are creating a RECOMMENDATION STACK for prioritized actions.

TASK: Extract recommendations and prioritize them by importance/urgency.

OUTPUT FORMAT (JSON):
{
    "mermaid": "flowchart TD\\n    subgraph Priority[Recommended Actions]\\n        H[🔴 HIGH: Action 1 - Do immediately]\\n        M[🟡 MEDIUM: Action 2 - Schedule soon]\\n        L[🟢 LOW: Action 3 - When possible]\\n    end\\n    H --> M --> L",
    "title": "Recommendations: Topic",
    "description": "Prioritized action items",
    "key_points": ["Top priority", "Key rationale", "Expected outcome"]
}

RULES:
- 3-5 recommendations maximum
- Clear priority levels (High/Medium/Low)
- Each action should be specific and actionable
- Include brief rationale for each""",

    "argument": """You are creating an ARGUMENT STRUCTURE diagram for logical persuasion.

TASK: Structure the argument as Claim → Evidence → Conclusion (Pyramid Principle).

OUTPUT FORMAT (JSON):
{
    "mermaid": "flowchart TD\\n    C[Main Claim]\\n    C --> E1[Evidence 1]\\n    C --> E2[Evidence 2]\\n    C --> E3[Evidence 3]\\n    E1 --> S1[Supporting Data]\\n    E2 --> S2[Supporting Data]\\n    E3 --> S3[Supporting Data]\\n    S1 & S2 & S3 --> CON[Conclusion]",
    "title": "Argument: Thesis",
    "description": "Logical structure supporting the main claim",
    "key_points": ["Main claim", "Strongest evidence", "Conclusion"]
}

RULES:
- One clear main claim at top
- 2-4 evidence points supporting the claim
- Supporting data under each evidence point
- Conclusion that follows logically""",

    "call_to_action": """You are creating a CALL TO ACTION visualization.

TASK: Make the desired action crystal clear with path to take it.

OUTPUT FORMAT (JSON):
{
    "mermaid": "flowchart LR\\n    A[Current State] --> B{Ready?}\\n    B -->|Yes| C[Take Action]\\n    B -->|No| D[Prepare]\\n    D --> B\\n    C --> E[🎯 Success!]\\n    style C fill:#90EE90\\n    style E fill:#FFD700",
    "title": "Call to Action: Next Steps",
    "description": "Clear path to taking action",
    "key_points": ["The action to take", "How to prepare", "Expected outcome"]
}

RULES:
- One clear primary action
- Show preparation path if needed
- Highlight success state
- Make action specific and immediate""",

    "scope": """You are creating a SCOPE DEFINITION diagram.

TASK: Show what is in scope vs out of scope for a project/discussion.

OUTPUT FORMAT (JSON):
{
    "mermaid": "flowchart TB\\n    subgraph InScope[✅ In Scope]\\n        I1[Item 1]\\n        I2[Item 2]\\n        I3[Item 3]\\n    end\\n    subgraph OutScope[❌ Out of Scope]\\n        O1[Excluded 1]\\n        O2[Excluded 2]\\n    end\\n    subgraph Future[🔮 Future Consideration]\\n        F1[Later Item]\\n    end",
    "title": "Scope: Project/Topic",
    "description": "Boundaries of what is included and excluded",
    "key_points": ["Core scope", "Key exclusion", "Future potential"]
}

RULES:
- 3-5 in-scope items
- 2-3 out-of-scope items
- Optional future consideration section
- Use emoji prefixes for clarity""",

    "overview": """You are creating an OVERVIEW MAP for big-picture orientation.

TASK: Show the landscape of key players, concepts, or components.

OUTPUT FORMAT (JSON):
{
    "mermaid": "mindmap\\n  root((Domain Overview))\\n    Category A\\n      Item 1\\n      Item 2\\n    Category B\\n      Item 3\\n      Item 4\\n    Category C\\n      Item 5\\n      Item 6",
    "title": "Overview: Domain",
    "description": "Big picture view of the landscape",
    "key_points": ["Main category", "Key player", "Important element"]
}

RULES:
- 3-5 main categories
- 2-4 items per category
- Keep it high-level (not detailed)
- Show the whole landscape at a glance""",

    # === PHASE 5: ADVANCED TEMPLATES ===
    
    "mece": """You are creating a MECE BREAKDOWN (Mutually Exclusive, Collectively Exhaustive).

TASK: Break down a topic into non-overlapping categories that cover everything.

OUTPUT FORMAT (JSON):
{
    "mermaid": "flowchart TD\\n    T[Total Topic]\\n    T --> C1[Category 1]\\n    T --> C2[Category 2]\\n    T --> C3[Category 3]\\n    C1 --> S1A[Sub 1A]\\n    C1 --> S1B[Sub 1B]\\n    C2 --> S2A[Sub 2A]\\n    C2 --> S2B[Sub 2B]\\n    C3 --> S3A[Sub 3A]\\n    C3 --> S3B[Sub 3B]",
    "title": "MECE Breakdown: Topic",
    "description": "Non-overlapping, complete categorization",
    "key_points": ["Total scope", "Key category", "Completeness check"]
}

RULES:
- Categories must NOT overlap (Mutually Exclusive)
- Categories must cover EVERYTHING (Collectively Exhaustive)
- 3-5 main categories
- 2-3 sub-items per category
- McKinsey consulting standard""",

    "force_field": """You are creating a FORCE FIELD ANALYSIS (Lewin model).

TASK: Show driving forces vs restraining forces for a change/decision.

OUTPUT FORMAT (JSON):
{
    "mermaid": "flowchart LR\\n    subgraph Driving[➡️ Driving Forces]\\n        D1[Force 1]\\n        D2[Force 2]\\n        D3[Force 3]\\n    end\\n    subgraph Center[ ]\\n        G[Goal/Change]\\n    end\\n    subgraph Restraining[⬅️ Restraining Forces]\\n        R1[Barrier 1]\\n        R2[Barrier 2]\\n        R3[Barrier 3]\\n    end\\n    D1 & D2 & D3 --> G\\n    R1 & R2 & R3 --> G",
    "title": "Force Field: Change/Decision",
    "description": "Forces for and against the change",
    "key_points": ["Strongest driver", "Biggest barrier", "Net assessment"]
}

RULES:
- Balance driving and restraining forces
- 3-5 forces on each side
- Show the goal/change in the center
- Consider relative strength of forces""",

    "stakeholder_map": """You are creating a STAKEHOLDER MAP (Power/Interest grid).

TASK: Map stakeholders by their power and interest level.

OUTPUT FORMAT (JSON):
{
    "mermaid": "quadrantChart\\n    title Stakeholder Map\\n    x-axis Low Interest --> High Interest\\n    y-axis Low Power --> High Power\\n    quadrant-1 Manage Closely\\n    quadrant-2 Keep Satisfied\\n    quadrant-3 Monitor\\n    quadrant-4 Keep Informed\\n    Stakeholder A: [0.8, 0.9]\\n    Stakeholder B: [0.3, 0.8]\\n    Stakeholder C: [0.7, 0.3]\\n    Stakeholder D: [0.2, 0.2]",
    "title": "Stakeholder Map: Project",
    "description": "Power vs Interest mapping of stakeholders",
    "key_points": ["Key stakeholder", "Management approach", "Risk area"]
}

RULES:
- 4-8 stakeholders
- Position based on Power (y) and Interest (x)
- Label quadrants with management approach
- Identify who needs closest attention""",

    "causal_loop": """You are creating a CAUSAL LOOP DIAGRAM for system dynamics.

TASK: Show cause-and-effect relationships and feedback loops.

OUTPUT FORMAT (JSON):
{
    "mermaid": "flowchart LR\\n    A[Variable A] -->|+| B[Variable B]\\n    B -->|+| C[Variable C]\\n    C -->|-| A\\n    B -->|+| D[Variable D]\\n    D -->|-| C",
    "title": "Causal Loop: System",
    "description": "Cause and effect relationships with feedback",
    "key_points": ["Key driver", "Feedback loop", "Leverage point"]
}

RULES:
- Use + for reinforcing relationships (more A → more B)
- Use - for balancing relationships (more A → less B)
- Identify feedback loops (reinforcing or balancing)
- 4-6 variables maximum for clarity
- Show system dynamics""",

    "evidence_synthesis": """You are creating an EVIDENCE SYNTHESIS diagram for multi-source triangulation.

TASK: Show how evidence from multiple sources supports conclusions.

OUTPUT FORMAT (JSON):
{
    "mermaid": "flowchart TD\\n    subgraph Sources\\n        S1[Source 1]\\n        S2[Source 2]\\n        S3[Source 3]\\n    end\\n    subgraph Evidence\\n        E1[Evidence A]\\n        E2[Evidence B]\\n        E3[Evidence C]\\n    end\\n    subgraph Conclusions\\n        C1[Conclusion 1]\\n        C2[Conclusion 2]\\n    end\\n    S1 --> E1\\n    S2 --> E1 & E2\\n    S3 --> E2 & E3\\n    E1 & E2 --> C1\\n    E2 & E3 --> C2",
    "title": "Evidence Synthesis: Topic",
    "description": "How multiple sources support conclusions",
    "key_points": ["Primary source", "Key evidence", "Main conclusion"]
}

RULES:
- Show 2-4 distinct sources
- Map evidence from each source
- Show how evidence supports conclusions
- Identify triangulation (multiple sources → same conclusion)
- Academic research standard""",
}


class VisualGenerator:
    """Generates high-quality visuals using template-specific prompts."""
    
    def __init__(self):
        self.base_url = settings.ollama_base_url
        self.model = settings.ollama_model
        self.max_retries = 3
        self.router = visual_router
        self.analyzer = visual_analyzer
    
    async def _call_llm(self, system_prompt: str, content: str) -> Dict[str, Any]:
        """Call LLM with JSON output."""
        timeout = httpx.Timeout(120.0)
        
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": f"{system_prompt}\n\nCONTENT TO VISUALIZE:\n{content[:6000]}",
                    "stream": False,
                    "format": "json",
                    "options": {
                        "temperature": 0.7,
                        "num_predict": 2000,
                    }
                }
            )
            result = response.json()
            
            try:
                return json.loads(result.get("response", "{}"))
            except json.JSONDecodeError:
                return {}
    
    def _fix_mermaid_syntax(self, code: str, mermaid_type: str) -> str:
        """Fix common Mermaid syntax issues."""
        if not code:
            return code
        
        # Ensure proper line breaks
        code = code.replace("\\n", "\n")
        
        # Fix double quotes in node labels
        code = re.sub(r'\[\"([^"]+)\"\]', r'[\1]', code)
        
        # Ensure diagram type is on first line
        first_line = code.split('\n')[0].strip().lower()
        valid_starts = ['flowchart', 'mindmap', 'timeline', 'pie', 'quadrantchart', 'xychart']
        
        if not any(first_line.startswith(s) for s in valid_starts):
            if mermaid_type == "mindmap":
                code = f"mindmap\n{code}"
            elif mermaid_type == "timeline":
                code = f"timeline\n{code}"
            elif mermaid_type == "pie":
                code = f"pie showData\n{code}"
            else:
                code = f"flowchart TD\n{code}"
        
        return code.strip()
    
    async def generate(
        self, 
        content: str, 
        template_id: Optional[str] = None
    ) -> GeneratedVisual:
        """Generate a visual from content.
        
        Args:
            content: The text content to visualize
            template_id: Optional specific template to use (auto-routes if None)
        
        Returns:
            GeneratedVisual with the result
        """
        # Analyze content and route to template
        self.analyzer.analyze(content)
        
        if template_id:
            template = self.router.get_template(template_id)
            if not template:
                return GeneratedVisual(
                    success=False,
                    template_id=template_id,
                    template_name="Unknown",
                    mermaid_code="",
                    title="",
                    description="",
                    key_points=[],
                    alternatives=[],
                    error=f"Template '{template_id}' not found"
                )
        else:
            template, _ = self.router.route(content)
        
        # Get alternatives
        alternatives = [
            {"id": t.id, "name": t.name, "reason": reason}
            for t, reason in self.router.get_alternatives(content, 3)
        ]
        
        # Get template-specific prompt
        system_prompt = TEMPLATE_PROMPTS.get(
            template.id,
            self._build_generic_prompt(template)
        )
        
        # Generate with retries
        for attempt in range(self.max_retries):
            try:
                result = await self._call_llm(system_prompt, content)
                
                mermaid_code = result.get("mermaid", "")
                if not mermaid_code:
                    continue
                
                # Fix syntax issues
                mermaid_code = self._fix_mermaid_syntax(mermaid_code, template.mermaid_type)
                
                return GeneratedVisual(
                    success=True,
                    template_id=template.id,
                    template_name=template.name,
                    mermaid_code=mermaid_code,
                    title=result.get("title", f"{template.name} Visualization"),
                    description=result.get("description", template.description),
                    key_points=result.get("key_points", []),
                    alternatives=alternatives,
                )
            except Exception as e:
                if attempt == self.max_retries - 1:
                    return GeneratedVisual(
                        success=False,
                        template_id=template.id,
                        template_name=template.name,
                        mermaid_code="",
                        title="",
                        description="",
                        key_points=[],
                        alternatives=alternatives,
                        error=str(e)
                    )
                await asyncio.sleep(1)
        
        return GeneratedVisual(
            success=False,
            template_id=template.id,
            template_name=template.name,
            mermaid_code="",
            title="",
            description="",
            key_points=[],
            alternatives=alternatives,
            error="Failed to generate after retries"
        )
    
    def _build_generic_prompt(self, template: VisualTemplate) -> str:
        """Build a generic prompt for templates without specific prompts."""
        return f"""You are creating a {template.name} visualization.

TASK: {template.description}

{template.prompt_enhancement}

OUTPUT FORMAT (JSON):
{{
    "mermaid": "valid {template.mermaid_type} Mermaid code",
    "title": "Descriptive title",
    "description": "What this visualization shows",
    "key_points": ["Insight 1", "Insight 2", "Insight 3"]
}}

EXAMPLE:
{template.example_code}

RULES:
- Output must be valid Mermaid syntax
- Keep it readable (not overcrowded)
- Use clear, concise labels
- Capture essential structure, not every detail"""
    
    async def generate_multiple(
        self, 
        content: str, 
        template_ids: List[str]
    ) -> List[GeneratedVisual]:
        """Generate multiple visuals from content.
        
        Args:
            content: The text content to visualize
            template_ids: List of template IDs to generate
        
        Returns:
            List of GeneratedVisual results
        """
        tasks = [self.generate(content, tid) for tid in template_ids]
        return await asyncio.gather(*tasks)
    
    def get_available_templates(self) -> List[Dict[str, str]]:
        """Get list of all available templates."""
        return [
            {
                "id": t.id,
                "name": t.name,
                "category": t.category.value,
                "description": t.description,
            }
            for t in VISUAL_TEMPLATES.values()
        ]


# Singleton instance
visual_generator = VisualGenerator()
