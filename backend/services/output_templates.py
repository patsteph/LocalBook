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
    recommended_tokens: int = 2000  # Recommended num_predict for this document type
    # Tier 3.7 (2026-06-01) — per-type pre-write reasoning move. Replaces
    # the generic CoT bolt-on ("identify themes, note contradictions, plan
    # structure") with the SPECIFIC structural reasoning each doc type
    # needs. For briefings it's situation→finding→action; for FAQs it's
    # questions-readers-would-actually-ask; for debates it's the genuine
    # disagreement; etc. Empty string falls back to a sane default.
    pre_write_move: str = ""
    # Tier 4.1 (2026-06-01) — default voice register. Controls how much
    # emotional texture / stake-feeling the prose carries. The user can
    # override per-generation via the /content/generate `register` param.
    # See REGISTER_BRIEFS for valid values: measured, engaged, warm, urgent.
    default_register: str = "measured"


# ──────────────────────────────────────────────────────────────────────
# Voice register briefs (Tier 4.1, 2026-06-01)
# ──────────────────────────────────────────────────────────────────────
# What separates an "AI summary" from "something I want to read" is rarely
# missing information — it's missing felt-stake. The writer doesn't seem
# to care. These briefs give the generator a CONCRETE perspective rather
# than abstract tone labels ("authoritative", "balanced") that 7B models
# can't reliably translate to register. Each brief reads like the back of
# the persona's business card: who they are, what they've seen, how they
# write.
#
# Documents, audio, and video all share these briefs — the same `measured`
# voice should sound consistent across an exec briefing and a podcast.
# Audio additionally appends prosody-affecting punctuation guidance via
# `AUDIO_PROSODY_OVERLAY` (see audio_generator.py).

REGISTER_BRIEFS: Dict[str, str] = {
    "measured": (
        "VOICE REGISTER — MEASURED.\n"
        "Write as someone who has been wrong before and learned to be careful. "
        "Distinguish what the evidence supports from what you're inferring. "
        "Avoid certainty words ('clearly', 'obviously', 'undoubtedly') unless the evidence is overwhelming. "
        "Conviction comes from precision, not volume. When uncertainty is real, name it once and move on — don't bury claims under hedges. "
        "Sentence rhythm: balanced, mid-length. No theatrical flourishes."
    ),
    "engaged": (
        "VOICE REGISTER — ENGAGED.\n"
        "You have stake in this. Write as someone who has seen the situation the sources describe play out — sometimes well, sometimes badly — and is writing for someone about to face it. "
        "Convey conviction without overclaim: anchor stakes-claims to specific evidence. "
        "Vary sentence length deliberately. A short sentence after three long ones is emotional emphasis — use it when the topic earns it. "
        "Use em-dashes for thought-breaks. Be direct when the situation calls for directness. "
        "The reader should finish thinking 'this writer cared whether I understood' — because you did."
    ),
    "warm": (
        "VOICE REGISTER — WARM.\n"
        "Write as a knowledgeable friend, not a textbook. The reader should feel respected, not lectured. "
        "Use concrete everyday examples; pick analogies the reader has actually experienced (driving, cooking, sorting cards), not other abstract concepts dressed up as familiar. "
        "Short questions are welcome. Sentence rhythm: conversational variety. "
        "When you correct a misconception, do it gently — explain why someone would naturally believe the wrong thing FIRST. Earn the correction by understanding why the mistake makes sense."
    ),
    "urgent": (
        "VOICE REGISTER — URGENT.\n"
        "Stakes are high and time is short. Sentences are tight. Verbs are decisive. Throat-clearing is out. "
        "Lead with the conclusion, then the evidence. Avoid hedge words. "
        "When you must qualify, do it once, briefly, and move on. "
        "The reader should finish the document knowing exactly what to do."
    ),
}


def get_register_brief(register: Optional[str], fallback: str = "measured") -> str:
    """Return the brief for a register, defaulting to `fallback`. Unknown
    values fall back rather than raising — the override knob shouldn't be
    able to break generation."""
    if register and register in REGISTER_BRIEFS:
        return REGISTER_BRIEFS[register]
    return REGISTER_BRIEFS.get(fallback, REGISTER_BRIEFS["measured"])


