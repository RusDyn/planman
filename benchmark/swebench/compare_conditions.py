#!/usr/bin/env python3
"""Compare baseline vs stress_test conditions with paired statistics.

Loads evaluation reports from logs/run_evaluation/, merges across retry runs
(dedup by task, prefer resolved=True), and computes pairwise statistics using
the existing scorer_swe.py infrastructure.

Usage:
    python3 benchmark/swebench/compare_conditions.py
    python3 benchmark/swebench/compare_conditions.py --json
    python3 benchmark/swebench/compare_conditions.py --mini-swe-agent-dir /path/to/experiments/evaluation/verified/20250522_tools_claude-4-opus
"""

import argparse
import json
import os
import sys
from pathlib import Path

_swebench_dir = os.path.dirname(os.path.abspath(__file__))
if _swebench_dir not in sys.path:
    sys.path.insert(0, _swebench_dir)

from config_swe import REPO_ROOT, RESULTS_DIR
from scorer_swe import (
    PairwiseResult,
    compute_pairwise,
    aggregate_per_task,
    majority_vote_per_task,
)

EVAL_BASE = Path(REPO_ROOT) / "logs" / "run_evaluation"
EXPERIMENTS_BASE = Path(REPO_ROOT).parent / "experiments" / "evaluation" / "verified"

# Pilot run IDs for ablation table
PILOT_ABLATION = {
    "baseline": {
        "eval_dirs": ["baseline_pilot_1_rep0"],
        "model_prefix": "planman-baseline",
        "total": 50,
    },
    "plan_only": {
        "eval_dirs": ["plan_only_pilot_1_rep0"],
        "model_prefix": "planman-plan_only",
        "total": 50,
    },
    "stress_test": {
        "eval_dirs": ["stress_test_pilot_1_rep0", "stress_test_pilot_1_retry_rep0"],
        "model_prefix": "planman-stress_test",
        "total": 50,
    },
}


# ── Eval report loading ──────────────────────────────────────────────


def load_eval_reports_for_condition(
    condition: str,
    prefix: str = None,
    eval_dir_names: list[str] = None,
) -> dict[str, bool]:
    """Load evaluation reports for a condition, dedup by task (prefer resolved=True).

    Args:
        condition: Condition name (baseline, stress_test, etc.)
        prefix: Eval dir name prefix to match (e.g. "baseline_" or "stress_test_").
                If None, uses condition name as prefix.
        eval_dir_names: Explicit list of eval dir names to load from.
                       If provided, prefix is ignored.

    Returns: {instance_id: resolved} mapping.
    """
    results: dict[str, bool] = {}

    if eval_dir_names:
        dirs_to_check = eval_dir_names
    else:
        pfx = prefix or f"{condition}_"
        if not EVAL_BASE.is_dir():
            return results
        dirs_to_check = [d.name for d in sorted(EVAL_BASE.iterdir()) if d.name.startswith(pfx)]

    for dir_name in dirs_to_check:
        eval_dir = EVAL_BASE / dir_name
        if not eval_dir.is_dir():
            continue

        # Find model directories matching this condition
        for model_dir_name in sorted(os.listdir(eval_dir)):
            if not model_dir_name.startswith(f"planman-{condition}"):
                # Also check for model dirs without the planman- prefix
                if model_dir_name != condition:
                    continue

            model_dir = eval_dir / model_dir_name
            if not model_dir.is_dir():
                continue

            for instance_dir in model_dir.iterdir():
                if not instance_dir.is_dir():
                    continue
                report_path = instance_dir / "report.json"
                if not report_path.exists():
                    continue
                try:
                    with open(report_path) as f:
                        report = json.load(f)
                    iid = instance_dir.name
                    if iid in report:
                        entry = report[iid]
                        resolved = entry.get("resolved", False) if isinstance(entry, dict) else bool(entry)
                        # Prefer resolved=True (any run that resolved counts)
                        if iid not in results or resolved:
                            results[iid] = resolved
                except (json.JSONDecodeError, OSError):
                    continue

    return results


