"""Visual System v2 — multi-prompt benchmark across categories.

Hardens the v2 pipeline against professional-grade prompts that expose
quality gaps the single-prompt e2e scripts miss. 9 detailed prompts span:

  • Architecture (3): distributed system, k8s topology, event pipeline
  • Infographic (3): exec dashboard, market quadrant, cost breakdown
  • Storytelling (3): customer journey, transformation case, vision hero

Each prompt runs once through the composer's auto-routing (which path is
selected depends on capability). Optionally, opt-in flags fire additional
runs through forced paths for cross-comparison:

  RUN_GEMMA_COMPARE=1  → also run all 9 via GEMMA_FREEFORM forced
  RUN_HYBRID_DEMO=1    → run vision_hero via hybrid (Klein required)

Outputs:
  backend/scripts/benchmark_output/
    <prompt_id>_<path>.svg
    <prompt_id>_<path>.png
    benchmark_scores.json
    index.html  ← open in browser for full side-by-side review

Usage:
    python backend/scripts/visual_v2_benchmark.py
    RUN_HYBRID_DEMO=1 python backend/scripts/visual_v2_benchmark.py
    RUN_GEMMA_COMPARE=1 RUN_HYBRID_DEMO=1 python backend/scripts/visual_v2_benchmark.py
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from services.svg_renderer import render_svg_to_png  # noqa: E402
from services.visual_capability import get_capability  # noqa: E402
from services.visual_composer import (  # noqa: E402
    ComposedVisual,
    GenerationPath,
    visual_composer,
)

OUT_DIR = Path(__file__).resolve().parent / "benchmark_output"

RUN_GEMMA_COMPARE = bool(os.getenv("RUN_GEMMA_COMPARE"))
RUN_HYBRID_DEMO = bool(os.getenv("RUN_HYBRID_DEMO"))
SKIP_AUTO = bool(os.getenv("SKIP_AUTO"))  # Skip pass 1 (auto routing) — useful
                                          # when re-running JUST gemma_compare
                                          # without burning ~12 min on auto again


# ──────────────────────────────────────────────────────────────────────
# Benchmark prompts — detailed, professional-grade
# ──────────────────────────────────────────────────────────────────────
PROMPTS = [
    # ============ ARCHITECTURE (3) ============
    {
        "id": "distributed_ecommerce",
        "category": "Architecture",
        "title": "Multi-region e-commerce deployment",
        "audience": "Enterprise security review board",
        "prompt": (
            "Acme Corp's new e-commerce platform deployment topology, designed "
            "for 100k peak queries-per-second across 3 AWS regions (us-east-1, "
            "us-west-2, eu-west-1). Show: external CDN (CloudFront) routing to "
            "regional Application Load Balancers, then to an API Gateway (Kong), "
            "then to 6 backend microservices: User, Catalog, Cart, Checkout, "
            "Payment, Order. Each microservice owns its own PostgreSQL database. "
            "Catalog also reads from a shared Elasticsearch cluster for product "
            "search. Payment integrates with Stripe externally. Order writes to "
            "a Kafka event bus, consumed by Analytics in a separate VPC. Each "
            "service runs in 3 availability zones for HA. The visual must show "
            "the data flow direction for a typical purchase request."
        ),
    },
    {
        "id": "k8s_cluster_topology",
        "category": "Architecture",
        "title": "Production Kubernetes cluster topology",
        "audience": "Platform engineering team",
        "prompt": (
            "Production Kubernetes cluster topology showing namespace boundaries, "
            "ingress, service mesh, and observability. Components: NGINX Ingress "
            "Controller at the edge. Istio service mesh providing mTLS between "
            "all services. Three application namespaces: 'frontend' (React SPA "
            "served from a static deploy + 3-replica nginx pod), 'api' (12 "
            "microservices, each a 3-replica Deployment), 'data' (2 StatefulSets "
            "for Postgres-HA and Redis-cluster). One platform namespace 'observ' "
            "running Prometheus + Grafana + Loki + Tempo. Cert-manager auto-renews "
            "TLS. ArgoCD watches GitOps repo and reconciles deployments. Use "
            "swimlanes to separate the namespaces visually. Audience: SRE team "
            "design review."
        ),
    },
    {
        "id": "event_driven_pipeline",
        "category": "Architecture",
        "title": "Event-driven CQRS data pipeline",
        "audience": "Engineering leadership architecture review",
        "prompt": (
            "Event-driven CQRS architecture for a financial trading platform. "
            "Write path: Trader UI submits order via REST → Order API validates → "
            "writes to Order Write-Store (PostgreSQL) → emits OrderPlaced event "
            "to Kafka. Read path: Projection service consumes from Kafka → "
            "denormalizes into Read-Store (DynamoDB) → Reporting API serves "
            "from Read-Store. Volume: 50k orders/sec peak, 99.9% latency under "
            "200ms write, sub-50ms read. Also show: Risk Engine consuming the "
            "same Kafka stream for real-time exposure calculations, with a "
            "separate output topic for risk alerts. Audience: Chief Architect "
            "review board."
        ),
    },

    # ============ INFOGRAPHIC / DATA (3) ============
    {
        "id": "quarterly_dashboard",
        "category": "Infographic",
        "title": "Q3 2026 SaaS performance dashboard",
        "audience": "Board of directors",
        "prompt": (
            "Quarterly performance summary for a B2B SaaS company. Key metrics "
            "to feature prominently: ARR $42.3M (up 38% YoY), Net Revenue "
            "Retention 124%, Logo Retention 96%, Customer Count 1,840 (up 31% "
            "YoY), Average Contract Value $23k, Sales Cycle 47 days, Gross "
            "Margin 78%, Cash Burn $1.2M/mo (improving from $1.8M). Highlight: "
            "expansion into European market generated $4.1M of the YoY growth. "
            "Lowlight: enterprise segment closed-won rate dropped to 18% from "
            "24% last quarter. Make 6 of the metrics the dominant visual "
            "elements (large numbers with context). Audience: board of "
            "directors quarterly review."
        ),
    },
    {
        "id": "market_quadrant",
        "category": "Infographic",
        "title": "Competitor positioning quadrant",
        "audience": "Sales kickoff strategy session",
        "prompt": (
            "Competitive positioning for the customer data platform market. "
            "Two axes: X = Implementation Complexity (low to high), Y = Feature "
            "Depth (basic to advanced). 8 competitors to place: Segment "
            "(low-complexity, mid-depth), Rudderstack (mid-complexity, mid-depth), "
            "mParticle (high-complexity, deep), Tealium (very-high complexity, "
            "deep), our product 'DataCanvas' (mid-complexity, advanced), Hightouch "
            "(low-complexity, advanced for reverse-ETL only), Census (low, advanced "
            "reverse-ETL), Treasure Data (very-high complexity, advanced). Label "
            "each quadrant: 'Quick Wins' (top-left), 'Leaders' (top-right), "
            "'Niche Tools' (bottom-left), 'Enterprise Heavy' (bottom-right). "
            "Audience: Sales kickoff strategy session — they need to know where "
            "we sit relative to deals they'll face."
        ),
    },
    {
        "id": "cost_breakdown",
        "category": "Infographic",
        "title": "Cloud cost breakdown FY26",
        "audience": "FinOps team + CFO",
        "prompt": (
            "Comparison of AWS cloud costs across our 3 product lines, fiscal "
            "year 2026 to-date. Products: Platform ($4.2M total — 62% compute, "
            "23% storage, 10% data transfer, 5% other), Analytics ($1.8M total "
            "— 35% compute, 48% storage, 12% data transfer, 5% other), ML "
            "Training ($2.7M total — 88% compute on GPU instances, 8% storage, "
            "3% data transfer, 1% other). Reserved instance coverage: Platform "
            "78%, Analytics 92%, ML Training 12%. Highlight the cost-per-customer "
            "ratio: Platform $2,280, Analytics $980, ML Training $14,500 (only 186 "
            "customers). Compare each category across the three products in a "
            "table. Audience: FinOps team + CFO cost-optimization review."
        ),
    },

    # ============ STORYTELLING (3) ============
    {
        "id": "customer_journey",
        "category": "Storytelling",
        "title": "Customer journey: lead → champion",
        "audience": "Growth team OKR planning",
        "prompt": (
            "Customer journey for a B2B SaaS company from anonymous visitor to "
            "expansion champion. 6 stages: Discover (anonymous visit, 312k/mo), "
            "Engage (signup for trial, 18k/mo, 5.8% conversion), Activate "
            "(complete onboarding + first integration, 11k/mo, 61% from trial), "
            "Convert (start paid plan, 2.4k/mo, 22% activation-to-paid), Expand "
            "(grow seat count by 50%+, 380/quarter, 16% of paid), Champion "
            "(refer ≥1 new logo OR speak in marketing case study, 92/quarter, "
            "24% of expansion). For each stage list: 2-3 key actions the user "
            "takes, the metric tracked, the team that owns it (Marketing, "
            "Growth, Product, CS, Sales). Highlight the biggest leak: 78% "
            "drop-off between Engage and Activate. Audience: growth team "
            "quarterly OKR planning."
        ),
    },
    {
        "id": "transformation_case",
        "category": "Storytelling",
        "title": "Monolith → microservices transformation",
        "audience": "Tech all-hands case study",
        "prompt": (
            "Before/after case study of our 18-month transformation from monolith "
            "to microservices. Before state: single PHP monolith on bare-metal "
            "servers, 14-day release cadence, 4-hour deploy windows requiring "
            "downtime, single MySQL master writing 8k QPS at peak, on-call "
            "engineer paged 4-6x per week, ~2000 lines per pull request average. "
            "After state: 47 microservices running on Kubernetes (AWS EKS), "
            "continuous deploy with ~120 deploys/day, zero-downtime blue-green "
            "rollouts, per-service databases (mostly PostgreSQL + Redis) with no "
            "single point handling more than 3k QPS, on-call paged ~1x per week, "
            "~180 lines per PR average. The transition mechanism: explicit "
            "strangler-fig migration with feature flags and traffic shadowing. "
            "Audience: engineering all-hands case study presentation."
        ),
    },
    {
        "id": "vision_hero",
        "category": "Storytelling",
        "title": "Vision: the future of cloud computing is serverless",
        "audience": "CTO pitch deck cover slide",
        "persuade_hero": True,  # Triggers hybrid path if Klein available
        "prompt": (
            "A hero image for our marketing campaign about the future of cloud "
            "computing. We are making the case for serverless architectures as "
            "the next generation of application deployment — the inevitable "
            "evolution beyond Kubernetes complexity. Three key benefits to "
            "highlight in supporting callouts: (1) Ship features 4x faster with "
            "zero infrastructure overhead, (2) Pay only for actual execution — "
            "typical 60-80% cost reduction vs always-on containers, (3) Infinite "
            "elasticity from 0 to 100k QPS with no capacity planning. The "
            "narrative: serverless is to containers what containers were to VMs "
            "— a step-change in developer productivity that becomes obvious "
            "in retrospect. Audience: CTO-targeted pitch deck cover slide."
        ),
    },
]


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────
def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", s.lower()).strip("_")[:60]


async def run_one(
    prompt_def: dict,
    path_override: Optional[GenerationPath],
    label: str,
) -> dict:
    """Run a single composer call and persist outputs. Returns a record dict."""
    pid = prompt_def["id"]
    print(f"\n[{label}] {pid} ({prompt_def['category']}) — path={path_override.value if path_override else 'auto'}")
    t0 = time.time()
    try:
        visual: ComposedVisual = await visual_composer.compose(
            content=prompt_def["prompt"],
            force_path=path_override,
        )
    except Exception as e:
        print(f"  ✗ exception: {e}")
        return {
            "id": pid,
            "label": label,
            "exception": str(e),
            "elapsed_s": round(time.time() - t0, 1),
        }
    elapsed = time.time() - t0

    record = {
        "id": pid,
        "label": label,
        "category": prompt_def["category"],
        "title_def": prompt_def["title"],
        "title_actual": visual.title,
        "audience": prompt_def["audience"],
        "path": visual.path.value,
        "setup": visual.setup.value,
        "output_format": visual.output_format.value,
        "idiom": visual.template_id,
        "model_used": visual.model_used,
        "retry_count": visual.retry_count,
        "generation_ms": visual.generation_ms,
        "elapsed_s": round(elapsed, 1),
        "success": visual.success,
        "error": visual.error,
        "svg_path": None,
        "png_path": None,
    }

    if visual.critic_score:
        c = visual.critic_score
        record["critic"] = {
            "legibility": c.legibility,
            "hierarchy": c.hierarchy,
            "balance": c.balance,
            "color_harmony": c.color_harmony,
            "message_clarity": c.message_clarity,
            "overall": c.overall,
            "strengths": c.strengths,
            "weaknesses": c.weaknesses,
            "suggestions": c.suggestions,
        }
        print(
            f"  ✓ overall={c.overall:.2f} in {elapsed:.1f}s "
            f"(leg={c.legibility:.2f} hie={c.hierarchy:.2f} bal={c.balance:.2f} "
            f"col={c.color_harmony:.2f} msg={c.message_clarity:.2f})"
        )
    else:
        print(f"  ✓ generated in {elapsed:.1f}s (no critic score)")

    # Save SVG + PNG
    file_base = f"{pid}_{label}"
    if visual.svg_markup:
        svg_path = OUT_DIR / f"{file_base}.svg"
        svg_path.write_text(visual.svg_markup)
        record["svg_path"] = svg_path.name
        png = await render_svg_to_png(visual.svg_markup)
        if png:
            png_path = OUT_DIR / f"{file_base}.png"
            png_path.write_bytes(png)
            record["png_path"] = png_path.name
    elif visual.mermaid_code:
        # Fallback path returned Mermaid (Tier D template)
        mm_path = OUT_DIR / f"{file_base}.mmd"
        mm_path.write_text(visual.mermaid_code)
        record["svg_path"] = mm_path.name  # show as link in index
    return record


def write_index_html(records: list[dict], capability_summary: str):
    """Generate the comparison index. Groups results by prompt_id; if a prompt
    has multiple runs (e.g., auto + gemma_compare), they're shown side by side."""
    # Group by prompt id, preserving prompt order
    by_id: dict[str, list[dict]] = {}
    order: list[str] = []
    for r in records:
        if r["id"] not in by_id:
            order.append(r["id"])
            by_id[r["id"]] = []
        by_id[r["id"]].append(r)

    # Aggregate per-path stats
    path_stats: dict[str, dict] = {}
    for r in records:
        if not r.get("critic"):
            continue
        path = r["path"]
        path_stats.setdefault(path, {"scores": [], "count": 0, "ms": []})
        path_stats[path]["scores"].append(r["critic"]["overall"])
        path_stats[path]["ms"].append(r["generation_ms"])
        path_stats[path]["count"] += 1

    stats_html_parts = []
    for path, st in path_stats.items():
        avg = sum(st["scores"]) / len(st["scores"]) if st["scores"] else 0
        avg_s = (sum(st["ms"]) / len(st["ms"])) / 1000 if st["ms"] else 0
        stats_html_parts.append(
            f'<div><strong style="color:#635BFF;font-size:22px">{avg:.2f}</strong>'
            f' <span style="color:#8898AA;font-size:12px">avg overall · {st["count"]} runs · {avg_s:.0f}s mean</span>'
            f' <div style="color:#0A2540;font-size:13px">{path}</div></div>'
        )
    stats_html = "".join(stats_html_parts)

    sections = []
    for pid in order:
        runs = by_id[pid]
        first = runs[0]
        cards = []
        for r in runs:
            cards.append(_run_card_html(r))
        sections.append(f"""
        <section style="margin-bottom:60px;border-bottom:1px solid #E3E8EE;padding-bottom:40px">
          <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px">
            <h2 style="margin:0;color:#0A2540;font-size:24px">
              {first['category']} · {first['title_def']}
            </h2>
            <span style="color:#8898AA;font-size:13px">audience: {first['audience']}</span>
          </div>
          <details style="margin-bottom:16px">
            <summary style="cursor:pointer;color:#635BFF;font-size:13px">prompt</summary>
            <p style="color:#425466;font-size:13px;line-height:1.6;margin-top:8px;
                      background:#F6F9FC;padding:12px 16px;border-radius:8px;white-space:pre-wrap">
              {_find_prompt_text(pid)}
            </p>
          </details>
          <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(560px,1fr));gap:20px">
            {''.join(cards)}
          </div>
        </section>
        """)

    html = f"""<!doctype html>
<html><head><meta charset='utf-8'>
<title>Visual v2 Benchmark</title>
<style>
  body {{ font-family: Inter, system-ui, -apple-system, sans-serif;
          max-width: 1700px; margin: 40px auto; padding: 0 24px;
          color: #0A2540; background: #fff }}
  h1 {{ font-size: 36px; margin: 0 0 8px }}
  .meta {{ color: #425466; font-size: 14px; margin-bottom: 32px }}
  .stats {{ background:#F6F9FC; padding:20px; border-radius:8px;
            display:flex; gap:32px; flex-wrap:wrap; margin-bottom:32px }}
  .axis-bar {{ display:inline-block; width:80px; height:8px;
               background:#E3E8EE; border-radius:4px; vertical-align:middle;
               margin-left:8px; position:relative; overflow:hidden }}
  .axis-fill {{ position:absolute; left:0; top:0; height:100%; border-radius:4px }}
</style></head>
<body>
  <h1>Visual v2 Benchmark — Professional-Grade Outputs</h1>
  <p class="meta">
    Capability: <code>{capability_summary}</code><br>
    {len(records)} runs across {len(by_id)} prompts ·
    Generated {time.strftime('%Y-%m-%d %H:%M')}
  </p>
  <div class="stats">{stats_html or '<em>No critic scores recorded</em>'}</div>
  {''.join(sections)}
</body></html>"""
    (OUT_DIR / "index.html").write_text(html)


