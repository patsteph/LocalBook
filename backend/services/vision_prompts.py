"""
Vision Prompts — single source of truth for OCR / scene description prompts.

Every prompt here follows these rules:
  - Imperative voice, action-first ("Read this page and output...")
  - No bullet markers (no •, no -, no *)
  - No special arrow chars (→ ←) — small vision models tokenize them
    unpredictably and sometimes echo them as part of the output
  - Positive framing only — no "do not" or "never" instructions
  - One concrete instruction per sentence
  - Compact: ~50% shorter than the prior prompts

These changes were driven by an audit that surfaced gemma3:4b literally
echoing the bullet-point prompt instead of transcribing the page. The
original prompts were ~10 lines of dense rules with •, →, and "do not"
clauses, which 2-4B parameter vision models cannot reliably follow.
"""


# ── Vision model prompts (per content mode) ──────────────────────────────

DOC_VISION_PROMPT = (
    "Read this page and output its text exactly as it appears, in plain markdown. "
    "Keep the original layout: same paragraph breaks, same lists, same tables, same headings. "
    "Use LaTeX for any equation. If a word is too blurry to read, write [unclear]. "
    "Output only the page text."
)

MATH_VISION_PROMPT = (
    "Read this page and transcribe everything in markdown. "
    "Write all equations in LaTeX, using $...$ for inline math and $$...$$ for display math. "
    "Use \\begin{pmatrix}...\\end{pmatrix} for matrices. "
    "Keep step numbers and surrounding explanatory text exactly as written. "
    "Output only the transcription."
)

WHITEBOARD_VISION_PROMPT = (
    "Read this whiteboard photo and transcribe its content as structured markdown. "
    "Write each text block as a paragraph or list item depending on how it appears. "
    "Describe diagrams as nested lists showing connections (for example: 'A connects to B'). "
    "Note color-coded groupings inline. "
    "Output only the transcription."
)

DRAWING_VISION_PROMPT = (
    "Describe this hand-drawn illustration as structured markdown. "
    "Cover overall layout, individual elements with their spatial relationships, "
    "any labels or text exactly as written, and the colors used. "
    "Output a clear markdown description."
)

PHOTO_VISION_PROMPT = (
    "Describe this photo in rich detail as plain prose. "
    "Cover composition, key objects, colors, lighting, and any visible text. "
    "Output only the description."
)


# ── Auto-classification ──────────────────────────────────────────────────

CLASSIFY_PROMPT = (
    "What type of content is this image? Answer with one word from this list: "
    "document, math, whiteboard, drawing, photo. Reply with only the word."
)


# ── Document cleanup pass (downstream small text model) ──────────────────

CLEANUP_SYSTEM = (
    "You output cleaned plain markdown. Fix obvious OCR typos. "
    "Keep the same structure: paragraphs stay paragraphs, lists stay lists, "
    "tables stay tables. Keep all LaTeX math and [unclear] markers exactly as written. "
    "Output starts with the first line of content."
)

CLEANUP_PROMPT_TMPL = (
    "Clean this OCR output and return only the cleaned markdown:\n\n{raw}"
)


# ── Photo enrichment (split into 2 narrow calls) ─────────────────────────

PHOTO_ENRICH_SYSTEM = (
    "You output structured markdown descriptions."
)

PHOTO_ENRICH_PROMPT_TMPL = (
    "Rewrite this scene description as structured markdown with sections for "
    "Composition, Subject, Lighting, and Atmosphere. "
    "Add a 'Reconstruction Prompt' section at the end suitable for an AI image generator.\n\n"
    "SCENE:\n{raw}"
)

PHOTO_KEYWORDS_PROMPT_TMPL = (
    "Extract 5 to 10 keywords from this scene description. "
    "Output only a comma-separated list, nothing else.\n\n"
    "SCENE:\n{raw}"
)


# ── Mode → prompt lookup ─────────────────────────────────────────────────

MODE_PROMPTS = {
    "document": DOC_VISION_PROMPT,
    "math": MATH_VISION_PROMPT,
    "whiteboard": WHITEBOARD_VISION_PROMPT,
    "drawing": DRAWING_VISION_PROMPT,
    "photo": PHOTO_VISION_PROMPT,
}
