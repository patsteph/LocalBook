"""Write the Jupyter golden fixture the Swift port is tested against.

The old app reads a notebook with nbformat (``DocumentProcessor._extract_from_jupyter``): cell markers, Markdown
sources, code in fences, stream outputs and plain-text results. ``JupyterText`` in LocalBookCore does the same from
the JSON. Run from ``backend/``:

    python ../scripts/ci/jupyter_golden.py ../swift/LocalBookCore/Tests/LocalBookCoreTests/Fixtures/jupyter_golden.json
"""
import asyncio
import json
import sys

sys.path.insert(0, ".")
from services.document_processor import DocumentProcessor  # noqa: E402

dp = DocumentProcessor()
cases = []


def nb(cells):
    return {"nbformat": 4, "nbformat_minor": 5, "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3"}}, "cells": cells}


def md(source, **k):
    return {"cell_type": "markdown", "metadata": {}, "source": source, **k}


def code(source, outputs=(), **k):
    return {"cell_type": "code", "metadata": {}, "execution_count": 1, "source": source, "outputs": list(outputs), **k}


def case(name, notebook):
    text = json.dumps(notebook, indent=1)
    out = asyncio.run(dp._extract_from_jupyter(text.encode("utf-8")))
    cases.append({"name": name, "notebook": text, "text": out})


case("markdown and code", nb([
    md("# Title\n\nSome *words*."),
    code("x = 1\nprint(x)", [{"output_type": "stream", "name": "stdout", "text": "1\n"}]),
]))
case("sources as line lists", nb([
    md(["# Heading\n", "\n", "Second line."]),
    code(["import math\n", "math.pi"], [{"output_type": "execute_result", "execution_count": 1, "data": {"text/plain": ["3.141592653589793"]}, "metadata": {}}]),
]))
case("outputs of every kind", nb([
    code("run()", [
        {"output_type": "stream", "name": "stderr", "text": ["warning line 1\n", "warning line 2\n"]},
        {"output_type": "display_data", "data": {"image/png": "iVBOR", "text/plain": "<Figure>"}, "metadata": {}},
        {"output_type": "execute_result", "execution_count": 2, "data": {"text/html": "<b>x</b>", "text/plain": "42"}, "metadata": {}},
        {"output_type": "error", "ename": "ValueError", "evalue": "bad", "traceback": ["Traceback"]},
    ]),
]))
case("raw and empty cells", nb([
    {"cell_type": "raw", "metadata": {}, "source": "raw text"},
    md(""),
    code("", []),
    md("Last."),
]))
case("unicode and quotes", nb([
    md("Café — “quotes” 日本語"),
    code("s = 'it''s'\nprint(s)", [{"output_type": "stream", "name": "stdout", "text": "its\n"}]),
]))

with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump({"cases": cases}, f, indent=1, ensure_ascii=False)
    f.write("\n")
