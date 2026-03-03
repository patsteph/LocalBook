"""Content Generation API endpoints - Text-based skill outputs

Uses professional-grade templates from output_templates.py to ensure
world-class document quality across all output types.
"""
import asyncio
import logging
import re
import time
import traceback
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Dict, List, Optional
import json


def _clean_llm_output(text: str) -> str:
    """Post-process LLM output: detect repetition loops and ensure clean ending.
    
    Addresses three failure modes:
    1. Sentence-level loops (same sentence repeats 3+ times)
    2. Paragraph-level loops (same paragraph block repeats)
    3. Mid-sentence cutoff (output ends abruptly)
    """
    if not text or len(text) < 100:
        return text
    
    original_len = len(text)
    
    # --- 0. Strip leaked prompt scaffolding ---
    # The LLM sometimes echoes internal pipeline markers into its output.
    text = re.sub(r'-{3,}\s*(RECENT CHAT|END CHAT|End Chat|Recent Chat)\s*-{3,}', '', text)
    text = re.sub(r'-{3,}\s*SECTION TO WRITE NOW\s*-{3,}', '', text)
    # Strip "SECTION TO WRITE NOW:" lines (outline-first pipeline leak)
    text = re.sub(r'^.*SECTION TO WRITE NOW.*$', '', text, flags=re.MULTILINE)
    # Strip "CONTENT WRITTEN SO FAR" blocks
    text = re.sub(r'^.*CONTENT WRITTEN SO FAR.*$', '', text, flags=re.MULTILINE)
    # Strip "Write section" instruction echoes
    text = re.sub(r'^Write (?:section|ONLY this section).*$', '', text, flags=re.MULTILINE)
    
    # --- 0b. Truncate degenerate run-on sentences ---
    # Detect individual "sentences" that are 100+ words with no period — a sign
    # the model is looping.  Break them at the last clause boundary.
    lines = text.split('\n')
    cleaned_lines = []
    for line in lines:
        words = line.split()
        if len(words) > 120 and '.' not in line[:500]:
            # Find a reasonable break point (last comma or semicolon in first 80 words)
            truncated = ' '.join(words[:80])
            last_break = max(truncated.rfind(','), truncated.rfind(';'), truncated.rfind(' — '))
            if last_break > len(truncated) * 0.4:
                line = truncated[:last_break + 1].rstrip(',;') + '.'
                logger.warning(f"[PostProcess] Truncated run-on sentence: {len(words)} words → ~80 words")
        cleaned_lines.append(line)
    text = '\n'.join(cleaned_lines)
    
    # --- 1. Detect sentence-level repetition ---
    # Split into sentences and find repeating patterns
    sentences = re.split(r'(?<=[.!?])\s+', text)
    if len(sentences) > 6:
        # Look for a repeating sentence (appears 3+ times)
        seen_count = {}
        first_repeat_idx = None
        for i, s in enumerate(sentences):
            # Normalize for comparison (strip whitespace, lowercase)
            key = s.strip().lower()[:200]
            if len(key) < 20:
                continue
            seen_count[key] = seen_count.get(key, 0) + 1
            if seen_count[key] >= 3 and first_repeat_idx is None:
                # Find where this sentence first appeared after unique content
                # Keep the first two occurrences, cut at third
                count = 0
                for j, s2 in enumerate(sentences):
                    if s2.strip().lower()[:200] == key:
                        count += 1
                        if count == 3:
                            first_repeat_idx = j
                            break
        
        if first_repeat_idx is not None and first_repeat_idx > 3:
            # Truncate at the point repetition starts (3rd occurrence)
            text = ' '.join(sentences[:first_repeat_idx]).strip()
            logger.warning(f"[PostProcess] Truncated repetitive output: "
                          f"{original_len} → {len(text)} chars "
                          f"(cut at sentence {first_repeat_idx}/{len(sentences)})")
    
    # --- 2. Detect paragraph-level loops ---
    # Split into paragraphs and check for repeated blocks
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    if len(paragraphs) > 4:
        seen_paras = {}
        cut_idx = None
        for i, p in enumerate(paragraphs):
            key = p[:300].lower()
            if len(key) < 50:
                continue
            if key in seen_paras:
                # This paragraph is a repeat — if it repeats 2+ times, cut
                seen_paras[key] += 1
                if seen_paras[key] >= 2 and cut_idx is None:
                    cut_idx = i
            else:
                seen_paras[key] = 1
        
        if cut_idx is not None and cut_idx > 2:
            text = '\n\n'.join(paragraphs[:cut_idx]).strip()
            logger.warning(f"[PostProcess] Truncated paragraph loop: "
                          f"cut at paragraph {cut_idx}/{len(paragraphs)}")
    
    # --- 3. Per-paragraph degeneration filter ---
    # Remove individual paragraphs that are degenerate (low unique trigram ratio
    # OR high filler-word density).
    # This catches phrase-level loops that sentence/paragraph exact-match misses.
    _FILLER_WORDS = {
        "thereby", "consequently", "fundamentally", "essentially", "progressively",
        "significantly", "substantially", "increasingly", "continuously", "ultimately",
        "revolutionizing", "transforming", "facilitating", "leveraging", "catalyzing",
        "unprecedented", "indispensable", "comprehensive", "redefining", "reshaping",
        "propelling", "fostering", "establishing", "navigating", "maximizing",
        "transcending", "perpetually", "relentlessly", "irrespective", "henceforth",
        "moreover", "furthermore", "additionally", "notably", "undeniably",
        "remarkably", "inherently", "profoundly", "pivotal", "paramount",
        "imperative", "multifaceted", "synergy", "paradigm", "holistic",
    }
    _FILLER_PHRASES = [
        "it is worth noting", "it is important to note", "it should be noted",
        "in the context of", "in terms of", "with respect to",
        "plays a crucial role", "plays a vital role", "plays a key role",
        "it goes without saying", "needless to say",
        "in today's rapidly evolving", "in an increasingly",
        "serves as a testament", "paving the way for",
    ]
    paragraphs2 = [p.strip() for p in text.split('\n\n') if p.strip()]
    if len(paragraphs2) > 2:
        kept = []
        removed = 0
        for p in paragraphs2:
            p_words = p.lower().split()
            if len(p_words) >= 30:
                # Check 3a: trigram uniqueness
                p_trigrams = [f"{p_words[j]} {p_words[j+1]} {p_words[j+2]}" for j in range(len(p_words) - 2)]
                p_unique = len(set(p_trigrams)) / len(p_trigrams) if p_trigrams else 1.0
                if p_unique < 0.40:
                    removed += 1
                    logger.warning(f"[PostProcess] Removed degenerate paragraph "
                                  f"({len(p_words)} words, trigram uniqueness {p_unique:.0%})")
                    continue
                
                # Check 3b: filler-word density — buzzword-stuffed paragraphs
                filler_hits = sum(1 for w in p_words if w.strip('.,;:!?') in _FILLER_WORDS)
                # Also count multi-word filler phrases
                p_lower = p.lower()
                filler_hits += sum(1 for ph in _FILLER_PHRASES if ph in p_lower)
                filler_pct = filler_hits / len(p_words)
                if filler_pct > 0.05 and len(p_words) > 60:
                    removed += 1
                    logger.warning(f"[PostProcess] Removed filler-heavy paragraph "
                                  f"({len(p_words)} words, {filler_pct:.0%} buzzwords)")
                    continue
            kept.append(p)
        if removed > 0:
            text = '\n\n'.join(kept)
    
    # --- 4. Ensure clean sentence ending ---
    text = text.rstrip()
    if text and text[-1] not in '.!?:*':
        # Find the last sentence-ending punctuation
        last_period = max(text.rfind('. '), text.rfind('.\n'), 
                         text.rfind('! '), text.rfind('!\n'),
                         text.rfind('? '), text.rfind('?\n'))
        # Also check if text ends with period right at the end
        if text.endswith('.') or text.endswith('!') or text.endswith('?'):
            pass  # Already ends cleanly
        elif last_period > len(text) * 0.5:
            # Only truncate if we keep at least 50% of content
            text = text[:last_period + 1]
            logger.warning(f"[PostProcess] Trimmed to last complete sentence: "
                          f"{original_len} → {len(text)} chars")
        else:
            # Can't find a good cut point — append ellipsis
            text = text.rstrip(',; ') + '...'
    
    return text

logger = logging.getLogger(__name__)


