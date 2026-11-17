"""Draw the 15-prompt visual golden set with the old app, for the side-by-side judgement against the Swift Mac.

The absorption map's "Visuals" row is judged, not measured: the same 15 asks (``VisualGoldenSet`` in LocalBookCore)
drawn from the same notebook by both apps, each pair looked at by Patrick. This script is the old app's half. It
asks the running old app (``/visual/smart`` on localhost:8000, with the per-launch token from the app's data
folder) for each prompt's template and writes ``old-app-visuals.html``, one Mermaid diagram per prompt rendered by
mermaid.js, into the folder you name. The Swift app writes its ``index.md`` and pictures into a folder of its own
from Settings › Diagnostics › Visual golden set; open the two side by side.

Run with the old app open::

    python scripts/visual_golden_export.py <notebook id> ~/Documents/visual-golden-<notebook>

The notebook id is in the old app's address bar or Settings › Diagnostics. Nothing is written into the library:
the smart visual route reads the notebook and answers with diagram code. Each prompt takes the old app as long as
a visual takes in its UI (its fast model).
"""
import datetime as dt
import html
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("LOCALBOOK_URL", "http://localhost:8000")
TOKEN_FILE = Path.home() / "Library" / "Application Support" / "LocalBook" / ".app_token"

# Keep in step with VisualGoldenSet.prompts (swift/LocalBookCore/Sources/LocalBookCore/Studio/VisualGoldenSet.swift).
PROMPTS = [
    ("01-mindmap-overview", "Mind map", "Overview", "", "mindmap"),
    ("02-mindmap-standard", "Mind map", "Standard", "the main ideas and how they connect", "mindmap"),
    ("03-mindmap-deep", "Mind map", "Deep", "every important concept, grouped by theme", "overview_map"),
    ("04-flow-overview", "Flowchart", "Overview", "how the main process works, step by step", "process_flow"),
    ("05-flow-standard", "Flowchart", "Standard", "the decisions and what follows from each", "decision_tree"),
    ("06-flow-deep", "Flowchart", "Deep", "the full sequence of steps with the branches and loops", "horizontal_steps"),
    ("07-compare-overview", "Comparison", "Overview", "the two most important options or sides", "side_by_side_analysis"),
    ("08-compare-standard", "Comparison", "Standard", "strengths and weaknesses of the approaches described", "pros_cons"),
    ("09-compare-deep", "Comparison", "Deep", "all the alternatives on the criteria that matter", "quadrant"),
    ("10-timeline-overview", "Timeline", "Overview", "the key events in order", "timeline"),
    ("11-timeline-standard", "Timeline", "Standard", "how things developed over time, with dates", "timeline"),
    ("12-timeline-deep", "Timeline", "Deep", "the complete history, phases and turning points", "timeline"),
    ("13-info-overview", "Infographic", "Overview", "the numbers that matter most", "key_stats"),
    ("14-info-standard", "Infographic", "Standard", "key facts, figures and names at a glance", "key_stats"),
    ("15-info-deep", "Infographic", "Deep", "a one-page summary of what matters and why", "exec_summary_context"),
]


def token() -> str:
    try:
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def call(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "x-localbook-token": token()},
    )
    with urllib.request.urlopen(req, timeout=600) as resp:   # a deep visual can take minutes on the fast model
        return json.loads(resp.read().decode("utf-8"))


def first_diagram(reply: dict) -> dict | None:
    """The primary option of /visual/smart: {diagram_type, code, title, template_id}, wherever the reply keeps it."""
    options = reply.get("diagrams") or reply.get("options") or []
    if isinstance(options, list) and options:
        return options[0]
    if "code" in reply:
        return reply
    primary = reply.get("primary")
    return primary if isinstance(primary, dict) else None


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    notebook_id, out_dir = sys.argv[1], Path(os.path.expanduser(sys.argv[2])).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    made = []
    for pid, layout, detail, topic, template in PROMPTS:
        ask = topic or "the whole notebook"
        print(f"[visual-golden] {pid}: {layout} / {detail} / {ask}")
        entry = {"id": pid, "layout": layout, "detail": detail, "topic": topic, "template_id": template}
        try:
            reply = call("/visual/smart", {"notebook_id": notebook_id, "topic": topic or "overview of this notebook",
                                           "template_id": template, "color_theme": "auto"})
            d = first_diagram(reply)
            if not d or not d.get("code"):
                raise ValueError(f"no diagram in the reply: {json.dumps(reply)[:200]}")
            entry.update({"diagram_type": d.get("diagram_type"), "code": d["code"], "title": d.get("title") or ""})
            print(f"[visual-golden]   {d.get('template_name') or d.get('template_id')}: {len(d['code'])} characters of Mermaid")
        except (urllib.error.URLError, ValueError, json.JSONDecodeError) as exc:
            entry["error"] = str(exc)
            print(f"[visual-golden]   failed: {exc}")
        made.append(entry)

    (out_dir / "old-app-visuals.json").write_text(json.dumps(made, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'><title>Old app visuals</title>",
        "<script type='module'>import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';",
        "mermaid.initialize({startOnLoad: true, securityLevel: 'loose'});</script>",
        "<style>body{font-family:-apple-system,Helvetica,sans-serif;max-width:1100px;margin:30px auto;padding:0 20px}",
        "section{border:1px solid #ddd;border-radius:10px;padding:16px;margin:20px 0}h2{margin:0 0 4px}.meta{color:#666;margin:0 0 12px}",
        ".mermaid{background:#fff}pre.err{color:#b00}</style></head><body>",
        f"<h1>Old app: visual golden set</h1><p class='meta'>Notebook {html.escape(notebook_id)}, drawn {stamp}. "
        "Open the Swift app's index.md for the same notebook beside this page.</p>",
    ]
    for i, e in enumerate(made, 1):
        parts.append(f"<section><h2>{i}. {html.escape(e['layout'])} · {html.escape(e['detail'])}</h2>")
        parts.append(f"<p class='meta'>{html.escape(e['topic'] or '(the whole notebook)')} · template {html.escape(e['template_id'])}</p>")
        if "code" in e:
            if e.get("title"):
                parts.append(f"<p><strong>{html.escape(e['title'])}</strong></p>")
            parts.append(f"<pre class='mermaid'>{html.escape(e['code'])}</pre>")
        else:
            parts.append(f"<pre class='err'>failed: {html.escape(e.get('error', ''))}</pre>")
        parts.append("</section>")
    parts.append("</body></html>")
    page = out_dir / "old-app-visuals.html"
    page.write_text("\n".join(parts), encoding="utf-8")
    drawn = sum(1 for e in made if "code" in e)
    print(f"[visual-golden] {drawn} of {len(made)} drawn; open {page}")
    return 0 if drawn else 1


if __name__ == "__main__":
    sys.exit(main())