def _find_prompt_text(pid: str) -> str:
    for p in PROMPTS:
        if p["id"] == pid:
            return p["prompt"]
    return ""


def _bar_color(score: float) -> str:
    if score >= 0.8:
        return "#00C896"
    if score >= 0.6:
        return "#635BFF"
    if score >= 0.4:
        return "#F5A623"
    return "#D32F2F"


def _run_card_html(r: dict) -> str:
    """One run's card in the side-by-side grid."""
    img_block = ""
    if r.get("png_path"):
        img_block = (
            f'<img src="{r["png_path"]}" alt="rendered" '
            f'style="width:100%;border:1px solid #E3E8EE;border-radius:8px;display:block;margin-top:8px"/>'
        )
    elif r.get("svg_path"):
        img_block = (
            f'<div style="padding:20px;background:#FFF4F4;border:1px dashed #D32F2F;'
            f'border-radius:8px;color:#D32F2F">PNG not rendered — see {r["svg_path"]}</div>'
        )
    elif r.get("exception"):
        img_block = (
            f'<div style="padding:20px;background:#FFF4F4;border:1px dashed #D32F2F;'
            f'border-radius:8px;color:#D32F2F">EXCEPTION: {r["exception"]}</div>'
        )

    critic_block = ""
    if r.get("critic"):
        c = r["critic"]
        axes = [
            ("Legibility", c["legibility"]),
            ("Hierarchy", c["hierarchy"]),
            ("Balance", c["balance"]),
            ("Color", c["color_harmony"]),
            ("Clarity", c["message_clarity"]),
        ]
        rows = "".join(
            f'<div style="font-size:11px;color:#425466;margin:2px 0">'
            f'<span style="display:inline-block;width:78px">{label}</span>'
            f'<strong style="color:#0A2540">{val:.2f}</strong>'
            f'<span class="axis-bar"><span class="axis-fill" '
            f'style="width:{int(val * 100)}%;background:{_bar_color(val)}"></span></span>'
            f'</div>'
            for label, val in axes
        )
        weak = ""
        if c.get("weaknesses"):
            weak = (
                "<details style='margin-top:6px'><summary style='cursor:pointer;color:#D32F2F;font-size:11px'>weaknesses</summary>"
                "<ul style='margin:4px 0 0;padding-left:16px;font-size:11px;color:#425466'>"
                + "".join(f"<li>{w}</li>" for w in c["weaknesses"])
                + "</ul></details>"
            )
        critic_block = (
            f'<div style="background:#F6F9FC;padding:10px 12px;border-radius:6px;margin-top:8px">'
            f'<div style="font-size:13px;color:#0A2540;font-weight:600;margin-bottom:6px">'
            f'Critic: {c["overall"]:.2f} overall {"✓" if c["overall"] >= 0.7 else "⚠"}</div>'
            f'{rows}{weak}'
            f'</div>'
        )

    title_label = r.get("title_actual", "")
    return f"""
    <div style="padding:12px;background:#fff;border:1px solid #E3E8EE;border-radius:10px">
      <div style="display:flex;justify-content:space-between;align-items:baseline">
        <strong style="font-size:13px;color:#635BFF">{r['label']}</strong>
        <span style="font-size:11px;color:#8898AA">{r['path']} · {r['idiom']} · {r['elapsed_s']:.0f}s</span>
      </div>
      <div style="font-size:13px;color:#0A2540;margin-top:4px">{title_label}</div>
      {img_block}
      {critic_block}
    </div>"""


