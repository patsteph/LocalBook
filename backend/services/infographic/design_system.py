"""Infographic design-system stylesheet — the ONE hand-authored L2 lane CSS.

This encodes the shared visual language of the reference targets:
  - off-white dotted background
  - single coral-red accent (#E0503A) on a monochrome base
  - rounded cards with corner brackets
  - selective red glow on hero elements
  - numbered citation superscripts (the provenance rule, made visible)
  - Lucide line-art icons (embedded as inline SVG by the builder)

HARD RULE (§2.3): the LLM never writes CSS. Every infographic is styled
100% by these hand-authored classes. Bad styling is ONE fix here, and it is
retroactive across every saved artifact (the artifact stores only the filled
skeleton body — never a copy of this CSS).

SOURCE-OF-TRUTH / MIRROR: this constant is byte-for-byte mirrored by the
frontend at `src/components/artifact/renderers/infographicDesignSystem.ts`
(`INFOGRAPHIC_L2_CSS`). The frontend renderer injects it into a Shadow DOM;
this Python copy is injected by the export pipeline (`artifact_renderer.py`)
because the PyInstaller-bundled backend cannot read the frontend source tree.
If you edit one, edit the other. (This mirrors the existing
`htmlArtifactTailwindSubset.ts` <-> `export_assets.TAILWIND_SUBSET_CSS` pair.)
"""
from __future__ import annotations

