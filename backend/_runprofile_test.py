"""Locker build B — output filters + auto-derived RunProfile.
Run: `.venv/bin/python3 _runprofile_test.py`
"""
from evaluator.output_filters import strip_thinking, extract_json, trim_preamble, apply_filters
from evaluator.run_profile import RunProfile, derive_run_profile, _parse_stops

passed = failed = 0
def check(name, cond):
    global passed, failed
    if cond: passed += 1; print(f"  PASS {name}")
    else: failed += 1; print(f"  FAIL {name}")

print("── strip_thinking (the non-olmo/gemma scoring fix) ──")
check("closed block removed",
      strip_thinking("<think>let me reason\nstep 2</think>The answer is 42.") == "The answer is 42.")
check("block + trailing prose",
      strip_thinking("<think>reasoning</think>\n\nParis is the capital.") == "Paris is the capital.")
check("no tags → unchanged",
      strip_thinking("Just a plain answer.") == "Just a plain answer.")
check("empty think block (Qwen 'off' still emits)",
      strip_thinking("<think></think>Final.") == "Final.")
check("UNCLOSED think (hit token cap) → no answer",
      strip_thinking("<think>reasoning that never closed and ran on") == "")
check("multiple blocks",
      strip_thinking("<think>a</think>X<think>b</think>Y").replace(" ","") == "XY")
check("<reasoning> variant",
      strip_thinking("<reasoning>hmm</reasoning>Done.") == "Done.")
check("None safe", strip_thinking(None) == "")

print("── extract_json (JSON-task scoring) ──")
check("bare object", extract_json('{"a": 1, "b": 2}') == '{"a": 1, "b": 2}')
check("fenced json", extract_json('Here:\n```json\n{"x": 5}\n```\ndone') == '{"x": 5}')
check("json after thinking",
      extract_json('<think>plan</think>{"ok": true}') == '{"ok": true}')
check("chatty prose around object",
      extract_json('Sure! {"n": 3} hope that helps') == '{"n": 3}')
check("array", extract_json('[1, 2, 3]') == '[1, 2, 3]')
check("no json → None", extract_json("no json here at all") is None)
check("invalid braces → None", extract_json("this {is not} json") is None)

print("── trim_preamble ──")
check("strips 'Sure, here is:'",
      trim_preamble("Sure, here is the list:\n- a\n- b").startswith("- a"))
check("leaves plain text",
      trim_preamble("The capital is Paris.") == "The capital is Paris.")

print("── filter pipeline ──")
check("json pipeline strips think + extracts",
      apply_filters('<think>x</think>```json\n{"v":1}\n```', ["strip_thinking","json"]) == '{"v":1}')
check("json filter → '' when absent (scorer fails cleanly)",
      apply_filters("just prose", ["json"]) == "")

print("── stop-sequence parsing from /api/show parameters ──")
params = 'stop "<|im_start|>"\nstop "<|im_end|>"\ntemperature 0.7\nstop "</s>"'
check("parses 3 stops", _parse_stops(params) == ["<|im_start|>", "<|im_end|>", "</s>"])
check("empty params → []", _parse_stops("") == [])

print("── RunProfile.to_ollama_options (stop union) ──")
rp = RunProfile(model="x", thinking_capable=True, stop_sequences=["<|end|>"])
opts = rp.to_ollama_options({"stop": ["\n\n"], "temperature": 0.3})
check("merges stops, no dupes", opts["stop"] == ["\n\n", "<|end|>"] and opts["temperature"] == 0.3)
check("thinking default OFF for eval", rp.thinking_enabled is False)
check("always strips thinking", "strip_thinking" in rp.normalize_filters)

print("── LIVE derive against real Ollama ──")
for m in ["gemma4:e4b", "phi4-mini:latest"]:
    p = derive_run_profile(m)
    print(f"  {m:24} thinking_capable={p.thinking_capable} stops={p.stop_sequences[:3]} src={p.source}")
check("gemma4 derived as thinking-capable", derive_run_profile("gemma4:e4b").thinking_capable is True)

print(f"\n{'='*48}\n_runprofile_test: {passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
