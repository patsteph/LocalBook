"""MLX in-process engine adapter (Wave 9 — dual-engine Ollama|MLX).

Wired into the `llm_service` seam per role via `config.*_engine` (default "ollama").
- **Fast** (phi) → `mlx-lm` (Wave 9.1).
- **Main** (gemma) → `mlx-vlm` text path (Wave 9.2) — the SAME load that serves vision
  (9.3) and structured (9.2b), so gemma is loaded ONCE (the ~½-RAM win). We never load
  gemma on both mlx-lm and mlx-vlm.

Design invariants:
- **Ollama-shaped output.** generate() returns / stream_generate() yields the SAME dict
  shape the Ollama path emits (`response` / `message.content` / `prompt_eval_count` /
  `eval_count` / `eval_duration`), so callers + `_record_ollama_tokens` are unchanged.
- **Everything lazy.** mlx_lm / mlx_vlm imported inside methods — importing this module
  (and the whole backend) works with or without the MLX deps present.
- **Memory safety.** `mx.set_memory_limit` cap + a per-model lock (serialize the heavy
  model) + the single-engine-per-family invariant (loading MLX gemma evicts Ollama gemma).
"""
from __future__ import annotations

import asyncio
import functools
import logging
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ─── Role → engine resolver (grows per wave) ─────────────────────────────────────
def mlx_model_for_role(ollama_model: str) -> Optional[str]:
    """If the role that `ollama_model` fills is configured `engine == "mlx"`, return the
    MLX model id to use in its place; else None (→ stay on Ollama). The single decision
    point the llm_service seam consults. Fast (9.1) + main (9.2); vision is resolved in
    the vision_describe path (9.3)."""
    try:
        from config import settings
    except Exception:
        return None
    if ollama_model == settings.ollama_fast_model and getattr(settings, "fast_engine", "ollama") == "mlx":
        return settings.mlx_fast_model
    if ollama_model == settings.ollama_model and getattr(settings, "main_engine", "ollama") == "mlx":
        return settings.mlx_main_model
    return None


def mlx_vision_model_if_enabled() -> Optional[str]:
    """Return the MLX vision model id iff `vision_engine == "mlx"`, else None. Vision has its
    own engine flag (Option A: it rides the gemma main model, but the toggle is independent)."""
    try:
        from config import settings
    except Exception:
        return None
    if getattr(settings, "vision_engine", "ollama") == "mlx":
        return settings.mlx_vision_model
    return None


def _combine(system: Optional[str], prompt: str) -> str:
    return f"{system}\n\n{prompt}" if system else prompt


# ─── Vision-only sanitize shim (Wave 9.3) — proven in the doc-26 spike ───────────
# The mlx-community Gemma-4 E4B checkpoint bundles an audio tower whose conv-weight
# layout trips mlx-vlm's loader (a bug in an audio path LocalBook never uses). Nulling
# `audio_config` + dropping the audio weights loads a clean VISION+TEXT-only model.
_AUDIO_WEIGHT_PREFIXES = ("audio_tower", "embed_audio")


def install_gemma_vision_only_shim() -> None:
    """Monkeypatch mlx_vlm's gemma4 Model.sanitize to drop audio-tower weights. Idempotent."""
    try:
        from mlx_vlm.models.gemma4 import gemma4 as _g4  # lazy
    except Exception as e:
        logger.debug(f"[mlx-engine] vision shim skipped ({e})")
        return
    if getattr(_g4.Model.sanitize, "_lb_vision_only", False):
        return
    _orig = _g4.Model.sanitize

    def _sanitize_no_audio(self, weights):
        w = _orig(self, weights)
        return {k: v for k, v in w.items() if not k.startswith(_AUDIO_WEIGHT_PREFIXES)}

    _sanitize_no_audio._lb_vision_only = True  # type: ignore[attr-defined]
    _g4.Model.sanitize = _sanitize_no_audio  # type: ignore[assignment]


