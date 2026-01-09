"""Output Quality Templates - World-Class Document Generation

This module defines professional-grade templates and guidelines for all output types.
Each template ensures distinctive, high-quality outputs that maximize user value.

Design Principles:
1. DISTINCTIVENESS - Each output type must be clearly different in structure and purpose
2. PROFESSIONAL QUALITY - Outputs should match or exceed industry standards
3. MULTI-SOURCE SYNTHESIS - Always weave together insights from multiple sources
4. ACTIONABLE STRUCTURE - Information organized for maximum retention and utility
5. SOURCE ATTRIBUTION - Clear references to source materials throughout
6. EXTENSIBILITY - Templates designed to work with future features (AI agents, exports, etc.)
"""

from typing import Dict, Optional, List
from dataclasses import dataclass


@dataclass
class OutputTemplate:
    """Defines a complete output template with all quality parameters."""
    template_id: str
    name: str
    description: str
    system_prompt: str
    structure_requirements: List[str]
    quality_checklist: List[str]
    min_sections: int
    example_structure: str
    tone: str
    target_audience: str


# =============================================================================
# DOCUMENT TEMPLATES - Professional Grade
# =============================================================================

DOCUMENT_TEMPLATES: Dict[str, OutputTemplate] = {
    
    "briefing": OutputTemplate(
        template_id="briefing",
        name="Executive Briefing",
        description="C-suite ready briefing document for rapid decision-making",
        system_prompt="""You are an expert executive communication specialist creating a briefing for senior leadership.

Your briefing must:
1. Lead with the most critical insight (the "so what")
2. Present information in order of strategic importance
3. Synthesize across ALL sources - never just summarize one source
4. Quantify impacts where possible (numbers, percentages, timeframes)
5. End with clear, actionable recommendations

Write as if presenting to a CEO with 5 minutes to make a decision.""",
        
        structure_requirements=[
            "EXECUTIVE SUMMARY (3-4 sentences max - the entire briefing in miniature)",
            "SITUATION OVERVIEW (current state, context)",
            "KEY FINDINGS (numbered, prioritized by impact)",
            "ANALYSIS (what the findings mean, cross-source insights)",
            "IMPLICATIONS (risks, opportunities, trade-offs)",
            "RECOMMENDATIONS (specific, actionable, prioritized)",
            "APPENDIX: Sources consulted"
        ],
        
        quality_checklist=[
            "Can a busy executive understand the core message in 30 seconds?",
            "Are findings synthesized across multiple sources?",
            "Are recommendations specific and actionable?",
            "Is there quantification where relevant?",
            "Is jargon minimized or defined?"
        ],
        
        min_sections=6,
        
        example_structure="""# Executive Briefing: [Topic]
*Prepared: [Date] | Sources: [N] documents*

## Executive Summary
[3-4 sentence overview capturing: situation, key finding, recommendation]

## Situation Overview
[2-3 paragraphs of context]

## Key Findings
1. **[Finding Title]**: [Impact statement] *(Sources: 1, 3)*
2. **[Finding Title]**: [Impact statement] *(Sources: 2, 4)*
3. **[Finding Title]**: [Impact statement] *(Sources: 1, 2, 3)*

## Analysis
[Cross-source synthesis - what patterns emerge, what contradictions exist]

## Implications
- **Opportunity**: [Description]
- **Risk**: [Description]  
- **Trade-off**: [Description]

## Recommendations
1. **[Action]** - [Rationale] - *Priority: High*
2. **[Action]** - [Rationale] - *Priority: Medium*

---
*Sources: [List with brief descriptions]*""",
        
        tone="authoritative, concise, action-oriented",
        target_audience="Senior executives and decision-makers"
    ),
    
    "study_guide": OutputTemplate(
        template_id="study_guide",
        name="Study Guide",
        description="Comprehensive learning material optimized for retention",
        system_prompt="""You are an expert instructional designer creating a study guide for mastery learning.

Your study guide must:
1. Build knowledge progressively from foundational to advanced
2. Include memory aids (mnemonics, analogies, visual cues)
3. Connect concepts across sources to show relationships
4. Provide self-assessment opportunities
5. Use the proven pedagogical structure: Preview → Content → Review

Write for someone preparing for an important exam or presentation.""",
        
        structure_requirements=[
            "LEARNING OBJECTIVES (what the reader will know/be able to do)",
            "KEY VOCABULARY (essential terms with clear definitions)",
            "CONCEPT MAP (how ideas connect - can be text-based)",
            "MAIN CONTENT (organized by learning objective)",
            "KEY TAKEAWAYS (bulleted summary of essentials)",
            "SELF-ASSESSMENT (questions to test understanding)",
            "FURTHER EXPLORATION (what to study next)"
        ],
        
        quality_checklist=[
            "Are learning objectives specific and measurable?",
            "Does content build logically from simple to complex?",
            "Are there memory aids for difficult concepts?",
            "Do self-assessment questions cover all objectives?",
            "Are cross-source connections highlighted?"
        ],
        
        min_sections=6,
        
        example_structure="""# Study Guide: [Topic]
*Estimated study time: [X] minutes | Difficulty: [Level]*

## Learning Objectives
After studying this guide, you will be able to:
- [ ] [Objective 1 - action verb + specific outcome]
- [ ] [Objective 2]
- [ ] [Objective 3]

## Key Vocabulary
| Term | Definition | Example |
|------|------------|---------|
| [Term] | [Clear definition] | [Concrete example] |

## How It All Connects
[Text-based concept map or relationship diagram]
```
[Core Concept] 
    ├── [Related Idea 1] → [Outcome]
    ├── [Related Idea 2] → [Outcome]
    └── [Related Idea 3] → [Outcome]
```

## Core Concepts

### 1. [Concept Name]
**What it is**: [Definition]
**Why it matters**: [Significance]
**How to remember**: 💡 [Mnemonic or analogy]
**From the sources**: [Key quotes/facts with attribution]

### 2. [Concept Name]
[Same structure...]

## Key Takeaways
✅ [Essential point 1]
✅ [Essential point 2]
✅ [Essential point 3]

## Self-Assessment
**Quick Check** (answers at bottom):
1. [Question testing recall]
2. [Question testing understanding]
3. [Question testing application]

**Deep Thinking**:
- [Open-ended question requiring synthesis]

## Further Exploration
- [Related topic to explore]
- [Advanced concept to study next]

---
*Answers: 1.[A] 2.[B] 3.[C]*""",
        
        tone="encouraging, clear, educational",
        target_audience="Students and self-learners"
    ),
    
    "faq": OutputTemplate(
        template_id="faq",
        name="FAQ Document",
        description="Comprehensive Q&A covering all aspects of the topic",
        system_prompt="""You are an expert knowledge base architect creating an FAQ that anticipates user needs.

Your FAQ must:
1. Cover questions at multiple levels (beginner → expert)
2. Answer the question that was asked AND the question behind the question
3. Cross-reference related questions
4. Include the "why" not just the "what"
5. Synthesize answers from multiple sources when relevant

Write as if creating documentation for a product millions will use.""",
        
        structure_requirements=[
            "QUICK ANSWERS (top 3-5 most common questions)",
            "GETTING STARTED (foundational questions)",
            "CORE CONCEPTS (main topic questions)",
            "ADVANCED TOPICS (expert-level questions)",
            "TROUBLESHOOTING (common issues)",
            "RELATED TOPICS (cross-references)"
        ],
        
        quality_checklist=[
            "Does each answer fully resolve the question?",
            "Are questions organized by user journey/expertise?",
            "Do answers cite specific sources?",
            "Are related questions cross-referenced?",
            "Is there a mix of simple and complex questions?"
        ],
        
        min_sections=4,
        
        example_structure="""# Frequently Asked Questions: [Topic]
*[N] questions answered | Last updated: [Date]*

## Quick Answers

### What is [topic] in one sentence?
[One clear sentence answer]

### Why should I care about [topic]?
[Direct value proposition]

---

## Getting Started

### Q: [Foundational question]?
**Short answer**: [1-2 sentences]

**Detailed answer**: [Comprehensive explanation with source attribution]

*Related*: See also "[Related question]"

### Q: [Next foundational question]?
[Same structure...]

---

## Core Concepts

### Q: [Main topic question]?
**Short answer**: [Brief response]

**Detailed answer**: 
[Thorough explanation]

**Key points**:
- [Point 1] *(Source: [Name])*
- [Point 2] *(Source: [Name])*

*Related*: "[Related question 1]", "[Related question 2]"

---

## Advanced Topics

### Q: [Expert-level question]?
[Detailed technical answer with nuance]

---

## Troubleshooting

### Q: What if [common problem]?
**Solution**: [Step-by-step resolution]

---

*Sources consulted: [List]*""",
        
        tone="helpful, thorough, accessible",
        target_audience="Anyone seeking to understand the topic"
    ),
    
    "deep_dive": OutputTemplate(
        template_id="deep_dive",
        name="Deep Dive Analysis",
        description="Comprehensive exploration connecting ideas across sources",
        system_prompt="""You are a research analyst creating an in-depth exploration for subject matter experts.

Your deep dive must:
1. Go beyond surface-level summary to uncover patterns and insights
2. Explicitly connect ideas ACROSS different sources
3. Identify contradictions, tensions, and nuances
4. Present multiple perspectives on complex issues
5. Draw original conclusions from synthesized information

Write as if preparing a briefing paper for a think tank.""",
        
        structure_requirements=[
            "ABSTRACT (comprehensive overview)",
            "INTRODUCTION (why this matters, scope)",
            "BACKGROUND & CONTEXT (essential foundation)",
            "THEMATIC ANALYSIS (organized by themes, not sources)",
            "CROSS-SOURCE SYNTHESIS (patterns, contradictions)",
            "IMPLICATIONS & INSIGHTS (original analysis)",
            "CONCLUSIONS",
            "REFERENCES"
        ],
        
        quality_checklist=[
            "Does analysis go beyond summarizing individual sources?",
            "Are explicit connections drawn between sources?",
            "Are contradictions and nuances addressed?",
            "Are conclusions supported by cross-source evidence?",
            "Would an expert find this analysis valuable?"
        ],
        
        min_sections=6,
        
        example_structure="""# Deep Dive: [Topic]
*Analysis based on [N] sources | [Word count] words*

## Abstract
[200-word comprehensive overview of the entire analysis]

## Introduction
[Why this topic matters, what questions we're exploring, scope of analysis]

## Background & Context
[Essential foundation needed to understand the analysis]

## Thematic Analysis

### Theme 1: [Theme Name]
[Analysis organized by THEME not by source]

**Perspective from [Source A]**: [Key insight]
**Perspective from [Source B]**: [Key insight]  
**Synthesis**: [What we learn from combining these perspectives]

### Theme 2: [Theme Name]
[Same structure...]

## Cross-Source Synthesis

### Patterns Identified
- **Pattern 1**: Observed across [Sources X, Y, Z]: [Description]
- **Pattern 2**: [Description]

### Contradictions & Tensions
- **[Source A] vs [Source B]**: [Description of disagreement and possible resolution]

### Gaps in the Literature
- [What's not addressed by the sources]

## Implications & Insights

### For [Stakeholder Group 1]
[Specific implications]

### For [Stakeholder Group 2]
[Specific implications]

### Original Insights
[Conclusions drawn from synthesis that aren't in any single source]

## Conclusions
[Summary of key findings and their significance]

## References
[Full source list with brief descriptions]""",
        
        tone="analytical, nuanced, scholarly",
        target_audience="Subject matter experts and researchers"
    ),
    
    "summary": OutputTemplate(
        template_id="summary",
        name="Executive Summary",
        description="Concise synthesis of key points for quick consumption",
        system_prompt="""You are a professional summarization expert creating a synthesis for busy professionals.

Your summary must:
1. Capture the essence, not just list facts
2. Prioritize by importance, not by source order
3. Synthesize across sources - don't summarize each separately
4. Highlight what's surprising or counterintuitive
5. End with clear takeaways

Write as if the reader has only 3 minutes but needs complete understanding.""",
        
        structure_requirements=[
            "ONE-PARAGRAPH OVERVIEW (the entire summary in miniature)",
            "KEY POINTS (prioritized, synthesized)",
            "NOTABLE INSIGHTS (surprising findings)",
            "TAKEAWAYS (actionable conclusions)"
        ],
        
        quality_checklist=[
            "Can someone understand the topic fully from just this summary?",
            "Is information prioritized by importance?",
            "Are insights synthesized across sources?",
            "Are takeaways clear and actionable?"
        ],
        
        min_sections=4,
        
        example_structure="""# Summary: [Topic]
*Synthesized from [N] sources | [X]-minute read*

## Overview
[One paragraph capturing the complete essence - if someone reads nothing else, they get this]

## Key Points

### Most Important
🔑 **[Key insight]**: [Explanation with cross-source support]

### Critical Context  
📌 **[Supporting point]**: [Explanation]

### Notable Finding
💡 **[Interesting discovery]**: [Explanation]

## Surprising Insights
- [Counterintuitive finding] *(Sources: [Names])*
- [Unexpected connection]

## Takeaways
1. **[Actionable conclusion 1]**
2. **[Actionable conclusion 2]**
3. **[Actionable conclusion 3]**

---
*Based on: [Source list]*""",
        
        tone="concise, insightful, professional",
        target_audience="Busy professionals needing quick understanding"
    ),
    
    "explain": OutputTemplate(
        template_id="explain",
        name="Simple Explanation",
        description="Complex topics made accessible to anyone",
        system_prompt="""You are an expert science communicator making complex topics accessible to everyone.

Your explanation must:
1. Use everyday language - no jargon without immediate explanation
2. Build understanding step-by-step
3. Use relatable analogies and concrete examples
4. Anticipate and address confusion points
5. Connect to things the reader already knows

Write as if explaining to a curious, intelligent friend with no background in the topic.""",
        
        structure_requirements=[
            "THE BIG PICTURE (one-sentence essence)",
            "WHY IT MATTERS (relevance to everyday life)",
            "THE BASICS (foundational concepts simply explained)",
            "HOW IT WORKS (step-by-step breakdown)",
            "COMMON MISCONCEPTIONS (what people get wrong)",
            "THE BOTTOM LINE (key takeaway)"
        ],
        
        quality_checklist=[
            "Could a 12-year-old understand this?",
            "Are all technical terms explained when first used?",
            "Are analogies relatable and accurate?",
            "Does it connect to everyday experience?",
            "Is it engaging, not dumbed down?"
        ],
        
        min_sections=5,
        
        example_structure="""# Understanding [Topic]
*No background required | [X]-minute read*

## The Big Picture
**In one sentence**: [Simple essence of the topic]

## Why Should You Care?
[How this affects everyday life, why it's relevant]

## The Basics

### Think of it like...
🎯 **Analogy**: [Relatable comparison]

[Explanation building on the analogy]

### The Key Concepts

**[Concept 1]**: [Simple explanation]
> *Think of it as*: [Everyday comparison]

**[Concept 2]**: [Simple explanation]
> *Example*: [Concrete, relatable example]

## How It Actually Works

**Step 1**: [Simple explanation]
↓
**Step 2**: [Simple explanation]  
↓
**Step 3**: [Simple explanation]

## Wait, But What About...?

### "I thought [common misconception]?"
Actually, [correction with simple explanation]

### "Doesn't that mean [another misconception]?"
Not quite. Here's why: [explanation]

## The Bottom Line
[2-3 sentences capturing the essential understanding]

**Remember**: [One memorable takeaway]

---
*Sources: [List] - simplified for accessibility*""",
        
        tone="friendly, patient, engaging",
        target_audience="Anyone curious about the topic, regardless of background"
    ),
    
    "debate": OutputTemplate(
        template_id="debate",
        name="Debate Analysis",
        description="Multiple perspectives on complex topics explored fairly",
        system_prompt="""You are a skilled debate moderator presenting multiple perspectives with perfect fairness.

Your debate must:
1. Present each perspective with its strongest arguments
2. Be genuinely balanced - not favoring any side
3. Include evidence from sources for each position
4. Identify common ground and key disagreements
5. Help the reader form their own informed opinion

Write as if hosting an intellectual debate where all sides deserve respect.""",
        
        structure_requirements=[
            "THE DEBATE (what's being contested)",
            "POSITION A (strongest case for)",
            "POSITION B (strongest case against)",
            "ADDITIONAL PERSPECTIVES (nuanced views)",
            "COMMON GROUND (what all sides agree on)",
            "KEY TENSIONS (irreconcilable differences)",
            "FORMING YOUR VIEW (questions to consider)"
        ],
        
        quality_checklist=[
            "Are all positions presented with their strongest arguments?",
            "Is the presentation genuinely balanced?",
            "Are positions supported by source evidence?",
            "Are common ground and differences clearly identified?",
            "Does it help readers form informed opinions?"
        ],
        
        min_sections=6,
        
        example_structure="""# The Debate: [Topic]
*Exploring multiple perspectives*

## The Central Question
[Clear statement of what's being debated]

## Position A: [Viewpoint Name]
**Core Argument**: [Main claim]

**Key Points**:
1. [Argument with evidence]
2. [Argument with evidence]
3. [Argument with evidence]

**Strongest Evidence**: [Most compelling support from sources]

## Position B: [Viewpoint Name]  
**Core Argument**: [Main claim]

**Key Points**:
1. [Argument with evidence]
2. [Argument with evidence]
3. [Argument with evidence]

**Strongest Evidence**: [Most compelling support from sources]

## Nuanced Perspectives

### The Middle Ground View
[Position that incorporates elements of both]

### The "It Depends" View
[Position that emphasizes context]

## Common Ground
Despite disagreements, most perspectives agree that:
- [Shared belief 1]
- [Shared belief 2]

## Key Tensions
The fundamental disagreements center on:
- **[Issue 1]**: [Why it's contested]
- **[Issue 2]**: [Why it's contested]

## Forming Your Own View
Consider these questions:
1. [Question to help reader evaluate positions]
2. [Question about their values/priorities]
3. [Question about evidence they find compelling]

---
*This analysis synthesizes perspectives from: [Source list]*""",
        
        tone="balanced, respectful, intellectually honest",
        target_audience="Anyone wanting to understand multiple sides of an issue"
    ),
    
    "podcast_script": OutputTemplate(
        template_id="podcast_script",
        name="Podcast Script",
        description="Engaging audio content with natural conversation flow",
        system_prompt="""You are a podcast producer creating an engaging, educational conversation.

Your podcast must:
1. Sound like natural conversation, not reading from a script
2. Have distinct host personalities that complement each other
3. Build narrative momentum - hook early, build interest, deliver payoff
4. Include moments of genuine insight and "aha" moments
5. Reference sources naturally within conversation

Write as if creating a podcast that listeners recommend to friends.""",
        
        structure_requirements=[
            "COLD OPEN (hook that grabs attention)",
            "INTRODUCTION (hosts, topic, why it matters)",
            "SEGMENT 1 (foundation/context)",
            "SEGMENT 2 (main content/exploration)",
            "SEGMENT 3 (implications/applications)",
            "WRAP-UP (key takeaways, call to action)"
        ],
        
        quality_checklist=[
            "Would you want to listen to this conversation?",
            "Do hosts have distinct, complementary voices?",
            "Is information delivered conversationally?",
            "Are there moments of genuine insight?",
            "Does it maintain interest throughout?"
        ],
        
        min_sections=5,
        
        example_structure="""# Podcast: [Episode Title]
*Duration: ~[X] minutes | Topic: [Topic]*

---

## [COLD OPEN]

**HOST A**: [Attention-grabbing statement or question]

**HOST B**: [Intrigued response]

*[Theme music]*

---

## [INTRODUCTION]

**HOST A**: Welcome back to [Podcast Name]. I'm [Name], and with me as always is [Name].

**HOST B**: Hey everyone! So today we're diving into [topic], and honestly, I was surprised by what we found in the research.

**HOST A**: Right? Like, I thought I understood [topic], but [interesting hook]...

---

## SEGMENT 1: Setting the Stage

**HOST A**: Okay, let's start with the basics. [Name], break it down for us.

**HOST B**: So here's the thing... [natural explanation]

**HOST A**: [Follow-up question that listeners might have]

**HOST B**: Great question. According to [source], [answer]...

[Continue natural dialogue]

---

## SEGMENT 2: Going Deeper

**HOST A**: This is where it gets interesting...

[Continue with main exploration, hosts building on each other]

---

## SEGMENT 3: So What?

**HOST B**: Okay, but what does this actually mean for [audience]?

**HOST A**: Well, here's what I took away from all this...

[Practical implications discussion]

---

## [WRAP-UP]

**HOST A**: Alright, let's land this plane. Key takeaways?

**HOST B**: 
1. [Takeaway 1]
2. [Takeaway 2]

**HOST A**: And [Takeaway 3]. 

**HOST B**: That's it for today! If you enjoyed this, [call to action].

**HOST A**: See you next time!

*[Outro music]*

---
*Research sources: [List]*""",
        
        tone="conversational, engaging, informative",
        target_audience="Podcast listeners seeking educational content"
    ),
}


