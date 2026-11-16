"""Write the chunking golden fixture the Swift port is tested against.

Runs the old app's own chunker (``services.rag_chunking``) and token-budget splitter
(``services.token_budget``) over a fixed set of texts and records what they produce, so
``swift/LocalBookCore/Tests/LocalBookCoreTests/ChunkerTests.swift`` can prove the Swift
``Chunker`` makes the same pieces. Run from ``backend/``:

    python ../scripts/ci/chunk_golden.py ../swift/LocalBookCore/Tests/LocalBookCoreTests/Fixtures/chunk_golden.json

(The chunker prints progress lines, so the fixture goes to the path given, not to stdout.)

The token counter used for the budget cases is the deliberate fallback (two characters per
token, plus one), the same estimate both sides use when the real tokenizer is not at hand.
"""
import json
import random
import sys

sys.path.insert(0, ".")
from services import rag_chunking, token_budget  # noqa: E402
from config import settings  # noqa: E402

random.seed(7)
WORDS = ("retrieval augmented generation keeps answers grounded in the user's own sources while the model "
         "writes fluently about them chunk size reranking evaluation latency memory tokens embeddings "
         "citations notebook passages questions synthesis contradictions timeline entities").split()


def prose(sentences, words_per=14, seed=1):
    r = random.Random(seed)
    out = []
    for _ in range(sentences):
        ws = [r.choice(WORDS) for _ in range(r.randint(6, words_per))]
        ws[0] = ws[0].capitalize()
        out.append(" ".join(ws) + r.choice([".", ".", ".", "!", "?"]))
    return " ".join(out)


cases = []


def case(name, source_type, filename, text):
    chunks = rag_chunking._chunk_text_by_type(text, source_type, filename)
    count = lambda t: int(len(t) / 2.0) + 1
    budget = 512 - token_budget.ENRICHMENT_RESERVE_TOKENS
    budgeted = []
    for c in chunks:
        budgeted.extend([c] if count(c) <= budget else token_budget.split_to_budget(c, budget, count))
    parents = [rag_chunking.get_parent_context(chunks, i, 2000) for i in range(len(chunks))]
    cases.append({"name": name, "source_type": source_type, "filename": filename, "text": text,
                  "chunks": chunks, "budgeted": budgeted, "parents": parents})


# 1. Short note: one chunk.
case("short note", "note", "note.txt", "Hello world.\n\nSecond paragraph, still short.")

# 2. Plain prose, several paragraphs, one over the chunk size, one sentence with no breaks at all.
paras = [prose(5, seed=i) for i in range(6)]
paras.insert(3, prose(60, seed=99))                       # one paragraph far over 1000 characters
paras.append("x" * 2600)                                  # one unbreakable run: character split with overlap
paras.append(prose(4, seed=5))
case("plain prose with a long paragraph", "txt", "essay.txt", "\n\n".join(paras))

# 3. Markdown text, under 2000 characters, so a .md goes the standard way even with headings.
case("short markdown", "md", "readme.md", "# Title\n\nIntro line.\n\n## Part A\n\n" + prose(6, seed=3) + "\n\n## Part B\n\n" + prose(5, seed=4))

# 4. A structured document (pdf) over 2000 characters: markdown headers, Title: lines, numbered steps,
#    page markers, an ALL CAPS heading, tiny paragraphs under 50 characters, and plain paragraphs.
doc = ["=== Page 1 ===", "# Overview", prose(8, seed=11), "", "Methods:", prose(7, seed=12), "",
       "1. Embeds the query using the embedding model", "2. Searches the vector store", "3. Reranks with a cross encoder",
       "", "tiny.", "", "RESULTS AND FINDINGS", prose(9, seed=13), "", "    indented paragraph start " + prose(3, seed=14),
       "=== Page 2 ===", "## Discussion", prose(12, seed=15), "", prose(2, seed=16), "", "Appendix:", prose(1, seed=17)]
case("structured pdf", "pdf", "paper.pdf", "\n".join(doc))

# 5. The same structure as a .docx but under 2000 characters: standard chunking.
case("short docx", "docx", "memo.docx", "# Memo\n\n" + prose(4, seed=21) + "\n\n1. First point\n2. Second point")

# 6. Tabular export the way the old app writes spreadsheets, with a header block and many rows.
rows = [f"Row {i}: Name: Person {i}, Score: {i * 3 % 97}, Team: {'Red' if i % 2 else 'Blue'}" for i in range(1, 80)]
tab = "=== Sheet: Scores ===\nData from sheet 'Scores' with 3 columns\nRow 1: Column headers: Name, Score, Team\n" + "\n".join(rows)
case("spreadsheet", "xlsx", "scores.xlsx", tab)

# 7. Text that merely looks tabular (Row N: lines near the top) from a .txt.
case("row-shaped text", "txt", "export.txt", "Row 1: a: 1\nRow 2: b: 2\n" + "\n".join(f"Row {i}: value: {i}" for i in range(3, 400)))

# 8. A long pdf whose sections have no paragraphs of 50+ characters (section leaves), plus one normal section.
doc2 = ["# Alpha", "short.", "also short", "# Beta", prose(40, seed=31), "# Gamma", "brief"]
case("pdf with leaf sections", "pdf", "notes.pdf", "\n".join(doc2))

# 9. Dense text over the token budget inside one chunk: the budget splitter has to cut a 1000-character chunk.
dense = " ".join("a1b2c3d4" for _ in range(300))
case("dense tokens", "txt", "dense.txt", dense)

with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump({"chunk_size": settings.chunk_size, "chunk_overlap": settings.chunk_overlap,
               "token_budget": 512 - token_budget.ENRICHMENT_RESERVE_TOKENS, "cases": cases}, f, indent=1, ensure_ascii=False)
    f.write("\n")
