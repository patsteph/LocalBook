"""Text extraction must never run on the event loop.

2026-09-16 field report: "the app is failing to launch." The backend was up and
had never restarted — it was *frozen*. A @research deep-dive over ten arXiv PDFs
produced repeated `[loop-monitor] event loop stalled 18.7s / 19.0s / 23.9s`
warnings, and `loop_watchdog`'s fatal dump named the exact frame:

    services/web_scraper.py:272   _scrape_arxiv_pdf
    services/document_processor.py:263  _extract_from_pdf
    pymupdf4llm ... to_markdown → table.extract_cells   ← on the loop thread

Seventeen extractors were written as `async def` with no `await` anywhere in the
body. That reads as asynchronous and behaves as a hard block: while one runs,
the single event loop is frozen — `/health` with it — and the Tauri watchdog
eventually decides the backend is dead. CLAUDE.md names this the #1 cause of
watchdog restarts; this is that bug, in seventeen places.

Two properties are asserted here, and both matter:

  1. Every CPU-bound extractor is decorated `@off_loop`. A NEW extractor added
     without it fails this test rather than shipping a fresh loop-freeze.
  2. `@off_loop` refuses to run a body that awaits — because the decorator is
     only sound for code that touches no event-loop state.
"""
import ast
import asyncio
import inspect
from pathlib import Path

import pytest

from services.document_processor import DocumentProcessor, document_processor, off_loop

_SOURCE = Path(inspect.getfile(DocumentProcessor)).read_text()


def _extractor_bodies():
    """Name → (is_async, awaits_anything), read from the ORIGINAL source.

    Read from source, not from the live objects: `functools.wraps` makes the
    decorated wrapper indistinguishable by introspection, which is exactly what
    would let a regression hide.
    """
    tree = ast.parse(_SOURCE)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef)
               and n.name == "DocumentProcessor")
    out = {}
    for fn in cls.body:
        if not isinstance(fn, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        if not fn.name.startswith("_extract_from"):
            continue
        awaits = any(isinstance(n, (ast.Await, ast.AsyncWith, ast.AsyncFor))
                     for n in ast.walk(fn))
        decorated = any(getattr(d, "id", "") == "off_loop" for d in fn.decorator_list)
        out[fn.name] = (isinstance(fn, ast.AsyncFunctionDef), awaits, decorated)
    return out


def test_every_cpu_bound_extractor_is_moved_off_the_loop():
    offenders = [
        name for name, (is_async, awaits, decorated) in _extractor_bodies().items()
        if is_async and not awaits and not decorated
    ]
    assert not offenders, (
        f"{offenders} are `async def` with no await — they run on the event loop "
        f"and freeze the whole backend while they work. Decorate with @off_loop."
    )


def test_the_pdf_path_that_froze_the_app_is_covered():
    """The specific regression. Named explicitly so it cannot be lost in a
    refactor of the generic check above."""
    kinds = _extractor_bodies()
    assert kinds["_extract_from_pdf"][2], "_extract_from_pdf must stay @off_loop"
    assert kinds["_extract_from_audio"][2], (
        "audio transcription is minutes of CPU — on the loop it is a guaranteed "
        "freeze, and linked folders ingest recordings"
    )


def test_an_extractor_that_awaits_keeps_its_own_coroutine():
    """`heic` and the fallback DO await (they reach llm_service). They must NOT
    be decorated — `off_loop` would run loop-bound work on a foreign thread."""
    kinds = _extractor_bodies()
    for name in ("_extract_from_heic",):
        is_async, awaits, decorated = kinds[name]
        assert awaits and not decorated, f"{name} awaits — it must stay on the loop"


# ── the decorator's own contract ────────────────────────────────────────────

def test_off_loop_returns_the_value_and_runs_on_another_thread():
    import threading
    seen = {}

    @off_loop
    async def work(x):
        seen["thread"] = threading.current_thread().name
        return x * 2

    async def main():
        seen["caller"] = threading.current_thread().name
        return await work(21)

    assert asyncio.run(main()) == 42
    assert seen["thread"] != seen["caller"], "the body ran on the event loop thread"


def test_off_loop_propagates_exceptions_unchanged():
    @off_loop
    async def boom():
        raise ValueError("no text content could be extracted")

    with pytest.raises(ValueError, match="no text content"):
        asyncio.run(boom())


def test_off_loop_refuses_a_body_that_awaits():
    """The guard that keeps the decorator honest. If someone adds an `await` to
    a decorated extractor, it must fail loudly here — not quietly run loop-bound
    work on a worker thread with no loop of its own."""
    @off_loop
    async def sneaky():
        await asyncio.sleep(0)
        return "nope"

    with pytest.raises(RuntimeError, match="awaited something"):
        asyncio.run(sneaky())


def test_the_real_extractors_still_extract():
    """End to end through the decorator, on a format with no heavy dependency."""
    import json
    nb = json.dumps({
        "nbformat": 4, "nbformat_minor": 5, "metadata": {},
        "cells": [{"cell_type": "markdown", "metadata": {},
                   "source": ["# Hello from a notebook"]}],
    }).encode()
    out = asyncio.run(document_processor._extract_from_jupyter(nb))
    assert "Hello from a notebook" in out
