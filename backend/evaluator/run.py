"""Headless Evaluator entrypoint (2.2.0 Wave 1).

Runs the full combo evaluation without the UI/HTTP layer, persists the scored
result (run_full_evaluation does this internally), and — unless --no-compare —
diffs the overall score against the previous persisted run, exiting non-zero on a
regression so release.sh (and the Wave-3 triage loop) can gate on quality.

Usage:
  python -m evaluator.run                 # run + persist + regression-check vs previous
  python -m evaluator.run --no-compare    # run + persist only
  python -m evaluator.run --threshold 8   # allow overall to drop up to 8 pts (default 5)
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

    baseline = None if args.no_compare else get_latest_result()
    summary = await run_full_evaluation()  # scores + persists internally
    new_score = float(summary.overall_score)
    is_reg, drop = evaluate_regression(baseline, new_score, args.threshold)

    base_score = baseline.get("overall_score") if baseline else None
    payload = {
        "overall_score": new_score,
        "overall_grade": summary.overall_grade,
        "baseline_score": base_score,
        "drop": drop,
        "threshold": args.threshold,
        "regression": is_reg,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"[eval] overall {new_score:.1f} ({summary.overall_grade})")
        if base_score is not None and drop is not None:
            print(f"[eval] baseline {float(base_score):.1f} → drop {drop:+.1f} "
                  f"(threshold {args.threshold})")
        print(f"[eval] {'REGRESSION' if is_reg else 'OK'}")
    return 1 if is_reg else 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m evaluator.run", description="Headless LocalBook Evaluator."
    )
    p.add_argument("--no-compare", action="store_true",
                   help="run + persist only; skip the baseline regression check")
    p.add_argument("--threshold", type=float, default=5.0,
                   help="max allowed overall-score drop vs baseline before it's a regression (default 5)")
    p.add_argument("--json", action="store_true", help="emit a machine-readable JSON summary")
    args = p.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