def _score_section_quality(text: str) -> tuple:
    """Fast heuristic quality score for a generated section (0-100).
    
    Detects degeneration patterns WITHOUT any LLM call:
    1. Low unique trigram ratio — phrase-level repetition loops
    2. Extreme sentence length — run-on degeneration
    3. Prompt scaffolding leaks — model echoed instructions
    4. Filler-word density — buzzword chains with no substance
    5. Average sentence length — catches paragraphs with few/no periods
    
    Returns (score, reason).  Score < 40 = should retry.
    """
    if not text or len(text.split()) < 20:
        return (10, "too_short")
    
    words = text.lower().split()
    total_words = len(words)
    
    # 1. Unique trigram ratio — degenerate text reuses the same 3-word phrases
    if total_words >= 30:
        trigrams = [f"{words[i]} {words[i+1]} {words[i+2]}" for i in range(total_words - 2)]
        unique_ratio = len(set(trigrams)) / len(trigrams) if trigrams else 1.0
        if unique_ratio < 0.40:
            return (15, f"repetitive (trigram uniqueness {unique_ratio:.0%})")
        if unique_ratio < 0.55:
            return (35, f"somewhat repetitive (trigram uniqueness {unique_ratio:.0%})")
    
    # 2. Max sentence length — degenerate text produces 100+ word "sentences"
    sentences = re.split(r'[.!?]\s+', text)
    if sentences:
        sent_lengths = [len(s.split()) for s in sentences if s.strip()]
        max_words = max(sent_lengths, default=0)
        if max_words > 120:
            return (20, f"run-on sentence ({max_words} words)")
        if max_words > 80:
            return (35, f"long sentence ({max_words} words)")
        
        # 5. Average sentence length — if few sentences relative to word count,
        # the text is one big run-on paragraph without periods
        if len(sent_lengths) > 0:
            avg_sent_len = total_words / len(sent_lengths)
            if avg_sent_len > 60 and total_words > 100:
                return (25, f"very few sentences (avg {avg_sent_len:.0f} words/sentence)")
    
    # 3. Prompt leak detection
    leak_patterns = ["SECTION TO WRITE", "CONTENT WRITTEN SO FAR", "Source material:", 
                     "Write section", "YOUR TASK:", "RULES:"]
    leak_count = sum(1 for p in leak_patterns if p in text)
    if leak_count >= 2:
        return (25, f"prompt leak ({leak_count} patterns)")
    
    # 4. Filler-word density — degenerate LLM output is packed with these
    filler_words = {
        "thereby", "consequently", "fundamentally", "essentially", "progressively",
        "significantly", "substantially", "increasingly", "continuously", "ultimately",
        "revolutionizing", "transforming", "facilitating", "leveraging", "catalyzing",
        "unprecedented", "indispensable", "comprehensive", "redefining", "reshaping",
        "propelling", "fostering", "establishing", "navigating", "maximizing",
        "moreover", "furthermore", "additionally", "notably", "undeniably",
        "remarkably", "inherently", "profoundly", "pivotal", "paramount",
        "imperative", "multifaceted", "synergy", "paradigm", "holistic",
    }
    if total_words >= 50:
        filler_count = sum(1 for w in words if w.strip('.,;:!?') in filler_words)
        filler_density = filler_count / total_words
        if filler_density > 0.06:
            return (20, f"filler-heavy ({filler_density:.0%} buzzwords)")
        if filler_density > 0.04:
            return (35, f"somewhat filler-heavy ({filler_density:.0%} buzzwords)")
    
    return (80, "ok")


async def _verify_section_structure(
    section_content: str,
    requirement: str,
    source_context: str,
) -> str:
    """P3: Heuristic structural check + targeted LLM fix for section deficits.
    
    Checks whether the section content matches what the requirement demands
    (e.g., heading present, vocabulary table).
    If a specific deficit is found, calls the fast model to append the missing
    piece — NOT to rewrite the whole section.
    
    Returns the (possibly fixed) section content.
    """
    req_lower = requirement.lower()
    issues = []
    
    # Check 1: Must start with a markdown heading
    has_heading = bool(re.search(r'^#{1,4}\s+\S', section_content, re.MULTILINE))
    if not has_heading and len(section_content.split()) > 30:
        issues.append("missing_heading")
    
    # Check 2: Vocabulary sections should have a table
    needs_table = "vocabulary" in req_lower
    if needs_table and '|' not in section_content:
        issues.append("missing_vocabulary_table")
    
    if not issues:
        return section_content
    
    # Targeted fix — ask fast model to append ONLY the missing piece
    logger.info(f"[STRUCTURE-CHECK] Issues detected: {issues}")
    
    fix_instructions = []
    if "missing_heading" in issues:
        fix_instructions.append("Add an appropriate markdown heading (##) at the top.")
    if "missing_vocabulary_table" in issues:
        fix_instructions.append(
            "Add a vocabulary table in markdown format: | Term | Plain English | Analogy |"
        )
    fix_prompt = f"""The following section is missing required elements.

SECTION CONTENT:
{section_content[:3000]}

MISSING ELEMENTS — add ONLY these, do not rewrite the section:
{chr(10).join(f'- {instr}' for instr in fix_instructions)}

Source material for accuracy:
{source_context[:2000]}

Write ONLY the missing elements now (they will be appended to the section):"""
    
    try:
        fix = await rag_engine._call_ollama(
            "You add missing structural elements to documents. Be concise and factual.",
            fix_prompt,
            model=settings.ollama_model,
            num_predict=400,
            temperature=0.4,
        )
        fix = fix.strip()
        if fix and len(fix) > 20:
            section_content = section_content.rstrip() + "\n\n" + fix
            logger.info(f"[STRUCTURE-CHECK] Appended fix for {issues} ({len(fix)} chars)")
    except Exception as e:
        logger.warning(f"[STRUCTURE-CHECK] Fix failed (non-fatal): {e}")
    
    return section_content



# NOTE: v1 Feynman functions (_normalize_feynman_headings, _inject_feynman_quiz_links,
# _embed_feynman_knowledge_map) removed — replaced by _generate_feynman_v2 pipeline.


# Temperature scheduling for different section types
_SECTION_TEMP_ADJUSTMENTS = {
    # Factual/structured sections → lower temperature for precision
    "overview": -0.10,
    "vocabulary": -0.15,
    "reflection": -0.05,
    "knowledge map": -0.10,
    # Creative/explanatory sections → slightly higher for engagement
    "foundation": +0.05,
    "analogy": +0.05,
    "teach": +0.05,
    # Analytical sections → baseline
    "first principles": 0.0,
    "building": 0.0,
    "mastery": 0.0,
}


def _get_section_temperature(base_temp: float, requirement: str) -> float:
    """Compute per-section temperature based on section type.
    
    Factual sections (tables, tests, maps) get lower temp for precision.
    Creative sections (analogies, teach-back) get slightly higher for engagement.
    """
    req_lower = requirement.lower()
    adjustment = 0.0
    for keyword, delta in _SECTION_TEMP_ADJUSTMENTS.items():
        if keyword in req_lower:
            adjustment = delta
            break  # Use first match (most specific)
    result = base_temp + adjustment
    return max(0.2, min(0.95, result))  # Clamp to safe range


from storage.skills_store import skills_store
from storage.content_store import content_store
from services.rag_engine import rag_engine
from services.output_templates import build_document_prompt, DOCUMENT_TEMPLATES
from services.context_builder import context_builder
from config import settings

# Skills that use Outline-First generation (multi-step) instead of single-pass.
# These are long-form document types (≥4500 tokens) where single-pass quality degrades.
# Shorter skills (summary, study_guide, etc.) use single-pass + Mirostat + verification gate.
OUTLINE_FIRST_SKILLS = {"deep_dive", "debate"}


# ═══════════════════════════════════════════════════════════════════════════
# Feynman Pipeline v2 — Multi-Phase Curriculum Generation
# ═══════════════════════════════════════════════════════════════════════════
# Unlike the generic outline-first pipeline, this treats curriculum creation
# as a multi-phase process: analyze → generate prose → enrich → map → assemble.
# Each LLM call has ONE focused job.  Structure is controlled by code, not LLM.

def _parse_feynman_concepts(analysis: str) -> list:
    """Parse concept list from Phase 1 analysis output."""
    concepts = []
    current = {}

    for line in analysis.split('\n'):
        line = line.strip()
        if not line:
            if current.get('name'):
                concepts.append(current)
                current = {}
            continue

        lower = line.lower()
        if lower.startswith('name:'):
            if current.get('name'):
                concepts.append(current)
            current = {'name': line.split(':', 1)[1].strip()}
        elif lower.startswith('definition:'):
            current['definition'] = line.split(':', 1)[1].strip()
        elif lower.startswith('example:'):
            current['example'] = line.split(':', 1)[1].strip()
        elif re.match(r'^concept\s*\d', lower):
            if current.get('name'):
                concepts.append(current)
            current = {}

    if current.get('name'):
        concepts.append(current)

    # Ensure all concepts have required fields
    for c in concepts:
        c.setdefault('definition', 'A key concept in this topic')
        c.setdefault('example', 'Real-world application')

    return concepts


