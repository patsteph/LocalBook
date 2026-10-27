"""Stored chunks over the shared token limit: near ones re-embedded, long ones split."""
import asyncio

import lancedb
import pyarrow as pa

from services import chunk_limit_migration as clm
from services import token_budget

MODEL = "mlx-community/embeddinggemma-2-bf16"


def _table(tmp_path, rows):
    schema = pa.schema([("vector", pa.list_(pa.float32(), 4)), ("text", pa.string()),
                        ("parent_text", pa.string()), ("synthetic_questions", pa.string()),
                        ("source_id", pa.string()), ("chunk_index", pa.int64()),
                        ("filename", pa.string()), ("source_type", pa.string())])
    db = lancedb.connect(str(tmp_path))
    return db, db.create_table("notebook_t", data=pa.Table.from_pylist(rows, schema=schema), schema=schema)


def _row(i, text):
    return {"vector": [1.0, 0, 0, 0], "text": text, "parent_text": text, "synthetic_questions": "q?",
            "source_id": "s", "chunk_index": i, "filename": "f", "source_type": "pdf"}


def test_long_rows_split_with_first_piece_keeping_the_index(tmp_path, monkeypatch):
    monkeypatch.setattr(token_budget, "token_counter", lambda m: (lambda t: len(t) // 2 + 1))
    db, table = _table(tmp_path, [_row(0, "short"), _row(1, "word. " * 600)])
    plan = clm.plan_table(table, MODEL)
    assert [p["kind"] for p in plan] == ["split"] and len(plan[0]["pieces"]) > 1

    async def embed(texts):
        return [[0.0, 1.0, 0.0, 0.0] for _ in texts]
    written = asyncio.run(clm.apply_table(db, "notebook_t", plan, MODEL, embed))
    rows = sorted(db.open_table("notebook_t").to_arrow().to_pylist(), key=lambda r: r["chunk_index"])
    assert written == len(plan[0]["pieces"]) and len({r["chunk_index"] for r in rows}) == len(rows)
    assert rows[1]["synthetic_questions"] == "q?" and rows[2]["synthetic_questions"] == ""
    assert clm.plan_table(db.open_table("notebook_t"), MODEL) == [] or all(
        p["kind"] == "near" for p in clm.plan_table(db.open_table("notebook_t"), MODEL))