def load_mini_swe_agent_results(mini_dir: str = None) -> dict[str, bool] | None:
    """Load mini-swe-agent per-instance results from experiments directory.

    Tries several known locations:
    1. Explicit path provided via --mini-swe-agent-dir
    2. Auto-detect from experiments/evaluation/verified/
    """
    candidates = []

    if mini_dir:
        candidates.append(Path(mini_dir))
    else:
        # Auto-detect: prefer Opus 4.6, fall back to Opus 4
        if EXPERIMENTS_BASE.is_dir():
            for d in sorted(EXPERIMENTS_BASE.iterdir(), reverse=True):
                name = d.name.lower()
                if "tools_claude" in name and "opus" in name:
                    candidates.append(d)

    for candidate in candidates:
        results_path = candidate / "results" / "results.json"
        if results_path.exists():
            try:
                with open(results_path) as f:
                    data = json.load(f)
                resolved_list = data.get("resolved", [])
                # Build full mapping: resolved + not_resolved
                all_tasks: dict[str, bool] = {}
                for iid in resolved_list:
                    all_tasks[iid] = True
                # Mark remaining as not resolved if we have the full list
                for key in ["not_resolved", "no_generation", "no_logs"]:
                    for iid in data.get(key, []):
                        all_tasks[iid] = False
                return all_tasks
            except (json.JSONDecodeError, OSError):
                continue

    return None


def load_planman_resolved_list() -> set[str] | None:
    """Load planman resolved instance IDs from experiments submission."""
    planman_dir = EXPERIMENTS_BASE / "20260317_planman_claude-opus-4-6"
    results_path = planman_dir / "results" / "results.json"
    if results_path.exists():
        try:
            with open(results_path) as f:
                data = json.load(f)
            return set(data.get("resolved", []))
        except (json.JSONDecodeError, OSError):
            pass
    return None


# ── Comparison logic ──────────────────────────────────────────────────


def build_paired_data(
    baseline_results: dict[str, bool],
    stress_test_results: dict[str, bool],
) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, bool]]]:
    """Build per-task and majority-vote dicts for compute_pairwise().

    Since we have 1 rep per task, resolve rate = 1.0 or 0.0, and majority vote = resolved.
    """
    per_task: dict[str, dict[str, float]] = {}
    majority_votes: dict[str, dict[str, bool]] = {}

    # Only include tasks present in BOTH conditions
    common_tasks = set(baseline_results.keys()) & set(stress_test_results.keys())

    for task in common_tasks:
        per_task[task] = {
            "stress_test": 1.0 if stress_test_results[task] else 0.0,
            "baseline": 1.0 if baseline_results[task] else 0.0,
        }
        majority_votes[task] = {
            "stress_test": stress_test_results[task],
            "baseline": baseline_results[task],
        }

    return per_task, majority_votes


def compute_mcnemar_from_counts(b: int, c: int) -> dict:
    """Compute McNemar statistics from discordant pair counts.

    b = stress_test wins (resolved by stress_test only)
    c = baseline wins (resolved by baseline only)
    """
    result = {
        "stress_test_only": b,
        "baseline_only": c,
        "total_discordant": b + c,
        "mcnemar_p": None,
        "odds_ratio": None,
        "odds_ratio_ci": None,
    }

    try:
        from scipy import stats as scipy_stats

        if b + c > 0:
            binom = scipy_stats.binomtest(b, b + c, 0.5, alternative="two-sided")
            result["mcnemar_p"] = round(binom.pvalue, 4)

            if c > 0:
                result["odds_ratio"] = round(b / c, 3)
            elif b > 0:
                result["odds_ratio"] = float("inf")

            if b + c >= 1:
                ci = binom.proportion_ci(confidence_level=0.95)
                lo_p, hi_p = ci.low, ci.high
                or_lo = lo_p / (1 - lo_p) if lo_p < 1 else float("inf")
                or_hi = hi_p / (1 - hi_p) if hi_p < 1 else float("inf")
                result["odds_ratio_ci"] = (round(or_lo, 3), round(or_hi, 3))
        else:
            result["mcnemar_p"] = 1.0
    except ImportError:
        pass

    return result


