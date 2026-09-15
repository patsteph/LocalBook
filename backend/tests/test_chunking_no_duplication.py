"""Chunking must index each piece of content exactly once, and must not lose any of it.

Two bugs found on 2026-09-14 while chasing a user report of slow PDF ingestion. Both were
silent: ingestion succeeded, the notebook looked fine, and the damage only showed up as
"a bit slow" and as questions the assistant could not answer about text that was plainly in
the document.

1. **Duplication.** `chunk_hierarchical` emitted every level-1 AND level-2 chunk, so a section
   and its own paragraphs both went into the store. On the test PDF, 15 of 16 paragraph chunks
   were contained verbatim in a section chunk — 4,239 chars indexed from a 2,901-char document.
   Cost: ~2x the embedding calls, and duplicate text competing for the five retrieval slots.

2. **Content loss.** `_detect_sections` consumed a heading line into `section_title` and never
   put it in the body, and when a heading immediately followed another heading it hit
   `if current_content:` while still empty — discarding the previous section WITH its title.
   The chunker's `^\d+\.\s+([A-Z].+)$` pattern classifies the steps of a numbered LIST as
   headings, so a five-step list lost steps 1-4 entirely. The text existed in the document and
   could never be retrieved.

Measured before → after on `evaluator/test_content/test_document.pdf`:

    chunks            20  →  7
    indexed/source  1.46x →  0.98x
    duplicated        15  →  0
    gold markers lost  5  →  0
"""
import json
import re
from pathlib import Path

import pytest

from services import rag_chunking
from services.hierarchical_chunker import HierarchicalChunker

BACKEND = Path(__file__).resolve().parents[1]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


# ── The heading bug, in isolation ───────────────────────────────────────────

def test_consecutive_headings_are_not_discarded():
    """THE numbered-list bug. Five consecutive lines that all match the numbered-section
    pattern: every one must survive somewhere, not just the last."""
    text = (
        "Pipeline steps\n\n"
        "1. Embeds the query using the embedding model\n"
        "2. Searches the vector store for relevant chunks\n"
        "3. Reranks results using cross-encoder scoring\n"
        "4. Builds a context window from the token budget\n"
        "5. Generates an answer using the main model\n\n"
        "That is the whole pipeline.\n"
    )
    sections = HierarchicalChunker()._detect_sections(text)
    body = _norm(" ".join(title + " " + content for title, content in sections))

    for step in ["embeds the query", "searches the vector store",
                 "reranks results", "builds a context window",
                 "generates an answer"]:
        assert step in body, f"lost from the section split: {step!r}"


def test_a_heading_line_survives_into_the_body():
    """A heading consumed only into `section_title` never reaches the index, because the index
    stores chunk TEXT."""
    sections = HierarchicalChunker()._detect_sections("# Overview\n\nSome body text here.\n")
    combined = _norm(" ".join(c for _, c in sections))
    assert "overview" in combined


# ── Leaf selection: no text indexed twice ───────────────────────────────────

def test_a_section_and_its_own_paragraphs_are_not_both_indexed():
    text = (
        "# Section One\n\n"
        + "Paragraph one about embeddings and vectors. " * 8 + "\n\n"
        + "Paragraph two about retrieval and ranking. " * 8 + "\n\n"
        "# Section Two\n\n"
        + "Paragraph three about generation and answers. " * 8 + "\n"
    )
    chunks = rag_chunking.chunk_hierarchical(text, "doc.pdf")
    bodies = [_norm(c) for c in chunks]
    for i, a in enumerate(bodies):
        for j, b in enumerate(bodies):
            if i != j and len(a) > 100:
                assert a[:100] not in b, (
                    f"chunk {i} is contained in chunk {j} — the same text is indexed twice"
                )


def test_chunks_are_not_fragments():
    """20 chunks averaging 213 chars against a chunk_size of 1000 gave the reranker fragments
    and cost an embedding each."""
    from config import settings

    text = ""
    for i in range(12):
        text += f"# Section {i}\n\nShort body for section {i}. It is deliberately brief.\n\n"
    chunks = rag_chunking.chunk_hierarchical(text, "doc.pdf")
    assert chunks, "produced nothing"
    mean = sum(len(c) for c in chunks) / len(chunks)
    assert mean > settings.chunk_size * 0.25, (
        f"mean chunk {mean:.0f} chars is still fragmentary against chunk_size "
        f"{settings.chunk_size}"
    )


def test_merging_respects_the_size_target():
    from config import settings

    text = ""
    for i in range(10):
        text += f"# Section {i}\n\n" + (f"Body text for section {i}. " * 20) + "\n\n"
    chunks = rag_chunking.chunk_hierarchical(text, "doc.pdf")
    # Allow a margin: a single leaf larger than the target is emitted whole rather than split
    # mid-sentence, and the tail fold-back can exceed it slightly.
    assert max(len(c) for c in chunks) <= settings.chunk_size * 2.0


# ── End to end on the real corpus ───────────────────────────────────────────

def test_the_committed_pdf_loses_no_gold_content():
    """The gold markers are verbatim substrings of the corpus, which makes them an exact
    content-preservation check: every one that is in the document must be in the index."""
    pytest.importorskip("fitz", reason="pymupdf not installed")
    import asyncio
    import inspect

    from services.document_processor import document_processor

    pdf = BACKEND / "evaluator" / "test_content" / "test_document.pdf"
    if not pdf.exists():
        pytest.skip("test corpus not generated")

    text = document_processor._extract_from_pdf(pdf.read_bytes())
    if inspect.iscoroutine(text):
        text = asyncio.run(text)

    chunks = rag_chunking.chunk_text_smart(text, "pdf", "test_document.pdf")
    joined = _norm(" ".join(chunks))

    gold = json.loads(
        (BACKEND / "evaluator" / "test_fixtures" / "retrieval_gold.json").read_text()
    )
    in_doc = [
        m for q in gold["questions"] for m in q["markers"] if _norm(m) in _norm(text)
    ]
    lost = [m for m in in_doc if _norm(m) not in joined]
    assert not lost, f"content present in the PDF never reached the index: {lost}"


def test_the_committed_pdf_is_not_over_indexed():
    """Indexing more characters than the document contains means the same text is in the store
    more than once."""
    pytest.importorskip("fitz", reason="pymupdf not installed")
    import asyncio
    import inspect

    from services.document_processor import document_processor

    pdf = BACKEND / "evaluator" / "test_content" / "test_document.pdf"
    if not pdf.exists():
        pytest.skip("test corpus not generated")

    text = document_processor._extract_from_pdf(pdf.read_bytes())
    if inspect.iscoroutine(text):
        text = asyncio.run(text)

    chunks = rag_chunking.chunk_text_smart(text, "pdf", "test_document.pdf")
    ratio = sum(len(c) for c in chunks) / len(text)
    assert ratio < 1.15, f"indexed {ratio:.2f}x the source — duplication has returned"
