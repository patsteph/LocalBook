"""EPUB ingestion: reading order, structure, and refusing DRM.

WHY THIS EXISTS (2026-09-02). EPUB extraction previously used ebooklib and iterated
`book.get_items()`, which yields MANIFEST order — the order files happen to be declared in
the package document — not SPINE order, which is the reading order. Books were ingested
scrambled, and nothing caught it because no test opened an EPUB whose manifest and spine
disagreed. Real EPUBs disagree constantly; a hand-built one that agrees proves nothing.

The old code also flattened the document with a bare `get_text()`, discarding every heading.
LocalBook does hierarchical chunking, so that threw away the structure the chunker exists to
consume.

ebooklib was also AGPL-3.0-or-later shipping inside the signed .app, which contradicted the
constraint requirements.in states for aioimaplib. It is gone; these tests build EPUBs with
the stdlib so they never depend on it returning.
"""
import asyncio
import io
import zipfile

import pytest

from services.document_processor import DocumentProcessor


CONTAINER = (
    '<?xml version="1.0"?>'
    '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
    '<rootfiles><rootfile full-path="OEBPS/content.opf"'
    ' media-type="application/oebps-package+xml"/></rootfiles></container>'
)


def _chapter(title: str, body: str) -> bytes:
    return (
        '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body>'
        f"<h1>{title}</h1><p>{body}</p><h2>Sub of {title}</h2><p>detail</p>"
        "</body></html>"
    ).encode()


def _build_epub(*, manifest: str, spine: str, files: dict, encrypted: bool = False) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", CONTAINER)
        if encrypted:
            z.writestr("META-INF/encryption.xml", "<encryption/>")
        z.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            "<dc:title>The Spine Test</dc:title><dc:creator>A. Author</dc:creator>"
            f"</metadata><manifest>{manifest}</manifest><spine>{spine}</spine></package>",
        )
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


# Manifest deliberately declares c3, c1, c2. The spine is c1, c2, c3.
SCRAMBLED = _build_epub(
    manifest=(
        '<item id="c3" href="ch3.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="c2" href="sub%20dir/ch2.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="css" href="s.css" media-type="text/css"/>'
    ),
    spine='<itemref idref="c1"/><itemref idref="c2"/><itemref idref="c3"/>',
    files={
        "OEBPS/ch1.xhtml": _chapter("Chapter One", "alpha"),
        "OEBPS/sub dir/ch2.xhtml": _chapter("Chapter Two", "beta"),
        "OEBPS/ch3.xhtml": _chapter("Chapter Three", "gamma"),
        "OEBPS/s.css": b"body{color:red}",
    },
)


@pytest.fixture(scope="module")
def extracted():
    return asyncio.run(DocumentProcessor()._extract_from_epub(SCRAMBLED))


def test_chapters_come_out_in_spine_order_not_manifest_order(extracted):
    """THE bug. The manifest declares c3 first; reading order must still be One, Two, Three."""
    positions = [extracted.index(t) for t in ("Chapter One", "Chapter Two", "Chapter Three")]
    assert positions == sorted(positions), (
        f"chapters are in manifest order, not reading order — positions {positions}"
    )


def test_headings_survive_as_markdown(extracted):
    """Hierarchical chunking builds its parent/child tree from these levels."""
    assert "# Chapter One" in extracted
    assert "## Sub of Chapter One" in extracted


def test_metadata_is_kept(extracted):
    assert "Title: The Spine Test" in extracted
    assert "Author: A. Author" in extracted


def test_non_content_manifest_items_are_skipped(extracted):
    """The spine can reference stylesheets and images; only content documents are text."""
    assert "color:red" not in extracted


def test_percent_encoded_and_nested_hrefs_resolve(extracted):
    """`sub%20dir/ch2.xhtml` is relative to the OPF's directory and URL-encoded. Getting
    either wrong drops the chapter silently rather than erroring."""
    assert "Chapter Two" in extracted and "beta" in extracted


# ── Refusals: each must be a clear ValueError, never a traceback ────────────────

def test_drm_is_refused_not_circumvented():
    """Circumventing DRM is a DMCA 1201 issue regardless of who owns the book. Detect and
    decline — never bundle or document a workaround."""
    drm = _build_epub(manifest="", spine="", files={}, encrypted=True)
    with pytest.raises(ValueError, match="DRM"):
        asyncio.run(DocumentProcessor()._extract_from_epub(drm))


def test_a_non_zip_file_fails_clearly():
    with pytest.raises(ValueError, match="not a zip"):
        asyncio.run(DocumentProcessor()._extract_from_epub(b"just some plain text"))


def test_a_zip_without_a_container_fails_clearly():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("random.txt", "hi")
    with pytest.raises(ValueError, match="container.xml"):
        asyncio.run(DocumentProcessor()._extract_from_epub(buf.getvalue()))


def test_an_empty_book_reports_no_text_rather_than_returning_nothing():
    """Returning "" would create a source with zero chunks that fails silently at query time."""
    empty = _build_epub(manifest="", spine="", files={})
    with pytest.raises(ValueError, match="no readable text"):
        asyncio.run(DocumentProcessor()._extract_from_epub(empty))


def test_the_agpl_dependency_stays_gone():
    """requirements.in refuses aioimaplib (GPL-3.0) for the bundled .app; ebooklib is AGPL and
    was shipping anyway. Re-adding it would reintroduce the same conflict.

    Checks the BUILD SCRIPTS as well as requirements, because dropping the import was not
    sufficient: `build_backend.sh` carried an explicit `--hidden-import=ebooklib`, so
    PyInstaller kept bundling it into the signed app with nothing in the source tree importing
    it. Verified by extracting the PYZ from the built binary — the source check alone passed
    while the artifact still shipped it.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent

    def _uncommented(path: pathlib.Path) -> str:
        return "\n".join(
            line for line in path.read_text().splitlines()
            if not line.strip().startswith("#")
        ).lower()

    assert "ebooklib" not in _uncommented(root / "requirements.in"), \
        "ebooklib is back in requirements.in"
    assert "ebooklib" not in _uncommented(root / "build_backend.sh"), \
        "build_backend.sh forces ebooklib into the bundle via --hidden-import"
    assert "ebooklib" not in _uncommented(root.parent / "build.sh"), \
        "build.sh still expects ebooklib to be installed"


def test_extraction_works_without_ebooklib_installed():
    """The point of the swap. If this ever fails with ImportError, something reintroduced the
    dependency rather than reading the container directly."""
    import importlib

    with pytest.raises(ImportError):
        importlib.import_module("ebooklib")

    out = asyncio.run(DocumentProcessor()._extract_from_epub(SCRAMBLED))
    assert "Chapter One" in out
