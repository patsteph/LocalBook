"""Visual System v2 — idiom catalog + two-stage picker.

Single source of truth for:
  • Idiom dataclass + IDIOMS list (Gemma freeform catalog)
  • _IDIOM_FITS one-line descriptions (used by both pickers)
  • CATEGORIES → idioms mapping (two-stage picker)
  • Stage-1 (category) and Stage-2 (idiom-in-category) prompt builders
  • pick_category_and_meta() and pick_idiom_in_category() callers

Extracted from visual_freeform.py in the v2 consolidation pass.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import List, Optional

from services.ollama_service import ollama_service
from services.visual_skeletons import HERO_IDIOMS, OLMO_IDIOMS as _SKELETON_IDIOMS

logger = logging.getLogger(__name__)

# Re-export the skeleton-derived idiom set so callers (and SkeletonGenerator)
# can validate idiom_ids without importing from visual_skeletons directly.
OLMO_IDIOMS = _SKELETON_IDIOMS

# Warm-call timeout reused across picker calls (matches visual_freeform constants)
WARM_TIMEOUT = 300.0


# Idiom catalog — what visual shapes Gemma can pick from
# ──────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Idiom:
    id: str
    label: str           # Human-readable name
    fits: str            # When to pick this (shown to LLM in pass 1)
    layout_hint: str     # Concrete layout guidance for pass 2


IDIOMS: List[Idiom] = [
    # ── Architecture / systems ──────────────────────────────────────
    Idiom("layered_architecture", "Layered architecture",
          "tiered systems (UI / API / data) shown vertically with explicit layers",
          "Vertical stack: 3-5 horizontal bands top-to-bottom, each band a different bg_alt tint. Band labels on the LEFT margin. Components inside as cards. Cross-band arrows (vertical) with marker-end for data flow between layers — ALWAYS draw the arrows, never imply with proximity."),
    Idiom("microservices_mesh", "Microservices mesh",
          "service-oriented systems with multiple peer components and shared infrastructure",
          "Central infrastructure (API gateway / message bus) as a horizontal band in the middle. Services arranged in a row above it. Databases in a row below. Every service has TWO explicit arrows: one to the central band (up-arrow), one to its database (down-arrow). No tree hierarchy."),
    Idiom("request_flow", "Request flow / sequence",
          "step-by-step traversal of a single request across system components",
          "Left-to-right chain of 4-7 components. NUMBERED arrows (1, 2, 3...) between them with payload/timing labels above the arrow. Return path as a dashed arrow below the main flow. Every arrow uses marker-end."),
    Idiom("deployment_topology", "Deployment topology",
          "physical/network deployment showing regions, clusters, instances",
          "Outer containers (regions) with dashed borders and region name in top-left corner. Inner containers (clusters) with solid bg_alt fill and cluster name. Instances/services as small cards inside clusters. Cross-region arrows clearly traverse region boundaries."),
    Idiom("data_pipeline", "Data flow pipeline",
          "ingestion → processing → storage → consumption flows",
          "Horizontal left-to-right chain. Each stage is a card with title, type icon-shape, and 1-line role. THICK arrows (stroke-width 3) between stages with volume/latency labels above each arrow. Branching outputs split with explicit branch arrows."),
    Idiom("hub_and_spoke", "Hub and spoke integration",
          "central system with multiple bidirectional integrations to peripheral systems",
          "Central hub card in the middle. 4-8 spoke cards arranged radially around it. EVERY spoke has a labeled bidirectional arrow to the hub (use two single-direction arrows side-by-side, never a double-headed arrow). Integration type labels (REST, webhook, gRPC, etc.) on each arrow."),
    Idiom("event_driven", "Event-driven architecture",
          "publish/subscribe systems with brokers, producers, consumers",
          "Three vertical bands left-to-right: PRODUCERS (left), EVENT BACKBONE (center, taller), CONSUMERS (right). Bands have header labels. Producer arrows flow into the backbone; backbone arrows flow out to consumers. Event types labeled on outflow arrows."),
    Idiom("cqrs_pattern", "CQRS / read-write split",
          "command/query responsibility segregation pattern with separate read/write paths",
          "Two parallel horizontal flows: WRITE PATH on top (UI → command → write store → event store) in one color, READ PATH below (UI → query → read store ← projection) in another. Connecting bridge between event store and read-store projection."),
    Idiom("serverless_topology", "Serverless topology",
          "function-as-a-service systems triggered by events / API gateway / scheduled invokes",
          "Trigger sources (API gateway, queue, schedule, storage event) on the LEFT. Functions in the CENTER as small rounded squares (lambda icon-style). Resources (DBs, queues, storage) on the RIGHT. Every function has labeled trigger arrow IN and labeled resource arrow OUT."),
    # ── Process / flow ─────────────────────────────────────────────
    Idiom("linear_process", "Linear process",
          "sequential workflow with N stages, no branches",
          "Horizontal or vertical chain of numbered stages. Each stage is a card with title + 1-2 sub-bullets. Connecting arrows centered between stages."),
    Idiom("swimlane", "Swimlane process",
          "process spanning multiple actors/teams with cross-actor handoffs",
          "Horizontal bands (one per actor) with actor name on the left. Components inside their lane. Cross-lane arrows clearly traverse band boundaries. Time progresses left-to-right."),
    Idiom("decision_tree", "Decision tree",
          "conditional branching logic with yes/no or multi-way paths",
          "Top-down tree. Diamond shapes for decisions, rectangles for actions. Branch labels (Yes/No) on the connecting arrows."),
    Idiom("journey_map", "Journey map",
          "customer/user journey across stages with sub-info at each stage",
          "Horizontal stage progression. ABOVE the stage row: metrics/KPIs. BELOW the stage row: owners/touchpoints. Each stage has 2-3 sub-activities listed inside."),
    # ── Comparison ─────────────────────────────────────────────────
    Idiom("comparison_matrix", "Comparison matrix",
          "side-by-side comparison of 2-5 options across N attributes",
          "Grid: header row = options, header column = attributes. Cells show comparative values. Use color/icon coding (checkmark, X, dot scale) for at-a-glance reading. Highlight winning cells."),
    Idiom("pros_cons", "Pros vs cons",
          "two-column tradeoff analysis of a single subject",
          "Two columns labeled Pros (success color) and Cons (warning color). Each item is a labeled card with brief justification."),
    Idiom("before_after", "Before/after",
          "transformation contrast (state, performance, architecture)",
          "Two columns: Before on the left, After on the right. Same internal structure in both. Arrow or gradient transition between. Optional delta callouts."),
    Idiom("quadrant_2x2", "2×2 quadrant matrix",
          "categorization by two orthogonal dimensions (e.g., effort/impact)",
          "Cross axes labeled at the ends. 4 quadrants with corner labels. Items plotted as small cards inside their quadrant."),
    # ── Structure / hierarchy ──────────────────────────────────────
    Idiom("tree_hierarchy", "Tree hierarchy",
          "parent-child structure with depth (org chart, taxonomy)",
          "Top-down tree with one root. Siblings at same level horizontally aligned. Connector lines from parent to each child."),
    Idiom("concept_map", "Concept map",
          "interconnected concepts/categories with named relationships",
          "Grouped category clusters with category-name headers. Items inside each cluster. Cross-cluster arrows with relationship labels."),
    Idiom("service_catalog", "Service catalog grid",
          "catalog of services/features organized by category",
          "Grid of cards grouped by category (each category gets a colored band header). Each card shows service name + 1-line description + icon-shape."),
    # ── Patterns / data ────────────────────────────────────────────
    Idiom("timeline", "Timeline",
          "events plotted along a time axis with dates and milestones",
          "Horizontal time axis at the center. Events alternate above/below with date labels. Milestone markers (circles) on the axis."),
    Idiom("venn_diagram", "Venn diagram",
          "overlapping sets / shared vs unique attributes",
          "2 or 3 overlapping circles with labels. Items placed in the appropriate region (own / shared). Soft fill colors with multiply blend feel."),
    Idiom("stat_callouts", "Statistic callouts",
          "key metrics highlighted as featured numbers",
          "Grid of 3-6 large numbers with units + 1-line caption each. Numbers in primary color, captions in body text. Optional delta arrows."),
    Idiom("ranked_list", "Ranked list",
          "ordered list with ranks/scores and brief justification",
          "Vertical list of cards numbered 1..N. Each card: rank badge + title + score + 1-2 line note. Visual emphasis decreases down the list."),
    Idiom("value_proposition", "Value proposition (vector hero)",
          "single value statement with 3 supporting benefits, no raster image needed",
          "Centered hero section with bold tagline + supporting line + iconic central glyph. Below, 3 benefit cards side-by-side, each with its own glyph, title, and 2-3 supporting lines. Strong typographic hierarchy. Use this when content is hero/value-prop framing but you cannot or do not need to generate a separate image."),
]


def _idiom_catalog_text() -> str:
    """Catalog text injected into pass-1 prompt."""
    lines = []
    for i in IDIOMS:
        lines.append(f"- {i.id} ({i.label}): {i.fits}")
    return "\n".join(lines)


def _idiom_by_id(idiom_id: str) -> Optional[Idiom]:
    for i in IDIOMS:
        if i.id == idiom_id:
            return i
    return None


# ──────────────────────────────────────────────────────────────────────
# _IDIOM_FITS + picker systems + CATEGORIES
# ──────────────────────────────────────────────────────────────────────

# One-line "fits" descriptions for the idiom picker. Kept in sync with
# the skeletons in visual_skeletons.py (hero idioms excluded — those
# need Klein, which Setup A doesn't have).
_IDIOM_FITS: dict[str, str] = {
    "linear_process": "a sequence of 5 stages, A → B → C → D → E",
    "comparison_matrix": "3 options compared across 5 attributes",
    "swimlane": "a process that crosses 3 actors/teams with handoffs",
    "layered_architecture": "4 horizontal tiers (e.g., UI / API / Service / Data)",
    "concept_map": "a central concept with 6 related sub-concepts radiating out",
    "microservices_mesh": "a central bus/gateway with 4 services + their databases",
    "request_flow": "a 5-component left-to-right request traversal with step labels",
    "journey_map": "5 customer-journey stages with metric above + owner below each",
    "decision_tree": "a yes/no decision branching to 2 actions, each with 2 follow-ups",
    "timeline": "6 dated events alternating above/below a horizontal time axis",
    "before_after": "two columns showing 4 before-state items vs 4 after-state items",
    "pros_cons": "two columns of 4 pros vs 4 cons for a single subject",
    "quadrant_2x2": "a 2x2 matrix with two axes and 4 items plotted by quadrant",
    "tree_hierarchy": "a 1-root, 3-children, 2-leaves-each top-down taxonomy",
    "stat_callouts": "a 3x2 grid of 6 featured statistics with label + note",
    "cqrs_pattern": "a CQRS architecture with separate write path, event store, and read path lanes",
    "value_proposition": "a vector hero slide with central tagline + 3 benefit cards (no raster image)",
    "hero_with_callouts": "a hero slide with an AI-generated illustration on the left + 3 callout cards on the right (requires Klein)",
}


def _olmo_pickable_idioms() -> list[str]:
    """Idioms Olmo (Setup A) is allowed to pick from. Excludes Klein-dependent
    hero idioms — those only exist in Setup B's hybrid path."""
    return [i for i in OLMO_IDIOMS if i not in HERO_IDIOMS and i in _IDIOM_FITS]


