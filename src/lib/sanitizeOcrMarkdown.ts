/**
 * sanitizeOcrMarkdown — strip the noise patterns that small vision/cleanup
 * models emit which would otherwise render as invisible-or-ugly content
 * inside the BlockNote editor.
 *
 * Observed failure modes (real production output):
 *
 *   1. Outer fenced wrapper:
 *
 *        ```markdown
 *        actual content
 *        ```
 *
 *      BlockNote renders this as a literal code block with a "markdown"
 *      language label, which on light themes ends up looking like a faint
 *      grey panel — what the user described as "white on white."
 *
 *   2. Hallucinated LaTeX preamble:
 *
 *        \documentclass{article}
 *        \usepackage[utf8]{inputenc}
 *        \usetikzlibrary{positioning, calc}
 *        ...
 *
 *      Granite-2B in particular emits a TeX preamble when the input is
 *      noisy or near-blank. None of it is meaningful and most editors
 *      render it as a wall of grey backslashes.
 *
 *   3. Conversational preamble:
 *
 *        Sure! Here is the cleaned-up text:
 *        Here you go:
 *
 *      Phi-4-mini occasionally ignores the system prompt and adds a
 *      lead-in line before the actual content.
 *
 *   4. Prose-as-table (the granite3.2-vision specialty):
 *
 *        |   | Title                                    |
 *        |---|------------------------------------------|
 *        | 0 | "For too long sales teams have been..."  |
 *        | 1 | "The King of LinkedIn..."                |
 *        | 2 | "Most people treat LinkedIn..."          |
 *
 *      The model sees 5 visually-separated paragraphs (testimonials,
 *      bulleted items, etc.) and emits a 2-column markdown table where
 *      column 0 is just an index and column 1 is the prose.  That is
 *      never what the source page actually contained — book covers,
 *      pull-quote sections, and FAQ pages all fall victim.  We detect
 *      this exact shape and unwrap each row's prose cell back to a
 *      paragraph.  Real 2-column data tables are preserved because
 *      their first column is rarely pure-numeric AND second column
 *      rarely averages > 5 words per cell.
 *
 * The sanitizer is intentionally conservative: it strips well-known noise
 * patterns and leaves anything ambiguous untouched. Better to ship a tiny
 * bit of model fluff than to delete real content the user captured.
 */

const FENCED_OUTER_RE = /^```(?:markdown|md|text|plaintext)?\s*\n([\s\S]*?)\n```\s*$/i;