def _parse_feynman_enrichment(text: str) -> tuple:
    """Parse enrichment output into (vocab_table_str, reflection_prompts_str)."""
    vocab = ""
    reflections = ""

    # Split on REFLECTION marker
    parts = re.split(r'(?i)(?:REFLECTION\s*PROMPTS?|REFLECTIONS?)\s*:?\s*\n', text, maxsplit=1)

    if len(parts) >= 2:
        vocab_section = parts[0]
        reflections_section = parts[1]
    else:
        # Try to find where table ends and numbered list begins
        table_end = text.rfind('|')
        if table_end > 0:
            vocab_section = text[:table_end + 1]
            reflections_section = text[table_end + 1:]
        else:
            vocab_section = text
            reflections_section = ""

    # Extract table rows
    table_lines = [l for l in vocab_section.split('\n') if '|' in l]
    if len(table_lines) >= 3:
        vocab = '\n'.join(table_lines)

    # Extract numbered reflections
    reflection_lines = []
    for line in reflections_section.split('\n'):
        line = line.strip()
        if re.match(r'^\d+[\.\)]\s', line):
            reflection_lines.append(line)
    if reflection_lines:
        reflections = '\n'.join(reflection_lines)

    return vocab, reflections


def _build_feynman_knowledge_map(concepts: list, topic: str) -> str:
    """Build an SVG knowledge map from extracted concepts — no LLM needed.

    Uses the proven SVG mindmap builder (svg_templates.py) instead of Mermaid.
    Mermaid mindmaps render as garbage colored lines; SVG renders reliably.
    """
    from services.svg_templates import build_svg_visual, COLOR_THEMES

    branches = []
    sub_items = {}
    for concept in concepts[:6]:
        name = concept.get('name', '')
        if not name:
            continue
        branches.append(name)
        # Use first sentence of definition as sub-item
        defn = concept.get('definition', '')
        if defn:
            short_def = defn.split('.')[0].strip()
            words = short_def.split()
            if len(words) > 12:
                short_def = ' '.join(words[:12]) + '...'
            if short_def:
                sub_items[name] = [short_def]

    if not branches:
        branches = [topic]

    structure = {
        "themes": branches,
        "subpoints": sub_items,
        "insight": f"Key concepts in {topic}",
    }
    colors = COLOR_THEMES.get("ocean", COLOR_THEMES["auto"])

    return build_svg_visual(
        template_id="mindmap",
        structure=structure,
        colors=colors,
        title=f"Knowledge Map: {topic[:80]}",
        dark_mode=True,
    )


# ── Feynman Quiz Pre-Generation Cache ────────────────────────────────────────
# Quizzes are generated in the background after document creation.
# By the time the user reads to a quiz section, it's already waiting.
# Key: "{notebook_id}:level{N}" → list of question dicts
_feynman_quiz_cache: Dict[str, list] = {}
# Track which notebooks have quiz generation in progress
_feynman_quiz_generating: Dict[str, bool] = {}


async def _pre_generate_feynman_quizzes(
    notebook_id: str,
    topic: str,
    parts: dict,
    level_concepts: dict,
    level_goals: dict,
):
    """Background task: generate quizzes from section narratives after doc creation.

    Uses the narrative text (Phase 3 output) as quiz input — this is focused,
    high-quality context that produces much better questions than raw sources.
    """
    from services.structured_llm import structured_llm

    _feynman_quiz_generating[notebook_id] = True
    logger.info(f"[FEYNMAN-QUIZ] Starting background quiz pre-generation for {notebook_id}")

    for level_num in range(1, 5):
        title = level_goals[level_num][0]
        narrative = parts.get(level_num, "")
        concepts = level_concepts.get(level_num, [])
        concepts_text = ", ".join(c['name'] for c in concepts)
        difficulty = "easy" if level_num <= 2 else "medium"

        quiz_input = (
            f"Topic: {topic}\nSection: {title}\n"
            f"Key Concepts: {concepts_text}\n\n"
            f"Study Material:\n{narrative}"
        )

        try:
            quiz = await structured_llm.generate_quiz(
                content=quiz_input,
                num_questions=4,
                difficulty=difficulty,
            )
            if quiz.questions:
                key = f"{notebook_id}:level{level_num}"
                _feynman_quiz_cache[key] = [
                    {
                        "q": q.question,
                        "a": q.answer,
                        "options": q.options or [],
                        "explanation": q.explanation,
                    }
                    for q in quiz.questions
                ]
                logger.info(f"[FEYNMAN-QUIZ] Level {level_num} ({title}): "
                            f"{len(quiz.questions)} questions cached")
            else:
                logger.warning(f"[FEYNMAN-QUIZ] Level {level_num}: 0 questions generated")
        except Exception as e:
            logger.warning(f"[FEYNMAN-QUIZ] Level {level_num} failed: {e}")

    _feynman_quiz_generating.pop(notebook_id, None)
    cached_count = sum(
        1 for lvl in range(1, 5)
        if f"{notebook_id}:level{lvl}" in _feynman_quiz_cache
    )
    logger.info(f"[FEYNMAN-QUIZ] Background complete: {cached_count}/4 levels cached")


def _assemble_feynman_document(
    concepts: list,
    parts: dict,
    passages: dict,
    enrichments: dict,
    knowledge_map: str,
    topic: str,
    level_concepts: dict = None,
    notebook_id: str = "",
) -> str:
    """Assemble the Feynman conductor document.  Pure code — no LLM.

    The document acts as a GUIDED LEARNING PATH that connects source content,
    LLM prose, quizzes, and audio into one cohesive experience.  Interactive
    elements use code blocks (feynman-quiz, feynman-audio) for reliable rendering.
    """
    doc = []

    # ── Learning Journey Header ──
    concept_bullets = '\n'.join(
        f"- **{c['name']}** — {c['definition']}" for c in concepts[:6]
    )
    doc.append(f"""# Your Learning Journey: {topic}

**4 levels of understanding.** Each includes readings from your sources, explanations, and a knowledge check. Work through them in order — don't skip ahead until you can pass each quiz.

**What you'll master:**

{concept_bullets}

*Estimated time: 2–4 hours across four levels*""")

    # ── Levels 1-4 ──
    level_configs = [
        (1, "Foundation", "easy", "Explain these ideas to a friend over coffee"),
        (2, "How It Connects", "medium", "See how these concepts work together in the real world"),
        (3, "First Principles", "hard", "Understand WHY things work, not just WHAT happens"),
        (4, "Mastery", "hard", "Teach it back — the ultimate test of understanding"),
    ]

    for num, title, difficulty, goal in level_configs:
        doc.append(f"\n---\n\n## Level {num}: {title}")
        doc.append(f"*Goal: {goal}*\n")

        # Source passages (from RAG retrieval)
        level_passages = passages.get(num, [])
        if level_passages:
            lvl_concept_names = [c['name'] for c in (level_concepts or {}).get(num, [])]
            if lvl_concept_names:
                intro = f"**From your sources** on {' and '.join(lvl_concept_names[:2])}:\n"
            else:
                intro = "**From your sources:**\n"
            doc.append(intro)
            for p in level_passages[:3]:
                text = p.get('text', '').strip()
                source = p.get('source', 'Source')
                if text:
                    # Trim to ~300 chars, end at sentence boundary
                    if len(text) > 300:
                        end = text[:300].rfind('.')
                        text = text[:end + 1] if end > 80 else text[:300] + '…'
                    doc.append(f'> {text}\n> — *{source}*\n')

        # LLM prose (connecting narrative)
        prose = parts.get(num, "")
        prose_lines = prose.strip().split('\n')
        # Strip any heading the LLM may have added
        while prose_lines and prose_lines[0].strip().startswith('#'):
            prose_lines.pop(0)
        cleaned_prose = '\n'.join(prose_lines).strip()
        if cleaned_prose:
            doc.append(cleaned_prose)

        # Enrichment (skip on Level 4 — mastery narration already has teach-back challenges)
        if num < 4:
            enr = enrichments.get(num, {})
            if enr.get('vocab'):
                doc.append(f"\n\n### Key Terms\n\n{enr['vocab']}")
            if enr.get('reflections'):
                doc.append(f"\n\n### Reflect Before Continuing\n\n{enr['reflections']}")
        else:
            enr = enrichments.get(num, {})
            if enr.get('vocab'):
                doc.append(f"\n\n### Key Terms\n\n{enr['vocab']}")
            # Skip reflections on Level 4 — the mastery prose already poses challenges

        # Interactive quiz block — references pre-generated quiz from cache.
        # Background task generates quizzes from section narrative AFTER doc creation.
        # Clicking the button fetches instantly from cache (no LLM call on click).
        quiz_data = json.dumps({
            "notebook_id": notebook_id,
            "level": num,
            "label": f"Check Your {title} Knowledge",
            "difficulty": difficulty,
        })
        doc.append(f"\n\n```feynman-quiz\n{quiz_data}\n```")

        # Audio link on Level 1 to introduce the feature
        if num == 1:
            audio_data = json.dumps({
                "label": "Open the Audio Studio",
                "section": "full"
            })
            doc.append(f"\n```feynman-audio\n{audio_data}\n```")

    # ── Knowledge Map (SVG) ──
    doc.append("\n---\n\n## Knowledge Map\n")
    doc.append(f"\n```feynman-knowledge-map\n{knowledge_map}\n```\n")

    return '\n'.join(doc)


