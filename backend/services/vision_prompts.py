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
  - Heading hierarchy mandate: H2 = section, H3 = subsection, no H1.
    The H1 ban exists because the source filename already carries the
    document title; emitting H1 in body content produced cross-page
    inconsistencies (one page H1, next page H3 for the same chapter).

These changes were driven by an audit that surfaced gemma3:4b literally
echoing the bullet-point prompt instead of transcribing the page. The
original prompts were ~10 lines of dense rules with •, →, and "do not"
clauses, which 2-4B parameter vision models cannot reliably follow.
"""


# ── Vision model prompts (per content mode) ──────────────────────────────

DOC_VISION_PROMPT = (
    "Read this page and output its text exactly as it appears, in plain markdown. "
    "Use H2 (##) for section headings and H3 (###) for subsection headings. "
    "Treat ambiguous bold lines as bold body text rather than headings. "
    "Preserve paragraph breaks, lists, and tables exactly. "
    "Use LaTeX for any equation. If a word is too blurry to read, write [unclear]. "
    "If the page has footnotes, render markers in the body as [^N] and definitions "
    "at the bottom as '[^N]: footnote text'. "
    "Output only the page text."
)

MATH_VISION_PROMPT = (
    "Read this page and transcribe everything in markdown. "
    "Use H2 (##) for section headings and H3 (###) for subsection headings. "
    "Write all equations in LaTeX, using $...$ for inline math and $$...$$ for display math. "
    "Use \\begin{pmatrix}...\\end{pmatrix} for matrices. "
    "Keep step numbers and surrounding explanatory text exactly as written. "
    "If footnotes appear, render markers in the body as [^N] and definitions "
    "at the bottom as '[^N]: footnote text'. "
    "Output only the transcription."
)

WHITEBOARD_VISION_PROMPT = (
    "Read this whiteboard photo and transcribe its content as structured markdown. "
    "Use H2 (##) for the main topic and H3 (###) for sub-topics if any are visible. "
    "Write each text block as a paragraph or list item depending on how it appears. "
    "Describe diagrams as nested lists showing connections (for example: 'A connects to B'). "
    "Note color-coded groupings inline. "
    "Output only the transcription."
)

DRAWING_VISION_PROMPT = (
    "Describe this hand-drawn illustration as structured markdown. "
    "Cover overall layout, individual elements with their spatial relationships, "
    "any labels or text exactly as written, and the colors used. "
    "Then add a section titled '## Reconstruction Spec' with these subsections: "
    "'### Palette' listing named colors, '### Composition' describing focal point and layout, "
    "'### Elements' as a list of key elements with their positions, "
    "and '### Style' describing the drawing style (sketch, cartoon, watercolor, ink, etc.). "
    "Output a clear markdown description."
)

PHOTO_VISION_PROMPT = (
    "Describe this photo in rich detail as plain prose. "
    "Cover composition, key objects, colors, lighting, and any visible text. "
    "Output only the description."
)

HANDWRITING_VISION_PROMPT = (
    "Read this handwritten page and transcribe it as plain markdown. "
    "Preserve paragraph breaks and lists exactly as written. "
    "If the handwriting forms a heading (visually larger or underlined), "
    "use H2 (##) for sections and H3 (###) for subsections. "
    "If a word is unreadable, write [unclear]. "
    "Render cross-outs as ~~strikethrough~~. "
    "Render margin notes as blockquotes prefixed with '> margin:'. "
    "If footnote markers appear, render them as [^N] and definitions as '[^N]: text'. "
    "Output only the transcription."
)

MIXED_PAGE_VISION_PROMPT = (
    "Read this page that combines printed text with handwritten annotations. "
    "Output the printed body as the primary markdown content, using H2 (##) "
    "for section headings and H3 (###) for subsection headings. "
    "Render every handwritten margin note or insertion as a blockquote on its "
    "own line, prefixed with '> margin:' followed by the handwritten text. "
    "If there is a small diagram, describe it as a nested list of elements "
    "and connections. Use [unclear] for unreadable words. "
    "Output only the transcription."
)

DIAGRAM_VISION_PROMPT = (
    "Read this diagram (flowchart, mind map, or process diagram) and output "
    "valid Mermaid syntax wrapped in a ```mermaid code block. "
    "Use 'graph TD' for top-down flowcharts, 'graph LR' for left-right flowcharts, "
    "'mindmap' for hub-and-spoke layouts, and 'sequenceDiagram' for sequence flows. "
    "Include every visible label exactly as written, with no abbreviations. "
    "Use arrow labels for any text written on connecting lines. "
    "After the Mermaid block, add a single short paragraph summarizing what the diagram shows. "
    "Output only the Mermaid block and summary."
)

RECEIPT_VISION_PROMPT = (
    "Read this receipt or invoice and output the data as a structured markdown document. "
    "Begin with a level-2 header naming the vendor (## VENDOR NAME). "
    "Add a metadata section with one bold field per line: **Date**, **Time**, **Receipt #**, "
    "**Payment**, and **Currency** when visible. "
    "Then add a markdown table with columns: Item | Qty | Unit Price | Total. "
    "After the table, add a totals block with **Subtotal**, **Tax**, and **Total** on separate lines. "
    "Use the exact currency symbol shown on the receipt. "
    "Output only the structured transcription."
)

BUSINESS_CARD_VISION_PROMPT = (
    "Read this business card and output the contact information as structured markdown. "
    "Format as: **Name** on the first line as level-2 header (## Name), "
    "then one bold field per line for **Title**, **Organization**, **Phone**, **Mobile**, "
    "**Email**, **Website**, and **Address** when visible. "
    "Preserve the exact characters of phone numbers, emails, and URLs. "
    "If the card has a tagline, render it as italic text below the address. "
    "Output only the contact block."
)

CODE_SCREEN_VISION_PROMPT = (
    "Read this image of code, terminal output, or screen text and transcribe it inside "
    "a fenced markdown code block. "
    "Detect the language from syntax cues (python, javascript, rust, go, sql, bash, "
    "yaml, json, markdown, plaintext) and use that as the fence language. "
    "Preserve every space of indentation exactly. "
    "If multiple distinct code regions are visible, output each as a separate fenced block "
    "with a short bold header naming what it shows. "
    "If a character is too small to read clearly, write [unclear] in its place. "
    "Output only the fenced code blocks and any necessary headers."
)

SLIDE_VISION_PROMPT = (
    "Read this presentation slide and output its content as structured markdown. "
    "Render the slide title as H2 (##). "
    "Render bullet points as a markdown list with '-' markers, preserving nesting. "
    "Render speaker notes, footers, page numbers, or legends as blockquotes prefixed with '> note:'. "
    "If the slide has a chart or diagram, describe it as a nested list of labels and relationships "
    "below the bullet content. "
    "Output only the transcription."
)

# ── User-pick specialized modes (B1) ──────────────────────────────────────
# These are NOT in the auto-classifier label list because the small vision
# model can't reliably distinguish them from generic 'document'. They live
# in MODE_PROMPTS so the user can explicitly select them in the capture UI
# when they know what they're scanning.

RECIPE_VISION_PROMPT = (
    "Read this recipe and output it as structured markdown. "
    "Begin with the recipe name as H2 (##). "
    "Add a metadata block with one bold field per line for **Servings**, **Prep time**, "
    "**Cook time**, and **Total time** when visible. "
    "Then render '### Ingredients' as an unordered list, preserving each amount and unit. "
    "Then render '### Instructions' as an ordered list, one numbered step per item. "
    "If notes, tips, or variations appear, add a '### Notes' section at the end. "
    "Output only the transcription."
)

RESUME_VISION_PROMPT = (
    "Read this resume or CV and output it as structured markdown. "
    "Begin with the person's name as H2 (##). "
    "Add a contact block with one bold field per line for **Title**, **Email**, **Phone**, "
    "**Location**, **Website**, **LinkedIn** when visible. "
    "Render each top-level section ('Experience', 'Education', 'Skills', 'Projects', "
    "'Certifications', 'Summary') as H3 (###). "
    "Within Experience and Education, render each entry as a sub-list with the role/degree "
    "as bold text on the first line and dates/location on the second. "
    "Skills can be a comma-separated list or a sub-list — match the layout. "
    "If a word is unreadable, write [unclear]. Output only the transcription."
)

GLOSSARY_VISION_PROMPT = (
    "Read this glossary page and output it as a markdown definition list. "
    "Render each entry on two lines: the term as bold text on the first line, "
    "then the definition on the next line. Separate entries with a blank line. "
    "Preserve cross-references (See: ...) and any 'See also' notes inline. "
    "If letters or alphabet section dividers appear (A, B, C…), render each as H3 (###). "
    "Output only the transcription."
)

TITLE_PAGE_VISION_PROMPT = (
    "Read this book or document title page and output the metadata as structured markdown. "
    "Render the main title as H2 (##). "
    "Add a subtitle as italic text below the title if present. "
    "Then list **Author**, **Editor**, **Translator**, **Publisher**, **Edition**, "
    "**Year**, **ISBN**, and **Series** as one bold field per line when visible. "
    "If a foreword author or dedication is present, capture them as separate bold fields. "
    "Output only the metadata block."
)

CALENDAR_VISION_PROMPT = (
    "Read this calendar, agenda, or schedule page and output it as structured markdown. "
    "If a month/year title is visible, render it as H2 (##). "
    "Render the date grid OR the event list as a markdown table with columns: "
    "Date | Time | Event | Location | Notes. "
    "Preserve every visible event verbatim including times in the original format. "
    "If a recurring or all-day event is shown, mark it in the Notes column. "
    "Output only the transcription."
)

FORM_VISION_PROMPT = (
    "Read this printed form and output its filled-in fields as structured markdown. "
    "Render each section header as H3 (###). "
    "Render each field as a bold label followed by the field value, e.g. '**Field Name:** value'. "
    "If a field is unfilled, render it as '**Field Name:** _' (underscore placeholder). "
    "If checkboxes appear, render them as '- [x] Option' for checked and '- [ ] Option' for unchecked. "
    "If a field value is illegible, write [unclear]. "
    "Output only the transcription."
)

MAP_VISION_PROMPT = (
    "Read this map or floor plan and describe its content as structured markdown. "
    "Begin with an H2 (##) naming the map (use the title if visible, otherwise summarize). "
    "Add a '### Legend' section listing each visible legend symbol or key item as bullets. "
    "Add a '### Regions' section listing each labeled region or room as bullets, "
    "with the label as bold text and a brief description of its position relative to other regions. "
    "Add a '### Scale' section if a scale bar or grid is shown. "
    "If a compass or orientation marker is present, note it in a '### Orientation' section. "
    "Output only the transcription."
)

INDEX_PAGE_VISION_PROMPT = (
    "Read this book index page and output it as a markdown list with page numbers preserved. "
    "Each top-level entry is a bullet with the term as bold text followed by the page numbers. "
    "Sub-entries (indented in the source) are nested bullets under their parent. "
    "Preserve 'See: …' and 'See also: …' cross-references inline as italic text. "
    "If letter dividers appear (A, B, C…), render each as H3 (###). "
    "Preserve every page number exactly. Output only the transcription."
)

COLLAGE_VISION_PROMPT = (
    "This image contains multiple distinct content blocks (sticky notes, index cards, "
    "scattered receipts, or a pinboard). Identify each distinct block. "
    "For each block, output a section using H3 (###) with a short descriptor "
    "('### Sticky note 1', '### Index card top-left', etc.) and below it the block's "
    "transcription as plain markdown. "
    "Separate blocks with horizontal rules (---). "
    "Preserve handwriting cross-outs as ~~strikethrough~~. "
    "If a block is too small to read, write '### Block N\\n[unclear]'. "
    "Output only the transcription."
)


# ── Auto-classification ──────────────────────────────────────────────────

CLASSIFY_PROMPT = (
    "What type of content is this image? Answer with one word from this list: "
    "document, handwriting, mixed, math, whiteboard, drawing, diagram, photo, "
    "receipt, business_card, code, slide. Reply with only the word."
)


# ── Document cleanup pass (downstream small text model) ──────────────────

CLEANUP_SYSTEM = (
    "You output cleaned plain markdown. Fix obvious OCR typos. "
    "Keep the same structure: paragraphs stay paragraphs, lists stay lists, "
    "tables stay tables. Keep all LaTeX math, [unclear] markers, '> margin:' "
    "blockquotes, and ```mermaid``` or ```<lang>``` code blocks exactly as written. "
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
    # Auto-classifiable broad categories.
    "document": DOC_VISION_PROMPT,
    "handwriting": HANDWRITING_VISION_PROMPT,
    "mixed": MIXED_PAGE_VISION_PROMPT,
    "math": MATH_VISION_PROMPT,
    "whiteboard": WHITEBOARD_VISION_PROMPT,
    "drawing": DRAWING_VISION_PROMPT,
    "diagram": DIAGRAM_VISION_PROMPT,
    "photo": PHOTO_VISION_PROMPT,
    "receipt": RECEIPT_VISION_PROMPT,
    "business_card": BUSINESS_CARD_VISION_PROMPT,
    "code": CODE_SCREEN_VISION_PROMPT,
    "slide": SLIDE_VISION_PROMPT,
    # User-pick specialized modes — not in the auto-classifier list.
    "recipe": RECIPE_VISION_PROMPT,
    "resume": RESUME_VISION_PROMPT,
    "glossary": GLOSSARY_VISION_PROMPT,
    "title_page": TITLE_PAGE_VISION_PROMPT,
    "calendar": CALENDAR_VISION_PROMPT,
    "form": FORM_VISION_PROMPT,
    "map": MAP_VISION_PROMPT,
    "index_page": INDEX_PAGE_VISION_PROMPT,
    "collage": COLLAGE_VISION_PROMPT,
}

# Modes that produce a single primary output block (table, vCard, code,
# Mermaid diagram, structured form) — the cleanup pass should be permissive
# about preserving their structure rather than rewriting them as prose.
STRUCTURED_MODES = frozenset({
    "diagram", "receipt", "business_card", "code",
    "calendar", "form", "index_page", "title_page",
})

# Modes whose output is fundamentally non-textual (photo description,
# drawing spec, map description). They skip the document-style cleanup
# pass entirely — the model's enrichment / refinement pipeline handles them.
DESCRIPTIVE_MODES = frozenset({"photo", "drawing", "map", "collage"})

# Modes the user explicitly picks (not chosen by the auto-classifier).
# These keep the auto-classifier's label list manageable for small vision
# models that struggle to choose between 20 categories at once.
USER_PICK_ONLY_MODES = frozenset({
    "recipe", "resume", "glossary", "title_page",
    "calendar", "form", "map", "index_page", "collage",
})
