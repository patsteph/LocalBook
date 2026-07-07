"""Locker build A — probe-first capability + slotting + RAM-fit.

Uses the EXACT /api/show shapes captured live from ollama 0.31 (2026-07-07) so the
tests pin real-world behavior with no network. Run: `.venv/bin/python3 _probe_test.py`
"""
from evaluator.capability_probe import OllamaCapabilityProbe, ProbedCapabilities
from evaluator import ram_fit

passed = failed = 0
def check(name, cond):
    global passed, failed
    if cond: passed += 1; print(f"  PASS {name}")
    else: failed += 1; print(f"  FAIL {name}")

# ── Real /api/show payloads (trimmed to the fields the probe reads) ──────────────
GEMMA4 = {
    "capabilities": ["completion", "vision", "audio", "tools", "thinking"],
    "details": {"family": "gemma4", "parameter_size": "8.0B", "quantization_level": "Q4_K_M"},
    "model_info": {"general.architecture": "gemma4", "gemma4.context_length": 131072,
                   "gemma4.embedding_length": 2560},
}
PHI4 = {
    "capabilities": ["completion", "tools"],
    "details": {"family": "phi3", "parameter_size": "3.8B", "quantization_level": "Q4_K_M"},
    "model_info": {"general.architecture": "phi3", "phi3.context_length": 131072,
                   "phi3.embedding_length": 3072},
}
EMBED = {
    "capabilities": ["embedding"],
    "details": {"family": "bert", "parameter_size": "566.70M", "quantization_level": "F16"},
    "model_info": {"general.architecture": "bert", "bert.context_length": 8192,
                   "bert.embedding_length": 1024},
}
# The reported bug: a Qwen vision model, uncurated.
QWEN_VL = {
    "capabilities": ["completion", "vision", "tools"],
    "details": {"family": "qwen3vl", "parameter_size": "8.0B", "quantization_level": "Q4_K_M"},
    "model_info": {"general.architecture": "qwen3vl", "qwen3vl.context_length": 262144,
                   "qwen3vl.embedding_length": 4096},
}
# Old runner with NO capabilities array → fallback heuristics.
OLD_VISION = {"details": {"family": "llava", "parameter_size": "7B"},
              "model_info": {"general.architecture": "llama", "llama.context_length": 4096}}

print("── capability parse (real shapes) ──")
g = OllamaCapabilityProbe.from_show("gemma4:e4b", GEMMA4)
check("gemma4 vision+text+tools+thinking+audio",
      g.vision and g.text and g.tools and g.thinking and g.audio and not g.embedding)
check("gemma4 native_ctx 131072", g.native_ctx == 131072)
check("gemma4 params 8.0B → 8.0", g.param_count_b == 8.0 and g.quantization == "Q4_K_M")
check("gemma4 source=probe", g.source == "probe")

p = OllamaCapabilityProbe.from_show("phi4-mini:latest", PHI4)
check("phi4 text-only (no vision/embed)", p.text and not p.vision and not p.embedding)

e = OllamaCapabilityProbe.from_show("snowflake-arctic-embed2", EMBED)
check("embed model: embedding=True, text=False-ish", e.embedding and not e.vision)
check("embed dim 1024", e.embedding_dim == 1024)

q = OllamaCapabilityProbe.from_show("qwen3-vl:8b", QWEN_VL)
check("QWEN-VL vision=True (the reported bug)", q.vision is True)

o = OllamaCapabilityProbe.from_show("llava:7b", OLD_VISION)
check("old runner fallback: vision from family", o.vision is True and o.source == "fallback")

print("── capability-based ROLES (the slotting fix) ──")
check("gemma4 roles = main+fast+vision",
      set(g.roles()) == {"main_model", "fast_model", "vision_model"})
check("phi4 roles = main+fast only", set(p.roles()) == {"main_model", "fast_model"})
check("embed roles = embedding_model ONLY (not Main!)", e.roles() == ["embedding_model"])
check("QWEN-VL roles include vision_model", "vision_model" in q.roles())

print("── RAM-fit (60% unified-mem budget) ──")
# 8B Q4 ≈ 8 * 0.61 = 4.88 GB weights
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
