#!/usr/bin/env python3
"""Blind side-by-side judging page for the visual golden set (test C6).

Takes the Swift app's golden folder (Settings › Diagnostics › Visual golden set › Show in Finder: the 15 PNGs, one per
ask) and the old app's folder (``scripts/visual_golden_export.py``: ``old-app-visuals.json`` with Mermaid or SVG per
ask) and writes ``blind.html`` into the Swift folder. Each of the 15 asks shows the two pictures side by side with the
sides shuffled and no label saying which app drew which. You pick Left, Tie or Right for each; "Reveal" at the end
scores it (the Swift app's wins and ties count towards the bar of 12 of 15) and gives a summary line to paste back.

    python3 scripts/visual_golden_blind.py <swift golden folder> <old app folder> [--seed N]

Blind to the label, not to the style: the old app's Mermaid drawings render live in the page, the Swift app's are
PNGs, so a practised eye can tell them apart. Judge the picture you would rather have been given, not the one you
recognise.
"""

from __future__ import annotations

import argparse
import html
import json
import random
import sys
from pathlib import Path

# Keep in step with VisualGoldenSet.prompts and visual_golden_export.py.
PROMPTS = [
    ("01-mindmap-overview", "Mind map", "Overview", ""),
    ("02-mindmap-standard", "Mind map", "Standard", "the main ideas and how they connect"),
    ("03-mindmap-deep", "Mind map", "Deep", "every important concept, grouped by theme"),
    ("04-flow-overview", "Flowchart", "Overview", "how the main process works, step by step"),
    ("05-flow-standard", "Flowchart", "Standard", "the decisions and what follows from each"),
    ("06-flow-deep", "Flowchart", "Deep", "the full sequence of steps with the branches and loops"),
    ("07-compare-overview", "Comparison", "Overview", "the two most important options or sides"),
    ("08-compare-standard", "Comparison", "Standard", "strengths and weaknesses of the approaches described"),
    ("09-compare-deep", "Comparison", "Deep", "all the alternatives on the criteria that matter"),
    ("10-timeline-overview", "Timeline", "Overview", "the key events in order"),
    ("11-timeline-standard", "Timeline", "Standard", "how things developed over time, with dates"),
    ("12-timeline-deep", "Timeline", "Deep", "the complete history, phases and turning points"),
    ("13-info-overview", "Infographic", "Overview", "the numbers that matter most"),
    ("14-info-standard", "Infographic", "Standard", "key facts, figures and names at a glance"),
    ("15-info-deep", "Infographic", "Deep", "a one-page summary of what matters and why"),
]

STYLE = """
body{font-family:-apple-system,Helvetica,sans-serif;max-width:1500px;margin:30px auto;padding:0 20px;color:#222}
h1{margin:0 0 6px}.meta{color:#666;margin:0 0 18px}
section{border:1px solid #ddd;border-radius:10px;padding:16px;margin:22px 0}
section h2{margin:0 0 4px;font-size:1.1em}section .ask{color:#666;margin:0 0 12px}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.panel{background:#fff;border:1px solid #eee;border-radius:8px;padding:8px;min-height:120px;overflow:auto}
.panel img{max-width:100%;height:auto;display:block}.panel .svg svg{max-width:100%;height:auto}
.panel .mermaid{background:#fff}.panel .missing{color:#b00;font-family:Menlo,monospace;white-space:pre-wrap}
.panel .which{display:none;margin-top:6px;font-size:.9em;color:#06c}
.choice{margin-top:12px;display:flex;gap:8px;align-items:center}
.choice button{padding:6px 14px;border:1px solid #bbb;border-radius:6px;background:#f7f7f7;cursor:pointer}
.choice button.on{background:#06c;color:#fff;border-color:#06c}
#result{border:2px solid #06c;border-radius:10px;padding:16px;margin:30px 0}
#result pre{background:#f4f4f4;padding:10px;border-radius:6px;white-space:pre-wrap}
"""


def panel(side: str, kind: str, body: str) -> str:
    return f"<div class='panel' data-kind='{kind}'>{body}<div class='which'>drawn by the {side}</div></div>"


def swift_panel(folder: Path, pid: str) -> tuple[str, str]:
    png = folder / f"{pid}.png"
    if png.exists():
        return "swift", f"<img src='{html.escape(png.name)}' alt=''>"
    return "swift", f"<div class='missing'>no picture (the Swift app did not write {html.escape(png.name)})</div>"


def old_panel(entry: dict | None) -> tuple[str, str]:
    if not entry:
        return "old", "<div class='missing'>no entry in old-app-visuals.json</div>"
    if entry.get("svg"):
        return "old", f"<div class='svg'>{entry['svg']}</div>"
    if entry.get("code"):
        return "old", f"<pre class='mermaid'>{html.escape(entry['code'])}</pre>"
    return "old", f"<div class='missing'>failed: {html.escape(str(entry.get('error', 'no picture')))}</div>"


