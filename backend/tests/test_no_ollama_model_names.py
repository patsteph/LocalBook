"""No code passes an Ollama-era model tag to the MLX engine.

After the v2.3.0 cutover every role is an MLX checkpoint id ("org/repo"). Two leftovers
still named Ollama tags and failed on every call (2026-10-01): the voice-profile
rebuild ("phi4-mini:latest") and the locker's vision fallback ("granite3.2-vision:2b").
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAG = re.compile(r"""(model\s*=\s*|return\s+)["'][A-Za-z0-9._-]+:[A-Za-z0-9._-]+["']""")


def test_no_hardcoded_ollama_tags():
    hits = []
    for d in ("services", "agents", "api", "storage", "utils"):
        for p in (ROOT / d).rglob("*.py"):
            for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
                if TAG.search(line) and not line.lstrip().startswith("#"):
                    hits.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()}")
    assert not hits, "use settings.<role>_model, not an Ollama tag:\n" + "\n".join(hits)


def test_the_vision_fallback_is_an_mlx_checkpoint():
    from services.llm_locker import _get_default_vision_model

    assert "/" in _get_default_vision_model() and ":" not in _get_default_vision_model()
