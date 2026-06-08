"""interactive_quiz_renderer — Phase 11 of v2-information-cortex.

Server-side composer that turns a list of QuizQuestion dicts into a
self-contained interactive HTML page suitable for the Phase 11
InteractiveHtmlArtifactRenderer's sandboxed iframe.

Why server-composed instead of LLM-authored? Same call as the Phase 10
dashboard:
  - The quiz JSON shape is already structured (QuizOutput / QuizQuestion);
    the LLM authored the content, not the layout.
  - Deterministic composition is testable in isolation.
  - LLM-generated interactive HTML would require allow-script sanitization
    that defeats the security boundary.

The composer emits ONE complete HTML document containing:
  - Inline CSS (no Tailwind dependency — iframe is isolated).
  - One card per question with the appropriate input type.
  - An inline bridge `<script>` that posts:
      {type: 'lb-resize', height: document.body.scrollHeight}  on any change
      {type: 'lb-result', payload: {score, total, answers}}    on completion

No external resources, no <img>, no fetch.
"""
from __future__ import annotations

import html as _html
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_CHOICE_KINDS = {"multiple_choice", "true_false"}
_OPEN_KINDS = {"fill_in_the_blank", "short_answer", "spot_the_error", "justify"}


def _safe(s: Any) -> str:
    return _html.escape(str(s or ""), quote=True)


def _render_choice_question(idx: int, q: Dict[str, Any]) -> str:
    options = q.get("options") or []
    if not isinstance(options, list) or not options:
        # Defensive: render as open-ended if the model omitted options.
        return _render_open_question(idx, q)
    answer = str(q.get("answer") or "")
    explanation = _safe(q.get("explanation") or "")
    opts_html = []
    for i, opt in enumerate(options):
        opt_str = str(opt)
        is_correct = "true" if opt_str.strip() == answer.strip() else "false"
        opts_html.append(
            f'<label class="lb-opt"><input type="radio" name="q{idx}" '
            f'value="{_safe(opt_str)}" data-correct="{is_correct}"> '
            f'<span>{_safe(opt_str)}</span></label>'
        )
    return f"""
    <div class="lb-q" data-kind="choice" data-idx="{idx}">
      <div class="lb-q-head">
        <span class="lb-q-num">{idx + 1}</span>
        <p class="lb-q-text">{_safe(q.get('question'))}</p>
      </div>
      <div class="lb-opts">{"".join(opts_html)}</div>
      <button type="button" class="lb-btn lb-check">Check</button>
      <div class="lb-feedback" hidden>
        <div class="lb-result-line"></div>
        <p class="lb-explanation">{explanation}</p>
      </div>
    </div>
    """


def _render_open_question(idx: int, q: Dict[str, Any]) -> str:
    explanation = _safe(q.get("explanation") or "")
    answer = _safe(q.get("answer") or "")
    applied = q.get("applied_scenario") or ""
    applied_html = (
        f'<p class="lb-applied">{_safe(applied)}</p>' if applied else ""
    )
    return f"""
    <div class="lb-q" data-kind="open" data-idx="{idx}">
      <div class="lb-q-head">
        <span class="lb-q-num">{idx + 1}</span>
        <p class="lb-q-text">{_safe(q.get('question'))}</p>
      </div>
      {applied_html}
      <textarea class="lb-input" rows="3" placeholder="Type your answer…"></textarea>
      <button type="button" class="lb-btn lb-reveal">Reveal answer</button>
      <div class="lb-feedback" hidden>
        <p class="lb-result-line"><strong>Answer:</strong> {answer}</p>
        <p class="lb-explanation">{explanation}</p>
      </div>
    </div>
    """


_BRIDGE_JS = r"""
(function() {
  function postResize() {
    try {
      var h = document.documentElement.scrollHeight || document.body.scrollHeight;
      parent.postMessage({type: 'lb-resize', height: h}, '*');
    } catch (_) {}
  }
  function postResult(payload) {
    try { parent.postMessage({type: 'lb-result', payload: payload}, '*'); } catch (_) {}
  }
  function totalQuestions() { return document.querySelectorAll('.lb-q').length; }
  function completedCount() {
    return document.querySelectorAll('.lb-q .lb-feedback:not([hidden])').length;
  }
  function correctCount() {
    return document.querySelectorAll('.lb-q[data-state="correct"]').length;
  }
  function updateProgress() {
    var done = completedCount();
    var total = totalQuestions();
    var correct = correctCount();
    var bar = document.getElementById('lb-progress');
    if (bar) {
      bar.textContent = correct + ' of ' + done + ' answered correctly · ' + done + '/' + total + ' done';
    }
    if (done === total && total > 0) {
      // Final result on completion.
      var answers = [];
      document.querySelectorAll('.lb-q').forEach(function(card) {
        answers.push({
          idx: parseInt(card.getAttribute('data-idx'), 10),
          kind: card.getAttribute('data-kind'),
          state: card.getAttribute('data-state') || 'revealed'
        });
      });
      postResult({score: correct, total: total, answers: answers});
    }
  }
  function handleCheck(card) {
    var picked = card.querySelector('input[type="radio"]:checked');
    var feedback = card.querySelector('.lb-feedback');
    var line = card.querySelector('.lb-result-line');
    if (!picked) {
      if (line) line.textContent = 'Pick one first.';
      if (feedback) feedback.hidden = false;
      postResize();
      return;
    }
    var correct = picked.getAttribute('data-correct') === 'true';
    card.setAttribute('data-state', correct ? 'correct' : 'wrong');
    if (line) line.textContent = correct ? 'Correct.' : 'Not quite.';
    if (feedback) feedback.hidden = false;
    // Highlight options.
    Array.prototype.forEach.call(card.querySelectorAll('label.lb-opt'), function(lab) {
      var input = lab.querySelector('input');
      if (input && input.getAttribute('data-correct') === 'true') lab.classList.add('lb-correct-opt');
      if (input === picked && !correct) lab.classList.add('lb-picked-wrong');
    });
    updateProgress();
    postResize();
  }
  function handleReveal(card) {
    var feedback = card.querySelector('.lb-feedback');
    if (feedback) feedback.hidden = false;
    card.setAttribute('data-state', 'revealed');
    updateProgress();
    postResize();
  }
  document.addEventListener('click', function(ev) {
    var t = ev.target;
    if (!t || !t.classList) return;
    var card = t.closest('.lb-q');
    if (!card) return;
    if (t.classList.contains('lb-check')) handleCheck(card);
    else if (t.classList.contains('lb-reveal')) handleReveal(card);
  });
  document.addEventListener('input', function() { postResize(); });
  document.addEventListener('DOMContentLoaded', function() {
    setTimeout(postResize, 0);
    updateProgress();
  });
  // Initial resize even if DOMContentLoaded already fired (defensive).
  setTimeout(postResize, 0);
})();
"""