// Match a single markdown heading line. Captures hash count + heading text.
// We deliberately operate on raw text (not inside code fences) to avoid
// touching code-block content that happens to look like a heading.
const HEADING_LINE_RE = /^(#{1,6})\s+(.+?)\s*$/;
// Recognize the start/end of a fenced code block (```lang or ```) so the
// heading normalizer skips lines inside fences. Mermaid, code, and structured
// blocks must pass through verbatim.
const FENCE_TOGGLE_RE = /^\s*```/;

const LATEX_PREAMBLE_LINE_RE =
  /^\s*\\(documentclass|usepackage|usetikzlibrary|begin\{document\}|end\{document\}|input|include|geometry|hypersetup|title|author|date|maketitle)\b.*$/;

const CONVERSATIONAL_LEAD_RE =
  /^(?:sure[!.,]?\s*|here(?:'s| is| are)?[\s,:-]+|okay[!.,]?\s*|certainly[!.,]?\s*|of course[!.,]?\s*)(?:the\s+)?(?:cleaned[- ]up\s+)?(?:text|markdown|content|output|result)?\s*[:\-—]?\s*$/i;

// Markdown table delimiter row: |---|:--:|---:| etc.
const TABLE_DELIM_RE = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$/;
// Any line that looks like a table row (starts and ends with a pipe).
// Trailing pipe is optional in some flavours of markdown but we match the
// strict GFM form because that's what BlockNote emits and the OCR cleanup
// model produces.
const TABLE_ROW_RE = /^\s*\|(.+)\|\s*$/;

/** Split a `| a | b | c |` row into trimmed cells `["a","b","c"]`. */
function parseTableRow(line: string): string[] | null {
  const m = line.match(TABLE_ROW_RE);
  if (!m) return null;
  // Split on `|`, but not on `\|` (escaped pipes inside cells).
  return m[1].split(/(?<!\\)\|/).map(c => c.trim());
}

/**
 * If the input is (or starts with) a 2-column markdown table where col 0
 * is just an index and col 1 is prose, replace that table with a sequence
 * of paragraphs containing each row's prose cell. Anything before/after
 * the table is preserved verbatim. If the input doesn't match the
 * heuristic, returns the input unchanged.
 *
 * Heuristic guards (all must hold to trigger the unwrap):
 *   • Header row exists and has exactly 2 cells.
 *   • Delimiter row immediately after.
 *   • At least 2 body rows, also exactly 2 cells each.
 *   • Every body row's first cell is empty or pure-numeric (an index).
 *   • Body rows' second cells average > 5 words per cell (prose, not data).
 *
 * The combination of "pure-numeric col 0" and "prose col 1" is the unique
 * fingerprint of the failure mode. Real lookup tables (e.g. "key | value"
 * with short values) fail the word-count check; real data tables with
 * meaningful col-0 labels fail the numeric check; the test for both
 * conditions makes false positives extremely unlikely.
 */
function unwrapProseTable(s: string): string {
  const allLines = s.split('\n');
  // Skip leading blank lines to find the start of the table.
  let i = 0;
  while (i < allLines.length && allLines[i].trim() === '') i++;
  if (i >= allLines.length) return s;

  const headerCells = parseTableRow(allLines[i]);
  if (!headerCells || headerCells.length !== 2) return s;
  if (i + 1 >= allLines.length || !TABLE_DELIM_RE.test(allLines[i + 1])) return s;

  // Collect contiguous body rows.
  const bodyRows: string[][] = [];
  let j = i + 2;
  while (j < allLines.length) {
    const row = parseTableRow(allLines[j]);
    if (!row || row.length !== 2) break;
    bodyRows.push(row);
    j++;
  }
  if (bodyRows.length < 2) return s;

  // Guard 1: column 0 must look like an index (empty or just digits).
  const col0IsIndex = bodyRows.every(r =>
    r[0] === '' || /^\d+\.?$/.test(r[0])
  );
  if (!col0IsIndex) return s;

  // Guard 2: column 1 must be prose (avg > 5 words/cell).
  const totalWords = bodyRows.reduce(
    (acc, r) => acc + r[1].split(/\s+/).filter(Boolean).length,
    0,
  );
  const avgWords = totalWords / bodyRows.length;
  if (avgWords < 5) return s;

  // Unwrap: each prose cell becomes its own paragraph. We strip a single
  // pair of surrounding straight-quotes if present because the cleanup
  // model often quotes pull-quotes; the user can re-quote if they want.
  const paragraphs = bodyRows.map(r => {
    let cell = r[1];
    if (
      (cell.startsWith('"') && cell.endsWith('"')) ||
      (cell.startsWith('“') && cell.endsWith('”'))
    ) {
      cell = cell.slice(1, -1).trim();
    }
    return cell;
  });

  const before = allLines.slice(0, i).join('\n').trim();
  const after = allLines.slice(j).join('\n').trim();

  return [before, paragraphs.join('\n\n'), after]
    .filter(p => p)
    .join('\n\n');
}

/**
 * Normalize markdown heading levels to a consistent H2/H3 hierarchy:
 *   • Demote H1 (#) → H2 (##). The source filename carries the document
 *     title; H1 inside body content drives the H1-vs-H3 cross-page drift.
 *   • Strip empty headings (just hashes with no text).
 *   • Skip any lines inside fenced code blocks so Mermaid, code, etc.
 *     pass through untouched.
 *
 * Defensive layer: the backend already runs an equivalent normalizer in
 * scan_pipeline._normalize_headings, but a saved scan might predate that
 * pass, or the user may paste raw markdown into the editor.
 */
function normalizeHeadingLevels(s: string): string {
  const lines = s.split('\n');
  let inFence = false;
  const out: string[] = [];
  for (const line of lines) {
    if (FENCE_TOGGLE_RE.test(line)) {
      inFence = !inFence;
      out.push(line);
      continue;
    }
    if (inFence) {
      out.push(line);
      continue;
    }
    const m = line.match(HEADING_LINE_RE);
    if (!m) {
      out.push(line);
      continue;
    }
    const hashes = m[1];
    const text = m[2].trim();
    if (!text) continue; // strip empty heading
    const level = hashes.length === 1 ? 2 : hashes.length;
    out.push(`${'#'.repeat(level)} ${text}`);
  }
  return out.join('\n');
}

export function sanitizeOcrMarkdown(input: string): string {
  if (!input) return '';
  let s = input.replace(/\r\n/g, '\n').trim();
  if (!s) return '';

  // 1) Unwrap an outer ```markdown ... ``` fence if the model wrapped
  //    its entire reply in one. Done in a loop because we have seen
  //    nested wraps (cleanup model wraps vision-model output that was
  //    already wrapped).
  for (let i = 0; i < 3; i++) {
    const m = s.match(FENCED_OUTER_RE);
    if (!m) break;
    s = m[1].trim();
  }

  // 2) Strip a hallucinated LaTeX preamble at the top of the output.
  //    We only strip CONTIGUOUS leading preamble lines so we don't eat
  //    legitimate \begin{equation} blocks that appear later in the doc.
  const lines = s.split('\n');
  let firstReal = 0;
  while (firstReal < lines.length) {
    const line = lines[firstReal];
    if (line.trim() === '' || LATEX_PREAMBLE_LINE_RE.test(line)) {
      firstReal++;
      continue;
    }
    break;
  }
  if (firstReal > 0) s = lines.slice(firstReal).join('\n').trim();

  // 3) Strip a single conversational lead-in line if present.
  const firstLine = s.split('\n', 1)[0];
  if (CONVERSATIONAL_LEAD_RE.test(firstLine.trim())) {
    s = s.slice(firstLine.length).replace(/^\n+/, '');
  }

  // 4) Unwrap a prose-as-table if the model emitted one. Runs after
  //    fence-stripping so a table wrapped in ```markdown ... ``` is
  //    detectable.
  s = unwrapProseTable(s);

  // 5) Normalize heading levels (H1 → H2; strip empties; preserve
  //    fenced code/mermaid/structured blocks). Defensive — the backend
  //    pass already does this, but anything that bypassed it (legacy
  //    notes, paste-in) lands here.
  s = normalizeHeadingLevels(s);

  return s.trim();
}
