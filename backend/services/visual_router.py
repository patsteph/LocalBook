"""Visual Template Router

Routes content to the best visualization template based on analysis.
Maps the 25 essential templates to their Mermaid implementations.
"""
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from enum import Enum

from services.visual_analyzer import visual_analyzer, ContentAnalysis


class TemplateCategory(Enum):
    """Template categories based on storytelling purpose."""
    CONTEXT = "context"        # Establish context
    MECHANISM = "mechanism"    # Explain how
    ANALYSIS = "analysis"      # Compare & contrast
    PATTERN = "pattern"        # Reveal patterns
    PERSUADE = "persuade"      # Persuade & teach


@dataclass
class VisualTemplate:
    """A visual template definition."""
    id: str
    name: str
    category: TemplateCategory
    description: str
    mermaid_type: str  # flowchart, mindmap, timeline, etc.
    prompt_enhancement: str
    example_code: str
    best_for: List[str]


# The 25 Essential Templates (organized by storytelling purpose)
VISUAL_TEMPLATES: Dict[str, VisualTemplate] = {
    
    # === CATEGORY 1: ESTABLISH CONTEXT ===
    
    "key_stats": VisualTemplate(
        id="key_stats",
        name="Key Stats Highlight",
        category=TemplateCategory.CONTEXT,
        description="Lead with impact numbers",
        mermaid_type="mindmap",
        prompt_enhancement="Focus on the 3-5 most impactful statistics. Each stat should have a label and context.",
        example_code="""mindmap
  root((Key Metrics))
    Revenue
      $12M ARR
      47% YoY Growth
    Users
      3.2M Active
      82% Retention
    Performance
      99.9% Uptime
      <50ms Latency""",
        best_for=["numbers_stats", "overview"]
    ),
    
    "exec_summary": VisualTemplate(
        id="exec_summary",
        name="Executive Summary",
        category=TemplateCategory.CONTEXT,
        description="TL;DR for busy readers",
        mermaid_type="flowchart",
        prompt_enhancement="Structure as: Situation → Key Findings → Recommendation. Keep each box concise.",
        example_code="""flowchart LR
    subgraph Situation
        S[Current State]
    end
    subgraph Findings
        F1[Key Finding 1]
        F2[Key Finding 2]
        F3[Key Finding 3]
    end
    subgraph Action
        R[Recommendation]
    end
    S --> F1 & F2 & F3
    F1 & F2 & F3 --> R""",
        best_for=["overview", "recommendations"]
    ),
    
    "timeline": VisualTemplate(
        id="timeline",
        name="Timeline",
        category=TemplateCategory.CONTEXT,
        description="Show evolution and context",
        mermaid_type="timeline",
        prompt_enhancement="Order events chronologically. Highlight key turning points. Include dates/periods.",
        example_code="""timeline
    title Project Evolution
    section Phase 1
        Jan 2024 : Project kickoff
        Mar 2024 : MVP launched
    section Phase 2
        Jun 2024 : Major milestone
        Sep 2024 : Scale achieved
    section Current
        Dec 2024 : Present state""",
        best_for=["temporal", "history"]
    ),
    
    "overview_map": VisualTemplate(
        id="overview_map",
        name="Overview Map",
        category=TemplateCategory.CONTEXT,
        description="Big picture orientation",
        mermaid_type="mindmap",
        prompt_enhancement="Show the landscape of key players, concepts, or components. Keep it high-level.",
        example_code="""mindmap
  root((Domain Overview))
    Category A
      Player 1
      Player 2
    Category B
      Concept X
      Concept Y
    Category C
      Area 1
      Area 2""",
        best_for=["hierarchy", "categories"]
    ),
    
    # === CATEGORY 2: EXPLAIN HOW ===
    
    "horizontal_steps": VisualTemplate(
        id="horizontal_steps",
        name="Horizontal Steps",
        category=TemplateCategory.MECHANISM,
        description="Simple left-to-right step sequence with colorful boxes",
        mermaid_type="flowchart",
        prompt_enhancement="Create a simple LEFT-TO-RIGHT flow (LR) with numbered steps. NO decision diamonds. Each step is a colored box. Use style statements to add colors like: style A fill:#6366f1,color:#fff",
        example_code="""flowchart LR
    A["1. First Step"] --> B["2. Second Step"]
    B --> C["3. Third Step"]
    C --> D["4. Fourth Step"]
    D --> E["5. Final Step"]
    
    style A fill:#6366f1,color:#fff
    style B fill:#8b5cf6,color:#fff
    style C fill:#a855f7,color:#fff
    style D fill:#d946ef,color:#fff
    style E fill:#ec4899,color:#fff""",
        best_for=["steps_sequence", "process"]
    ),
    
    "process_flow": VisualTemplate(
        id="process_flow",
        name="Process Flow",
        category=TemplateCategory.MECHANISM,
        description="Step-by-step sequence with decision points",
        mermaid_type="flowchart",
        prompt_enhancement="Show clear start and end. Only use decision diamonds if there are ACTUAL branching decisions. For simple linear steps, use horizontal_steps instead.",
        example_code="""flowchart TD
    A[Start] --> B[Step 1]
    B --> C{Decision?}
    C -->|Yes| D[Path A]
    C -->|No| E[Path B]
    D --> F[Step 3]
    E --> F
    F --> G[End]""",
        best_for=["process", "decisions"]
    ),
    
    "system_architecture": VisualTemplate(
        id="system_architecture",
        name="System Architecture",
        category=TemplateCategory.MECHANISM,
        description="Components and connections",
        mermaid_type="flowchart",
        prompt_enhancement="Show components as boxes, connections as arrows. Group related items in subgraphs.",
        example_code="""flowchart TB
    subgraph Frontend
        UI[User Interface]
        API[API Layer]
    end
    subgraph Backend
        SVC[Services]
        DB[(Database)]
    end
    UI --> API
    API --> SVC
    SVC --> DB""",
        best_for=["hierarchy", "relationships"]
    ),
    
    "cycle_loop": VisualTemplate(
        id="cycle_loop",
        name="Cycle/Loop",
        category=TemplateCategory.MECHANISM,
        description="Recurring processes",
        mermaid_type="flowchart",
        prompt_enhancement="Show the circular nature of the process. Highlight feedback loops.",
        example_code="""flowchart LR
    A[Plan] --> B[Do]
    B --> C[Check]
    C --> D[Act]
    D --> A""",
        best_for=["steps_sequence", "relationships"]
    ),
    
    "anatomy": VisualTemplate(
        id="anatomy",
        name="Anatomy/Breakdown",
        category=TemplateCategory.MECHANISM,
        description="Parts of a whole",
        mermaid_type="mindmap",
        prompt_enhancement="Break down the subject into its constituent parts. Show how parts relate to the whole.",
        example_code="""mindmap
  root((Subject))
    Component A
      Sub-part 1
      Sub-part 2
    Component B
      Sub-part 3
      Sub-part 4
    Component C
      Sub-part 5""",
        best_for=["hierarchy", "categories"]
    ),
    
    "decision_tree": VisualTemplate(
        id="decision_tree",
        name="Decision Tree",
        category=TemplateCategory.MECHANISM,
        description="Branching logic",
        mermaid_type="flowchart",
        prompt_enhancement="Use diamonds for decisions, show all possible paths, label branches clearly.",
        example_code="""flowchart TD
    A{Main Question?}
    A -->|Option 1| B{Sub-question?}
    A -->|Option 2| C[Outcome A]
    B -->|Yes| D[Outcome B]
    B -->|No| E[Outcome C]""",
        best_for=["steps_sequence", "comparison"]
    ),
    
    # === CATEGORY 3: COMPARE & CONTRAST ===
    
    "side_by_side": VisualTemplate(
        id="side_by_side",
        name="Side-by-Side Comparison",
        category=TemplateCategory.ANALYSIS,
        description="A vs B comparison",
        mermaid_type="flowchart",
        prompt_enhancement="Create two parallel columns. Compare equivalent aspects. Highlight differences.",
        example_code="""flowchart LR
    subgraph Option_A[Option A]
        A1[Feature 1: High]
        A2[Feature 2: Medium]
        A3[Feature 3: Low]
    end
    subgraph Option_B[Option B]
        B1[Feature 1: Medium]
        B2[Feature 2: High]
        B3[Feature 3: High]
    end""",
        best_for=["comparison"]
    ),
    
    "quadrant": VisualTemplate(
        id="quadrant",
        name="2x2 Matrix/Quadrant",
        category=TemplateCategory.ANALYSIS,
        description="Two-dimensional analysis",
        mermaid_type="quadrantChart",
        prompt_enhancement="Choose meaningful axis dimensions. Place items accurately. Label quadrants.",
        example_code="""quadrantChart
    title Strategic Priority Matrix
    x-axis Low Effort --> High Effort
    y-axis Low Impact --> High Impact
    quadrant-1 Do First
    quadrant-2 Schedule
    quadrant-3 Delegate
    quadrant-4 Eliminate
    Task A: [0.8, 0.9]
    Task B: [0.3, 0.7]
    Task C: [0.6, 0.3]""",
        best_for=["comparison", "ranking"]
    ),
    
    "pros_cons": VisualTemplate(
        id="pros_cons",
        name="Pros/Cons List",
        category=TemplateCategory.ANALYSIS,
        description="Balanced evaluation",
        mermaid_type="flowchart",
        prompt_enhancement="Split into two clear columns. Keep items parallel and comparable.",
        example_code="""flowchart LR
    subgraph Pros[✅ Pros]
        P1[Benefit 1]
        P2[Benefit 2]
        P3[Benefit 3]
    end
    subgraph Cons[❌ Cons]
        C1[Drawback 1]
        C2[Drawback 2]
        C3[Drawback 3]
    end""",
        best_for=["pros_cons", "comparison"]
    ),
    
    "ranking": VisualTemplate(
        id="ranking",
        name="Ranking/Leaderboard",
        category=TemplateCategory.ANALYSIS,
        description="Ordered comparison",
        mermaid_type="flowchart",
        prompt_enhancement="Order items by importance/score. Show clear hierarchy. Include metrics if available.",
        example_code="""flowchart TD
    subgraph Rankings
        R1[🥇 #1: Leader - Score: 95]
        R2[🥈 #2: Runner-up - Score: 87]
        R3[🥉 #3: Third - Score: 82]
        R4[#4: Fourth - Score: 75]
        R5[#5: Fifth - Score: 68]
    end
    R1 --> R2 --> R3 --> R4 --> R5""",
        best_for=["ranking", "numbers_stats"]
    ),
    
    "spectrum": VisualTemplate(
        id="spectrum",
        name="Spectrum/Scale",
        category=TemplateCategory.ANALYSIS,
        description="Position on continuum",
        mermaid_type="flowchart",
        prompt_enhancement="Show a linear scale with clear endpoints. Position items along the spectrum.",
        example_code="""flowchart LR
    L[Low Risk] --- M1[Conservative] --- M2[Moderate] --- M3[Aggressive] --- H[High Risk]
    style M2 fill:#f9f,stroke:#333""",
        best_for=["comparison", "ranking"]
    ),
    
    # === CATEGORY 4: REVEAL PATTERNS ===
    
    "trend_chart": VisualTemplate(
        id="trend_chart",
        name="Trend Visualization",
        category=TemplateCategory.PATTERN,
        description="Change over time",
        mermaid_type="xychart-beta",
        prompt_enhancement="Show data points over time. Highlight significant changes or trends.",
        example_code="""xychart-beta
    title "Growth Trend"
    x-axis [Q1, Q2, Q3, Q4]
    y-axis "Revenue ($M)" 0 --> 100
    bar [30, 45, 62, 85]
    line [30, 45, 62, 85]""",
        best_for=["temporal", "numbers_stats"]
    ),
    
    "distribution": VisualTemplate(
        id="distribution",
        name="Distribution/Breakdown",
        category=TemplateCategory.PATTERN,
        description="Composition of a whole",
        mermaid_type="pie",
        prompt_enhancement="Show how parts make up the whole. Include percentages. Limit to 5-7 segments.",
        example_code="""pie showData
    title Market Share
    "Company A" : 35
    "Company B" : 28
    "Company C" : 20
    "Others" : 17""",
        best_for=["categories", "numbers_stats"]
    ),
    
    "funnel": VisualTemplate(
        id="funnel",
        name="Funnel",
        category=TemplateCategory.PATTERN,
        description="Conversion/attrition flow",
        mermaid_type="flowchart",
        prompt_enhancement="Show progressive narrowing. Include conversion rates between stages.",
        example_code="""flowchart TD
    A[Visitors: 10,000] --> B[Leads: 2,500]
    B --> C[Qualified: 500]
    C --> D[Proposals: 100]
    D --> E[Closed: 25]
    
    style A fill:#e0e0ff
    style E fill:#90EE90""",
        best_for=["steps_sequence", "numbers_stats"]
    ),
    
    "heatmap": VisualTemplate(
        id="heatmap",
        name="Heat Map",
        category=TemplateCategory.PATTERN,
        description="Intensity patterns across categories",
        mermaid_type="flowchart",
        prompt_enhancement="Show intensity using color coding. Red=high, Yellow=medium, Green=low.",
        example_code="""flowchart TB
    subgraph Matrix[Intensity Map]
        subgraph Row1[Category A]
            A1[High]
            A2[Medium]
            A3[Low]
        end
        subgraph Row2[Category B]
            B1[Low]
            B2[High]
            B3[Medium]
        end
    end
    style A1 fill:#ff6b6b
    style B2 fill:#ff6b6b
    style A2 fill:#ffd93d
    style B3 fill:#ffd93d
    style A3 fill:#6bcb77
    style B1 fill:#6bcb77""",
        best_for=["categories", "comparison"]
    ),
    
    # === CATEGORY 5: PERSUADE & TEACH ===
    
    "recommendation_stack": VisualTemplate(
        id="recommendation_stack",
        name="Recommendation Stack",
        category=TemplateCategory.PERSUADE,
        description="Prioritized actions",
        mermaid_type="flowchart",
        prompt_enhancement="Order by priority. Include rationale for each. Make actions specific.",
        example_code="""flowchart TD
    subgraph Priority[Recommended Actions]
        H[🔴 HIGH: Action 1 - Do immediately]
        M[🟡 MEDIUM: Action 2 - Schedule this week]
        L[🟢 LOW: Action 3 - When resources allow]
    end
    H --> M --> L""",
        best_for=["recommendations"]
    ),
    
    "key_takeaways": VisualTemplate(
        id="key_takeaways",
        name="Key Takeaways",
        category=TemplateCategory.PERSUADE,
        description="Memory anchors",
        mermaid_type="mindmap",
        prompt_enhancement="Limit to 3-5 key points. Make them memorable and actionable.",
        example_code="""mindmap
  root((Key Takeaways))
    💡 Insight 1
      Supporting detail
    💡 Insight 2
      Supporting detail
    💡 Insight 3
      Supporting detail""",
        best_for=["overview", "recommendations"]
    ),
    
    "concept_map": VisualTemplate(
        id="concept_map",
        name="Concept Map",
        category=TemplateCategory.PERSUADE,
        description="Relationship of ideas",
        mermaid_type="mindmap",
        prompt_enhancement="Show how concepts connect. Use clear relationship labels. Keep it navigable.",
        example_code="""mindmap
  root((Central Concept))
    Related Idea A
      Detail 1
      Detail 2
    Related Idea B
      Detail 3
        Sub-detail
    Related Idea C
      Detail 4""",
        best_for=["hierarchy", "relationships"]
    ),
    
    "call_to_action": VisualTemplate(
        id="call_to_action",
        name="Call to Action",
        category=TemplateCategory.PERSUADE,
        description="Clear next steps",
        mermaid_type="flowchart",
        prompt_enhancement="Make the desired action crystal clear. Show the path to take it.",
        example_code="""flowchart LR
    A[Current State] --> B{Ready?}
    B -->|Yes| C[Take Action]
    B -->|No| D[Prepare]
    D --> B
    C --> E[Success!]
    
    style C fill:#90EE90
    style E fill:#FFD700""",
        best_for=["recommendations", "steps_sequence"]
    ),
    
    # === PHASE 4: ADDITIONAL PERSUASION ===
    
    "exec_summary": VisualTemplate(
        id="exec_summary",
        name="Executive Summary",
        category=TemplateCategory.PERSUADE,
        description="Situation → Findings → Recommendation",
        mermaid_type="flowchart",
        prompt_enhancement="Distill into clear flow: Situation → Key Findings → Recommendation.",
        example_code="""flowchart LR
    subgraph Situation
        S[Current State]
    end
    subgraph Findings
        F1[Finding 1]
        F2[Finding 2]
    end
    subgraph Action
        R[Recommendation]
    end
    S --> F1 & F2
    F1 & F2 --> R""",
        best_for=["overview", "recommendations"]
    ),
    
    "argument": VisualTemplate(
        id="argument",
        name="Argument Structure",
        category=TemplateCategory.PERSUADE,
        description="Claim → Evidence → Conclusion (Pyramid Principle)",
        mermaid_type="flowchart",
        prompt_enhancement="Structure logically: Main claim at top, evidence below, conclusion follows.",
        example_code="""flowchart TD
    C[Main Claim]
    C --> E1[Evidence 1]
    C --> E2[Evidence 2]
    E1 --> S1[Data]
    E2 --> S2[Data]
    S1 & S2 --> CON[Conclusion]""",
        best_for=["recommendations", "hierarchy"]
    ),
    
    "scope": VisualTemplate(
        id="scope",
        name="Scope Definition",
        category=TemplateCategory.PERSUADE,
        description="In scope vs out of scope",
        mermaid_type="flowchart",
        prompt_enhancement="Show clear boundaries: what's included, excluded, and future consideration.",
        example_code="""flowchart TB
    subgraph InScope[✅ In Scope]
        I1[Item 1]
        I2[Item 2]
    end
    subgraph OutScope[❌ Out of Scope]
        O1[Excluded 1]
    end""",
        best_for=["categories", "comparison"]
    ),
    
    "overview": VisualTemplate(
        id="overview",
        name="Overview Map",
        category=TemplateCategory.CONTEXT,
        description="Big picture landscape orientation",
        mermaid_type="mindmap",
        prompt_enhancement="Show the full landscape of players, concepts, or components at a glance.",
        example_code="""mindmap
  root((Domain))
    Category A
      Item 1
      Item 2
    Category B
      Item 3
    Category C
      Item 4""",
        best_for=["hierarchy", "categories"]
    ),
    
    # === PHASE 5: ADVANCED CONSULTING/ACADEMIC ===
    
    "mece": VisualTemplate(
        id="mece",
        name="MECE Breakdown",
        category=TemplateCategory.ANALYSIS,
        description="Mutually Exclusive, Collectively Exhaustive categorization",
        mermaid_type="flowchart",
        prompt_enhancement="Categories must NOT overlap and must cover EVERYTHING. McKinsey standard.",
        example_code="""flowchart TD
    T[Total]
    T --> C1[Category 1]
    T --> C2[Category 2]
    T --> C3[Category 3]
    C1 --> S1A[Sub A]
    C1 --> S1B[Sub B]
    C2 --> S2A[Sub A]
    C3 --> S3A[Sub A]""",
        best_for=["hierarchy", "categories"]
    ),
    
    "force_field": VisualTemplate(
        id="force_field",
        name="Force Field Analysis",
        category=TemplateCategory.ANALYSIS,
        description="Driving forces vs restraining forces (Lewin)",
        mermaid_type="flowchart",
        prompt_enhancement="Balance forces for and against. Show goal in center.",
        example_code="""flowchart LR
    subgraph Driving[➡️ Drivers]
        D1[Force 1]
        D2[Force 2]
    end
    subgraph Center[ ]
        G[Goal]
    end
    subgraph Restraining[⬅️ Barriers]
        R1[Barrier 1]
        R2[Barrier 2]
    end
    D1 & D2 --> G
    R1 & R2 --> G""",
        best_for=["pros_cons", "comparison"]
    ),
    
    "stakeholder_map": VisualTemplate(
        id="stakeholder_map",
        name="Stakeholder Map",
        category=TemplateCategory.ANALYSIS,
        description="Power/Interest grid for stakeholder management",
        mermaid_type="quadrantChart",
        prompt_enhancement="Map stakeholders by Power (y) and Interest (x). Label management approach.",
        example_code="""quadrantChart
    title Stakeholder Map
    x-axis Low Interest --> High Interest
    y-axis Low Power --> High Power
    quadrant-1 Manage Closely
    quadrant-2 Keep Satisfied
    quadrant-3 Monitor
    quadrant-4 Keep Informed
    CEO: [0.9, 0.9]
    Team: [0.8, 0.3]""",
        best_for=["comparison", "ranking"]
    ),
    
    "side_by_side": VisualTemplate(
        id="side_by_side",
        name="Side by Side Columns",
        category=TemplateCategory.CONTEXT,
        description="Items displayed in equal columns side by side - Napkin.ai style",
        mermaid_type="flowchart",
        prompt_enhancement="""Create a BEAUTIFUL LEFT-TO-RIGHT horizontal flowchart showing items side by side.
CRITICAL RULES:
1. Use ONLY the exact items/stages provided - do NOT add fake data, dates, or extra categories
2. Use flowchart LR direction for horizontal layout
3. EVERY node MUST have vibrant, distinct colors using style statements
4. Keep labels SHORT (2-4 words max)
5. Add stroke and stroke-width for professional borders""",
        example_code="""flowchart LR
    A[AI Assistant] --> B[Co-Pilot]
    B --> C[Modular Tasks]
    C --> D[Autonomous Ops]
    D --> E[Full Replacement]
    style A fill:#3b82f6,color:#fff,stroke:#2563eb,stroke-width:2px
    style B fill:#22c55e,color:#fff,stroke:#16a34a,stroke-width:2px
    style C fill:#f59e0b,color:#000,stroke:#d97706,stroke-width:2px
    style D fill:#8b5cf6,color:#fff,stroke:#7c3aed,stroke-width:2px
    style E fill:#ec4899,color:#fff,stroke:#db2777,stroke-width:2px""",
        best_for=["stages", "columns", "comparison", "side_by_side"]
    ),
    
    "stages_progression": VisualTemplate(
        id="stages_progression",
        name="Stages/Phases",
        category=TemplateCategory.CONTEXT,
        description="Show progression through numbered stages or phases - Napkin.ai style",
        mermaid_type="flowchart",
        prompt_enhancement="""Create a BEAUTIFUL flowchart showing stages/phases in progression.
CRITICAL RULES:
1. Use ONLY the exact stages mentioned in the content - do NOT invent stages
2. Do NOT add dates, years, or numbers that are not explicitly provided
3. Keep labels SHORT (2-4 words max per stage)
4. Use flowchart LR for horizontal layout
5. EVERY node MUST have vibrant, distinct colors
6. Add stroke and stroke-width for professional borders""",
        example_code="""flowchart LR
    A[Phase 1] --> B[Phase 2]
    B --> C[Phase 3]
    C --> D[Phase 4]
    D --> E[Phase 5]
    style A fill:#3b82f6,color:#fff,stroke:#2563eb,stroke-width:2px
    style B fill:#22c55e,color:#fff,stroke:#16a34a,stroke-width:2px
    style C fill:#f59e0b,color:#000,stroke:#d97706,stroke-width:2px
    style D fill:#8b5cf6,color:#fff,stroke:#7c3aed,stroke-width:2px
    style E fill:#ec4899,color:#fff,stroke:#db2777,stroke-width:2px""",
        best_for=["stages", "phases", "progression", "evolution"]
    ),
    
    "causal_loop": VisualTemplate(
        id="causal_loop",
        name="Causal Loop Diagram",
        category=TemplateCategory.PATTERN,
        description="System dynamics with feedback loops",
        mermaid_type="flowchart",
        prompt_enhancement="Use + for reinforcing, - for balancing relationships. Show feedback loops.",
        example_code="""flowchart LR
    A[Sales] -->|+| B[Revenue]
    B -->|+| C[Investment]
    C -->|+| A
    B -->|-| D[Costs]""",
        best_for=["relationships", "steps_sequence"]
    ),
    
    "evidence_synthesis": VisualTemplate(
        id="evidence_synthesis",
        name="Evidence Synthesis",
        category=TemplateCategory.ANALYSIS,
        description="Multi-source triangulation for research",
        mermaid_type="flowchart",
        prompt_enhancement="Show how evidence from multiple sources supports conclusions.",
        example_code="""flowchart TD
    subgraph Sources
        S1[Source 1]
        S2[Source 2]
    end
    subgraph Evidence
        E1[Evidence A]
        E2[Evidence B]
    end
    subgraph Conclusions
        C1[Conclusion]
    end
    S1 --> E1
    S2 --> E1 & E2
    E1 & E2 --> C1""",
        best_for=["hierarchy", "relationships"]
    ),
}