def _styles() -> str:
    return """
    *, *::before, *::after { box-sizing: border-box; }
    body {
      margin: 0; padding: 16px;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      font-size: 14px; line-height: 1.5; color: #111827; background: #ffffff;
    }
    .lb-progress {
      font-size: 12px; color: #6b7280; text-transform: uppercase;
      letter-spacing: 0.04em; margin-bottom: 16px;
    }
    .lb-q {
      border: 1px solid #e5e7eb; border-radius: 8px;
      padding: 14px; margin-bottom: 12px; background: #ffffff;
    }
    .lb-q[data-state="correct"] { border-color: #16a34a; background: #f0fdf4; }
    .lb-q[data-state="wrong"]   { border-color: #dc2626; background: #fef2f2; }
    .lb-q[data-state="revealed"]{ border-color: #2563eb; background: #eff6ff; }
    .lb-q-head { display: flex; gap: 10px; align-items: flex-start; }
    .lb-q-num {
      flex-shrink: 0; width: 22px; height: 22px; border-radius: 999px;
      background: #e5e7eb; color: #374151; font-size: 12px; font-weight: 600;
      display: flex; align-items: center; justify-content: center;
    }
    .lb-q-text { margin: 0; font-weight: 500; }
    .lb-applied { margin: 8px 0 0 32px; font-size: 12px; color: #4b5563; font-style: italic; }
    .lb-opts { display: flex; flex-direction: column; gap: 6px; margin: 12px 0 0 32px; }
    .lb-opt {
      display: flex; align-items: center; gap: 8px; padding: 6px 10px;
      border-radius: 6px; cursor: pointer; user-select: none;
    }
    .lb-opt:hover { background: rgba(0,0,0,0.03); }
    .lb-opt.lb-correct-opt   { background: #dcfce7; }
    .lb-opt.lb-picked-wrong  { background: #fee2e2; }
    .lb-input {
      width: 100%; margin-top: 10px; padding: 8px 10px;
      border: 1px solid #d1d5db; border-radius: 6px; font: inherit;
      resize: vertical;
    }
    .lb-btn {
      margin-top: 10px; padding: 6px 12px; font: inherit;
      border: 1px solid #2563eb; background: #2563eb; color: white;
      border-radius: 6px; cursor: pointer;
    }
    .lb-btn:hover { background: #1d4ed8; }
    .lb-feedback { margin: 10px 0 0 32px; font-size: 13px; }
    .lb-result-line { margin: 0; font-weight: 500; }
    .lb-explanation { margin: 6px 0 0 0; color: #4b5563; }
    """


def quiz_to_interactive_html(
    questions: List[Dict[str, Any]],
    title: Optional[str] = None,
) -> str:
    """Compose a self-contained interactive HTML page from a quiz JSON list.

    Returns a complete HTML document string. Iframe-safe: no external
    resources, no inline tracking, only postMessage to parent.
    """
    title_safe = _safe(title or "Quiz")
    cards: List[str] = []
    for i, q in enumerate(questions or []):
        if not isinstance(q, dict):
            continue
        kind = str(q.get("question_type") or "short_answer").lower()
        if kind in _CHOICE_KINDS:
            cards.append(_render_choice_question(i, q))
        elif kind in _OPEN_KINDS:
            cards.append(_render_open_question(i, q))
        else:
            cards.append(_render_open_question(i, q))

    return (
        "<!DOCTYPE html><html><head>"
        f"<meta charset='utf-8'><title>{title_safe}</title>"
        f"<style>{_styles()}</style>"
        "</head><body>"
        '<div class="lb-progress" id="lb-progress">0 of 0 answered correctly · '
        f'0/{len(cards)} done</div>'
        + "".join(cards)
        + f"<script>{_BRIDGE_JS}</script>"
        + "</body></html>"
    )


# Convenience for backends that have a QuizOutput Pydantic model rather than
# a list of dicts.
def quiz_output_to_interactive_html(output: Any) -> str:
    """Accept either a QuizOutput Pydantic model or a dict shaped like one."""
    if hasattr(output, "model_dump"):
        data = output.model_dump()
    else:
        data = output or {}
    return quiz_to_interactive_html(
        questions=data.get("questions") or [],
        title=data.get("topic"),
    )