def _build_olmo_pick_system() -> str:
    options = "\n".join(
        f"- {idiom_id}: {_IDIOM_FITS[idiom_id]}"
        for idiom_id in _olmo_pickable_idioms()
    )
    return f"""You are choosing the visual idiom that best matches the STRUCTURAL SHAPE of the content. Reject surface-keyword matching — words like "services", "process", "journey", "transformation" appear in many shapes and don't determine the idiom by themselves.

WORKFLOW (follow in order, do not skip):
1. Identify the CORE STRUCTURAL PATTERN in the content (not the topic, not the keywords).
2. Match that pattern to an idiom using the IF-THEN rules below.
3. If multiple match, prefer the one with the MOST SPECIFIC fit to the actual content shape.

IF-THEN RULES (apply BEFORE looking at surface keywords):
- Content compares a CURRENT/LEGACY state vs a NEW/FUTURE state with concrete metrics on BOTH sides → before_after (NOT request_flow, NOT linear_process, NOT journey_map)
- Content lists 2-5 OPTIONS compared across 4-6 ATTRIBUTES → comparison_matrix (NOT pros_cons unless explicit pro/con framing)
- Content plots 4+ ITEMS on TWO ORTHOGONAL AXES (effort/impact, cost/value, etc.) → quadrant_2x2 (NOT comparison_matrix)
- Content features 4+ PROMINENT METRICS as the dominant message → stat_callouts (NOT timeline unless tied to specific dates)
- Content lists DATED EVENTS along a time axis → timeline
- Content describes a process that CROSSES MULTIPLE ACTORS or TEAMS with handoffs → swimlane
- Content describes CQRS / event sourcing with WRITE PATH and READ PATH through an event store → cqrs_pattern (preferred over swimlane when CQRS is explicit)
- Content describes a process with multiple actors but NOT CQRS (e.g., write/read paths) → swimlane
- Content describes a STACK/HIERARCHY of TIERS (UI/API/Service/Data) → layered_architecture
- Content describes a CENTRAL bus, gateway, or orchestrator with attached services → microservices_mesh
- Content describes 5 ORDERED STAGES with no actor split → linear_process
- Content describes STAGES + METRICS per stage + OWNERS per stage → journey_map (the specific shape, not just any process)
- Content describes YES/NO conditional branching → decision_tree
- Content explicitly weighs 3-4 PROS vs 3-4 CONS → pros_cons
- Content describes a PARENT concept with 2-3 CHILDREN and LEAVES per child → tree_hierarchy
- Content describes a HUB concept with 4-6 RELATED SPOKES → concept_map
- Content is HERO / VALUE-PROP framing (single value statement + 3 supporting benefits) WITHOUT a request for an image → value_proposition (vector hero — no raster needed)
- DEFAULT (if uncertain): linear_process

ANTI-PATTERNS — these false signals trick the picker:
- "Microservices" or "services" in the prompt does NOT automatically mean microservices_mesh. Check the actual structural pattern.
- "Process" or "flow" does NOT automatically mean process flow. Check whether there are actors, before/after states, or other dominant structural cues.
- "Journey" as a phrase ("18-month journey to X") does NOT mean journey_map. journey_map requires explicit STAGES + METRICS + OWNERS per stage.
- "Transformation" does NOT mean linear_process. If both old and new states are described with metrics, that's before_after.
- "Marketing case study" mentioned once does NOT make this a marketing visual. Look at the content body, not incidental phrases.

OPTIONS (catalog):
{options}

CONCRETE EXAMPLE PICKS:
- "Monolith to microservices migration: legacy was X with these metrics, now is Y with these metrics" → before_after (BOTH states are described with metrics)
- "REST vs GraphQL vs gRPC across transport, schema, caching" → comparison_matrix
- "Map 8 competitors on complexity-vs-feature-depth axes" → quadrant_2x2
- "Q3 metrics: ARR, NRR, customer count, gross margin, sales cycle" → stat_callouts
- "Customer onboarding 6 stages, each with conversion rate and owning team" → journey_map
- "K8s cluster with namespaces frontend/api/data each holding services" → swimlane (namespaces are lanes)
- "CQRS write path → store → events; read path → projection → query" → cqrs_pattern (3-lane structure with event store in the middle)
- "The future of cloud is serverless: ship faster, lower cost, infinite scale" (3 benefits, no image requested) → value_proposition
- "Multi-region deployment: CDN → ALB → API GW → microservices → DBs (per service)" → microservices_mesh (central GW + radiating services)
- "Web tier → API tier → service tier → data tier with components in each" → layered_architecture

Return ONLY valid JSON:
{{
  "idiom_id": "exact id from the OPTIONS list",
  "title": "concrete, specific title that names the actual subject (never just 'Process Flow' or 'Architecture')",
  "subtitle": "single-line audience/context (e.g., 'for enterprise security review')"
}}"""