# ──────────────────────────────────────────────────────────────────────
# Presentation quality (2026-06-03) — explicit markdown formatting brief
# ──────────────────────────────────────────────────────────────────────
# 7B models reliably follow EXPLICIT markdown instructions and unreliably
# infer them from voice exemplars. The chat side (rag_generation.py) has
# had a `PRESENTATION QUALITY` + `FORMAT REQUIREMENTS (mandatory)` pair
# for a while, which is why chat answers read like a well-formatted doc.
# Studio outputs were missing this block — the prompt told the model what
# to write (structure_requirements, voice exemplar) but not how to format
# it visually, so plain-prose-with-no-headings became the failure mode.
#
# Apply this to every document-generation prompt. The block is render-
# layer guidance only — it does NOT change what to write, only the
# markdown texture the model emits.

PRESENTATION_QUALITY = """PRESENTATION QUALITY (mandatory — apply throughout):
- Use markdown headers (## for major sections, ### for sub-sections). Every required section above MUST have its own header.
- Use **bold** for key terms, names, definitions, and load-bearing findings. Not every sentence — pick the words that carry meaning.
- Use *italics* sparingly: emphasis, foreign/technical terms on first use, or short scene-setting.
- Use bullet or numbered lists when enumerating 3+ items. Avoid hiding lists inside long paragraphs.
- Use tables for side-by-side comparisons (2-4 columns max). Don't simulate a table with bullets.
- Use `>` blockquotes (sparingly) for sourced quotes or a single high-impact callout.
- Use `---` horizontal rules only between major top-level sections, never inside a section.
- Use `inline code` for literal values: filenames, identifiers, exact strings, error messages, model/version names.
- Keep paragraphs short: 2-4 sentences. Long monoliths kill scan-ability.
- Lead each major section with a one-line takeaway, then evidence below it.
- Open the document with a direct, scannable lede — not a "this document will..." preamble.
- Every paragraph should pass the scan test: can a busy reader pull the point from the first sentence and the bolded terms?"""


# Phase 4 of v2-information-cortex — mixed-medium injection.
# Authorizes (does not require) the model to emit inline visualization
# fences. The post-processor in `services/visual_resolver.py` resolves
# `lb-chart` fences (Pydantic-validate ChartConfig) and `lb-visual-hint`
# fences (call visual_composer for SVG) before the doc reaches the user.
# Failure policy: dropped silently with an italic "*chart unavailable*"
# / "*visual unavailable*" placeholder. Skills that already plan their
# own visuals (Feynman knowledge-map) keep emitting their own fences;
# this generic injection sits below the skill-specific instructions.

VISUAL_INTERLEAVE = """VISUAL INTERLEAVING (optional but encouraged when content warrants):
- For quantitative comparisons, trends, or magnitudes, emit a chart inline:
  ```lb-chart
  {"chart_type": "bar", "title": "Quarterly Revenue", "x_axis": {"key": "q"}, "series": [{"key": "rev", "label": "Revenue ($M)"}], "data": [{"q": "Q1", "rev": 1.0}, {"q": "Q2", "rev": 1.5}]}
  ```
- For processes, mind-maps, or hero illustrations, request a visual:
  ```lb-visual-hint
  flowchart: ingestion → embedding → retrieval → answer
  ```
- Use sparingly: at most one visual per major section, and only when the visual carries information the prose can't.
- The chart JSON must be valid ChartConfig (chart_type, series, data keys aligned). Empty objects ({}) are dropped silently.
- Never wrap the fences in extra prose; place them on their own block."""