# ──────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────
async def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cap = await get_capability()
    print("=" * 70)
    print("Visual v2 Benchmark")
    print(f"Capability: {cap.summary()}")
    print(f"RUN_GEMMA_COMPARE={RUN_GEMMA_COMPARE}  RUN_HYBRID_DEMO={RUN_HYBRID_DEMO}")
    print(f"Output dir: {OUT_DIR}")
    print("=" * 70)

    # If skipping the auto pass, load prior records so the merged index still
    # shows the auto results alongside the new gemma_compare runs
    records: list[dict] = []
    prior_path = OUT_DIR / "benchmark_scores.json"
    if SKIP_AUTO and prior_path.exists():
        try:
            records = json.loads(prior_path.read_text())
            print(f"\n=== Loaded {len(records)} prior records (SKIP_AUTO) ===")
        except Exception as e:
            print(f"  (could not load prior records: {e})")

    # Pass 1: every prompt via composer auto-routing
    if not SKIP_AUTO:
        print("\n=== Pass 1: composer auto-routing ===")
        for prompt in PROMPTS:
            records.append(await run_one(prompt, path_override=None, label="auto"))

    # Pass 2 (opt-in): all prompts via GEMMA_FREEFORM forced
    if RUN_GEMMA_COMPARE and cap.gemma_model:
        print("\n=== Pass 2: forced GEMMA_FREEFORM ===")
        for prompt in PROMPTS:
            records.append(await run_one(prompt, path_override=GenerationPath.GEMMA_FREEFORM, label="gemma"))

    # Pass 3 (opt-in): one hybrid demo
    if RUN_HYBRID_DEMO and cap.gemma_model and cap.klein_model:
        print("\n=== Pass 3: hybrid (Klein) demo ===")
        hero_prompt = next((p for p in PROMPTS if p.get("persuade_hero")), None)
        if hero_prompt:
            records.append(await run_one(hero_prompt, path_override=GenerationPath.GEMMA_FREEFORM, label="hybrid"))

    # Persist + render
    (OUT_DIR / "benchmark_scores.json").write_text(json.dumps(records, indent=2))
    write_index_html(records, cap.summary())

    # Final summary
    print("\n" + "=" * 70)
    print("Benchmark complete")
    scored = [r for r in records if r.get("critic")]
    if scored:
        avg = sum(r["critic"]["overall"] for r in scored) / len(scored)
        print(f"  {len(scored)}/{len(records)} runs with critic scores")
        print(f"  Avg overall: {avg:.2f}")
    print(f"  Open: {OUT_DIR / 'index.html'}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