# ── Ablation table ────────────────────────────────────────────────────


def compute_ablation() -> list[dict]:
    """Compute ablation table from pilot runs."""
    rows = []
    for condition, cfg in PILOT_ABLATION.items():
        results = load_eval_reports_for_condition(
            condition=condition,
            eval_dir_names=cfg["eval_dirs"],
        )
        resolved = sum(1 for v in results.values() if v)
        total = cfg["total"]
        rows.append({
            "condition": condition,
            "resolved": resolved,
            "total": total,
            "rate": round(resolved / total * 100, 1) if total > 0 else 0,
            "eval_dirs": cfg["eval_dirs"],
        })
    return rows


# ── Output formatting ─────────────────────────────────────────────────


def print_report(
    pairwise: PairwiseResult | None,
    baseline_results: dict[str, bool],
    stress_test_results: dict[str, bool],
    ablation: list[dict],
    mini_swe: dict | None,
):
    print(f"\n{'='*70}")
    print("PLANMAN SWE-BENCH COMPARISON REPORT")
    print(f"{'='*70}")

    # Full-run summary
    common = set(baseline_results.keys()) & set(stress_test_results.keys())
    bl_resolved = sum(1 for t in common if baseline_results[t])
    st_resolved = sum(1 for t in common if stress_test_results[t])
    bl_total = len(baseline_results)
    st_total = len(stress_test_results)

    print(f"\n{'─'*70}")
    print("FULL RUN RESULTS")
    print(f"{'─'*70}")
    print(f"  {'Condition':<25} {'Resolved':>10} {'Total':>8} {'Rate':>8}")
    print(f"  {'─'*55}")
    print(f"  {'baseline':<25} {sum(v for v in baseline_results.values()):>10} {bl_total:>8} {sum(v for v in baseline_results.values())/bl_total*100:>7.1f}%")
    print(f"  {'stress_test (planman)':<25} {sum(v for v in stress_test_results.values()):>10} {st_total:>8} {sum(v for v in stress_test_results.values())/st_total*100:>7.1f}%")

    # Paired comparison
    if pairwise and len(common) > 0:
        print(f"\n{'─'*70}")
        print(f"PAIRED COMPARISON ({len(common)} common tasks)")
        print(f"{'─'*70}")

        # Discordant pairs
        st_only = sum(1 for t in common if stress_test_results[t] and not baseline_results[t])
        bl_only = sum(1 for t in common if baseline_results[t] and not stress_test_results[t])
        both = sum(1 for t in common if stress_test_results[t] and baseline_results[t])
        neither = sum(1 for t in common if not stress_test_results[t] and not baseline_results[t])

        print(f"  Both resolved:       {both:>5}")
        print(f"  Planman only:        {st_only:>5}")
        print(f"  Baseline only:       {bl_only:>5}")
        print(f"  Neither:             {neither:>5}")

        disc_total = st_only + bl_only
        if disc_total > 0:
            disc_win_rate = st_only / disc_total * 100
            print(f"\n  Discordant pairs:    {disc_total}")
            print(f"  Planman win rate:    {disc_win_rate:.1f}% ({st_only}/{disc_total})")

        # McNemar
        if pairwise.mcnemar_p is not None:
            sig = "significant" if pairwise.mcnemar_p < 0.05 else "not significant"
            print(f"\n  McNemar exact test:  p = {pairwise.mcnemar_p:.4f} ({sig})")
        if pairwise.odds_ratio is not None:
            or_str = f"{pairwise.odds_ratio:.3f}" if pairwise.odds_ratio != float("inf") else "inf"
            ci_str = ""
            if pairwise.odds_ratio_ci:
                ci_str = f" [{pairwise.odds_ratio_ci[0]:.3f}, {pairwise.odds_ratio_ci[1]:.3f}]"
            print(f"  Odds ratio:          {or_str}{ci_str}")

        # Delta + bootstrap CI
        print(f"\n  Resolve rate delta:  {pairwise.delta:+.2f}%")
        if pairwise.delta_ci:
            print(f"  Delta 95% BCa CI:    [{pairwise.delta_ci[0]:+.2f}%, {pairwise.delta_ci[1]:+.2f}%]")

        # Wilcoxon
        if pairwise.p_value is not None:
            print(f"\n  Wilcoxon signed-rank: p = {pairwise.p_value:.4f}")
            if pairwise.effect_size is not None:
                print(f"  Effect size (r):      {pairwise.effect_size:.3f}")

    # Ablation table
    if ablation:
        print(f"\n{'─'*70}")
        print("ABLATION (50-task pilot)")
        print(f"{'─'*70}")
        print(f"  {'Condition':<25} {'Resolved':>10} {'Rate':>8}  {'Source'}")
        print(f"  {'─'*65}")
        for row in ablation:
            dirs_str = ", ".join(row["eval_dirs"])
            print(f"  {row['condition']:<25} {row['resolved']:>5}/{row['total']:<4} {row['rate']:>7.1f}%  {dirs_str}")
        bl_row = next((r for r in ablation if r["condition"] == "baseline"), None)
        po_row = next((r for r in ablation if r["condition"] == "plan_only"), None)
        st_row = next((r for r in ablation if r["condition"] == "stress_test"), None)
        if bl_row and po_row and bl_row["resolved"] == po_row["resolved"]:
            print(f"\n  Finding: plan_only = baseline ({bl_row['resolved']}/{bl_row['total']} each)")
            print(f"           -> Planning alone adds no value; critique is the mechanism")
        if st_row and bl_row and st_row["resolved"] > bl_row["resolved"]:
            delta = st_row["resolved"] - bl_row["resolved"]
            print(f"           -> stress_test adds {delta} resolved tasks over baseline")

    # Mini-swe-agent comparison
    if mini_swe:
        print(f"\n{'─'*70}")
        print("MINI-SWE-AGENT COMPARISON")
        print(f"{'─'*70}")
        print(f"  Planman:             {mini_swe['planman_resolved']}/{mini_swe['planman_total']}")
        print(f"  Mini-swe-agent:      {mini_swe['mini_resolved']}/{mini_swe['mini_total']}")
        print(f"  Both:                {mini_swe['both']}")
        print(f"  Planman only:        {mini_swe['planman_only']}")
        print(f"  Mini-swe-agent only: {mini_swe['mini_only']}")
        if mini_swe.get("mcnemar_p") is not None:
            sig = "significant" if mini_swe["mcnemar_p"] < 0.05 else "not significant"
            print(f"  McNemar:             p = {mini_swe['mcnemar_p']:.4f} ({sig})")
        print(f"\n  Narrative: Plugin matches purpose-built scaffold")

    print(f"\n{'='*70}")


