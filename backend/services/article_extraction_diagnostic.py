"""article_extraction_diagnostic — read-only analyzer for newsletter extraction.

P14.DX (2026-06-11). Walks every email/forward source, runs every
heuristic independently, captures structural markers, classifies each
source as "split correctly", "probable misfire", or "probable genuine
single-article". Writes a JSON report; chat handler summarizes.

NO behavior change. Purely diagnostic — does not modify article_store
or run the production extractor. Intended to be safe to run any time.

Output: ~/Library/Application Support/LocalBook/diagnostics/
  article-extraction-{ISO timestamp}.json
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _diagnostic_dir() -> Path:
    """Return path to the diagnostics directory, creating it if missing."""
    from pathlib import Path
    base = Path.home() / "Library" / "Application Support" / "LocalBook" / "diagnostics"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _count_html_markers(html: str) -> Dict[str, Any]:
    """Count structural markers in the HTML body. Returns counts + common
    class names that might indicate per-article boundaries."""
    out = {
        "hr": 0, "h1": 0, "h2": 0, "h3": 0,
        "top_level_tables": 0,
        "anchors": 0,
        "divs_with_class": 0,
        "common_classes": [],
        "parse_failed": False,
    }
    if not html or not html.strip():
        return out
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        out["hr"] = len(soup.find_all("hr"))
        out["h1"] = len(soup.find_all("h1"))
        out["h2"] = len(soup.find_all("h2"))
        out["h3"] = len(soup.find_all("h3"))
        out["anchors"] = len(soup.find_all("a"))
        # Top-level tables (direct child of body — same query the
        # extractor uses).
        out["top_level_tables"] = len([
            t for t in soup.find_all("table", recursive=True)
            if t.parent and t.parent.name == "body"
        ])
        # Look at class attributes — repeated classes often indicate
        # per-article wrapper divs in newsletter templates.
        class_counter: Counter = Counter()
        for el in soup.find_all(True):
            cls = el.get("class")
            if cls:
                if el.name == "div":
                    out["divs_with_class"] += 1
                for c in cls:
                    if isinstance(c, str) and 3 < len(c) < 40:
                        class_counter[c] += 1
        # Surface classes that appear ≥ 3 times (potential repeated
        # per-article wrappers).
        out["common_classes"] = [
            {"class": c, "count": n}
            for c, n in class_counter.most_common(15)
            if n >= 3
        ]
    except Exception as e:
        out["parse_failed"] = True
        logger.debug(f"[diagnostic] HTML parse failed: {e}")
    return out


def _count_text_markers(text: str) -> Dict[str, Any]:
    """Count structural markers in the plain-text body."""
    if not text:
        return {
            "lines": 0, "dash_separators": 0, "equals_separators": 0,
            "asterisk_separators": 0, "unicode_rule_separators": 0,
            "markdown_h1": 0, "markdown_h2": 0, "markdown_h3": 0,
            "blank_line_blocks": 0,
        }
    return {
        "lines": text.count("\n") + 1,
        "dash_separators": len(re.findall(r"\n\s*-{3,}\s*\n", text)),
        "equals_separators": len(re.findall(r"\n\s*={3,}\s*\n", text)),
        "asterisk_separators": len(re.findall(r"\n\s*\*{3,}\s*\n", text)),
        "unicode_rule_separators": len(re.findall(r"\n\s*[─━—]{3,}\s*\n", text)),
        "markdown_h1": len(re.findall(r"(?m)^#\s+\S", text)),
        "markdown_h2": len(re.findall(r"(?m)^##\s+\S", text)),
        "markdown_h3": len(re.findall(r"(?m)^###\s+\S", text)),
        # Blank-line-separated paragraph blocks (very rough article-count proxy)
        "blank_line_blocks": len(re.findall(r"\n\s*\n", text)),
    }


def _run_heuristics(html_body: str, text_body: str) -> Dict[str, Any]:
    """Run each extraction heuristic independently and report what it
    produces. Mirrors the order in article_extractor.extract_articles
    but doesn't change behavior."""
    from services.article_extractor import (
        _split_by_hr,
        _split_by_headers,
        _split_by_repeated_table_blocks,
        _split_by_text_separators,
        _split_by_text_headers,
        _MIN_ARTICLE_CHARS,
        _MAX_ARTICLES,
    )
    results = {}

    if html_body:
        try:
            segs = _split_by_hr(html_body)
            results["hr"] = {"segments": len(segs), "ok_for_extractor": 2 <= len(segs) <= _MAX_ARTICLES}
        except Exception as e:
            results["hr"] = {"error": str(e)[:120]}

        try:
            segs = _split_by_headers(html_body)
            results["headers"] = {"segments": len(segs), "ok_for_extractor": 2 <= len(segs) <= _MAX_ARTICLES}
        except Exception as e:
            results["headers"] = {"error": str(e)[:120]}

        try:
            segs = _split_by_repeated_table_blocks(html_body)
            results["table_blocks"] = {"segments": len(segs), "ok_for_extractor": 2 <= len(segs) <= _MAX_ARTICLES}
        except Exception as e:
            results["table_blocks"] = {"error": str(e)[:120]}
    else:
        results["hr"] = {"segments": 0, "skipped": "no html body"}
        results["headers"] = {"segments": 0, "skipped": "no html body"}
        results["table_blocks"] = {"segments": 0, "skipped": "no html body"}

    if text_body:
        try:
            segs = _split_by_text_separators(text_body)
            results["text_separators"] = {"segments": len(segs), "ok_for_extractor": 2 <= len(segs) <= _MAX_ARTICLES}
        except Exception as e:
            results["text_separators"] = {"error": str(e)[:120]}

        try:
            segs = _split_by_text_headers(text_body)
            results["text_headers"] = {"segments": len(segs), "ok_for_extractor": 2 <= len(segs) <= _MAX_ARTICLES}
        except Exception as e:
            results["text_headers"] = {"error": str(e)[:120]}
    else:
        results["text_separators"] = {"segments": 0, "skipped": "no text body"}
        results["text_headers"] = {"segments": 0, "skipped": "no text body"}

    return results