INFOGRAPHIC_L2_CSS = r"""
/* -- Design tokens ------------------------------------------------ */
.ib{
  --ib-ink:#1b1a18;
  --ib-ink-soft:#4a4844;
  --ib-muted:#8b8880;
  --ib-bg:#f4f2ee;
  --ib-card-a:#ffffff;
  --ib-card-b:#eeece6;
  --ib-line:#d8d5cd;
  --ib-accent:#e0503a;
  --ib-accent-soft:rgba(224,80,58,.16);
  --ib-glow:rgba(224,80,58,.42);
  --ib-bracket:rgba(52,50,46,.5);
  --ib-font:-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,Helvetica,Arial,sans-serif;
  --ib-mono:"SF Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;

  box-sizing:border-box;
  font-family:var(--ib-font);
  color:var(--ib-ink);
  background-color:var(--ib-bg);
  background-image:radial-gradient(rgba(52,50,46,.10) 1px, transparent 1.5px);
  background-size:22px 22px;
  padding:34px 34px 30px;
  line-height:1.35;
  -webkit-font-smoothing:antialiased;
}
.ib *{box-sizing:border-box;}

/* -- Header ------------------------------------------------------- */
.ib-head{margin-bottom:22px;text-align:center;}
.ib-title{
  font-size:30px;font-weight:800;letter-spacing:-.02em;
  color:var(--ib-ink);margin:0 0 4px;
}
.ib-subtitle{font-size:14px;font-weight:500;color:var(--ib-muted);margin:0;}
.ib-section-label{
  font-size:15px;font-weight:700;color:var(--ib-ink);
  text-align:center;margin:0 0 12px;
}

/* -- Corner-bracket frame (all 4 corners, pure CSS) -------------- */
.ib-bracketed{position:relative;}
.ib-bracketed::before,
.ib-bracketed::after{
  content:"";position:absolute;inset:9px;pointer-events:none;
  --l:20px;--w:2px;--c:var(--ib-bracket);
}
.ib-bracketed::before{
  background:
    linear-gradient(var(--c),var(--c)) top left/var(--l) var(--w) no-repeat,
    linear-gradient(var(--c),var(--c)) top left/var(--w) var(--l) no-repeat,
    linear-gradient(var(--c),var(--c)) bottom right/var(--l) var(--w) no-repeat,
    linear-gradient(var(--c),var(--c)) bottom right/var(--w) var(--l) no-repeat;
}
.ib-bracketed::after{
  background:
    linear-gradient(var(--c),var(--c)) top right/var(--l) var(--w) no-repeat,
    linear-gradient(var(--c),var(--c)) top right/var(--w) var(--l) no-repeat,
    linear-gradient(var(--c),var(--c)) bottom left/var(--l) var(--w) no-repeat,
    linear-gradient(var(--c),var(--c)) bottom left/var(--w) var(--l) no-repeat;
}

/* -- Card -------------------------------------------------------- */
.ib-card{
  position:relative;
  background:linear-gradient(158deg,var(--ib-card-a),var(--ib-card-b));
  border:1px solid var(--ib-line);
  border-radius:18px;
  padding:20px 22px;
}
.ib-glow{
  border-color:rgba(224,80,58,.6);
  box-shadow:0 0 0 1px rgba(224,80,58,.30),0 0 26px 2px var(--ib-glow);
}

/* -- Icons (Lucide inline SVG) ----------------------------------- */
.ib-icon{width:38px;height:38px;display:inline-flex;align-items:center;justify-content:center;color:var(--ib-ink-soft);}
.ib-icon svg{width:100%;height:100%;stroke:currentColor;fill:none;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round;}
.ib-icon--accent{color:var(--ib-accent);}
.ib-icon--sm{width:26px;height:26px;}

/* -- Node row (icon over label) ---------------------------------- */
.ib-node{display:flex;flex-direction:column;align-items:center;text-align:center;gap:8px;flex:1;min-width:0;}
.ib-node-label{font-size:12.5px;font-weight:600;color:var(--ib-ink-soft);max-width:110px;}
.ib-flowrow{display:flex;align-items:center;justify-content:center;gap:6px;}
.ib-arrow{color:var(--ib-ink-soft);flex:0 0 auto;}
.ib-arrow svg{width:26px;height:20px;stroke:currentColor;fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;}
.ib-arrow--accent{color:var(--ib-accent);}

/* -- Two-column compare ------------------------------------------ */
/* minmax(0,1fr) (not bare 1fr) forces the two columns to stay equal-width:
   a bare 1fr floors at min-content, so one wide child (the fan / branch)
   would blow its column out to ~70%. min-width:0 lets wide children shrink
   inside the column instead of overflowing off the canvas. */
.ib-compare{display:grid;grid-template-columns:minmax(0,1fr) 1px minmax(0,1fr);gap:26px;align-items:start;max-width:100%;}
.ib-vrule{background:var(--ib-line);width:1px;align-self:stretch;}
.ib-col{display:flex;flex-direction:column;gap:16px;min-width:0;}

/* -- Looping "every query" pill under a card --------------------- */
.ib-loop{
  position:relative;margin:8px 0 4px;padding:6px 0;text-align:center;
  font-size:11.5px;font-weight:700;letter-spacing:.03em;color:var(--ib-accent);
}
.ib-loop::before{
  content:"";position:absolute;left:8%;right:8%;top:50%;height:2px;
  background:var(--ib-accent);opacity:.55;border-radius:2px;z-index:0;
}
.ib-loop span{position:relative;z-index:1;background:var(--ib-bg);padding:0 10px;}

/* -- Fan-out (Nx) under compile-time card ------------------------ */
.ib-fan-label{display:flex;justify-content:center;margin:14px 0 10px;}
.ib-fan-label b{
  font-size:16px;font-weight:800;color:var(--ib-ink);
  border:1.5px solid var(--ib-line);border-radius:8px;padding:2px 12px;background:var(--ib-card-a);
}
.ib-fan{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;max-width:100%;}
.ib-fan>*{min-width:0;}

/* -- Facts table (task-optimized artifact) ----------------------- */
.ib-table{
  width:100%;border-collapse:separate;border-spacing:0;
  font-size:15px;
}
.ib-table caption{
  caption-side:top;text-align:left;font-size:18px;font-weight:800;
  color:var(--ib-ink);padding:0 0 12px 2px;
}
.ib-table td{
  padding:13px 14px;border-bottom:1px solid var(--ib-line);
  color:var(--ib-ink);vertical-align:top;
}
.ib-table tr:last-child td{border-bottom:none;}
.ib-table td:first-child{color:var(--ib-ink-soft);font-weight:500;border-right:1px solid var(--ib-line);}
.ib-table td:last-child{font-weight:700;position:relative;}
.ib-cite{
  color:var(--ib-accent);font-size:.62em;font-weight:800;
  vertical-align:super;margin-left:3px;line-height:0;
}

/* -- Messy raw-chunks motif (rotate + blur + stack) -------------- */
.ib-messy{position:relative;height:300px;}
.ib-chunk{
  position:absolute;width:58%;padding:14px 16px;border-radius:10px;
  background:rgba(255,255,255,.86);border:1px solid var(--ib-line);
  font-family:var(--ib-mono);font-size:11px;color:var(--ib-muted);
  box-shadow:0 6px 18px rgba(0,0,0,.08);white-space:pre-line;
  filter:blur(1.1px);opacity:.9;
}
.ib-chunk--1{left:2%;top:6%;transform:rotate(-7deg);}
.ib-chunk--2{right:2%;top:2%;transform:rotate(6deg);}
.ib-chunk--3{left:6%;bottom:2%;transform:rotate(4deg);}
.ib-chunk--4{right:6%;bottom:6%;transform:rotate(-5deg);}
.ib-chunk--front{
  left:20%;top:26%;transform:rotate(-1deg);filter:blur(.2px);opacity:1;
  color:var(--ib-ink-soft);z-index:3;box-shadow:0 12px 30px rgba(0,0,0,.14);
}

/* -- 3-stage pipeline (Compile -> Index -> Serve) ---------------- */
.ib-stages{display:grid;grid-template-columns:1fr auto 1fr auto 1fr;gap:10px;align-items:stretch;}
.ib-stage{
  position:relative;display:flex;flex-direction:column;align-items:center;gap:14px;
  background:linear-gradient(158deg,rgba(255,255,255,.55),rgba(238,236,230,.32));
  border:1px solid rgba(255,255,255,.65);border-radius:18px;padding:22px 18px;
  backdrop-filter:blur(9px) saturate(1.12);
  -webkit-backdrop-filter:blur(9px) saturate(1.12);
  box-shadow:inset 0 1px 0 rgba(255,255,255,.75),0 8px 24px rgba(52,50,46,.10);
}
.ib-stage-title{font-size:22px;font-weight:800;color:var(--ib-ink);margin:0;}
.ib-stage-body{display:flex;flex-direction:column;align-items:center;gap:12px;flex:1;justify-content:center;width:100%;}
.ib-stage-note{font-size:13px;font-weight:600;color:var(--ib-ink-soft);text-align:center;}
.ib-stage-arrow{display:flex;align-items:center;color:var(--ib-accent);}
.ib-stage-arrow svg{width:30px;height:24px;stroke:currentColor;fill:none;stroke-width:2.4;stroke-linecap:round;stroke-linejoin:round;}

/* dashed coral phase divider */
.ib-phase-divider{
  align-self:stretch;width:0;border-left:2.5px dashed var(--ib-accent);
  opacity:.75;margin:0 2px;
}

/* layered graph + heatmap motif (Index stage) — the network glyph sits over
   this embedding-matrix grid so the stage reads as "vectors indexed" */
.ib-heatmap{
  display:grid;grid-template-columns:repeat(6,1fr);gap:3px;
  width:100%;max-width:154px;margin-top:2px;
}
.ib-heatmap i{aspect-ratio:1;border-radius:3px;background:var(--ib-accent-soft);}
.ib-heatmap i.h2{background:rgba(224,80,58,.34);}
.ib-heatmap i.h3{background:rgba(224,80,58,.58);}
.ib-heatmap i.h4{background:rgba(224,80,58,.82);}

/* curved looping + branching coral arrows (pipeline_compare) — replace the
   flat pill/fan with drawn arrows closer to the reference target */
.ib-looped{display:flex;flex-direction:column;gap:2px;align-items:stretch;}
.ib-loop-arrow{display:flex;justify-content:center;color:var(--ib-accent);margin:1px 0;}
.ib-loop-arrow svg{width:42px;height:26px;stroke:currentColor;fill:none;stroke-width:2.2;stroke-linecap:round;stroke-linejoin:round;}
.ib-branch{display:flex;justify-content:center;color:var(--ib-accent);margin:4px 0 2px;max-width:100%;}
.ib-branch svg{width:100%;max-width:200px;height:auto;stroke:currentColor;fill:none;stroke-width:2.2;stroke-linecap:round;stroke-linejoin:round;}

/* code block */
.ib-code{
  width:100%;font-family:var(--ib-mono);font-size:11.5px;line-height:1.5;
  background:rgba(255,255,255,.72);border:1px solid var(--ib-line);border-radius:10px;
  padding:12px 14px;color:var(--ib-ink-soft);white-space:pre;overflow:hidden;
}
.ib-code .ib-comment{color:var(--ib-muted);}

/* json output + doc-citation chips */
.ib-json{font-family:var(--ib-mono);font-size:12px;color:var(--ib-ink-soft);white-space:pre;}
.ib-doc-chip{
  display:inline-flex;align-items:center;gap:3px;color:var(--ib-accent);
  font-family:var(--ib-mono);font-size:10.5px;font-weight:700;
}
.ib-doc-chip svg{width:15px;height:18px;stroke:currentColor;fill:none;stroke-width:1.8;}

/* -- L1 annotation chrome (shared tokens; used by chart overlay) - */
.ib-callout{
  position:relative;background:var(--ib-card-a);
  border:2px solid var(--ib-accent);border-radius:12px;padding:10px 14px;
  box-shadow:0 0 0 1px rgba(224,80,58,.18),0 0 22px 1px var(--ib-glow);
  max-width:260px;
}
.ib-callout b{display:block;font-size:16px;font-weight:800;color:var(--ib-ink);line-height:1.15;}
.ib-callout small{display:block;font-size:12px;color:var(--ib-muted);margin-top:2px;}
.ib-series-label{font-size:14px;font-weight:800;color:var(--ib-ink);}
.ib-series-label--accent{color:var(--ib-accent);}
.ib-note{font-size:13px;font-weight:600;color:var(--ib-ink-soft);}

/* -- utility ----------------------------------------------------- */
.ib-row{display:flex;align-items:center;gap:10px;}
.ib-center{display:flex;align-items:center;justify-content:center;}
.ib-mt{margin-top:14px;}
.ib-fallback{font-family:var(--ib-mono);font-size:13px;color:var(--ib-ink-soft);white-space:pre-wrap;}

/* -- Family B "deck" chrome (07-31 targets) — pills, badges, step cards,
      takeaway, tier ladder, layer stack. Var-driven so accent restyle + the
      cream tone recolor them for free. ------------------------------- */
.ib-pill{
  display:inline-flex;align-items:center;gap:6px;white-space:nowrap;
  font-family:var(--ib-mono);font-size:11px;font-weight:700;letter-spacing:.04em;
  color:var(--ib-accent);background:var(--ib-accent-soft);
  border:1px solid var(--ib-accent);border-radius:999px;padding:3px 11px;
}
.ib-pill--muted{color:var(--ib-ink-soft);background:var(--ib-card-b);border-color:var(--ib-line);}
.ib-vs{font-family:var(--ib-mono);font-size:13px;font-weight:700;letter-spacing:.08em;color:var(--ib-muted);text-align:center;}
.ib-hairline{border-top:1px dashed var(--ib-line);margin:2px 0;}
.ib-takeaway{
  margin-top:18px;padding:16px 20px;border-radius:12px;text-align:center;line-height:1.5;
  background:var(--ib-accent-soft);border:1px solid var(--ib-accent);
  font-family:var(--ib-mono);font-size:13px;font-weight:600;color:var(--ib-accent);
}
.ib-icon-square{
  display:inline-flex;align-items:center;justify-content:center;
  width:44px;height:44px;border-radius:12px;
  color:var(--ib-accent);background:var(--ib-accent-soft);border:1px solid var(--ib-line);
}
.ib-icon-square svg{width:24px;height:24px;stroke:currentColor;fill:none;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round;}
.ib-step-num{font-family:var(--ib-mono);font-size:11px;font-weight:700;letter-spacing:.08em;color:var(--ib-muted);text-transform:uppercase;}
.ib-badge-band{
  display:flex;flex-wrap:wrap;align-items:center;justify-content:center;gap:10px;
  margin-top:16px;padding:18px 16px;border-radius:14px;
  background:var(--ib-card-b);border:1px solid var(--ib-line);
}
.ib-loopline{display:flex;align-items:center;gap:10px;margin-top:14px;font-family:var(--ib-mono);font-size:12px;font-weight:600;color:var(--ib-accent);}
.ib-loopline-rule{flex:1;height:0;border-top:1.5px solid var(--ib-accent);opacity:.6;}
.ib-loopline svg{width:22px;height:16px;stroke:var(--ib-accent);fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;flex:0 0 auto;}
.ib-chip{
  display:flex;flex-direction:column;justify-content:center;min-width:0;padding:12px 14px;border-radius:12px;
  background:var(--ib-card-a);border:1px solid var(--ib-line);
}
.ib-chip-val{font-size:23px;font-weight:800;color:var(--ib-accent);line-height:1.05;}
.ib-chip-lab{font-size:11px;font-weight:600;color:var(--ib-muted);margin-top:4px;}
.ib-tier-list{display:flex;flex-direction:column;gap:10px;min-width:0;}
.ib-tier{
  display:flex;align-items:center;gap:12px;min-width:0;
  background:linear-gradient(158deg,var(--ib-card-a),var(--ib-card-b));
  border:1px solid var(--ib-line);border-radius:14px;padding:14px 16px;
}
.ib-tier-rung{
  flex-shrink:0;width:28px;height:28px;border-radius:50%;background:var(--ib-accent);
  color:#fff;font-weight:800;font-size:13px;display:flex;align-items:center;justify-content:center;
}
.ib-tier-meta{font-family:var(--ib-mono);font-size:10px;font-weight:700;letter-spacing:.06em;color:var(--ib-muted);text-transform:uppercase;margin-bottom:3px;}
.ib-anchor{gap:10px;background:var(--ib-accent-soft);border:1px solid var(--ib-accent);border-radius:14px;padding:14px 16px;color:var(--ib-ink);font-weight:800;}
.ib-layer-stack{display:flex;flex-direction:column;gap:12px;}
.ib-layer{display:flex;align-items:center;gap:12px;min-width:0;}
.ib-layer-num{
  flex:0 0 auto;display:inline-flex;align-items:center;justify-content:center;
  width:26px;height:26px;border-radius:999px;font-size:12px;font-weight:800;color:#ffffff;background:var(--ib-accent);
}
.ib-layer-band{
  flex:0 0 46%;padding:16px 18px;border-radius:10px;font-weight:800;color:var(--ib-ink);min-width:0;
  background:linear-gradient(158deg,var(--ib-accent-soft),var(--ib-card-b));
  border:1px solid var(--ib-accent);box-shadow:0 3px 0 -1px var(--ib-line),0 8px 16px rgba(0,0,0,.06);
}
.ib-leader{flex:0 0 34px;height:0;border-top:1.5px dashed var(--ib-line);}
.ib-layer-note{flex:1;min-width:0;font-size:12.5px;font-weight:600;color:var(--ib-ink-soft);}
"""