def resolve_swift_folder(folder: Path) -> Path | None:
    """The folder holding the 15 PNGs, or the newest ``visual-golden-*`` child of a parent folder; None (with a message)
    when neither holds any of them, so a wrong path fails here instead of producing a page of empty panels."""
    wanted = {f"{pid}.png" for pid, *_ in PROMPTS}
    if any((folder / name).exists() for name in wanted):
        return folder
    children = sorted((c for c in folder.glob("visual-golden-*") if c.is_dir() and any((c / n).exists() for n in wanted)),
                      key=lambda c: c.stat().st_mtime)
    if children:
        print(f"[visual-golden] using {children[-1]} (newest visual-golden-* folder inside {folder})")
        return children[-1]
    print(f"no Swift pictures: {folder} holds none of the 15 PNGs (01-mindmap-overview.png …) and has no visual-golden-* "
          "subfolder with them.\nPass the app's visual-golden-<stamp> folder (Settings › Diagnostics › Visual golden set › "
          "Show in Finder, then option-drag it to the Desktop) or the folder you copied it into.", file=sys.stderr)
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("swift_folder", help="the Swift app's visual-golden-<stamp> folder")
    ap.add_argument("old_folder", help="the folder visual_golden_export.py wrote old-app-visuals.json into")
    ap.add_argument("--seed", type=int, default=None, help="shuffle seed (printed; pass it again to get the same sides)")
    args = ap.parse_args()

    swift = Path(args.swift_folder).expanduser().resolve()
    old = Path(args.old_folder).expanduser().resolve()
    old_json = old / "old-app-visuals.json"
    if not swift.is_dir():
        print(f"not a folder: {swift}", file=sys.stderr)
        return 2
    swift = resolve_swift_folder(swift)
    if swift is None:
        return 2
    if not old_json.exists():
        print(f"missing {old_json}", file=sys.stderr)
        return 2
    entries = {e["id"]: e for e in json.loads(old_json.read_text(encoding="utf-8"))}

    seed = args.seed if args.seed is not None else random.SystemRandom().randrange(1_000_000)
    rng = random.Random(seed)
    key: dict[str, str] = {}   # pid -> which side the Swift app is on ("left" / "right")

    sections = []
    for i, (pid, layout, detail, topic) in enumerate(PROMPTS, 1):
        s_kind, s_body = swift_panel(swift, pid)
        o_kind, o_body = old_panel(entries.get(pid))
        swift_left = rng.random() < 0.5
        key[pid] = "left" if swift_left else "right"
        left, right = ((s_kind, s_body), (o_kind, o_body)) if swift_left else ((o_kind, o_body), (s_kind, s_body))
        sections.append(
            f"<section data-id='{pid}'><h2>{i}. {html.escape(layout)} · {html.escape(detail)}</h2>"
            f"<p class='ask'>{html.escape(topic or '(the whole notebook)')}</p>"
            f"<div class='pair'>{panel('new app' if left[0] == 'swift' else 'old app', left[0], left[1])}"
            f"{panel('new app' if right[0] == 'swift' else 'old app', right[0], right[1])}</div>"
            "<div class='choice'><button data-pick='left'>Left is better</button>"
            "<button data-pick='tie'>Tie</button><button data-pick='right'>Right is better</button></div></section>"
        )

    script = f"""
const KEY = {json.dumps(key)};
const picks = {{}};
document.querySelectorAll('.choice button').forEach(b => b.addEventListener('click', () => {{
  const section = b.closest('section'); const id = section.dataset.id;
  picks[id] = b.dataset.pick;
  section.querySelectorAll('.choice button').forEach(x => x.classList.toggle('on', x === b));
  document.getElementById('count').textContent = Object.keys(picks).length + ' of 15 judged';
}}));
document.getElementById('reveal').addEventListener('click', () => {{
  const ids = Object.keys(KEY); let wins = 0, ties = 0, losses = 0, undecided = 0; const lines = [];
  ids.forEach(id => {{
    const p = picks[id];
    if (!p) {{ undecided++; lines.push(id + ': not judged'); return; }}
    const verdict = p === 'tie' ? 'tie' : (p === KEY[id] ? 'new app' : 'old app');
    if (verdict === 'tie') ties++; else if (verdict === 'new app') wins++; else losses++;
    lines.push(id + ': ' + verdict);
  }});
  const score = wins + ties;
  const summary = 'C6 blind (seed {seed}): new app better ' + wins + ', ties ' + ties + ', old app better ' + losses +
    (undecided ? ', not judged ' + undecided : '') + ' → ' + score + ' of 15 (bar 12)' + (score >= 12 ? ' PASS' : ' FAIL');
  document.getElementById('summary').textContent = summary + '\\n' + lines.join('\\n');
  document.querySelectorAll('.which').forEach(w => w.style.display = 'block');
  document.getElementById('result').style.display = 'block';
}});
"""
    page = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'><title>Visual golden set · blind</title>"
        "<script type='module'>import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';"
        "mermaid.initialize({startOnLoad: true, securityLevel: 'loose'});</script>"
        f"<style>{STYLE}</style></head><body>"
        "<h1>Visual golden set · blind judgement</h1>"
        f"<p class='meta'>Swift folder: {html.escape(str(swift))} · old app: {html.escape(str(old))} · seed {seed}. "
        "For each ask pick the picture you would rather have been given. Press Reveal when all 15 are judged.</p>"
        + "".join(sections)
        + "<p><span id='count'>0 of 15 judged</span> &nbsp; <button id='reveal' style='padding:8px 18px'>Reveal</button></p>"
        "<div id='result' style='display:none'><h2>Result</h2><p>Paste this back:</p><pre id='summary'></pre></div>"
        f"<script>{script}</script></body></html>"
    )
    out = swift / "blind.html"
    out.write_text(page, encoding="utf-8")
    print(f"[visual-golden] seed {seed}; wrote {out}")
    print(f"open '{out}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