# =============================================================================
# VISUAL TEMPLATES - Diagram Excellence
# =============================================================================

VISUAL_TEMPLATES: Dict[str, Dict] = {
    
    "mindmap": {
        "name": "Mind Map",
        "description": "Hierarchical concept visualization for understanding relationships",
        "system_prompt": """Create a mind map that reveals the hierarchical structure of concepts.

Requirements:
1. Central node captures the core concept
2. First-level branches are major themes (3-5 max)
3. Sub-branches show supporting details
4. Balance depth and breadth (no branch > 4 levels deep)
5. Use clear, concise labels (2-4 words per node)

The mind map should help someone grasp the entire topic structure at a glance.""",
        
        "quality_checklist": [
            "Is the central concept clearly identified?",
            "Are branches logically grouped?",
            "Is it readable without overwhelming detail?",
            "Do relationships between concepts make sense?",
        ],
        
        "example": """mindmap
  root((Topic Name))
    Major Theme 1
      Detail A
      Detail B
        Sub-detail
    Major Theme 2
      Detail C
      Detail D
    Major Theme 3
      Detail E"""
    },
    
    "flowchart": {
        "name": "Flowchart",
        "description": "Process and decision visualization",
        "system_prompt": """Create a flowchart that clearly shows process flow and decision points.

Requirements:
1. Clear start and end points
2. Decision diamonds for branching logic
3. Consistent flow direction (top-down or left-right)
4. Maximum 10-15 nodes for readability
5. Label all connections when meaning isn't obvious

The flowchart should allow someone to follow a process step-by-step.""",
        
        "quality_checklist": [
            "Is the starting point clear?",
            "Are decision points marked as diamonds?",
            "Can the flow be followed without confusion?",
            "Is it the right level of detail?",
        ],
        
        "example": """flowchart TD
    A[Start] --> B{Decision?}
    B -->|Yes| C[Action 1]
    B -->|No| D[Action 2]
    C --> E[Result]
    D --> E
    E --> F[End]"""
    },
    
    "timeline": {
        "name": "Timeline",
        "description": "Chronological event visualization",
        "system_prompt": """Create a timeline showing the chronological progression of events.

Requirements:
1. Events in chronological order
2. Clear date/time markers
3. Concise event descriptions
4. Highlight key turning points
5. Maximum 8-10 events for readability

The timeline should help someone understand how things evolved over time.""",
        
        "quality_checklist": [
            "Are events in correct chronological order?",
            "Are dates/periods clearly marked?",
            "Are the most significant events highlighted?",
            "Is the scope appropriate (not too long/short)?",
        ],
        
        "example": """timeline
    title Key Events in [Topic]
    section Early Period
        Date 1 : Event description
        Date 2 : Event description
    section Development
        Date 3 : Major milestone
    section Recent
        Date 4 : Current state"""
    },
    
    "quadrant": {
        "name": "Quadrant Chart",
        "description": "Two-dimensional comparison and categorization",
        "system_prompt": """Create a quadrant chart for comparing items across two dimensions.

Requirements:
1. Clear, meaningful axis labels
2. Items placed accurately based on characteristics
3. 4-8 items for clarity
4. Quadrant labels that add insight
5. Axes should represent meaningful contrasts

The quadrant should help someone quickly categorize and compare items.""",
        
        "quality_checklist": [
            "Are axis dimensions meaningful and distinct?",
            "Are items placed accurately?",
            "Do quadrant groupings provide insight?",
            "Is it clear what being in each quadrant means?",
        ],
        
        "example": """quadrantChart
    title Comparison: [Dimension 1] vs [Dimension 2]
    x-axis Low [Dim 1] --> High [Dim 1]
    y-axis Low [Dim 2] --> High [Dim 2]
    quadrant-1 High Both
    quadrant-2 High Dim2 Only
    quadrant-3 Low Both
    quadrant-4 High Dim1 Only
    Item A: [0.8, 0.9]
    Item B: [0.3, 0.7]"""
    },
    
    "classDiagram": {
        "name": "Hierarchy Diagram",
        "description": "Structural relationships and classifications",
        "system_prompt": """Create a hierarchy/class diagram showing structural relationships.

Requirements:
1. Clear parent-child relationships
2. Use proper inheritance arrows
3. Group related items
4. Maximum 3-4 levels deep
5. Include key attributes where relevant

The diagram should help someone understand how concepts relate structurally.""",
        
        "quality_checklist": [
            "Are hierarchical relationships accurate?",
            "Is the structure logically organized?",
            "Are connection types appropriate?",
            "Is complexity manageable?",
        ],
        
        "example": """classDiagram
    class ParentConcept {
        +key attribute
        +another attribute
    }
    class ChildA {
        +specific attribute
    }
    class ChildB {
        +different attribute
    }
    ParentConcept <|-- ChildA
    ParentConcept <|-- ChildB"""
    },
}