def load_gemma_vision_only(model_id_or_path: str):
    """Load a Gemma-4 VLM checkpoint text+vision-only (audio tower skipped)."""
    from mlx_vlm.utils import get_model_path  # lazy
    import mlx_vlm  # lazy
    import json as _json

    install_gemma_vision_only_shim()
    path = Path(get_model_path(model_id_or_path))
    cfgp = path / "config.json"
    bak = path / "config.json.lb_vision_only.bak"
    if not bak.exists():
        shutil.copy2(cfgp, bak)
    try:
        cfg = _json.load(open(cfgp))
        if cfg.get("audio_config") is not None:
            cfg["audio_config"] = None
            _json.dump(cfg, open(cfgp, "w"))
        return mlx_vlm.load(str(path))
    finally:
        if bak.exists():
            os.replace(str(bak), str(cfgp))


# ─── Ollama-shaped result helper (the seam contract) ─────────────────────────────
def _ollama_shaped_generate_result(
    text: str, *, prompt_tokens: int = 0, eval_tokens: int = 0, eval_ns: int = 0,
    model: str = "",
) -> Dict[str, Any]:
    return {
        "response": text,
        "message": {"role": "assistant", "content": text},
        "model": model,
        "done": True,
        "prompt_eval_count": prompt_tokens,
        "eval_count": eval_tokens,
        "eval_duration": eval_ns,
    }


# ─── Grammar-constrained JSON (Wave 9.6, Path B) ─────────────────────────────────
# MLX has no native `format=json` grammar like Ollama, so JSON used to be only prompt-nudged
# → gemma-MLX returned blank/truncated/mis-typed JSON on demanding structured outputs (empty
# visuals, the chat 'list'.lower crash). mlx-vlm ships `build_json_schema_logits_processor`
# (backed by llguidance, a single Rust wheel) which FORCES tokens to a JSON schema — the same
# guarantee Ollama's JSON mode gives. Applied via the `logits_processors` hook both mlx-lm and
# mlx-vlm honor. Permissive default ({"type":"object"}) guarantees a valid JSON object even when
# the caller has no explicit schema.
_PERMISSIVE_JSON_SCHEMA: Dict[str, Any] = {"type": "object"}


def _raw_fast_tokenizer(tok_or_proc):
    """llguidance needs a HF *fast* tokenizer (is_fast=True). The right object differs by engine:
    mlx-vlm's `processor.tokenizer` (GemmaTokenizer) is ALREADY fast; mlx-lm's `TokenizerWrapper`
    needs `._tokenizer` (the TokenizersBackend). Probe candidates and return the first fast one."""
    proc_tok = getattr(tok_or_proc, "tokenizer", None)      # mlx-vlm processor → GemmaTokenizer
    candidates = [
        proc_tok,                                           # vlm: already fast
        tok_or_proc,                                        # a bare tokenizer
        getattr(tok_or_proc, "_tokenizer", None),           # mlx-lm wrapper → TokenizersBackend
        getattr(proc_tok, "_tokenizer", None),
    ]
    for c in candidates:
        if c is not None and getattr(c, "is_fast", False):
            return c
    return proc_tok or tok_or_proc                          # best-effort (will raise in llguidance → nudge fallback)


def _json_logits_processor(tok_or_proc, schema):
    """Build a fresh (stateful) JSON-schema logits processor, or None if llguidance is
    unavailable (→ caller keeps the prompt-nudge fallback). Never raises."""
    try:
        from mlx_vlm.structured import build_json_schema_logits_processor  # lazy
        raw = _raw_fast_tokenizer(tok_or_proc)
        return build_json_schema_logits_processor(raw, schema or _PERMISSIVE_JSON_SCHEMA)
    except Exception as e:
        logger.warning(f"[mlx-engine] grammar-constrained JSON unavailable ({type(e).__name__}: {e}); "
                       f"using prompt-nudge fallback")
        return None