OLMO_PICK_SYSTEM = _build_olmo_pick_system()


# ──────────────────────────────────────────────────────────────────────
# Two-stage picker — much easier for any model than a 17-option flat pick
# ──────────────────────────────────────────────────────────────────────
# Stage 1: classify content into a category (1 of 6, simple short prompt)
# Stage 2: pick the idiom within that category (1 of 3-4, focused options)
#
# Validated approach: smaller decision space = more reliable picks. Olmo
# (7B) struggles with 17 options + complex rules; thrives at 1-of-6.

CATEGORIES = {
    "ARCHITECTURE": {
        "fit": "system / deployment / infrastructure diagrams — components, services, infra",
        "idioms": ["microservices_mesh", "layered_architecture", "cqrs_pattern", "swimlane"],
    },
    "COMPARISON": {
        "fit": "side-by-side analysis — options, alternatives, before/after, pros/cons",
        "idioms": ["comparison_matrix", "quadrant_2x2", "before_after", "pros_cons"],
    },
    "PROCESS": {
        "fit": "sequential steps, workflows, journeys, decisions",
        "idioms": ["linear_process", "request_flow", "journey_map", "decision_tree"],
    },
    "DATA": {
        "fit": "featured metrics, statistics, time-series, dashboards",
        "idioms": ["stat_callouts", "timeline"],
    },
    "STRUCTURE": {
        "fit": "hierarchies, concept maps, organizational layouts",
        "idioms": ["tree_hierarchy", "concept_map"],
    },
    "HERO": {
        "fit": "value propositions, vision statements, persuasive cover slides",
        "idioms": ["value_proposition", "hero_with_callouts"],
    },
}