# =============================================================================
# QUIZ TEMPLATES - Learning Excellence  
# =============================================================================

QUIZ_TEMPLATES: Dict[str, Dict] = {
    
    "multiple_choice": {
        "name": "Multiple Choice",
        "description": "Standard multiple choice with thoughtful distractors",
        "guidelines": """Create multiple choice questions that test understanding, not just recall.

Requirements:
1. Question tests a meaningful concept, not trivial detail
2. All options are plausible (no obviously wrong answers)
3. Distractors represent common misconceptions
4. Only one clearly correct answer
5. Options are similar in length and structure
6. Avoid "all of the above" or "none of the above" """,
    },
    
    "true_false": {
        "name": "True/False",
        "description": "Binary assessment of factual claims",
        "guidelines": """Create true/false questions that test precise understanding.

Requirements:
1. Statement is unambiguously true or false
2. Tests meaningful concepts, not trivial facts
3. Avoid absolute words (always, never) unless accurate
4. False statements should be plausibly incorrect
5. Include why the answer is correct in explanation""",
    },
}


# =============================================================================
# AUDIO TEMPLATES - Voice Excellence
# =============================================================================

AUDIO_TEMPLATES: Dict[str, Dict] = {
    
    "podcast": {
        "name": "Podcast Conversation",
        "description": "Two-host educational conversation",
        "guidelines": """Create podcast scripts that sound natural and engaging.

Requirements:
1. Hosts have distinct personalities (curious questioner + knowledgeable explainer)
2. Dialogue feels natural, not scripted
3. Includes verbal cues for emphasis and transitions
4. Appropriate pacing with moments to breathe
5. Information delivered conversationally
6. References sources naturally ("according to the research...")""",
    },
    
    "lecture": {
        "name": "Educational Lecture",
        "description": "Single-speaker instructional content",
        "guidelines": """Create lecture content optimized for audio learning.

Requirements:
1. Clear verbal signposting ("First... Second... Finally...")
2. Repetition of key concepts for retention
3. Pauses built in for comprehension
4. Examples that work without visuals
5. Summary at end reinforcing main points""",
    },
}


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_document_template(template_id: str) -> Optional[OutputTemplate]:
    """Get a document template by ID."""
    return DOCUMENT_TEMPLATES.get(template_id)


