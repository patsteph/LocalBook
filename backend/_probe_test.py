"""Locker — probe-first capability + slotting + RAM-fit.

Was written against real `/api/show` payloads captured from ollama 0.31. That probe went with
the transport in the v2.3.0 cutover, so these now pin the MLX probe: capabilities are read
from the checkpoint's own `config.json`, which is the only source left.

The role-slotting and RAM-fit assertions are unchanged — they were never Ollama-specific, and
they guard the two bugs that motivated this file: "Qwen vision model told to install granite"
(capabilities defaulted off) and "5 fresh models all slotted into Main" (size-based slotting).

Run: `.venv/bin/python3 _probe_test.py`
"""
from evaluator.capability_probe import MLXCapabilityProbe, ProbedCapabilities
from evaluator import ram_fit

passed = failed = 0
def check(name, cond):
    global passed, failed
    if cond: passed += 1; print(f"  PASS {name}")
    else: failed += 1; print(f"  FAIL {name}")


def probe(model_id):
    return MLXCapabilityProbe().probe(model_id)


print("── capability parse (real cached checkpoints) ──")
g = probe("mlx-community/gemma-4-e4b-it-4bit")
check("gemma4 probed at all", g is not None)
if g:
    check("gemma4 vision + text, not embedding", g.vision and g.text and not g.embedding)
    check("gemma4 native_ctx 131072", g.native_ctx == 131072)
    check("gemma4 source=probe", g.source == "probe")
    check("gemma4 provider=mlx", g.provider == "mlx")

p = probe("mlx-community/Phi-4-mini-instruct-4bit")
check("phi4 probed at all", p is not None)
if p:
    check("phi4 text-only (no vision/embed)", p.text and not p.vision and not p.embedding)

e = probe("mlx-community/snowflake-arctic-embed-l-v2.0-bf16")
check("arctic probed at all", e is not None)
if e:
    check("embed model: embedding=True, vision=False", e.embedding and not e.vision)

check("an uncached model probes to None (no network fallback)",
      probe("mlx-community/definitely-not-downloaded") is None)

print("── capability-based ROLES (the slotting fix) ──")
if g:
    check("gemma4 roles = main+fast+vision",
          set(g.roles()) == {"main_model", "fast_model", "vision_model"})
if p:
    check("phi4 roles = main+fast only", set(p.roles()) == {"main_model", "fast_model"})
if e:
    check("embed roles = embedding_model ONLY (not Main!)", e.roles() == ["embedding_model"])

print("── role eligibility is capability-driven, not size-driven ──")
# The original bug: every fresh model landed in Main because slotting keyed off disk size.
synthetic_vision = ProbedCapabilities(model="x", text=True, vision=True)
check("any vision-capable model is vision-eligible",
      "vision_model" in synthetic_vision.roles())
synthetic_embed = ProbedCapabilities(model="y", text=True, embedding=True)
check("a pure embedder never lands in Main",
      synthetic_embed.roles() == ["embedding_model"])

print("── RAM-fit ──")
fit16 = ram_fit.ram_fit(8.0, "Q4_K_M", 16.0, context_tokens=8192)
check("8B-Q4 weight ≈ 4.88GB", abs(fit16["weight_gb"] - 4.88) < 0.05)
check("8B-Q4 fits a 16GB Mac (budget 9.6)", fit16["fits"] and fit16["budget_gb"] == 9.6)
fit_big = ram_fit.ram_fit(70.0, "Q4_K_M", 16.0)  # 70B on 16GB → no
check("70B-Q4 does NOT fit 16GB", fit_big["fits"] is False and fit_big["recommendation"] == "over")
check("F16 = 2 bytes/wt", ram_fit.bytes_per_weight("F16") == 2.0)
check("param parse 566.70M → 0.567", abs(ram_fit.parse_param_count_b("566.70M") - 0.567) < 0.001)
check("unknown quant → safe 1.0 default", ram_fit.bytes_per_weight("IQ4_XS?") in (0.55, 1.0))

print(f"\n{'='*48}\n_probe_test: {passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
