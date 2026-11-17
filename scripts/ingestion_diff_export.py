"""Record what the old app reads and cuts from a folder of documents, for the Swift Mac's ingestion diff.

Phase 3's gate (docs/swift-migration/00-decision.md) asks for a 50-document ingestion diff: the same files read
by the Python app and by the Swift Mac, with every difference in the extracted text or the chunks explained.
This script is the Python half. It walks one folder, runs each document through the old app's own reader
(``DocumentProcessor._extract_text``) and chunker (``rag_chunking.chunk_text_smart``, which includes the token
budget), and writes ``ingestion-fixture.json`` into that folder. The Swift Mac then opens the fixture in
Settings › Diagnostics › Ingestion diff, reads the same files from the same folder and compares.

Run from ``backend/`` with the old app's Python, pointing at the folder of documents::

    python ../scripts/ingestion_diff_export.py ~/Documents/LocalBook-diff-set

Nothing is written anywhere but that one fixture file, no notebook is touched and no model is loaded (the token
budget uses the embedder's tokenizer when it is in the local Hugging Face cache, the character estimate otherwise;
the fixture says which). Audio, video and pictures are skipped: their text comes from models, not readers, and the
diff is about the readers.

The 30-URL web diff uses the same fixture. Point the script at a text file of addresses (one per line, ``#`` starts
a comment) and it reads each with the old app's scraper (Playwright + trafilatura, YouTube transcripts, remote
documents), chunks the text as a web source, and writes the fixture beside the list::

    python ../scripts/ingestion_diff_export.py ~/Documents/LocalBook-diff-set/urls.txt

The Swift Mac opens that fixture the same way and reads each address itself.
"""
import asyncio
import datetime as dt
import hashlib
import json
import os
import platform
import sys
from pathlib import Path

sys.path.insert(0, ".")
from services import rag_chunking, token_budget  # noqa: E402
from services.document_processor import INGESTIBLE_EXTENSIONS, DocumentProcessor  # noqa: E402

FIXTURE_NAME = "ingestion-fixture.json"
FORMAT = "localbook-ingestion-fixture/1"
SKIPPED_KINDS = {
    "mp3", "wav", "m4a", "ogg", "flac", "aac", "wma",
    "mp4", "mov", "avi", "mkv", "webm", "wmv", "flv", "m4v",
    "png", "jpg", "jpeg", "tiff", "bmp", "gif", "webp", "heic", "heif",
}


def token_counter_name() -> str:
    try:
        from services import embedding_profile
        prof = embedding_profile.active()
        counter = token_budget.token_counter(prof.model_id)
        probe = "Seven words that no estimate counts right."     # 9 tokens to a real tokenizer, 22 to the estimate
        return "tokenizer" if counter(probe) != int(len(probe) / 2.0) + 1 else "estimate"
    except Exception:
        return "estimate"


async def read_folder(folder: Path, recursive: bool) -> dict:
    dp = DocumentProcessor()
    files = []
    paths = sorted(folder.rglob("*") if recursive else folder.iterdir())
    for path in paths:
        if not path.is_file() or path.name.startswith(".") or path.name == FIXTURE_NAME:
            continue
        ext = path.suffix.lower().lstrip(".")
        if ext not in INGESTIBLE_EXTENSIONS or ext in SKIPPED_KINDS:
            continue
        content = path.read_bytes()
        entry = {
            "name": str(path.relative_to(folder)),
            "format": dp._get_file_type(path.name, content),
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        print(f"[ingestion-diff] {entry['name']} ({entry['format']}, {len(content):,} bytes)")
        try:
            text = await dp._extract_text(content, path.name)
            if not text or not text.strip():
                raise ValueError("no text")
            entry["text"] = text
            entry["chunks"] = rag_chunking.chunk_text_smart(text, entry["format"], path.name)
        except Exception as exc:  # the fixture records the failure; the Mac shows it beside its own result
            entry["error"] = f"{type(exc).__name__}: {exc}"
            print(f"[ingestion-diff]   failed: {entry['error']}")
        files.append(entry)
    return {
        "format": FORMAT,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "folder": folder.name,
        "recursive": recursive,
        "python": platform.python_version(),
        "token_counter": token_counter_name(),
        "token_budget": max(32, 512 - token_budget.ENRICHMENT_RESERVE_TOKENS),
        "files": files,
    }


async def read_urls(listing: Path) -> dict:
    from services.web_scraper import WebScraper
    urls = [ln.strip() for ln in listing.read_text(encoding="utf-8").splitlines()]
    urls = [u for u in urls if u and not u.startswith("#")]
    scraper = WebScraper()
    files = []
    for url in urls:                                       # one at a time, so the old app's rate limits are not tripped
        print(f"[ingestion-diff] {url}")
        result = (await scraper.scrape_urls([url]))[0]
        source_type = "youtube" if scraper._is_youtube_url(url) else "web"
        entry = {"name": url, "url": url, "format": source_type, "bytes": 0, "sha256": ""}
        if result.get("success") and (result.get("text") or "").strip():
            text = result["text"]
            title = result.get("title") or url
            entry.update({
                "title": title,
                "bytes": len(text.encode("utf-8")),
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "text": text,
                "chunks": rag_chunking.chunk_text_smart(text, source_type, title),
            })
            print(f"[ingestion-diff]   {title!r}: {len(text):,} characters, {len(entry['chunks'])} chunks")
        else:
            entry["error"] = result.get("error") or "no text"
            print(f"[ingestion-diff]   failed: {entry['error']}")
        files.append(entry)
    return {
        "format": FORMAT,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "folder": listing.parent.name,
        "recursive": False,
        "python": platform.python_version(),
        "token_counter": token_counter_name(),
        "token_budget": max(32, 512 - token_budget.ENRICHMENT_RESERVE_TOKENS),
        "files": files,
    }


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        print(__doc__)
        return 2
    target = Path(os.path.expanduser(args[0])).resolve()
    if target.is_file():
        fixture = asyncio.run(read_urls(target))
        folder = target.parent
    elif target.is_dir():
        folder = target
        fixture = asyncio.run(read_folder(folder, recursive="--recursive" in sys.argv))
    else:
        print(f"{target} is neither a folder nor a list of addresses")
        return 2
    out = folder / FIXTURE_NAME
    with open(out, "w", encoding="utf-8") as f:
        json.dump(fixture, f, indent=1, ensure_ascii=False)
        f.write("\n")
    read = sum(1 for e in fixture["files"] if "text" in e)
    print(f"[ingestion-diff] {read} of {len(fixture['files'])} files read; fixture at {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
