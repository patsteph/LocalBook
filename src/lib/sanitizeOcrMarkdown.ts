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
 * The sanitizer is intentionally conservative: it strips well-known noise
 * patterns and leaves anything ambiguous untouched. Better to ship a tiny
 * bit of model fluff than to delete real content the user captured.
 */

const FENCED_OUTER_RE = /^```(?:markdown|md|text|plaintext)?\s*\n([\s\S]*?)\n```\s*$/i;

const LATEX_PREAMBLE_LINE_RE =
  /^\s*\\(documentclass|usepackage|usetikzlibrary|begin\{document\}|end\{document\}|input|include|geometry|hypersetup|title|author|date|maketitle)\b.*$/;

const CONVERSATIONAL_LEAD_RE =
  /^(?:sure[!.,]?\s*|here(?:'s| is| are)?[\s,:-]+|okay[!.,]?\s*|certainly[!.,]?\s*|of course[!.,]?\s*)(?:the\s+)?(?:cleaned[- ]up\s+)?(?:text|markdown|content|output|result)?\s*[:\-—]?\s*$/i;

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

  return s.trim();
}