# ─── Blocking generation helpers (run via thread) ────────────────────────────────
def _lm_generate_sync(model, tokenizer, prompt_str, *, max_tokens, temperature, stop,
                      logits_processors=None):
    """mlx-lm non-streaming (accumulate). Returns (text, prompt_tokens, gen_tokens, gen_ns).
    gen_ns is decode-only time (first→last token) for tokens/sec parity with Ollama."""
    from mlx_lm import stream_generate  # lazy
    kwargs: Dict[str, Any] = {"max_tokens": max_tokens}
    if temperature is not None:
        from mlx_lm.sample_utils import make_sampler
        kwargs["sampler"] = make_sampler(temp=max(float(temperature), 0.0))
    if logits_processors:
        kwargs["logits_processors"] = logits_processors  # grammar-constrained JSON
    text = ""
    ptoks = gtoks = 0
    t_first = None
    for resp in stream_generate(model, tokenizer, prompt_str, **kwargs):
        if t_first is None:
            t_first = time.perf_counter()
        text += resp.text
        ptoks = getattr(resp, "prompt_tokens", ptoks) or ptoks
        gtoks = getattr(resp, "generation_tokens", gtoks) or gtoks
        if stop:
            cut = min((text.find(s) for s in stop if s and s in text), default=-1)
            if cut != -1:
                return text[:cut], ptoks, gtoks, _since(t_first)
    return text, ptoks, gtoks, _since(t_first)


def _vlm_generate_sync(model, processor, config, prompt_str, *, max_tokens, stop,
                       logits_processors=None):
    """mlx-vlm text-only non-streaming (gemma). Returns (text, prompt_tokens, gen_tokens, gen_ns)."""
    from mlx_vlm import stream_generate  # lazy
    from mlx_vlm.prompt_utils import apply_chat_template
    formatted = apply_chat_template(processor, config, prompt_str, num_images=0)
    vkwargs: Dict[str, Any] = {"image": [], "max_tokens": max_tokens}
    if logits_processors:
        vkwargs["logits_processors"] = logits_processors  # grammar-constrained JSON
    text = ""
    ptoks = gtoks = 0
    t_first = None
    for resp in stream_generate(model, processor, formatted, **vkwargs):
        if t_first is None:
            t_first = time.perf_counter()
        text += resp.text
        ptoks = getattr(resp, "prompt_tokens", ptoks) or ptoks
        gtoks = getattr(resp, "generation_tokens", gtoks) or gtoks
        if stop:
            cut = min((text.find(s) for s in stop if s and s in text), default=-1)
            if cut != -1:
                return text[:cut], ptoks, gtoks, _since(t_first)
    return text, ptoks, gtoks, _since(t_first)


def _since(t_first: Optional[float]) -> int:
    """Nanoseconds since the first-token timestamp (0 if no tokens produced)."""
    return int((time.perf_counter() - t_first) * 1e9) if t_first else 0


def _resolve_image(image_path_or_b64: str):
    """Callers pass either a filesystem path or a base64-encoded image (the Ollama vision
    convention). Return a path (as-is) or a decoded PIL Image for mlx-vlm."""
    s = image_path_or_b64
    try:
        if os.path.exists(s):
            return s
    except Exception:
        pass
    try:
        import base64 as _b64, io as _io
        from PIL import Image  # lazy
        return Image.open(_io.BytesIO(_b64.b64decode(s))).convert("RGB")
    except Exception:
        return s  # let mlx-vlm try to interpret it (URL/path)


def _vlm_vision_sync(model, processor, config, prompt_str, image, *, max_tokens):
    """mlx-vlm vision (gemma, one image). Returns (text, prompt_tokens, gen_tokens)."""
    from mlx_vlm import generate as vl_generate  # lazy
    from mlx_vlm.prompt_utils import apply_chat_template
    formatted = apply_chat_template(processor, config, prompt_str, num_images=1)
    out = vl_generate(model, processor, formatted, [image], max_tokens=max_tokens, verbose=False)
    if isinstance(out, str):
        return out, 0, 0
    text = getattr(out, "text", str(out))
    return text, getattr(out, "prompt_tokens", 0) or 0, getattr(out, "generation_tokens", 0) or 0