def _build_category_pick_system() -> str:
    """Stage 1: pick one of 6 categories. Simple, deterministic."""
    options = "\n".join(
        f"- {name}: {meta['fit']}"
        for name, meta in CATEGORIES.items()
    )
    return f"""You are classifying source content into ONE of 6 visual categories. Pick the BEST single match.

CATEGORIES:
{options}

CLASSIFICATION RULES:
- Content about technical systems, services, deployments, architectures → ARCHITECTURE
- Content comparing 2+ options OR showing before/after states → COMPARISON
- Content describing sequential steps, decisions, or customer journeys → PROCESS
- Content featuring statistics, metrics, dashboards, or time-series events → DATA
- Content showing parent-child hierarchies or hub-spoke concept maps → STRUCTURE
- Content making a persuasive case, hero statement, or value proposition → HERO

Return ONLY valid JSON:
{{
  "category": "exact match: ARCHITECTURE, COMPARISON, PROCESS, DATA, STRUCTURE, or HERO",
  "title": "concrete specific slide title",
  "subtitle": "single-line audience/context"
}}"""


def _build_idiom_pick_system(category: str, allow_hero_klein: bool = False) -> str:
    """Stage 2: pick one idiom from the chosen category. Focused 3-4 options."""
    meta = CATEGORIES.get(category)
    if not meta:
        # Should never happen, but guard against bad stage-1 outputs
        return _build_category_pick_system()

    options = []
    for idiom_id in meta["idioms"]:
        # Exclude hero_with_callouts from Setup A picks (needs Klein)
        if idiom_id == "hero_with_callouts" and not allow_hero_klein:
            continue
        fit = _IDIOM_FITS.get(idiom_id, "")
        options.append(f"- {idiom_id}: {fit}")

    options_text = "\n".join(options)

    # Per-category disambiguation rules
    rules = {
        "ARCHITECTURE": (
            "- Central bus/gateway + radiating services → microservices_mesh\n"
            "- Vertical tiers (UI/API/Service/Data) → layered_architecture\n"
            "- Explicit write path + read path + event store (CQRS) → cqrs_pattern\n"
            "- Multi-actor or multi-namespace process → swimlane"
        ),
        "COMPARISON": (
            "- Multiple options across multiple attributes → comparison_matrix\n"
            "- Items plotted on TWO orthogonal axes → quadrant_2x2\n"
            "- One subject changing from before-state to after-state → before_after\n"
            "- One subject with explicit pros AND cons → pros_cons"
        ),
        "PROCESS": (
            "- 5 ordered stages, no actors/branches → linear_process\n"
            "- Single request traversing components → request_flow\n"
            "- 5 stages with per-stage metrics + per-stage owners → journey_map\n"
            "- YES/NO conditional branching → decision_tree"
        ),
        "DATA": (
            "- Featured metrics as dominant elements → stat_callouts\n"
            "- Dated events on a time axis → timeline"
        ),
        "STRUCTURE": (
            "- Parent + children + leaves → tree_hierarchy\n"
            "- Hub concept + related spokes → concept_map"
        ),
        "HERO": (
            "- ABSTRACT value-prop / vision / philosophy / 'the future of X' framing → value_proposition (vector hero, NO image)\n"
            "- CONCRETE visual subject (a product photo, a metaphor like a rocket / mountain / bridge, a scene that can be depicted) → "
            + ("hero_with_callouts (a raster image WILL be generated by an AI image model)\n" if allow_hero_klein else "value_proposition (vector hero — concrete imagery not possible without a separate image model)\n")
            + "IMPORTANT: 'vision', 'mission', 'future of', 'value proposition', 'transformation' content is ABSTRACT — prefer value_proposition unless the content explicitly describes a concrete visual scene that an AI illustrator should depict."
        ),
    }.get(category, "")

    return f"""You are choosing the visual idiom within the {category} category. Pick the BEST single match.

OPTIONS:
{options_text}

WHEN TO PICK WHICH:
{rules}

Return ONLY valid JSON:
{{
  "idiom_id": "exact id from the options above"
}}"""