def list_registers() -> List[str]:
    """Available register values for UI dropdowns."""
    return list(REGISTER_BRIEFS.keys())


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
        
        example_structure="""VOICE EXEMPLAR (match the tone, density, and citation discipline — NOT the literal subject):

Adoption of automated triage in financial services has accelerated 2.3× over the past 18 months, driven primarily by labor-cost pressure rather than accuracy gains [S2]. Two of the three deployments studied here delivered the headline savings; the third underperformed because the underlying queue was already efficient [S1][S3].

The pattern across all three: a 4-8 week pilot, a 6-month rollout, and a year of declining marginal returns. No deployment achieved its third-year ROI projection [S2].

Recommendation: pre-audit queue efficiency before committing to procurement. The technology amplifies what's already working; it does not fix what isn't [S1].""",
        
        tone="authoritative, concise, action-oriented",
        target_audience="Senior executives and decision-makers",
        recommended_tokens=2500,
        pre_write_move=(
            "Before writing a single line, complete this sentence in your head: "
            "'The situation is X, the key finding is Y, the recommended action is Z.' "
            "If you can't fill in all three, the briefing isn't ready — pause and re-read the sources. "
            "The Executive Summary at the top must be exactly that sentence expanded to 3-4 lines. "
            "Don't bury the recommendation deeper than that — executives stop reading after the Summary."
        ),
        default_register="engaged",  # Executives need to feel the stakes, not just see them
    ),

    "research_dossier": OutputTemplate(
        template_id="research_dossier",
        name="Research Dossier",
        description="McKinsey-style research report — cornerstone summary, evidence-driven sections, risks, sources appendix. Built for printing or PDF export as a deliverable.",
        system_prompt="""You are a senior research partner writing a dossier that will be circulated to a board or executive committee. Your reader has 10 minutes for the summary and may read the body sections selectively.

Your dossier must:
1. Open with a single-sentence cornerstone summary that compresses the entire dossier into one declarative claim.
2. Follow with an EXECUTIVE SUMMARY of ≤ 4 sentences that expands the cornerstone with evidence.
3. Order body sections from highest-leverage insight to lowest. Each section leads with a one-line takeaway, then the evidence below it.
4. Cite every factual claim with the [Sn] tag of the source it came from. Never invent citations.
5. End with an honest assessment of risks, open questions, and what the evidence does NOT yet show.
6. Close with a sources appendix that names each [Sn] tag with the file/URL it maps to.

Voice: authoritative, neutral, evidence-first. Avoid hedging like "it could be argued" or "some might say"; either state the finding with its citation or leave it out. Avoid marketing language. Avoid stacking adjectives.

Match the visual rhythm of a McKinsey deliverable: bolded takeaways at the top of each section, supporting bullets below, a chart or table in any section where the data warrants it (use the inline visual fences from VISUAL INTERLEAVING below).""",

        structure_requirements=[
            "CORNERSTONE SUMMARY (a single declarative sentence — the whole dossier in one claim)",
            "EXECUTIVE SUMMARY (≤ 4 sentences expanding the cornerstone with evidence and citations)",
            "KEY FINDINGS (3-5 findings, each a one-line takeaway followed by 2-4 evidence bullets with citations)",
            "DETAILED ANALYSIS (deep-dive sections — one per finding, cross-source synthesis, charts/tables where they earn their keep)",
            "RISKS & OPEN QUESTIONS (what could change the conclusion; what the evidence does not yet establish)",
            "SOURCES APPENDIX ([S1] filename → one-line description for each source consulted)",
        ],

        quality_checklist=[
            "Does the cornerstone summary stand alone as a defensible claim, or does it need the dossier to make sense?",
            "Does every factual claim end with a [Sn] tag pointing to a real source?",
            "Does each body section open with a one-line takeaway bolded at the top?",
            "Are charts or tables used wherever the data is comparison- or magnitude-driven?",
            "Are risks and open questions written honestly, not as a checklist filler?",
            "Could a board member skim only the takeaways and still walk away with the right mental model?",
        ],

        min_sections=6,

        example_structure="""VOICE EXEMPLAR (match the tone, density, and citation discipline — NOT the literal subject):

**Cornerstone**: The shift to local-first AI tooling has been driven not by privacy ideology but by latency and unit-economics — both of which are now structural, not transient [S2][S4].

**Executive Summary**: Across the three deployments studied, the move from hosted inference to on-device inference reduced per-task latency by 8-12× and per-task cost by 60-95% [S1][S2]. Two of the three deployments retained or improved retrieval quality despite running on consumer hardware [S1][S3]. The third deployment underperformed because the corpus exceeded the embedding model's optimal chunk-budget — a tuning issue, not a structural ceiling [S3]. Adoption is therefore likely to continue tracking the trajectory of consumer-hardware ML acceleration, not the trajectory of privacy regulation [S4].

**Key Findings**:

**1. Latency improvements are structural, not transient.**
- Median end-to-end response time fell from 1.8s (hosted) to 0.18s (local) across the three deployments [S1][S2].
- The gap widened, not narrowed, as the corpus grew beyond 50k chunks [S2].
- Apple Silicon's unified memory architecture is the primary driver; commodity GPUs did not show the same gains [S1].""",

        tone="authoritative, neutral, evidence-first",
        target_audience="C-suite executives, board members, senior research partners",
        recommended_tokens=4000,
        pre_write_move=(
            "Before writing a single line, write down the cornerstone summary as a single declarative sentence. "
            "Test it: if a reader saw ONLY that sentence, would they walk away with the right mental model? "
            "If no, the sources don't yet support a defensible cornerstone — pause and re-read. "
            "Then list the 3-5 load-bearing claims that defend the cornerstone, and for each one, identify the specific [Sn] evidence. "
            "Only then start drafting. The cornerstone goes at the very top; the Executive Summary expands it; every body section traces back to it."
        ),
        default_register="measured",  # Dossier is read; it doesn't perform.
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
        
        example_structure="""VOICE EXEMPLAR (match the tone, density, and citation discipline — NOT the literal subject):

Learning objective: by the end of this guide, you'll be able to explain why reinforcement learning produces emergent strategies that supervised methods cannot, and identify when each approach is appropriate [S1].

Key term — *credit assignment*: the problem of figuring out which past action led to a current reward when the rewards are delayed and aggregated. In supervised learning you don't have this problem because feedback is immediate; in RL it's the hardest part [S2].

How to remember: think of training a dog by yelling "Good!" only at the end of a 10-minute trick. The dog has to figure out which step in the trick earned the praise. That's credit assignment.

Self-check: if a learner can articulate why an RL agent might explore "useless" actions during training but a supervised model never does, they have the concept [S1][S3].""",
        
        tone="encouraging, clear, educational",
        target_audience="Students and self-learners",
        recommended_tokens=3500,
        pre_write_move=(
            "Before writing: identify the specific things the reader will be able to DO after studying this — "
            "verbs like 'distinguish', 'apply', 'derive', not 'understand'. Then identify the 2-3 misconceptions "
            "they'll most likely arrive with. The guide's structure must address both. Learning objectives that "
            "use 'understand' as the verb are usually too vague to evaluate — replace with action verbs."
        ),
        default_register="warm",  # Learners feel respected, not lectured
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

        example_structure="""VOICE EXEMPLAR (match the tone, density, and citation discipline — NOT the literal subject):

### Q: How long does a typical bulk migration take for a 100-table Postgres database?
**Short answer:** In the three benchmarks reported, the bulk-copy phase ran 3-6 hours; the cutover window was under 90 seconds in every case [S2].

**Detailed answer:** The wall-clock cost is dominated by the largest table, not the table count. All three studies converged on the same advice: parallelize the long-tail tables and accept that the rest run in serial because foreign-key dependencies block parallelism [S1][S2]. Indexing strategy at the destination changes the back-of-envelope by 2-3× — leave indexes off during copy and rebuild after [S3].

*Related:* "What about logical replication for zero-downtime?", "How do you size the destination instance?" """,
        
        tone="helpful, thorough, accessible",
        target_audience="Anyone seeking to understand the topic",
        recommended_tokens=3000,
        pre_write_move=(
            "Before writing: enumerate the actual questions a real reader would ask, in the order they'd "
            "ask them. A beginner asks 'what is X?' first; an expert asks 'when does X fail?' last. Bad FAQs "
            "answer questions nobody asked — usually the ones an author wants to talk about, not the ones the "
            "reader has. If a question feels forced, drop it. Better to ship 8 high-signal Q&As than 20 with "
            "filler."
        ),
        default_register="measured",  # FAQ readers want accuracy over emotion
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
        
        example_structure="""VOICE EXEMPLAR (match the tone, density, and citation discipline — NOT the literal subject):

Two of the four studies surveyed here converge on a 40-60% performance gain from aggressive prefetching; the third reports no gain, and the fourth reports a regression [S1][S2][S3][S4]. The discrepancy isn't methodological — the four teams used comparable benchmarks. It's an artifact of which bottleneck was binding when the test ran.

Where the disagreement actually lives: workloads with predictable access patterns benefit from prefetching because the cost of a wasted fetch is amortized over many useful ones [S1][S2]. Workloads with random or adversarial access patterns are penalized — every wasted prefetch displaces a future useful read [S4]. The headline number ("prefetching is good") hides this.

What none of the sources address: the cache-hierarchy interaction. All four assume a flat memory model, but in practice the L3 displacement effect dominates above some threshold of prefetch aggression [S2]. This is the most interesting open question.""",
        
        tone="analytical, nuanced, scholarly",
        target_audience="Subject matter experts and researchers",
        recommended_tokens=6000,
        pre_write_move=(
            "Before writing: identify 3-5 THEMES across the sources (NOT a summary of each source separately). "
            "The structure must be thematic — themes are sections, sources are evidence cited under themes. "
            "Then identify the 1-2 places where sources DISAGREE or where evidence pulls in different directions. "
            "Those tensions are the spine of the analysis — without them you have a literature review, not a deep dive. "
            "If you can't find any tensions, the sources may be too homogeneous for a deep_dive — flag it in the intro."
        ),
        default_register="measured",  # Scholarly analysis — precision over conviction
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
        
        example_structure="""VOICE EXEMPLAR (match the tone, density, and citation discipline — NOT the literal subject):

Three patterns hold across all five papers reviewed: rapid initial gains in the first two months, a plateau by month six, and measurable regression in the absence of explicit retraining cycles [S1][S2][S5]. The plateau isn't a model-capacity limit — it's a labeling-distribution drift problem masquerading as one [S3].

The most actionable finding: teams that scheduled a quarterly relabeling pass held their month-six accuracy through month twelve; teams that didn't lost an average of 14 points [S1][S4]. Cost of the relabeling pass was 8-12% of the original training budget — small relative to the alternative of retraining from scratch [S5].

What to remember: the headline metric drift is a symptom; relabel cadence is the lever.""",
        
        tone="concise, insightful, professional",
        target_audience="Busy professionals needing quick understanding",
        recommended_tokens=1500,
        pre_write_move=(
            "Before writing: list the 3-5 claims a reader will walk away knowing. Each must be specific enough "
            "that another reader could verify it (numbers, named entities, decisions). 'Trends are accelerating' "
            "is not a claim — 'Adoption is up 47% YoY across the sample' is. Everything else in the summary "
            "exists to support those claims; if a sentence doesn't, cut it."
        ),
        default_register="measured",  # Summaries earn trust through precision
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
        
        example_structure="""VOICE EXEMPLAR (match the tone, density, and citation discipline — NOT the literal subject):

Imagine you're trying to sort a deck of cards in a dark room. You can compare two cards at a time, but you can't see the whole deck. That's the kind of problem an online algorithm has to solve [S2] — it has to make decisions one item at a time, without ever seeing what's coming.

Here's why this matters: most real systems work this way. A search engine doesn't know what you'll type next. A trading system doesn't know tomorrow's prices. The "optimal" answer is unavailable; the question is how close you can get with only what you've seen so far [S1].

The intuition that throws people off: even when you can't reach optimal, you can often guarantee you'll be within a small constant of optimal — what computer scientists call a *competitive ratio* [S3]. Not bad for working in the dark.""",
        
        tone="friendly, patient, engaging",
        target_audience="Anyone curious about the topic, regardless of background",
        recommended_tokens=2000,
        pre_write_move=(
            "Before writing: identify the ONE concept that, once understood, unlocks the rest. Build the entire "
            "explanation around that hinge. Most failed explanations try to explain everything in parallel — "
            "good explanations are sequential: hinge concept first, everything else explained in terms of it. "
            "Then pick the analogy. The analogy must be something the reader has actually experienced (driving, "
            "cooking, sorting cards), not another abstract concept dressed up as familiar."
        ),
        default_register="warm",  # Explanations land when the reader feels respected
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
        
        example_structure="""VOICE EXEMPLAR (match the tone, density, and citation discipline — NOT the literal subject):

**Position A — Centralization improves throughput.** Shared infrastructure amortizes fixed cost across more users, and the operational team only has to keep one system healthy [S1]. The case studies bear this out: every centralization migration in the dataset reduced per-user infrastructure cost by 30-50% within the first year [S2].

**Counter from Position B.** That argument assumes uniform demand. Under spiky load, central queues become single points of failure — and three of the four migrations in the dataset experienced a *more* severe outage in year two than they had pre-migration [S3]. The cost savings are real; so is the new failure mode.

**Where they actually agree.** Both sides accept that the right answer depends on demand variance. Centralization wins when load is predictable; federation wins when load is bursty and partitionable. The debate is really about *which workloads belong in which bucket* [S1][S3] — and that's an empirical question, not an ideological one.""",
        
        tone="balanced, respectful, intellectually honest",
        target_audience="Anyone wanting to understand multiple sides of an issue",
        recommended_tokens=4500,
        pre_write_move=(
            "Before writing: state the genuine disagreement in ONE sentence — the specific claim Position A "
            "asserts that Position B denies. If you can't write that sentence, there isn't a real debate, just "
            "two different topics; the debate format isn't appropriate. Then identify the strongest argument "
            "FOR each side — not strawmen. A debate where one side is obviously right is a lecture in disguise. "
            "If the sources mostly agree, name that explicitly and surface the narrower tensions that DO exist."
        ),
        default_register="engaged",  # Each side must actually argue its case
    ),

    "podcast_script": OutputTemplate(
        template_id="podcast_script",
        name="Podcast Script",
        description="Engaging audio content with natural conversation flow",
        system_prompt="""You are a podcast producer creating an engaging, educational conversation that will be converted to audio via text-to-speech.

CRITICAL: Every word you write will be spoken aloud. Write ONLY dialogue — no stage directions, no sound effects, no music cues, no markdown formatting, no action descriptions.

Your podcast must:
1. Sound like natural conversation, not reading from a script
2. Have distinct host personalities that complement each other
3. Open with a compelling hook — mid-conversation, surprising fact, or provocative question
4. NEVER open with "Welcome to the show" or "I'm [name] and today we'll discuss"
5. Include moments of genuine insight, reactions, and "aha" moments
6. Reference sources naturally ("I was reading this piece that said...") — never "According to Source 1"
7. End with a natural wind-down, a final thought, or a question for the listener

Write as if creating a podcast that listeners recommend to friends.""",
        
        structure_requirements=[
            "COLD OPEN (hook that grabs attention — start mid-thought)",
            "EXPLORATION (build context, share surprising findings)",
            "DEEP DIVE (main content with back-and-forth discussion)",
            "IMPLICATIONS (what this means, why it matters)",
            "WIND-DOWN (final thoughts, question for the listener)"
        ],
        
        quality_checklist=[
            "Would you want to listen to this conversation?",
            "Do hosts have distinct, complementary voices?",
            "Is information delivered conversationally with short sentences?",
            "Are there genuine reactions and moments of insight?",
            "Does the opening hook you immediately?",
            "Is the ending natural, not a stiff sign-off?"
        ],
        
        min_sections=4,
        
        example_structure="""VOICE EXEMPLAR (match the density, the move-by-move structure, and the citation discipline — NOT the literal subject. Do NOT copy interjections like "wait really" or "that's wild"; build your own).

Host A: There's a finding in this dataset that I think most people are going to push back on. The migration cost they reported isn't tracking the dollars — it's tracking the half-time of legacy expertise on the team [S1].

Host B: Half-time as in radioactive decay. Like, the longer the migration drags, the fewer people remain who know how the old thing worked.

Host A: Exactly. And by the time you're six months in, that institutional knowledge has dropped by half, which means the migration team is making decisions blind to what the old system was actually doing. That's where the cost overruns live.

Host B: The thing I'd push on, though, is that the same paper shows the *fast* migrations also overran [S2]. So is it really the timeline that's the problem?

Host A: Yeah, that's the part I had to read twice. Their answer is that speed only helps if you've documented enough to absorb the knowledge loss. Without documentation, fast or slow, you're toast [S1][S3].

Host B: So the lever isn't pace, it's docs.""",
        
        tone="conversational, engaging, informative, natural",
        target_audience="Podcast listeners seeking educational content",
        recommended_tokens=5000,
        pre_write_move=(
            "Before writing: identify the opening claim that earns the listener's attention in the first 15 "
            "seconds. NOT 'today we'll discuss X' or 'welcome back to the show' — a concrete observation that "
            "surprises, a specific finding, or a tension the hosts will work through. If you can't find such "
            "an opener in the source material, the topic may not deserve a 20-minute podcast — say so. Then "
            "identify the two hosts' POVs: how would they disagree on this topic? Without contrast, the "
            "conversation is one person agreeing with themselves out loud."
        ),
        default_register="engaged",  # Hosts must sound invested
    ),

    "feynman_curriculum": OutputTemplate(
        template_id="feynman_curriculum",
        name="Feynman Learning Curriculum",
        description="Multi-part progressive learning system using the Feynman Technique — novice to near-expert",
        system_prompt="""You are an expert instructional designer applying Richard Feynman's learning methodology to create a multi-part curriculum.

THE FEYNMAN TECHNIQUE:
Richard Feynman believed that if you can't explain something simply, you don't truly understand it. His method:
1. Study and map knowledge into categories
2. Explain it as if teaching a 12-year-old — use analogies, everyday language, zero jargon
3. Identify gaps — what you can't explain simply reveals what you don't actually understand
4. Simplify further — break everything to first principles, then rebuild at progressive difficulty levels

STEP-BACK PROMPTING (CRITICAL PRE-STEP):
Before explaining anything, you MUST explicitly identify the deep underlying principle or law of physics/logic that governs the concept you are about to explain. First state "The Underlying Principle:", then explain the concept based purely on that foundation.

HEADING FORMAT — USE EXACTLY THESE HEADINGS (do NOT use Roman numerals):
- # Curriculum Overview
- ## Part 1: Foundation
- ## Part 2: Building Understanding
- ## Part 3: First Principles
- ## Part 4: Mastery Synthesis
- ## Knowledge Map

YOUR CURRICULUM MUST:
1. Build knowledge in 4 progressive levels: Foundation → Building → First Principles → Mastery
2. Each level MUST be self-contained and useful on its own
3. Use concrete analogies and everyday examples at EVERY level — this is the heart of Feynman's approach
4. End each level with 2-3 REFLECTION PROMPTS — open-ended questions that make the learner think deeply
5. Explicitly identify and debunk common misconceptions
6. Connect concepts across sources — show HOW ideas relate to each other
7. At the Mastery level, include "teach it back" prompts — the ultimate Feynman test
8. ONLY use facts from the provided research — do not invent claims or statistics

IMPORTANT — QUIZZES:
- Do NOT generate multiple-choice quiz questions (A/B/C/D) inside the document.
- Interactive quizzes are provided separately by the Quiz system.
- Instead, end each Part with 2-3 open-ended REFLECTION PROMPTS that encourage deep thinking.
- Good reflection prompts: "How would you explain X to a friend?", "Why does Y work this way and not some other way?", "What would happen if Z changed?"

WRITING STYLE — CRITICAL:
- Keep sentences SHORT: 10-25 words maximum. Feynman despised verbose writing.
- One idea per sentence. One idea per paragraph.
- NEVER write run-on sentences stringing clauses together with commas.
- If a sentence has more than one comma, split it into two sentences.
- Do NOT use vague filler like "ensuring sustained growth advancement across diverse applications".
- Every sentence must contain a SPECIFIC fact, analogy, or instruction — no padding.

Write as if creating a curriculum that would make Feynman himself proud — clear, joyful, and ruthlessly honest about complexity.""",
        
        structure_requirements=[
            "CURRICULUM OVERVIEW (subject, estimated learning time, what you'll master)",
            "PART 1: FOUNDATION (explain to a 12-year-old — core concepts with analogies, essential vocabulary, reflection prompts)",
            "PART 2: BUILDING UNDERSTANDING (deeper dive, how concepts connect, real-world examples, misconceptions debunked, reflection prompts)",
            "PART 3: FIRST PRINCIPLES (WHY things work this way, root mechanisms, edge cases, expert insights, reflection prompts)",
            "PART 4: MASTERY SYNTHESIS (teach-it-back challenges, expert-level questions, what's still debated/unknown, learning path forward, reflection prompts)",
            "KNOWLEDGE MAP (list the core concepts and how they connect — a visual diagram will be generated automatically)"
        ],
        
        quality_checklist=[
            "Could a motivated 12-year-old understand Part 1?",
            "Does each part build clearly on the previous one?",
            "Are there concrete analogies at every level, not just Part 1?",
            "Do reflection prompts encourage deep thinking, not just recall?",
            "Are misconceptions explicitly identified and corrected?",
            "Does Part 4 include genuine teach-it-back challenges?",
            "Is the knowledge map accurate and useful?",
            "Would Feynman approve of the clarity?"
        ],
        
        min_sections=6,
        
        example_structure="""VOICE EXEMPLAR (per-Part density and tone to match — NOT a literal scaffold to copy. Use the actual headings listed above in HEADING FORMAT, not the bracketed examples below.):

[Part 1 — Foundation, voice and density]
Most introductory treatments of gradient descent skip the part that actually matters: why the learning rate matters more than the loss function shape. The intuition is this — imagine you're walking down a hill in fog, and the only thing you can do is decide how big each step is [S1]. Tiny steps mean you'll eventually reach the bottom but it'll take forever. Huge steps mean you might overshoot the valley entirely and end up climbing the next hill. The "learning rate" is just the size of your step.

[Part 3 — First Principles, voice and density]
Why does this work AT ALL? The deep reason is that for any smooth function, the gradient at a point is the locally-best direction to reduce the function value [S2]. Not globally best — locally best. This is the same idea as the chain rule from calculus, just applied many times. The fact that "locally best, repeated" usually converges to "globally good enough" is what makes the whole field of neural network training possible. When it fails — and it does fail, in roughly 15% of training runs in modern systems [S3] — it's because the loss surface has structures (saddle points, plateaus, ravines) that locally-best-step doesn't navigate well.

[Reflection-prompt voice, end of any Part]
1. Walk a colleague through why an adaptive optimizer (Adam, AdamW) might converge faster than vanilla gradient descent on the same data. What is it adapting?
2. Where would you bet the field is wrong about learning-rate scheduling, and why?""",
        
        tone="clear, encouraging, intellectually honest, joyful about learning",
        target_audience="Self-learners seeking deep understanding, not just surface knowledge",
        recommended_tokens=6000,
        pre_write_move=(
            "Before writing: identify ONE deep principle for each of the four Parts. Foundation rests on a "
            "natural analogy. Building Understanding rests on a relationship between concepts. First Principles "
            "rests on a mechanism — the WHY underneath. Mastery rests on a teach-it-back challenge that exposes "
            "gaps. If you can't articulate the single deep principle for a Part, the Part isn't ready — go back "
            "to the sources. Every Part should be useful in isolation; the curriculum is a sequence, not a "
            "single document chopped into four."
        ),
        default_register="warm",  # Feynman taught with warmth, not authority
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


def build_document_prompt(
    template_id: str,
    topic: str,
    style: str,
    source_count: int,
    register: Optional[str] = None,
) -> tuple[str, str]:
    """Build complete system and user prompts for document generation.

    Args:
        template_id: doc type key
        topic: focus topic for this generation
        style: format style (professional / academic / casual / ...)
        source_count: number of source documents
        register: optional voice-register override (measured / engaged /
            warm / urgent). When None, uses the doc type's default_register.

    Returns:
        Tuple of (system_prompt, format_instructions)
    """
    template = DOCUMENT_TEMPLATES.get(template_id)

    if not template:
        # Fallback for unknown template
        return (
            f"Create high-quality, well-structured content based on the provided sources.\n\n{PRESENTATION_QUALITY}\n\n{VISUAL_INTERLEAVE}",
            "Format clearly with appropriate sections using markdown."
        )

    # Per-type pre-write reasoning move (Tier 3.7, 2026-06-01).
    # Replaces the previous generic CoT bolt-on with each doc type's own
    # structural reasoning step. Falls back to a sane default if the type
    # hasn't defined one yet.
    pre_write = template.pre_write_move or (
        "Before writing, identify the 2-3 most important claims in the sources and the structure "
        "those claims demand. Plan the section order before drafting any prose."
    )

    # Voice register (Tier 4.1, 2026-06-01). Caller can override; otherwise
    # use the doc type's default. Unknown registers fall back to "measured".
    register_brief = get_register_brief(
        register or template.default_register,
        fallback=template.default_register or "measured",
    )

    system_prompt = f"""{template.system_prompt}

PRE-WRITE REASONING (do this before drafting any prose):
{pre_write}

{register_brief}

TARGET AUDIENCE: {template.target_audience}
TONE: {template.tone}
STYLE: {style}

QUALITY REQUIREMENTS:
{chr(10).join(f'- {check}' for check in template.quality_checklist)}

{PRESENTATION_QUALITY}

{VISUAL_INTERLEAVE}

You are working with {source_count} source document(s). Synthesize across ALL sources.

CRITICAL RULES:
- Draw from EVERY source provided — do not let one source dominate the output
- NEVER repeat the same sentence, phrase, or paragraph. Each sentence must add new information.
- If you find yourself writing something you already wrote, STOP and move to the next section.
- Complete every section fully. End with a proper conclusion — never stop mid-sentence."""
    
    format_instructions = f"""REQUIRED STRUCTURE:
{chr(10).join(f'{i+1}. {req}' for i, req in enumerate(template.structure_requirements))}

{template.example_structure}

Match the VOICE EXEMPLAR's tone, density, and citation discipline. Do NOT copy its literal subject or phrasing — use it as a feel guide only. Build your own structure from the REQUIRED STRUCTURE list above. Ensure your output has at least {template.min_sections} distinct sections."""
    
    return system_prompt, format_instructions


def build_visual_prompt(template_id: str) -> str:
    """Build system prompt for visual/diagram generation."""
    template = VISUAL_TEMPLATES.get(template_id)
    
    if not template:
        return "Create a clear, well-structured Mermaid diagram."
    
    return f"""{template['system_prompt']}

Before generating the diagram, analyze the content step by step: identify the key entities, their relationships, and the best visual structure to represent them.

QUALITY CHECKLIST:
{chr(10).join(f'- {check}' for check in template['quality_checklist'])}

EXAMPLE SYNTAX:
```mermaid
{template['example']}
```

Generate valid Mermaid syntax that renders correctly."""