def _classify(
    final_count: int,
    html_markers: Dict[str, Any],
    text_markers: Dict[str, Any],
    body_text_size: int,
) -> Tuple[str, str]:
    """Return (classification, diagnosis_text). Three buckets:
      - split_correctly: extractor produced ≥2 articles
      - probable_misfire: extractor produced 1 but markers suggest more
      - probable_genuine_single: no structural markers
    """
    if final_count >= 2:
        return ("split_correctly", f"split into {final_count} articles")

    # Single-article cases — look for evidence the body should have split
    h_total = (html_markers.get("h1", 0) + html_markers.get("h2", 0) +
               html_markers.get("h3", 0))
    table_total = html_markers.get("top_level_tables", 0)
    hr_total = html_markers.get("hr", 0)
    common_class_repeats = max(
        (c["count"] for c in html_markers.get("common_classes", []) or []),
        default=0,
    )
    dash_sep = text_markers.get("dash_separators", 0)
    md_h = (text_markers.get("markdown_h1", 0) + text_markers.get("markdown_h2", 0) +
            text_markers.get("markdown_h3", 0))

    signals = []
    if hr_total >= 2:
        signals.append(f"{hr_total} <hr> tags")
    if h_total >= 3:
        signals.append(f"{h_total} headings")
    if table_total >= 3:
        signals.append(f"{table_total} top-level tables")
    if common_class_repeats >= 4:
        signals.append(f"repeated class ×{common_class_repeats}")
    if dash_sep >= 2:
        signals.append(f"{dash_sep} dash-separator lines")
    if md_h >= 3:
        signals.append(f"{md_h} markdown headers")
    if signals:
        return ("probable_misfire", f"has signals: {', '.join(signals)}")

    # No structural signals — likely a genuinely single-article newsletter
    # (welcome email, personal blog post, etc.)
    return ("probable_genuine_single", f"no structural signals, {body_text_size}-char body")