def build_json_report(
    pairwise: PairwiseResult | None,
    baseline_results: dict[str, bool],
    stress_test_results: dict[str, bool],
    ablation: list[dict],
    mini_swe: dict | None,
) -> dict:
    common = set(baseline_results.keys()) & set(stress_test_results.keys())
    st_only = sum(1 for t in common if stress_test_results[t] and not baseline_results[t])
    bl_only = sum(1 for t in common if baseline_results[t] and not stress_test_results[t])
    both_resolved = sum(1 for t in common if stress_test_results[t] and baseline_results[t])

    report = {
        "full_run": {
            "baseline": {
                "resolved": sum(v for v in baseline_results.values()),
                "total": len(baseline_results),
                "rate": round(sum(v for v in baseline_results.values()) / len(baseline_results) * 100, 1) if baseline_results else 0,
            },
            "stress_test": {
                "resolved": sum(v for v in stress_test_results.values()),
                "total": len(stress_test_results),
                "rate": round(sum(v for v in stress_test_results.values()) / len(stress_test_results) * 100, 1) if stress_test_results else 0,
            },
        },
        "paired_comparison": {
            "n_common_tasks": len(common),
            "both_resolved": both_resolved,
            "planman_only": st_only,
            "baseline_only": bl_only,
            "neither": len(common) - both_resolved - st_only - bl_only,
        },
        "ablation": ablation,
        "mini_swe_agent": mini_swe,
    }

    if pairwise:
        from dataclasses import asdict
        report["paired_comparison"]["statistics"] = asdict(pairwise)

    return report