class VisualRouter:
    """Routes content to the best visual template."""
    
    def __init__(self):
        self.templates = VISUAL_TEMPLATES
        self.analyzer = visual_analyzer
    
    def get_template(self, template_id: str) -> Optional[VisualTemplate]:
        """Get a template by ID."""
        return self.templates.get(template_id)
    
    def route(self, text: str) -> Tuple[VisualTemplate, ContentAnalysis]:
        """Route content to the best template.
        
        Returns: (best_template, analysis)
        """
        analysis = self.analyzer.analyze(text)
        
        if analysis.suggested_templates:
            template_id = analysis.suggested_templates[0]
            template = self.templates.get(template_id)
            if template:
                return template, analysis
        
        # Fallback to concept_map
        return self.templates["concept_map"], analysis
    
    def get_alternatives(self, text: str, count: int = 3) -> List[Tuple[VisualTemplate, str]]:
        """Get alternative template suggestions with reasons.
        
        GUARANTEES diverse options by selecting templates with different mermaid_types.
        Returns: List of (template, reason) tuples
        """
        recommendations = self.analyzer.get_template_recommendations(text, count + 5)  # Get extra for better filtering
        
        alternatives = []
        seen_types = set()
        
        for template_id, confidence, reason in recommendations:
            template = self.templates.get(template_id)
            if template:
                # Ensure diversity: don't repeat the same mermaid_type
                if template.mermaid_type not in seen_types:
                    alternatives.append((template, reason))
                    seen_types.add(template.mermaid_type)
                    if len(alternatives) >= count:
                        break
        
        # ALWAYS fill to count with diverse fallbacks - this guarantees multiple options
        # Order by visual diversity and quality
        diverse_fallbacks = [
            ("mindmap", "Hierarchical concept overview"),
            ("timeline", "Chronological progression"),
            ("quadrant", "Two-dimensional comparison"),
            ("horizontal_steps", "Step-by-step process flow"),
            ("pie", "Proportional distribution"),
            ("concept_map", "Interconnected concepts"),
            ("stages_progression", "Sequential stages"),
            ("side_by_side", "Parallel comparison"),
        ]
        
        for fallback_id, reason in diverse_fallbacks:
            if len(alternatives) >= count:
                break
            template = self.templates.get(fallback_id)
            if template and template.mermaid_type not in seen_types:
                alternatives.append((template, reason))
                seen_types.add(template.mermaid_type)
        
        return alternatives[:count]
    
    def list_templates_by_category(self, category: TemplateCategory) -> List[VisualTemplate]:
        """List all templates in a category."""
        return [t for t in self.templates.values() if t.category == category]
    
    def get_all_template_ids(self) -> List[str]:
        """Get all available template IDs."""
        return list(self.templates.keys())


# Singleton instance
visual_router = VisualRouter()