def _is_quality_passage(text: str) -> bool:
    """Return True if a RAG passage is worth showing to the user.

    Filters out image descriptions, raw URL metadata, very short fragments,
    web navigation/chrome, and other low-quality chunks that leak from ingestion.
    """
    t = text.strip()
    if len(t) < 60:
        return False
    tl = t.lower()
    # Image description leaks from OCR / vision processing
    if 'the image does not contain' in tl or 'the image shows' in tl:
        return False
    if tl.startswith('the image '):
        return False
    # Raw URL metadata from web scraping
    if '=== URL:' in t or '=== Raw Content' in t or '=== Most relevant' in t:
        return False
    # Web page navigation / chrome (leaked from scraping)
    nav_signals = [
        'skip to content', 'skip to main', 'share this story',
        'subscribe', 'newsletter', 'cookie', 'privacy policy',
        'terms of service', 'all rights reserved', 'copyright ©',
        'follow us on', 'sign up for', 'log in', 'sign in',
    ]
    if any(sig in tl for sig in nav_signals):
        # Only reject if it's short (real content might mention these in passing)
        if len(t) < 200:
            return False
    # Category/tag listings (e.g., "Categories: AI HARDWARE & CHIPS DEEP DIVES")
    if re.match(r'^(?:categories|tags|topics|sections)\s*:', t, re.IGNORECASE):
        return False
    # Navigation menus: many short capitalized words without sentence structure
    words = t.split()
    if len(words) < 20 and sum(1 for w in words if w[0:1].isupper()) > len(words) * 0.6:
        # Mostly capitalized fragments — likely a nav menu
        if not any(p in t for p in ('.', '!', '?')):
            return False
    # Starts mid-sentence (lowercase first char that isn't a number/symbol)
    first = t[0]
    if first.islower() and len(t) < 120:
        return False
    return True


def _extract_topic_from_chat(chat_context: str) -> str:
    """Extract the user's core question/topic from recent chat context.

    When the user triggers a Feynman (or other doc) from chat, the topic field
    may be empty.  The chat_context contains the user's actual question which
    is the best signal for source ranking.  We grab the last user message.
    """
    if not chat_context:
        return ""
    # Chat context is formatted as lines of dialogue.
    # Look for the last user question — usually the last "User:" or "Human:" line,
    # or just the first substantive line if format is unknown.
    lines = chat_context.strip().split('\n')
    # Walk backwards to find the user's last message
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        # Strip common chat prefixes
        for prefix in ('User:', 'Human:', 'user:', 'human:'):
            if line.startswith(prefix):
                line = line[len(prefix):].strip()
                break
        # Skip assistant/system lines
        low = line.lower()
        if low.startswith(('assistant:', 'ai:', 'system:')):
            continue
        # Skip very short lines or metadata
        if len(line) > 15 and not line.startswith(('---', '```')):
            return line[:200]  # Cap at 200 chars
    # Fallback: use first substantive line
    for line in lines:
        line = line.strip()
        if len(line) > 15:
            return line[:200]
    return ""


def _infer_topic_from_context(source_context: str) -> str:
    """Extract a meaningful topic from source context when topic_focus is empty.

    Looks at source filenames/headers in the context to build a topic string.
    Falls back to extracting the first meaningful heading.
    """
    # Context builder uses "## Source: filename" separators.
    # SKIP the map-reduce overview header ("## Source Overview (all notebook sources)").
    filenames = re.findall(r'##\s*Source:\s*(.+)', source_context[:6000])
    # Filter out the overview header and any non-filename matches
    filenames = [
        f.strip() for f in filenames
        if 'overview' not in f.lower() and 'all notebook' not in f.lower()
    ]
    if filenames:
        clean = [re.sub(r'\.(pdf|md|txt|html|docx?)$', '', f, flags=re.IGNORECASE).strip()
                 for f in filenames[:3]]
        clean = [c for c in clean if c]  # drop empties
        if clean:
            return ' & '.join(clean)

    # Try to find a markdown heading (## or #)
    heading = re.search(r'^#{1,3}\s+(.+)', source_context[:3000], re.MULTILINE)
    if heading:
        val = heading.group(1).strip()
        # Skip if it's the overview header itself
        if 'source overview' not in val.lower():
            return val[:80]

    # Last resort: first non-empty line that isn't a metadata header
    for line in source_context[:2000].split('\n'):
        line = line.strip()
        if (len(line) > 10
                and not line.startswith(('---', '```', '|', '##'))
                and 'source overview' not in line.lower()):
            return line[:80]

    return "your research sources"