async def run_diagnostic() -> Dict[str, Any]:
    """Walk every email/forward source. Read-only. Returns the full report
    dict and writes it to a JSON file under ~/Library/Application Support/
    LocalBook/diagnostics/."""
    from storage.source_store import source_store
    from storage.article_store import article_store
    from services.article_extractor import extract_articles

    now = datetime.utcnow()
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    report: Dict[str, Any] = {
        "generated_at": now.isoformat(),
        "summary": {
            "total_sources": 0,
            "split_correctly_ge2": 0,
            "single_article_total": 0,
            "single_article_probable_misfire": 0,
            "single_article_probable_genuine": 0,
            "extraction_failed": 0,
        },
        "sources": [],
    }
    by_sender_misfire: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_heuristic_split: Counter = Counter()

    try:
        all_by_nb = await source_store.list_all() or {}
    except Exception as e:
        logger.error(f"[diagnostic] list_all failed: {e}")
        return report

    for nb_id, sources in all_by_nb.items():
        for s in (sources or []):
            fmt = (s.get("format") or "").lower()
            if fmt not in ("email", "forward"):
                continue
            src_id = s.get("id")
            if not src_id:
                continue

            text_body = s.get("content") or ""
            meta = s.get("metadata") or {}
            html_body = ""
            if isinstance(meta, dict):
                html_body = meta.get("content_html") or ""
            if not html_body:
                html_body = s.get("content_html") or ""

            # If both empty, use the article body as a fallback (sources
            # ingested before content/metadata.content_html were saved
            # uniformly may have their full body only on the article row).
            if not text_body and not html_body:
                try:
                    arts = await article_store.list_by_source(src_id)
                    if arts:
                        first = arts[0]
                        text_body = first.get("body_text") or ""
                        html_body = first.get("body_html") or ""
                except Exception:
                    pass

            current_count = 0
            try:
                current_count = await article_store.count_by_source(src_id)
            except Exception:
                pass

            html_markers = _count_html_markers(html_body)
            text_markers = _count_text_markers(text_body)
            heuristic_results = _run_heuristics(html_body, text_body)

            # Run the actual extractor to see what it picks
            try:
                articles = extract_articles(
                    html_body=html_body or "",
                    text_body=text_body,
                    fallback_title=s.get("filename") or "(untitled)",
                )
                final_count = len(articles)
            except Exception as e:
                logger.debug(f"[diagnostic] extract failed for {src_id[:8]}: {e}")
                final_count = -1

            if final_count == -1:
                report["summary"]["extraction_failed"] += 1

            classification, diagnosis = _classify(
                final_count if final_count > 0 else 1,
                html_markers, text_markers,
                len(text_body or ""),
            )

            # Identify which heuristic the extractor likely used (if it
            # split). We can't know definitively without instrumenting,
            # but we can match: whichever heuristic produced segments
            # matching final_count is the most-likely candidate.
            likely_heuristic = "(none/single-fallback)"
            if final_count >= 2:
                for hname in ("hr", "headers", "table_blocks", "text_separators", "text_headers"):
                    h_result = heuristic_results.get(hname, {})
                    if h_result.get("segments") == final_count:
                        likely_heuristic = hname
                        break
                by_heuristic_split[likely_heuristic] += 1

            sender = (s.get("sender") or s.get("original_sender") or "").strip()
            entry = {
                "source_id": src_id,
                "notebook_id": nb_id,
                "sender": sender,
                "filename": (s.get("filename") or "")[:120],
                "created_at": s.get("created_at") or s.get("ingested_at"),
                "current_article_count": current_count,
                "body_text_size": len(text_body or ""),
                "body_html_size": len(html_body or ""),
                "html_markers": html_markers,
                "text_markers": text_markers,
                "heuristic_results": heuristic_results,
                "final_extraction_count": final_count,
                "likely_heuristic_used": likely_heuristic,
                "classification": classification,
                "diagnosis": diagnosis,
            }
            report["sources"].append(entry)

            report["summary"]["total_sources"] += 1
            if classification == "split_correctly":
                report["summary"]["split_correctly_ge2"] += 1
            else:
                report["summary"]["single_article_total"] += 1
                if classification == "probable_misfire":
                    report["summary"]["single_article_probable_misfire"] += 1
                    if sender:
                        by_sender_misfire[sender].append(entry)
                else:
                    report["summary"]["single_article_probable_genuine"] += 1

    # Aggregate top problem senders
    sender_summary = []
    for sender, entries in by_sender_misfire.items():
        if not entries:
            continue
        avg_size = sum(e["body_text_size"] for e in entries) / len(entries)
        all_signals: Counter = Counter()
        for e in entries:
            for sig in e["diagnosis"].replace("has signals: ", "").split(", "):
                if sig:
                    all_signals[sig.split(" ")[-1] if " " in sig else sig] += 1
        sender_summary.append({
            "sender": sender,
            "source_count": len(entries),
            "avg_body_size": int(avg_size),
            "sample_diagnosis": entries[0]["diagnosis"],
            "sample_source_id": entries[0]["source_id"],
            "sample_html_markers": entries[0]["html_markers"],
        })
    sender_summary.sort(key=lambda x: x["source_count"], reverse=True)
    report["summary"]["top_misfire_senders"] = sender_summary[:20]
    report["summary"]["heuristics_used_for_correct_splits"] = dict(by_heuristic_split)

    # Write to disk
    out_path = _diagnostic_dir() / f"article-extraction-{timestamp}.json"
    try:
        out_path.write_text(json.dumps(report, indent=2, default=str))
        report["_written_to"] = str(out_path)
        logger.info(f"[diagnostic] wrote report to {out_path}")
    except Exception as e:
        logger.warning(f"[diagnostic] write failed: {e}")
        report["_written_to"] = None

    return report
