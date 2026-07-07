"""Locker build C — RunProfile-aware scoring makes thinking models score FAIRLY.
Run: `.venv/bin/python3 _scoring_fairness_test.py`
"""
from evaluator import scoring
from evaluator.run_profile import RunProfile

passed = failed = 0
def check(name, cond):
    global passed, failed
    if cond: passed += 1; print(f"  PASS {name}")
    else: failed += 1; print(f"  FAIL {name}")

# A thinking model's answer: reasoning block THEN the real answer. A non-thinking
# model would produce just the answer. Before build C, the <think> block polluted
# every deterministic check; now it's stripped before scoring.
THINK = "<think>The user wants Paris. Let me recall French geography.</think>Paris"
CLEAN = "Paris"

print("── must_contain: thinking answer scores == clean answer ──")
check("clean answer finds fact", scoring.score_must_contain(CLEAN, ["Paris"]) == 100)
check("thinking answer ALSO 100 (was polluted before)",
      scoring.score_must_contain(THINK, ["Paris"]) == 100)
check("banned-word NOT triggered by reasoning-only mention",
      # 'geography' appears only inside <think>; format check must not see it
      scoring.score_format_compliance(
          "<think>France geography lesson</think>Here is the answer.",
          {"banned_keywords": ["geography"]}) == 100)

print("── json_validity: JSON after a think block still scores ──")
s, _ = scoring.score_json_validity('<think>plan the shape</think>{"answer": 42}')
check("JSON-after-think scores 100", s == 100)
s2, _ = scoring.score_json_validity('<think>reasoning</think>Here you go:\n```json\n{"x":1}\n```')
check("fenced JSON after think scores 90 (extract-needed)", s2 == 90)
s3, _ = scoring.score_json_validity("<think>I cannot</think>sorry no json")
check("no JSON → 0", s3 == 0)

print("── format/length ignore reasoning word-count ──")
# Word limit 5: the answer is 3 words; reasoning is long. Must judge the answer.
long_think = "<think>" + ("blah " * 200) + "</think>Short and sweet answer."
check("word-limit judged on answer, not reasoning",
      scoring.score_format_compliance(long_think, {"max_word_count": 6}) == 100)
check("output_length judged on answer",
      scoring.score_output_length(long_think, max_words=6) == 100)

print("── citations survive de-thinking ──")
check("citation in answer found",
      scoring.score_has_citations("<think>cite it</think>The sky is blue [1].", 1) == 100)

print("── active-profile plumbing ──")
rp = RunProfile(model="qwen3:8b", thinking_capable=True, normalize_filters=["strip_thinking"])
scoring.set_active_run_profile(rp)
check("profile set → still strips", scoring.score_must_contain(THINK, ["Paris"]) == 100)
scoring.clear_active_run_profile()
check("profile cleared → default still strips (safe no-op on plain)",
      scoring.score_must_contain(CLEAN, ["Paris"]) == 100)

print(f"\n{'='*52}\n_scoring_fairness_test: {passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