async def _generate_feynman_v2(
    system_prompt: str,
    source_context: str,
    topic_focus: str,
    temperature: float,
    chat_preamble: str = "",
    notebook_id: str = "",
) -> str:
    """Conductor Document Generator — Feynman Learning Journey.

    Produces a guided learning path that orchestrates source content, LLM prose,
    quizzes, and audio into one cohesive experience.  The LLM writes SHORT
    connecting narratives (150-200 words per level) instead of full content.
    Source passages carry the depth.

    Phase 1: ANALYZE   — extract 6 key concepts from sources (1 LLM call)
    Phase 2: RETRIEVE  — RAG search for best passages per level (4 vector searches)
    Phase 3: NARRATE   — LLM writes connecting prose per level (4 small LLM calls)
    Phase 4: ENRICH    — vocab tables + reflection prompts (4 small LLM calls)
    Phase 5: MAP       — programmatic Mermaid from concepts (no LLM)
    Phase 6: ASSEMBLE  — pure code, code blocks for quiz/audio interactivity
    """
    pipeline_start = time.time()

    # ── Topic Inference ──────────────────────────────────────────────────
    # Fix: when topic_focus is empty/generic, infer from source content
    topic = topic_focus
    if not topic or topic in ("this topic", "the main topics and insights"):
        topic = _infer_topic_from_context(source_context)
        logger.info(f"[FEYNMAN-V2] Inferred topic: '{topic}'")

    # ── Phase 1: Source Analysis ─────────────────────────────────────────
    analysis_prompt = f"""{chat_preamble}You are preparing a teaching curriculum about: {topic}

Analyze the source material and extract the 6 most important concepts a student must understand.

For each concept, provide:
- NAME: a short name (2-5 words)
- DEFINITION: one simple sentence explaining it
- EXAMPLE: a concrete real-world example or analogy

List them from most basic/foundational to most advanced/complex.

Source material:
{source_context[:6000]}

List exactly 6 concepts now, formatted as:

CONCEPT 1:
NAME: [name]
DEFINITION: [one sentence]
EXAMPLE: [concrete example]

CONCEPT 2:
NAME: [name]
DEFINITION: [one sentence]
EXAMPLE: [concrete example]

...through CONCEPT 6."""

    analysis = await rag_engine._call_ollama(
        "You extract key educational concepts from source material. Be precise and factual.",
        analysis_prompt,
        model=settings.ollama_model,
        num_predict=600,
        temperature=max(0.3, temperature - 0.1),
    )
    logger.info(f"[FEYNMAN-V2] Phase 1: Source analysis complete ({len(analysis)} chars)")

    # Parse concepts
    concepts = _parse_feynman_concepts(analysis)
    if len(concepts) < 4:
        logger.warning(f"[FEYNMAN-V2] Only parsed {len(concepts)} concepts, padding to 6")
        while len(concepts) < 6:
            concepts.append({
                'name': f"Aspect {len(concepts)+1} of {topic[:30]}",
                'definition': f"A key aspect of {topic}",
                'example': 'Real-world application',
            })

    logger.info(f"[FEYNMAN-V2] Phase 1: Extracted {len(concepts)} concepts: "
                f"{[c['name'] for c in concepts]}")

    # Distribute concepts across Levels
    n = len(concepts)
    level_concepts = {
        1: concepts[:max(2, n // 3)],                     # Foundation: first ~2
        2: concepts[max(1, n // 3):max(3, 2 * n // 3)],   # Connections: middle ~2
        3: concepts[max(2, n // 2):],                     # First Principles: latter ~2-3
        4: concepts,                                      # Mastery: ALL (synthesis)
    }

    # ── Phase 2: RAG Retrieval ───────────────────────────────────────────
    passages = {}
    seen_passage_keys = set()  # Deduplicate across levels
    if notebook_id:
        for level_num, lvl_concepts in level_concepts.items():
            search_query = f"{topic} {' '.join(c['name'] for c in lvl_concepts)}"
            try:
                # Over-fetch so we have room after quality filtering
                chunks = rag_engine.search_chunks(notebook_id, search_query, top_k=8)
                raw = [
                    {
                        'text': chunk.get('text', ''),
                        'source': chunk.get('filename', 'Source'),
                    }
                    for chunk in chunks
                    if chunk.get('text', '').strip()
                ]
                # Filter out garbage + deduplicate across levels
                level_passages = []
                for p in raw:
                    if not _is_quality_passage(p['text']):
                        continue
                    # Dedup key: first 150 chars (catches same chunk in different levels)
                    dedup_key = p['text'][:150].strip().lower()
                    if dedup_key in seen_passage_keys:
                        continue
                    seen_passage_keys.add(dedup_key)
                    level_passages.append(p)
                    if len(level_passages) >= 3:
                        break
                passages[level_num] = level_passages
                skipped = len(raw) - len(passages[level_num])
                if skipped > 0:
                    logger.info(f"[FEYNMAN-V2] Phase 2: Level {level_num} — "
                                f"filtered {skipped} low-quality passages")
            except Exception as e:
                logger.warning(f"[FEYNMAN-V2] Phase 2: RAG search failed for level {level_num}: {e}")
                passages[level_num] = []
            logger.info(f"[FEYNMAN-V2] Phase 2: Level {level_num} — "
                        f"{len(passages.get(level_num, []))} passages retrieved")
    else:
        logger.warning("[FEYNMAN-V2] Phase 2: No notebook_id — skipping RAG retrieval")

    # ── Phase 3: Narration (LLM writes connecting prose GIVEN passages) ──
    level_goals = {
        1: ("Foundation",
            "Explain these concepts to someone new. Use everyday analogies. "
            "Connect the source passages to what the reader needs to understand."),
        2: ("How It Connects",
            "Show how these concepts relate to each other and to real-world situations. "
            "Help the reader see the bigger picture from the source material."),
        3: ("First Principles",
            "Explain WHY these things work. Get to root causes and mechanisms. "
            "Address what the sources reveal about underlying principles."),
        4: ("Mastery",
            "Challenge the reader to teach these concepts back. Include 2-3 "
            "'explain it to a friend' challenges with specific scenarios. "
            "Pose expert-level questions about what's still debated or unknown."),
    }

    parts = {}
    for level_num in range(1, 5):
        title, goal = level_goals[level_num]
        concepts_text = "\n".join(
            f"- {c['name']}: {c['definition']}"
            for c in level_concepts[level_num]
        )

        # Include retrieved passages as context for the LLM
        passage_context = ""
        for p in passages.get(level_num, [])[:3]:
            ptext = p['text'][:300].strip()
            if ptext:
                passage_context += f'\nSource excerpt: "{ptext}"\n'

        sec_prompt = f"""{chat_preamble}You are writing the narrative for the "{title}" level of a learning journey about: {topic}

The reader has already read source passages. Your job is to CONNECT and EXPLAIN — not repeat what they read.

Concepts for this level:
{concepts_text}
{f'{chr(10)}Source passages the reader has seen:{passage_context}' if passage_context else ''}
YOUR TASK: {goal}

RULES:
- Write 150-250 words of clear, engaging explanation
- Use simple language with short sentences (under 20 words each)
- Include at least one analogy or everyday comparison
- Reference what the sources say — "As the research shows..." or "This connects to..."
- Do NOT repeat source passages word-for-word
- Do NOT add headings, quiz questions, or vocabulary tables
- Do NOT use filler phrases like "furthermore" or "consequently"
- Every sentence must teach something specific

Write the narrative now:"""

        sec_temp = _get_section_temperature(temperature, f"Level {level_num}: {title}")

        section_content = await rag_engine._call_ollama(
            system_prompt,
            sec_prompt,
            model=settings.ollama_model,
            num_predict=600,
            temperature=sec_temp,
            extra_options={
                "mirostat": 2,
                "mirostat_tau": 3.5,
                "mirostat_eta": 0.1,
                "repeat_penalty": 1.15,
                "repeat_last_n": 256,
            },
        )
        section_content = _clean_llm_output(section_content)

        # Quality gate — retry once if degenerate
        score, reason = _score_section_quality(section_content)
        if score < 40:
            logger.warning(f"[FEYNMAN-V2] Level {level_num} quality {score}/100 ({reason}) — retrying")
            retry = await rag_engine._call_ollama(
                system_prompt,
                sec_prompt + "\n\nIMPORTANT: Short, clear sentences. No filler. Be specific.",
                model=settings.ollama_model,
                num_predict=600,
                temperature=min(sec_temp + 0.15, 0.95),
                extra_options={
                    "mirostat": 2,
                    "mirostat_tau": 2.5,
                    "mirostat_eta": 0.1,
                    "repeat_penalty": 1.2,
                    "repeat_last_n": 256,
                    "seed": int(time.time()) % 10000,
                },
            )
            retry = _clean_llm_output(retry)
            retry_score, _ = _score_section_quality(retry)
            if retry_score > score:
                section_content = retry
                score = retry_score

        parts[level_num] = section_content
        logger.info(f"[FEYNMAN-V2] Phase 3: Level {level_num} ({title}) — "
                    f"{len(section_content.split())} words, quality {score}/100")

    # ── Phase 4: Enrichment ──────────────────────────────────────────────
    enrichments = {}
    for level_num in range(1, 5):
        title = level_goals[level_num][0]
        concepts_text = ", ".join(c['name'] for c in level_concepts[level_num])

        enrich_prompt = f"""For a learning curriculum section titled "{title}" covering: {concepts_text}

Generate EXACTLY this:

KEY TERMS:
| Term | Plain English | Analogy |
|------|--------------|---------|
| [term1] | [simple definition] | [everyday analogy] |
| [term2] | [simple definition] | [everyday analogy] |
| [term3] | [simple definition] | [everyday analogy] |

REFLECTION PROMPTS:
1. [open-ended question about {title.lower()}]
2. [open-ended question connecting concepts]
3. [thought-provoking question for deeper thinking]

Write only the table and prompts. Nothing else."""

        enrichment = await rag_engine._call_ollama(
            "You create educational vocabulary tables and reflection questions. Be concise.",
            enrich_prompt,
            model=settings.ollama_model,
            num_predict=400,
            temperature=0.4,
        )

        vocab, reflections = _parse_feynman_enrichment(enrichment)
        enrichments[level_num] = {'vocab': vocab, 'reflections': reflections}
        logger.info(f"[FEYNMAN-V2] Phase 4: Level {level_num} enrichment — "
                    f"vocab={'yes' if vocab else 'no'}, reflections={'yes' if reflections else 'no'}")

    # ── Phase 5: Knowledge Map (programmatic — no LLM) ───────────────────
    knowledge_map = _build_feynman_knowledge_map(concepts, topic)
    logger.info(f"[FEYNMAN-V2] Phase 5: Knowledge map built ({len(knowledge_map)} chars, "
                f"{len(knowledge_map.splitlines())} lines)")

    # ── Phase 6: Assembly (pure code) ────────────────────────────────────
    document = _assemble_feynman_document(
        concepts=concepts,
        parts=parts,
        passages=passages,
        enrichments=enrichments,
        knowledge_map=knowledge_map,
        topic=topic,
        level_concepts=level_concepts,
        notebook_id=notebook_id,
    )

    elapsed = time.time() - pipeline_start
    total_words = len(document.split())
    logger.info(f"[FEYNMAN-V2] Complete: {total_words} words, {elapsed:.1f}s total "
                f"(~9 LLM calls + 4 RAG searches)")

    # ── Phase 7: Background Quiz Pre-Generation ──────────────────────
    # Fire-and-forget: quizzes generate while user reads the document.
    # By the time they reach a quiz button, it's already cached.
    if notebook_id:
        asyncio.create_task(_pre_generate_feynman_quizzes(
            notebook_id=notebook_id,
            topic=topic,
            parts=parts,
            level_concepts=level_concepts,
            level_goals=level_goals,
        ))
        logger.info("[FEYNMAN-V2] Phase 7: Background quiz generation started")

    return document


async def _generate_outline_first(
    system_prompt: str,
    source_context: str,
    skill_name: str,
    topic_focus: str,
    structure_requirements: List[str],
    total_token_budget: int,
    temperature: float,
    chat_preamble: str = "",
) -> str:
    """Generate a long-form document using the Outline-First pipeline.

    Three-step process inspired by Hierarchical Expansion (OpenCredo) and
    Writing Path (KAIST 2024):

    Step 1: OUTLINE — LLM generates a structured outline with section titles
            and brief descriptions, guided by the template's requirements.
    Step 2: EXPAND  — Each section is generated independently with:
            • The full outline as scaffolding
            • A running Chain-of-Density summary of content written so far
            • The source context
            • A per-section token budget
    Step 3: ASSEMBLE — Join sections, clean up, verify completeness.

    This eliminates the three failure modes of single-pass generation:
    1. Premature cutoff (each section is small enough to complete)
    2. Cross-section repetition (running summary prevents it)
    3. Missing sections (outline drives the expansion loop)
    """
    pipeline_start = time.time()
    num_sections = len(structure_requirements)

    # ── Step 1: Generate Outline ──────────────────────────────────────────
    outline_prompt = f"""{chat_preamble}You are planning a {skill_name} about: {topic_focus}

Based on the source material below, create a DETAILED OUTLINE with exactly {num_sections} sections.
For each section, write:
- The section title (matching the required structure)
- 2-3 bullet points summarizing what that section should cover, drawn from the sources

REQUIRED SECTIONS:
{chr(10).join(f'{i+1}. {req}' for i, req in enumerate(structure_requirements))}

Source material:
{source_context[:8000]}

Write the outline now — section titles and bullet points only, no full prose:"""

    outline = await rag_engine._call_ollama(
        system_prompt,
        outline_prompt,
        model=settings.ollama_model,
        num_predict=800,
        temperature=max(0.3, temperature - 0.1),  # Slightly lower temp for planning
    )
    logger.info(f"[OUTLINE-FIRST] Step 1: Outline generated ({len(outline)} chars, "
                f"{len(outline.split(chr(10)))} lines)")

    # ── Step 2: Expand Sections ───────────────────────────────────────────
    # Budget per section: total budget divided evenly, with 10% overhead for
    # the outline step and assembly.
    tokens_per_section = max(600, int(total_token_budget * 0.9 / num_sections))
    running_summary = ""
    sections_text = []

    for i, requirement in enumerate(structure_requirements):
        is_first = i == 0
        is_last = i == num_sections - 1

        # Build continuity context
        continuity = ""
        if running_summary:
            continuity = f"""
CONTENT WRITTEN SO FAR (summary):
{running_summary}

Continue from where the previous section left off. Do NOT repeat information already covered."""

        # Position awareness
        if is_first:
            position_note = "This is the OPENING section. Set the stage and engage the reader."
        elif is_last:
            position_note = "This is the CLOSING section. Synthesize all prior sections into a strong conclusion."
        else:
            position_note = f"This is section {i+1} of {num_sections}. Build on previous sections."

        section_prompt = f"""{chat_preamble}{position_note}

DOCUMENT OUTLINE (for context — you are writing section {i+1} only):
{outline}

YOUR TASK: Write section {i+1}: {requirement}

RULES:
- Write ONLY the content for this one section — start with a markdown heading, then prose.
- Do NOT reprint the outline, other section titles, or the document structure.
- Do NOT echo these instructions or use phrases like "SECTION TO WRITE NOW".
- Use SHORT sentences (under 30 words each). Break complex ideas into multiple sentences.
- Every claim must come from the source material — no filler or vague generalizations.
- Target length: {tokens_per_section // 4}-{tokens_per_section // 2} words of substantive content.
{continuity}

Source material:
{source_context[:6000]}

Begin writing section {i+1} now — start with the heading:"""

        # Per-section temperature scheduling — factual sections (tables, tests)
        # get lower temp for precision; creative sections get slightly higher.
        section_temp = _get_section_temperature(temperature, requirement)

        # Force Mirostat 2.0 on section expansions — these are ~750 tokens each,
        # which normally falls into the "medium docs" path (repeat_penalty=1.3).
        # Mirostat's adaptive perplexity targeting prevents degenerate loops far
        # more effectively than a fixed penalty window for structured content.
        # tau=3.0 is tuned for shorter sections (vs 4.0 for 3000+ token output).
        section_content = await rag_engine._call_ollama(
            system_prompt,
            section_prompt,
            model=settings.ollama_model,
            num_predict=tokens_per_section,
            temperature=section_temp,
            extra_options={
                "mirostat": 2,
                "mirostat_tau": 3.0,
                "mirostat_eta": 0.1,
                "repeat_penalty": 1.15,
                "repeat_last_n": 512,
            },
        )

        # Clean each section individually
        section_content = _clean_llm_output(section_content)

        # Quality gate — retry once if section is degenerate
        score, reason = _score_section_quality(section_content)
        if score < 40:
            logger.warning(f"[OUTLINE-FIRST] Section {i+1} quality {score}/100 ({reason}) — retrying")
            retry_content = await rag_engine._call_ollama(
                system_prompt,
                section_prompt + "\n\nIMPORTANT: Use short, clear sentences. No filler.",
                model=settings.ollama_model,
                num_predict=tokens_per_section,
                temperature=min(section_temp + 0.15, 0.95),
                extra_options={
                    "mirostat": 2,
                    "mirostat_tau": 2.5,   # Tighter for retry
                    "mirostat_eta": 0.1,
                    "repeat_penalty": 1.2,
                    "repeat_last_n": 512,
                    "seed": int(time.time()) % 10000,  # Different seed
                },
            )
            retry_content = _clean_llm_output(retry_content)
            retry_score, retry_reason = _score_section_quality(retry_content)
            if retry_score > score:
                section_content = retry_content
                score = retry_score
                logger.info(f"[OUTLINE-FIRST] Retry improved: {retry_score}/100 ({retry_reason})")
            else:
                logger.info(f"[OUTLINE-FIRST] Retry not better ({retry_score}/100), keeping original")

        # P3: Structural compliance check — verify section has required elements
        # (headings, vocab table, knowledge map) and fix if missing.
        # Only runs targeted LLM fix when a specific deficit is detected.
        section_content = await _verify_section_structure(
            section_content, requirement, source_context,
        )

        sections_text.append(section_content)

        word_count = len(section_content.split())
        logger.info(f"[OUTLINE-FIRST] Step 2: Section {i+1}/{num_sections} "
                    f"'{requirement}' — {word_count} words (quality: {score}/100)")

        # Update running summary (Chain of Density) for next section's context.
        # Summarize everything written so far into a dense ~200-word summary.
        all_content_so_far = "\n\n".join(sections_text)
        if len(all_content_so_far) > 500:
            running_summary = await rag_engine._call_ollama(
                "You are a precise summarizer. Create an information-dense summary "
                "preserving ALL key topics, arguments, data points, and conclusions. "
                "Do not add new information.",
                f"Summarize the following document sections in 150-200 words:\n\n{all_content_so_far[:6000]}",
                model=settings.ollama_model,
                num_predict=300,
                temperature=0.2,
            )

    # ── Step 3: Assemble ──────────────────────────────────────────────────
    full_document = "\n\n".join(sections_text)

    # Final cleanup
    full_document = _clean_llm_output(full_document)

    elapsed = time.time() - pipeline_start
    total_words = len(full_document.split())
    logger.info(f"[OUTLINE-FIRST] Complete: {total_words} words, "
                f"{num_sections} sections, {elapsed:.1f}s total")

    return full_document


async def _verify_and_fill_sections(
    content: str,
    structure_requirements: List[str],
    system_prompt: str,
    source_context: str,
    skill_name: str,
    temperature: float,
) -> str:
    """Completion verification gate — ensure all required sections are present.

    Scans the generated document for each required section heading.  If any are
    missing, generates them individually and appends them.  This catches both
    outline-first and single-pass gaps.
    """
    if not structure_requirements:
        return content

    content_lower = content.lower()
    missing = []

    for req in structure_requirements:
        # Extract the core section name (e.g. "ABSTRACT (comprehensive overview)" → "abstract")
        core = re.split(r'[(\[{]', req)[0].strip().lower()
        # Also extract just the key words (e.g. "part 1" from "part 1: foundation")
        core_words = core.split(':')[0].strip()
        
        # Check for the heading in various markdown formats
        found = False
        for pattern in [
            f"# {core}",        # ## Abstract
            f"# {core_words}",  # ## Part 1 (without subtitle)
            f"**{core}",        # **Abstract**
            f"**{core_words}",  # **Part 1**
            f"\n{core}\n",      # Standalone line
            f"\n{core_words}\n",
            f"\n{core}:",       # Abstract:
            f"\n{core_words}:",
        ]:
            if pattern in content_lower:
                found = True
                break
        if not found:
            missing.append(req)

    if not missing:
        logger.info(f"[VERIFY] All {len(structure_requirements)} sections present ✓")
        return content

    logger.warning(f"[VERIFY] Missing {len(missing)}/{len(structure_requirements)} sections: "
                   f"{[m.split('(')[0].strip() for m in missing]}")

    # Generate missing sections individually
    for req in missing:
        fill_prompt = f"""The following {skill_name} is missing the section: {req}

Write ONLY this missing section with an appropriate markdown heading.
Be thorough — 150-300 words. Draw from the source material.

Source material:
{source_context[:4000]}

Existing document (for context — do NOT repeat its content):
{content[:3000]}

Write the missing section "{req}" now:"""

        section = await rag_engine._call_ollama(
            system_prompt,
            fill_prompt,
            model=settings.ollama_model,
            num_predict=600,
            temperature=temperature,
        )
        section = _clean_llm_output(section)
        if section and len(section.strip()) > 50:
            content = content.rstrip() + "\n\n" + section
            logger.info(f"[VERIFY] Filled missing section: {req.split('(')[0].strip()} "
                        f"({len(section.split())} words)")

    return content


router = APIRouter()


@router.get("/feynman-quiz-cache")
async def get_feynman_quiz_cache(notebook_id: str, level: int):
    """Serve pre-generated Feynman quiz from cache.

    Quizzes are generated in the background after document creation (Phase 7).
    Returns instantly if ready, or a 'generating' status if still in progress.
    """
    key = f"{notebook_id}:level{level}"
    if key in _feynman_quiz_cache:
        return {"status": "ready", "questions": _feynman_quiz_cache[key]}
    if _feynman_quiz_generating.get(notebook_id):
        return {"status": "generating"}
    return {"status": "not_found"}


class ContentGenerateRequest(BaseModel):
    """Request model for content generation"""
    notebook_id: str
    skill_id: str
    topic: Optional[str] = None
    style: Optional[str] = "professional"  # Output style: professional, casual, academic, etc.
    chat_context: Optional[str] = None  # Recent chat conversation for "From Chat" mode


class ContentGenerateResponse(BaseModel):
    """Response model for content generation"""
    notebook_id: str
    skill_id: str
    skill_name: str
    content: str
    sources_used: int
    source_names: list[str] = []
    relevance_scores: dict[str, float] = {}


class ContentExportRequest(BaseModel):
    """Request for exporting content"""
    content: str
    title: str
    format: str = "markdown"  # markdown or text


@router.post("/generate", response_model=ContentGenerateResponse)
async def generate_content(request: ContentGenerateRequest):
    """Generate text content using a skill with RAG context"""
    try:
        # Get skill
        skill = await skills_store.get(request.skill_id)
        if not skill:
            raise HTTPException(status_code=404, detail="Skill not found")
        
        # Derive effective topic for source selection.
        # When topic is empty but chat_context exists, extract the user's
        # last question from the chat so the context builder can rank sources
        # by relevance to what the user actually asked about.
        effective_topic = request.topic
        if not effective_topic and request.chat_context:
            effective_topic = _extract_topic_from_chat(request.chat_context)
            logger.info(f"[STUDIO] Derived topic from chat context: '{effective_topic}'")
        
        # Build adaptive context using the centralized context builder
        built = await context_builder.build_context(
            notebook_id=request.notebook_id,
            skill_id=request.skill_id,
            topic=effective_topic,
        )
        
        if built.sources_used == 0:
            raise HTTPException(status_code=400, detail="No sources in notebook")
        
        # Build prompt based on skill using professional templates
        skill_name = skill.get("name", "Content")
        topic_focus = effective_topic or request.topic or "the main topics and insights"
        
        # Use professional template if available, otherwise fall back to skill's own prompt
        if request.skill_id in DOCUMENT_TEMPLATES:
            template_system, template_format = build_document_prompt(
                request.skill_id, 
                topic_focus, 
                request.style or "professional",
                built.sources_used
            )
            system_prompt = f"""{template_system}

{template_format}

FOCUS: {topic_focus}

CRITICAL: Use ONLY the provided source content. Synthesize across multiple sources.
Do not make up information. Attribute insights to specific sources where possible."""
        else:
            # Fallback to skill's own prompt for custom skills
            skill_prompt = skill.get("system_prompt", "")
            format_instructions = _get_format_instructions(request.skill_id)
            style_instructions = _get_style_instructions(request.style)
            
            system_prompt = f"""{skill_prompt}

{format_instructions}

{style_instructions}

Focus on: {topic_focus}

Use ONLY the provided source content. Do not make up information."""

        # Inject chat context if provided ("From Chat" mode)
        chat_preamble = ""
        if request.chat_context:
            chat_preamble = f"""The user has been exploring this topic in a chat conversation. Use their discussion to focus on what matters most to them:

--- RECENT CHAT ---
{request.chat_context[:3000]}
--- END CHAT ---

"""

        user_prompt = f"""{chat_preamble}Based on the following {built.sources_used} source document(s), create a world-class {skill_name}:

{built.context}

Generate the {skill_name} now, ensuring you synthesize insights across ALL sources:"""

        # Use template-specific token limit for thorough generation
        template = DOCUMENT_TEMPLATES.get(request.skill_id)
        doc_num_predict = template.recommended_tokens if template else 2000
        
        logger.info(f"[STUDIO] Context: {built.total_chars} chars from {built.sources_used} sources "
                    f"(strategy={built.strategy_used}, profile={built.profile_used}, "
                    f"build_time={built.build_time_ms}ms)")
        
        # Get adaptive temperature from context profile
        from services.context_builder import CONTEXT_PROFILES
        skill_temp = CONTEXT_PROFILES.get(request.skill_id, CONTEXT_PROFILES["default"]).temperature
        
        # Generate content — route to appropriate pipeline:
        # 1. Feynman v2: dedicated multi-phase pipeline (analyze → generate → enrich → map → assemble)
        # 2. Outline-First: generic multi-section pipeline for long-form skills
        # 3. Single-pass: standard generation for shorter document types
        if request.skill_id == 'feynman_curriculum':
            logger.info(f"[STUDIO] Using Feynman Pipeline v2 for {request.skill_id}")
            content = await _generate_feynman_v2(
                system_prompt=system_prompt,
                source_context=built.context,
                topic_focus=topic_focus,
                temperature=skill_temp,
                chat_preamble=chat_preamble,
                notebook_id=request.notebook_id,
            )
            # v2 handles everything internally — skip normalization, verification, and embedding
        elif request.skill_id in OUTLINE_FIRST_SKILLS and template and template.structure_requirements:
            logger.info(f"[STUDIO] Using Outline-First pipeline for {request.skill_id} "
                        f"({len(template.structure_requirements)} sections, {doc_num_predict} token budget)")
            raw_content = await _generate_outline_first(
                system_prompt=system_prompt,
                source_context=built.context,
                skill_name=skill_name,
                topic_focus=topic_focus,
                structure_requirements=template.structure_requirements,
                total_token_budget=doc_num_predict,
                temperature=skill_temp,
                chat_preamble=chat_preamble,
            )
            # Outline-first already runs _clean_llm_output per section + at assembly
            content = raw_content
        else:
            raw_content = await rag_engine._call_ollama(system_prompt, user_prompt, model=settings.ollama_model, num_predict=doc_num_predict, temperature=skill_temp)
            # Post-process: detect loops, ensure clean ending
            content = _clean_llm_output(raw_content)
            if len(content) < len(raw_content) * 0.8:
                logger.warning(f"[STUDIO] Post-processing removed {len(raw_content) - len(content)} chars "
                              f"({len(raw_content)} → {len(content)})")
        
        # Post-pipeline steps (skipped for feynman_curriculum — v2 handles internally)
        if request.skill_id != 'feynman_curriculum':
            # Completion verification gate — ensure all required sections present
            if template and template.structure_requirements:
                content = await _verify_and_fill_sections(
                    content=content,
                    structure_requirements=template.structure_requirements,
                    system_prompt=system_prompt,
                    source_context=built.context,
                    skill_name=skill_name,
                    temperature=skill_temp,
                )

        # Save to content store for persistence
        await content_store.create(
            notebook_id=request.notebook_id,
            skill_id=request.skill_id,
            skill_name=skill_name,
            content=content,
            topic=request.topic,
            sources_used=built.sources_used
        )
        
        return ContentGenerateResponse(
            notebook_id=request.notebook_id,
            skill_id=request.skill_id,
            skill_name=skill_name,
            content=content,
            sources_used=built.sources_used,
            source_names=built.source_names,
            relevance_scores=built.topic_relevance_scores,
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[STUDIO] Content generation failed for skill={request.skill_id}, notebook={request.notebook_id}")
        logger.error(f"[STUDIO] Error: {type(e).__name__}: {str(e)}")
        logger.error(f"[STUDIO] Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Content generation failed: {str(e)}")


@router.post("/generate/stream")
async def generate_content_stream(request: ContentGenerateRequest):
    """Stream content generation for real-time display"""
    try:
        # Get skill
        skill = await skills_store.get(request.skill_id)
        if not skill:
            raise HTTPException(status_code=404, detail="Skill not found")
        
        # Derive effective topic for source selection (same as non-streaming)
        effective_topic = request.topic
        if not effective_topic and request.chat_context:
            effective_topic = _extract_topic_from_chat(request.chat_context)
            logger.info(f"[STUDIO-STREAM] Derived topic from chat context: '{effective_topic}'")
        
        # Build adaptive context using the centralized context builder
        built = await context_builder.build_context(
            notebook_id=request.notebook_id,
            skill_id=request.skill_id,
            topic=effective_topic,
        )
        
        if built.sources_used == 0:
            raise HTTPException(status_code=400, detail="No sources in notebook")
        
        skill_name = skill.get("name", "Content")
        topic_focus = effective_topic or request.topic or "the main topics and insights"
        
        # Use professional template if available
        if request.skill_id in DOCUMENT_TEMPLATES:
            template_system, template_format = build_document_prompt(
                request.skill_id, 
                topic_focus, 
                request.style or "professional",
                built.sources_used
            )
            system_prompt = f"""{template_system}

{template_format}

FOCUS: {topic_focus}

CRITICAL: Use ONLY the provided source content. Synthesize across multiple sources.
Do not make up information. Attribute insights to specific sources where possible."""
        else:
            skill_prompt = skill.get("system_prompt", "")
            format_instructions = _get_format_instructions(request.skill_id)
            style_instructions = _get_style_instructions(request.style)
            
            system_prompt = f"""{skill_prompt}

{format_instructions}

{style_instructions}

Focus on: {topic_focus}

Use ONLY the provided source content. Do not make up information."""

        # Inject chat context if provided ("From Chat" mode)
        chat_preamble = ""
        if request.chat_context:
            chat_preamble = f"""The user has been exploring this topic in a chat conversation. Use their discussion to focus on what matters most to them:

--- RECENT CHAT ---
{request.chat_context[:3000]}
--- END CHAT ---

"""

        user_prompt = f"""{chat_preamble}Based on the following {built.sources_used} source document(s), create a world-class {skill_name}:

{built.context}

Generate the {skill_name} now, ensuring you synthesize insights across ALL sources:"""

        # Use template-specific token limit for thorough generation
        template = DOCUMENT_TEMPLATES.get(request.skill_id)
        doc_num_predict = template.recommended_tokens if template else 2000
        
        logger.info(f"[STUDIO] Streaming context: {built.total_chars} chars from {built.sources_used} sources "
                    f"(strategy={built.strategy_used}, build_time={built.build_time_ms}ms)")

        # Get adaptive temperature from context profile
        from services.context_builder import CONTEXT_PROFILES
        skill_temp = CONTEXT_PROFILES.get(request.skill_id, CONTEXT_PROFILES["default"]).temperature

        async def stream_generator():
            # Feynman v2: multi-phase pipeline (non-streaming internally, sent as complete result)
            if request.skill_id == 'feynman_curriculum':
                logger.info(f"[STREAM] Using Feynman Pipeline v2 (non-streaming internally)")
                content = await _generate_feynman_v2(
                    system_prompt=system_prompt,
                    source_context=built.context,
                    topic_focus=topic_focus,
                    temperature=skill_temp,
                    chat_preamble=chat_preamble,
                    notebook_id=request.notebook_id,
                )
                yield f"data: {json.dumps({'content': content})}\n\n"
                yield "data: [DONE]\n\n"
                return

            # Use Outline-First streaming for long-form skills
            if request.skill_id in OUTLINE_FIRST_SKILLS and template and template.structure_requirements:
                reqs = template.structure_requirements
                num_sections = len(reqs)
                tokens_per_section = max(600, int(doc_num_predict * 0.9 // num_sections))

                # Step 1: Generate outline (non-streaming — fast, ~800 tokens)
                outline_prompt = f"""{chat_preamble}You are planning a {skill_name} about: {topic_focus}

Based on the source material below, create a DETAILED OUTLINE with exactly {num_sections} sections.
For each section, write the section title and 2-3 bullet points of what to cover.

REQUIRED SECTIONS:
{chr(10).join(f'{i+1}. {r}' for i, r in enumerate(reqs))}

Source material:
{built.context[:8000]}

Write the outline now — section titles and bullet points only:"""

                outline = await rag_engine._call_ollama(
                    system_prompt, outline_prompt,
                    model=settings.ollama_model,
                    num_predict=800, temperature=max(0.3, skill_temp - 0.1),
                )
                logger.info(f"[STREAM-OUTLINE] Outline ready ({len(outline)} chars)")

                # Step 2: Stream each section expansion
                running_summary = ""
                for i, requirement in enumerate(reqs):
                    is_first = i == 0
                    is_last = i == num_sections - 1

                    continuity = ""
                    if running_summary:
                        continuity = f"\nCONTENT WRITTEN SO FAR (summary):\n{running_summary}\nDo NOT repeat information already covered."

                    position_note = ("This is the OPENING section." if is_first
                                     else "This is the CLOSING section. Synthesize all prior sections." if is_last
                                     else f"This is section {i+1} of {num_sections}.")

                    sec_prompt = f"""{chat_preamble}{position_note}

DOCUMENT OUTLINE (for context — you are writing section {i+1} only):
{outline}

YOUR TASK: Write section {i+1}: {requirement}

RULES:
- Write ONLY the content for this one section — start with a markdown heading, then prose.
- Do NOT reprint the outline, other section titles, or the document structure.
- Do NOT echo these instructions or use phrases like "SECTION TO WRITE NOW".
- Use SHORT sentences (under 30 words each). Break complex ideas into multiple sentences.
- Every claim must come from the source material — no filler or vague generalizations.
- Target length: {tokens_per_section // 4}-{tokens_per_section // 2} words of substantive content.
{continuity}

Source material:
{built.context[:6000]}

Begin writing section {i+1} now — start with the heading:"""

                    # Per-section temperature scheduling
                    sec_temp = _get_section_temperature(skill_temp, requirement)

                    # Stream this section's tokens with Mirostat to prevent degeneration
                    section_chunks = []
                    async for chunk in rag_engine._stream_ollama(
                        system_prompt, sec_prompt,
                        num_predict=tokens_per_section,
                        temperature_override=sec_temp,
                        extra_options={
                            "mirostat": 2,
                            "mirostat_tau": 3.0,   # Tuned for ~750 token sections
                            "mirostat_eta": 0.1,
                            "repeat_penalty": 1.15,
                            "repeat_last_n": 512,
                        },
                    ):
                        section_chunks.append(chunk)
                        yield f"data: {json.dumps({'content': chunk})}\n\n"

                    # Section separator
                    yield f"data: {json.dumps({'content': chr(10) + chr(10)})}\n\n"

                    # Update running summary for next section (non-streaming, background)
                    section_text = "".join(section_chunks)
                    if len(section_text) > 200 and not is_last:
                        all_so_far = section_text if i == 0 else f"{running_summary}\n\n{section_text}"
                        running_summary = await rag_engine._call_ollama(
                            "You are a precise summarizer. Preserve ALL key topics and conclusions.",
                            f"Summarize in 150-200 words:\n\n{all_so_far[:6000]}",
                            model=settings.ollama_model,
                            num_predict=300, temperature=0.2,
                        )

                    logger.info(f"[STREAM-OUTLINE] Section {i+1}/{num_sections} streamed ({len(section_text.split())} words)")

            else:
                # Standard single-stream for shorter document types
                async for chunk in rag_engine._stream_ollama(system_prompt, user_prompt, num_predict=doc_num_predict, temperature_override=skill_temp):
                    yield f"data: {json.dumps({'content': chunk})}\n\n"

            yield "data: [DONE]\n\n"
        
        return StreamingResponse(
            stream_generator(),
            media_type="text/event-stream"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[STUDIO] Content streaming failed for skill={request.skill_id}, notebook={request.notebook_id}")
        logger.error(f"[STUDIO] Error: {type(e).__name__}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Content streaming failed: {str(e)}")


@router.get("/list/{notebook_id}")
async def list_content_generations(notebook_id: str):
    """List all content generations for a notebook"""
    try:
        generations = await content_store.list(notebook_id)
        return {"generations": generations}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{content_id}")
async def get_content_generation(content_id: str):
    """Get a specific content generation"""
    try:
        generation = await content_store.get(content_id)
        if not generation:
            raise HTTPException(status_code=404, detail="Content not found")
        return generation
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{content_id}")
async def delete_content_generation(content_id: str):
    """Delete a content generation"""
    try:
        deleted = await content_store.delete(content_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Content not found")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _get_format_instructions(skill_id: str) -> str:
    """Get formatting instructions based on skill type"""
    formats = {
        "study_guide": """Format as a structured study guide with:
- Clear section headings (use ## for main sections)
- Key concepts with definitions
- Important facts and details
- Review questions at the end
Use markdown formatting.""",
        
        "summary": """Format as a clear, well-organized summary with:
- Executive summary paragraph at the top
- Key points organized by theme
- Concise bullet points for main takeaways
Use markdown formatting.""",
        
        "faq": """Format as a FAQ document with:
- Questions in bold (use **)
- Clear, detailed answers
- Mix of basic and advanced questions
- Organized by topic
Use markdown formatting.""",
        
        "briefing": """Format as an executive briefing with:
- Executive Summary section
- Key Findings section with bullet points
- Implications section
- Recommended Actions section
Use professional, concise language. Use markdown formatting.""",
        
        "deep_dive": """Format as an in-depth analysis with:
- Introduction and context
- Detailed exploration of key themes
- Connections between ideas
- Nuances and implications
- Conclusion
Use markdown formatting with clear section headings.""",
        
        "explain": """Format as a simple explanation:
- Use everyday language
- Include helpful analogies
- Break complex ideas into simple parts
- Use examples the average person would understand
Avoid jargon and technical terms.""",
    }
    
    return formats.get(skill_id, "Format clearly with appropriate sections and markdown formatting.")


def _get_style_instructions(style: str) -> str:
    """Get writing style instructions"""
    styles = {
        "professional": "Write in a professional, business-appropriate tone. Be clear, concise, and authoritative.",
        "casual": "Write in a friendly, conversational tone. Be approachable and easy to read.",
        "academic": "Write in a formal academic style. Be precise, well-structured, and cite sources appropriately.",
        "technical": "Write in a technical style for expert audiences. Include specific details and use domain terminology.",
        "creative": "Write in an engaging, creative style. Use vivid language and compelling narratives.",
        "concise": "Write in an extremely concise style. Minimize words while maximizing information density.",
    }
    
    return styles.get(style, styles["professional"])