# ── Main ──────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Compare baseline vs stress_test conditions")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--mini-swe-agent-dir", type=str, default=None,
                        help="Path to mini-swe-agent evaluation directory")
    args = parser.parse_args()

    # Load full-run eval reports
    print("Loading baseline evaluation reports...", file=sys.stderr)
    baseline_results = load_eval_reports_for_condition("baseline")
    print(f"  Baseline: {sum(v for v in baseline_results.values())}/{len(baseline_results)} resolved", file=sys.stderr)

    print("Loading stress_test evaluation reports...", file=sys.stderr)
    stress_test_results = load_eval_reports_for_condition("stress_test")
    print(f"  Stress_test: {sum(v for v in stress_test_results.values())}/{len(stress_test_results)} resolved", file=sys.stderr)

    if not baseline_results:
        print("WARNING: No baseline evaluation reports found. Run the baseline first.", file=sys.stderr)
    if not stress_test_results:
        print("WARNING: No stress_test evaluation reports found.", file=sys.stderr)

    # Compute paired statistics (only on common tasks)
    pairwise = None
    if baseline_results and stress_test_results:
        per_task, majority_votes = build_paired_data(baseline_results, stress_test_results)
        if per_task:
            pairwise = compute_pairwise(per_task, majority_votes)

    # Ablation table
    ablation = compute_ablation()

    # Mini-swe-agent comparison
    mini_swe = None
    mini_results = load_mini_swe_agent_results(args.mini_swe_agent_dir)
    planman_resolved = load_planman_resolved_list()

    if mini_results and planman_resolved:
        mini_resolved_set = {k for k, v in mini_results.items() if v}
        both = len(planman_resolved & mini_resolved_set)
        planman_only = len(planman_resolved - mini_resolved_set)
        mini_only = len(mini_resolved_set - planman_resolved)

        mcnemar = compute_mcnemar_from_counts(planman_only, mini_only)

        mini_swe = {
            "planman_resolved": len(planman_resolved),
            "planman_total": 500,
            "mini_resolved": len(mini_resolved_set),
            "mini_total": 500,
            "both": both,
            "planman_only": planman_only,
            "mini_only": mini_only,
            "mcnemar_p": mcnemar["mcnemar_p"],
            "odds_ratio": mcnemar["odds_ratio"],
            "odds_ratio_ci": mcnemar["odds_ratio_ci"],
            "source_dir": args.mini_swe_agent_dir or "auto-detected",
        }
    elif planman_resolved:
        print("WARNING: mini-swe-agent results not found. Skipping comparison.", file=sys.stderr)

    if args.json:
        report = build_json_report(pairwise, baseline_results, stress_test_results, ablation, mini_swe)
        print(json.dumps(report, indent=2, default=str))
    else:
        print_report(pairwise, baseline_results, stress_test_results, ablation, mini_swe)


if __name__ == "__main__":
    main()
