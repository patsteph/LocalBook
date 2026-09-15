"""Headless Evaluator entrypoint (2.2.0 Wave 1).

Runs the full combo evaluation without the UI/HTTP layer, persists the scored
result (run_full_evaluation does this internally), and — unless --no-compare —
diffs the overall score against the previous persisted run, exiting non-zero on a
regression so release.sh (and the Wave-3 triage loop) can gate on quality.

Two independent gates, because they catch different things:
  • REGRESSION — the overall score dropped more than `--threshold` vs the previous run.
    Blind to anything that was already broken at the baseline.
  • BROKEN — a capability scored below the fail floor, however good the average is. The
    overall is a weighted mean, so one dead feature dissolves into it (2026-09-14: a release
    run reported 87.7 B+ with a category at the bottom of the table).

Usage:
  python -m evaluator.run                 # run + persist + both gates
  python -m evaluator.run --no-compare    # run + persist only
  python -m evaluator.run --threshold 8   # allow overall to drop up to 8 pts (default 5)
  python -m evaluator.run --allow-failures # ignore the fail floor (exploratory runs)
  python -m evaluator.run --json          # machine-readable summary to stdout

NOTE: the actual run needs a live model (Ollama/MLX) + writes to the production
data dir (a temp test notebook, auto-cleaned; plus eval_results/). It is a
local / primary-machine step, NOT a CI job — CI unit-tests the pure regression
logic below instead.
"""
import argparse
import asyncio
import json
import sys
from typing import Optional, Tuple


def scoring_changed(baseline: Optional[dict], current_version: int) -> bool:
    """True when the baseline was scored under a DIFFERENT scoring model.

    A regression check compares two numbers and assumes they mean the same thing. When the
    scoring model changes they do not, and the comparison is meaningless in both directions:
    it can invent a regression that is really a fix, or hide a real one behind a scoring
    change that happened to raise the average. 2026-09-14 is exactly that case — faithfulness
    stopped contributing a flat 60 and four categories started counting.

    Baselines written before versioning carry no `scoring_version`; those are treated as
    version 1, which is what they were.
    """
    if not baseline:
        return False
    return int(baseline.get("scoring_version", 1) or 1) != int(current_version)


def blocking_failures(summary: Optional[dict]) -> list:
    """Capabilities that FAILED outright, independent of the overall average.

    The overall score is a weighted MEAN, so one broken capability dissolves into it — the
    2026-09-14 release run reported 87.7 (B+) with a category sitting at the bottom of the
    table. A mean is the wrong instrument for "is anything actually broken"; that needs a
    floor. Regression-vs-baseline does not catch it either: a capability that has been broken
    since the baseline shows no drop at all.

    Reuses the verdicts `feature_parity` already computes (score < 40 and not skipped → fail)
    rather than re-deriving a second definition of "broken" that could disagree with what the
    UI shows. Skipped/not-applicable categories are never blockers — a feature that is not part
    of this combo has not failed.
    """
    if not summary:
        return []
    return [
        {"category": e.get("category"), "feature": e.get("feature"),
         "score": float(e.get("score") or 0)}
        for e in (summary.get("feature_parity") or [])
        if e.get("verdict") == "fail"
    ]


def evaluate_regression(
    baseline: Optional[dict], new_score: float, threshold: float
) -> Tuple[bool, Optional[float]]:
    """Pure regression decision. Returns (is_regression, drop).

    A regression is an overall-score drop STRICTLY GREATER THAN `threshold` vs the
    baseline run's overall_score. No baseline (first run / missing field) → never a
    regression. `drop` is baseline - new (positive = got worse); None when no baseline.
    """
    if not baseline:
        return False, None
    base = baseline.get("overall_score")
    if base is None:
        return False, None
    drop = float(base) - float(new_score)
    return (drop > threshold), drop


async def _run(args) -> int:
    # Heavy imports are lazy so `--help` and the unit test stay light.
    from evaluator.evaluator_service import run_full_evaluation, get_latest_result

    from evaluator.scoring import SCORING_VERSION

    baseline = None if args.no_compare else get_latest_result()
    summary = await run_full_evaluation()  # scores + persists internally
    new_score = float(summary.overall_score)

    # A scoring-model change re-baselines rather than reporting itself as a regression.
    rebaselined = scoring_changed(baseline, SCORING_VERSION)
    if rebaselined:
        is_reg, drop = False, None
    else:
        is_reg, drop = evaluate_regression(baseline, new_score, args.threshold)
    blockers = [] if args.allow_failures else blocking_failures(summary.to_dict())

    base_score = baseline.get("overall_score") if baseline else None
    payload = {
        "overall_score": new_score,
        "overall_grade": summary.overall_grade,
        "baseline_score": base_score,
        "drop": drop,
        "threshold": args.threshold,
        "regression": is_reg,
        "readiness": (summary.production_readiness or {}).get("headline"),
        "blocking_failures": blockers,
        "scoring_version": SCORING_VERSION,
        "rebaselined": rebaselined,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"[eval] overall {new_score:.1f} ({summary.overall_grade})")
        if rebaselined:
            print(f"[eval] scoring model changed (v{int((baseline or {}).get('scoring_version', 1))}"
                  f" → v{SCORING_VERSION}) — RE-BASELINED, not compared. "
                  f"This run becomes the new baseline.")
        if base_score is not None and drop is not None:
            print(f"[eval] baseline {float(base_score):.1f} → drop {drop:+.1f} "
                  f"(threshold {args.threshold})")
        readiness = (summary.production_readiness or {}).get("headline")
        if readiness:
            print(f"[eval] readiness: {readiness}")
        for b in blockers:
            print(f"[eval] ✗ BROKEN: {b['feature']} scored {b['score']:.0f}")
        verdict = "REGRESSION" if is_reg else ("BROKEN" if blockers else "OK")
        print(f"[eval] {verdict}")
    return 1 if (is_reg or blockers) else 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m evaluator.run", description="Headless LocalBook Evaluator."
    )
    p.add_argument("--no-compare", action="store_true",
                   help="run + persist only; skip the baseline regression check")
    p.add_argument("--threshold", type=float, default=5.0,
                   help="max allowed overall-score drop vs baseline before it's a regression (default 5)")
    p.add_argument("--json", action="store_true", help="emit a machine-readable JSON summary")
    p.add_argument("--allow-failures", action="store_true",
                   help="do not fail the run when a capability scores below the fail floor "
                        "(exploratory runs on a model you already know is partial)")
    args = p.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