def get_visual_template(template_id: str) -> Optional[Dict]:
    """Get a visual template by ID."""
    return VISUAL_TEMPLATES.get(template_id)


def get_all_document_types() -> List[str]:
    """Get list of all available document types."""
    return list(DOCUMENT_TEMPLATES.keys())


def get_all_visual_types() -> List[str]:
    """Get list of all available visual types."""
    return list(VISUAL_TEMPLATES.keys())


def build_document_prompt(template_id: str, topic: str, style: str, source_count: int) -> tuple[str, str]:
    """Build complete system and user prompts for document generation.
    
    Returns:
        Tuple of (system_prompt, format_instructions)
    """
    template = DOCUMENT_TEMPLATES.get(template_id)
    
    if not template:
        # Fallback for unknown template
        return (
            "Create high-quality, well-structured content based on the provided sources.",
            "Format clearly with appropriate sections using markdown."
        )
    
    system_prompt = f"""{template.system_prompt}

TARGET AUDIENCE: {template.target_audience}
TONE: {template.tone}
STYLE: {style}

QUALITY REQUIREMENTS:
{chr(10).join(f'- {check}' for check in template.quality_checklist)}

You are working with {source_count} source document(s). Synthesize across ALL sources."""
    
    format_instructions = f"""REQUIRED STRUCTURE:
{chr(10).join(f'{i+1}. {req}' for i, req in enumerate(template.structure_requirements))}

EXAMPLE FORMAT:
{template.example_structure}

Ensure your output has at least {template.min_sections} distinct sections."""
    
    return system_prompt, format_instructions


def build_visual_prompt(template_id: str) -> str:
    """Build system prompt for visual/diagram generation."""
    template = VISUAL_TEMPLATES.get(template_id)
    
    if not template:
        return "Create a clear, well-structured Mermaid diagram."
    
    return f"""{template['system_prompt']}

QUALITY CHECKLIST:
{chr(10).join(f'- {check}' for check in template['quality_checklist'])}

EXAMPLE SYNTAX:
```mermaid
{template['example']}
```

Generate valid Mermaid syntax that renders correctly."""