async def pick_category_and_meta(content: str, model: str, num_predict: int) -> Optional[dict]:
    """Stage 1: category + title + subtitle."""
    logger.info(f"[visual_freeform] stage 1 (category) model={model}")
    result = await ollama_service.generate(
        prompt=f"SOURCE CONTENT:\n{content}\n\nClassify and return JSON only.",
        system=_build_category_pick_system(),
        model=model,
        temperature=0.2,
        num_predict=num_predict,
        timeout=WARM_TIMEOUT,
        format="json",
        voice_modifier=False,
    )
    raw = result.get("response", "")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    return None


async def pick_idiom_in_category(
    content: str,
    category: str,
    model: str,
    num_predict: int,
    allow_hero_klein: bool = False,
) -> Optional[str]:
    """Stage 2: idiom within category."""
    logger.info(f"[visual_freeform] stage 2 (idiom in {category}) model={model}")
    result = await ollama_service.generate(
        prompt=f"SOURCE CONTENT:\n{content}\n\nPick the best idiom within {category} and return JSON only.",
        system=_build_idiom_pick_system(category, allow_hero_klein=allow_hero_klein),
        model=model,
        temperature=0.2,
        num_predict=num_predict,
        timeout=WARM_TIMEOUT,
        format="json",
        voice_modifier=False,
    )
    raw = result.get("response", "")
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return None
        try:
            parsed = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return parsed.get("idiom_id") if isinstance(parsed, dict) else None