# ─── The engine ──────────────────────────────────────────────────────────────────
class MLXEngine:
    def __init__(self) -> None:
        self._resident: Dict[str, Any] = {}              # model_id -> (model, tokenizer/processor)
        self._vlm_config: Dict[str, Any] = {}            # model_id -> config (vlm only)
        self._kind: Dict[str, str] = {}                  # model_id -> "lm" | "vlm"
        self._model_locks: Dict[str, asyncio.Lock] = {}  # per-model serialization
        self._load_lock = asyncio.Lock()
        self._mem_limit_set = False
        # ALL MLX work (load + generate + stream producer) runs on this ONE thread.
        # mlx-lm uses thread-local GPU streams — if a model loads on pool-thread A and a
        # later generate runs on pool-thread B, MLX raises "There is no Stream(gpu, N) in
        # current thread" and the call fails (→ Ollama fallback, and the streaming eval
        # test scores 0). Pinning to a single worker keeps the thread-local stream
        # consistent. Serialization is fine: one GPU + the memory-safety invariant already
        # want one model computing at a time.
        self._exec = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mlx-engine")

    async def _run(self, fn, *args, **kwargs):
        """Run a blocking MLX callable on the single dedicated MLX thread."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._exec, functools.partial(fn, *args, **kwargs))

    @staticmethod
    def available() -> bool:
        try:
            import importlib.util
            return all(importlib.util.find_spec(m) is not None
                       for m in ("mlx", "mlx_lm", "mlx_vlm"))
        except Exception:
            return False

    # -- scheduler internals -----------------------------------------------------
    def _ensure_memory_limit(self) -> None:
        if self._mem_limit_set:
            return
        self._mem_limit_set = True
        try:
            import mlx.core as mx
            limit_gb = float(os.environ.get("LOCALBOOK_MLX_MEMORY_LIMIT_GB", "12"))
            mx.set_memory_limit(int(limit_gb * 1024 ** 3))
            logger.info(f"[mlx-engine] memory limit set to {limit_gb} GB")
        except Exception as e:
            logger.debug(f"[mlx-engine] could not set memory limit: {e}")

    def _model_kind(self, model_id: str) -> str:
        """'vlm' (mlx-vlm, gemma) or 'lm' (mlx-lm, phi). Cached. Cheap config.json fetch
        with a name heuristic fallback (never downloads the whole model)."""
        if model_id in self._kind:
            return self._kind[model_id]
        kind = "vlm" if "gemma" in model_id.lower() else "lm"
        try:
            from huggingface_hub import hf_hub_download
            import json as _json
            cfg = _json.load(open(hf_hub_download(model_id, "config.json")))
            kind = "vlm" if cfg.get("vision_config") is not None else "lm"
        except Exception:
            pass
        self._kind[model_id] = kind
        return kind

    def _evict_ollama_twin(self, mlx_model_id: str) -> None:
        """Single-engine-per-family invariant: evict the Ollama model this MLX one
        replaces (reboot-avoidance). Sync httpx — called inside the load thread."""
        try:
            from config import settings
            import httpx
            twin = None
            if mlx_model_id == getattr(settings, "mlx_main_model", None):
                twin = settings.ollama_model
            elif mlx_model_id == getattr(settings, "mlx_vision_model", None):
                twin = settings.vision_model
            if twin:
                httpx.post(f"{settings.ollama_base_url}/api/generate",
                           json={"model": twin, "prompt": "", "keep_alive": 0}, timeout=10.0)
                logger.info(f"[mlx-engine] evicted Ollama twin '{twin}' for {mlx_model_id}")
        except Exception as e:
            logger.debug(f"[mlx-engine] evict twin skipped: {e}")

    async def _load(self, model_id: str) -> Tuple[Any, Any]:
        """Load (cache) an MLX model — mlx-vlm for gemma, mlx-lm for phi. Loads run
        one-at-a-time; gemma load evicts the Ollama twin first."""
        if model_id in self._resident:
            return self._resident[model_id]
        kind = self._model_kind(model_id)
        async with self._load_lock:
            if model_id in self._resident:
                return self._resident[model_id]
            self._ensure_memory_limit()
            logger.info(f"[mlx-engine] loading {model_id} ({kind}) …")
            t0 = time.perf_counter()

            def _load():
                if kind == "vlm":
                    self._evict_ollama_twin(model_id)
                    from mlx_vlm.utils import get_model_path, load_config
                    pair = load_gemma_vision_only(model_id)
                    self._vlm_config[model_id] = load_config(str(get_model_path(model_id)))
                    return pair
                from mlx_lm import load
                return load(model_id)

            pair = await self._run(_load)
            self._resident[model_id] = pair
            logger.info(f"[mlx-engine] loaded {model_id} in {time.perf_counter() - t0:.1f}s")
            return pair

    # -- text / structured (fast 9.1 · main 9.2 · structured 9.2b) ---------------
    async def generate(
        self, prompt: str, *, model: str, system: Optional[str] = None,
        temperature: float = 0.3, num_predict: int = 500, num_ctx: Optional[int] = None,
        format: Optional[str] = None, stop: Optional[List[str]] = None,
        images: Optional[List[str]] = None, **kwargs: Any,
    ) -> Dict[str, Any]:
        """Non-streaming text generate → Ollama-shaped dict. Routes gemma→mlx-vlm, phi→mlx-lm."""
        kind = self._model_kind(model)
        pair = await self._load(model)
        lock = self._model_locks.setdefault(model, asyncio.Lock())
        # Grammar-constrained JSON (Path B): force schema-compliant JSON via llguidance — but ONLY
        # when the caller passes an explicit `json_schema`. A permissive `{"type":"object"}` grammar
        # is a trap: the model can satisfy it with an empty `{}` and skip every field, which broke the
        # schema-less idiom picker (it got `{}` → failed → fell to the template path). So schema-less
        # `format=json` keeps the prompt-nudge (its long-proven behaviour); grammar is opt-in per schema.
        lps = None
        if format == "json":
            schema = kwargs.get("json_schema")
            if schema:
                lp = _json_logits_processor(pair[1], schema)
                if lp is not None:
                    lps = [lp]
            if lps is None:
                prompt = f"{prompt}\n\nOutput ONLY valid JSON — no prose, no markdown code fences."
        t0 = time.perf_counter()
        async with lock:
            if kind == "vlm":
                mobj, processor = pair
                cfg = self._vlm_config.get(model)
                text, ptoks, gtoks, gen_ns = await self._run(
                    _vlm_generate_sync, mobj, processor, cfg, _combine(system, prompt),
                    max_tokens=num_predict, stop=stop, logits_processors=lps)
            else:
                mobj, tok = pair
                messages = ([{"role": "system", "content": system}] if system else []) + \
                           [{"role": "user", "content": prompt}]
                try:
                    prompt_str = tok.apply_chat_template(messages, add_generation_prompt=True, tokenize=False)
                except Exception:
                    prompt_str = _combine(system, prompt)
                text, ptoks, gtoks, gen_ns = await self._run(
                    _lm_generate_sync, mobj, tok, prompt_str,
                    max_tokens=num_predict, temperature=temperature, stop=stop, logits_processors=lps)
        # Prefer decode-only time (Ollama parity for tokens/sec); fall back to total wall-clock.
        eval_ns = gen_ns or int((time.perf_counter() - t0) * 1e9)
        return _ollama_shaped_generate_result(
            text, prompt_tokens=ptoks, eval_tokens=gtoks, eval_ns=eval_ns, model=model)

    async def stream_generate(
        self, prompt: str, *, model: str, system: Optional[str] = None,
        temperature: float = 0.3, num_predict: int = 500, num_ctx: Optional[int] = None,
        stop: Optional[List[str]] = None, **kwargs: Any,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Streaming text generate → yields Ollama-shaped chunks. Wave 9.2 (main/gemma via
        mlx-vlm; fast/phi via mlx-lm). Bridges the blocking MLX generator to async via an
        asyncio.Queue fed with call_soon_threadsafe (no per-token thread round-trip)."""
        kind = self._model_kind(model)
        pair = await self._load(model)
        lock = self._model_locks.setdefault(model, asyncio.Lock())
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        _SENTINEL = object()

        def _producer():
            try:
                if kind == "vlm":
                    mobj, processor = pair
                    cfg = self._vlm_config.get(model)
                    from mlx_vlm import stream_generate as _sg
                    from mlx_vlm.prompt_utils import apply_chat_template
                    formatted = apply_chat_template(processor, cfg, _combine(system, prompt), num_images=0)
                    gen = _sg(mobj, processor, formatted, image=[], max_tokens=num_predict)
                else:
                    mobj, tok = pair
                    from mlx_lm import stream_generate as _sg
                    from mlx_lm.sample_utils import make_sampler
                    messages = ([{"role": "system", "content": system}] if system else []) + \
                               [{"role": "user", "content": prompt}]
                    try:
                        prompt_str = tok.apply_chat_template(messages, add_generation_prompt=True, tokenize=False)
                    except Exception:
                        prompt_str = _combine(system, prompt)
                    gen = _sg(mobj, tok, prompt_str, max_tokens=num_predict,
                              sampler=make_sampler(temp=max(float(temperature), 0.0)))
                acc = ""
                ptoks = gtoks = 0
                t_first = None          # decode start = first token (parity with Ollama eval_duration)
                for resp in gen:
                    if t_first is None:
                        t_first = time.perf_counter()
                    tok_text = resp.text
                    acc += tok_text
                    ptoks = getattr(resp, "prompt_tokens", ptoks) or ptoks
                    gtoks = getattr(resp, "generation_tokens", gtoks) or gtoks
                    if stop:
                        cut = min((acc.find(s) for s in stop if s and s in acc), default=-1)
                        if cut != -1:
                            # emit only the part before the stop marker in this token
                            keep = tok_text[: max(0, len(tok_text) - (len(acc) - cut))]
                            if keep:
                                loop.call_soon_threadsafe(q.put_nowait, {"response": keep, "done": False})
                            break
                    loop.call_soon_threadsafe(q.put_nowait, {"response": tok_text, "done": False})
                # eval_duration = generation-only ns (first→last token), matching Ollama's field so
                # tokens/sec computes identically across engines (was hardcoded 0 → blank MLX stats).
                gen_ns = int((time.perf_counter() - t_first) * 1e9) if t_first else 0
                loop.call_soon_threadsafe(q.put_nowait, {
                    "response": "", "done": True,
                    "prompt_eval_count": ptoks, "eval_count": gtoks, "eval_duration": gen_ns})
            except Exception as e:
                loop.call_soon_threadsafe(q.put_nowait, {"__error__": f"{type(e).__name__}: {e}"})
            finally:
                loop.call_soon_threadsafe(q.put_nowait, _SENTINEL)

        async with lock:
            fut = loop.run_in_executor(self._exec, _producer)
            try:
                while True:
                    item = await q.get()
                    if item is _SENTINEL:
                        break
                    if "__error__" in item:
                        raise RuntimeError(item["__error__"])
                    yield item
            finally:
                await fut

    # -- vision (Wave 9.3) -------------------------------------------------------
    async def vision_describe(
        self, image_path_or_b64: str, prompt: str, *, model: str,
        system: Optional[str] = None, num_predict: int = 400, **kwargs: Any,
    ) -> Dict[str, Any]:
        """Semantic image description via mlx-vlm (gemma, vision-only). Same one gemma load
        as text/structured. Accepts a path or base64. → Ollama-shaped dict."""
        pair = await self._load(model)          # vlm (gemma)
        mobj, processor = pair
        cfg = self._vlm_config.get(model)
        img = _resolve_image(image_path_or_b64)
        lock = self._model_locks.setdefault(model, asyncio.Lock())
        t0 = time.perf_counter()
        async with lock:
            text, ptoks, gtoks = await self._run(
                _vlm_vision_sync, mobj, processor, cfg, _combine(system, prompt), img,
                max_tokens=num_predict)
        dur_ns = int((time.perf_counter() - t0) * 1e9)
        return _ollama_shaped_generate_result(
            text, prompt_tokens=ptoks, eval_tokens=gtoks, eval_ns=dur_ns, model=model)

    # -- embeddings (deferred — stays Ollama for v2.1.0, re-index-gated) ----------
    async def embed(self, texts: List[str], *, model: str, **kwargs: Any) -> List[List[float]]:
        raise NotImplementedError("MLX embeddings are deferred (re-index-gated); stays Ollama")


mlx_engine = MLXEngine()
