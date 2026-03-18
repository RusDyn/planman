#!/usr/bin/env python3
"""Recompute baseline_summary.json from source data (run-results + evaluation reports)."""

import json
import os
import sys
from pathlib import Path

# Paths
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RESULTS_DIR = REPO_ROOT / "benchmark" / "swebench" / "results"
EVAL_DIR = REPO_ROOT / "logs" / "run_evaluation"

# Run-results JSONL files (patches & cost)
RUN_RESULTS_GLOB = "run-results_baseline_baseline_*500*.jsonl"

# Explicit allowlist of evaluation run IDs
EVAL_RUNS = [
    "baseline_baseline_full_500_rep0",
    "baseline_baseline_retry_500_rep0",
]

TOTAL_TASKS = 500


def load_run_results():
    """Load all run-results JSONL files and dedup by task (prefer entry with patch)."""
    best = {}  # task -> entry
    for path in sorted(RESULTS_DIR.glob(RUN_RESULTS_GLOB)):
        with open(path) as f:
            for line in f:
                entry = json.loads(line)
                task = entry["task"]
                has_patch = bool(entry.get("model_patch"))
                prev = best.get(task)
                if prev is None:
                    best[task] = entry
                elif has_patch and not bool(prev.get("model_patch")):
                    # Prefer entry with a patch
                    best[task] = entry
    return best


def load_eval_reports():
    """Load evaluation reports from allowlisted runs, dedup by instance_id."""
    results = {}  # instance_id -> resolved (bool)
    for run_id in EVAL_RUNS:
        run_dir = EVAL_DIR / run_id / "planman-baseline"
        if not run_dir.exists():
            print(f"WARNING: eval run dir not found: {run_dir}", file=sys.stderr)
            continue
        for instance_dir in run_dir.iterdir():
            if not instance_dir.is_dir():
                continue
            report_path = instance_dir / "report.json"
            if not report_path.exists():
                continue
            with open(report_path) as f:
                report = json.load(f)
            instance_id = instance_dir.name
            if instance_id not in report:
                print(f"WARNING: instance_id {instance_id} not in report keys: {list(report.keys())}", file=sys.stderr)
                continue
            resolved = report[instance_id].get("resolved", False)
            if instance_id in results:
                if results[instance_id] != resolved:
                    print(f"WARNING: conflict for {instance_id}: was {results[instance_id]}, now {resolved} in {run_id}", file=sys.stderr)
                    # Use resolved=True if ANY run resolved it
                    results[instance_id] = results[instance_id] or resolved
            else:
                results[instance_id] = resolved
    return results


def main():
    # Load source data
    run_results = load_run_results()
    eval_reports = load_eval_reports()

    # Validate: all evaluated instance_ids should be in the task set
    task_set = set(run_results.keys())
    for instance_id in eval_reports:
        if instance_id not in task_set:
            print(f"ERROR: evaluated instance {instance_id} not in {TOTAL_TASKS}-task manifest!", file=sys.stderr)
            sys.exit(1)

    # Tasks without evaluation reports (malformed patch, timeout, or no patch)
    # are definitionally not resolved — count them as evaluated=False
    unevaluated = task_set - set(eval_reports.keys())
    if unevaluated:
        print(f"  {len(unevaluated)} tasks without reports (counted as not resolved):", file=sys.stderr)
        for iid in sorted(unevaluated):
            reason = "no patch" if not run_results[iid].get("model_patch") else "eval error"
            eval_reports[iid] = False
            print(f"    {iid} ({reason})", file=sys.stderr)

    # Compute metrics
    patches_generated = sum(1 for e in run_results.values() if bool(e.get("model_patch")))
    evaluated = len(eval_reports)
    resolved = sum(1 for v in eval_reports.values() if v)
    not_resolved = evaluated - resolved

    # Cost stats (only from entries with valid cost)
    costs = [e["cost_usd"] for e in run_results.values() if e.get("cost_usd") is not None]
    wall_times = [e["wall_time_s"] for e in run_results.values() if e.get("wall_time_s") is not None]

    total_cost = sum(costs)
    mean_cost = total_cost / len(costs) if costs else 0
    median_cost = sorted(costs)[len(costs) // 2] if costs else 0

    total_wall_time = sum(wall_times)
    mean_wall_time = total_wall_time / len(wall_times) if wall_times else 0

    summary = {
        "condition": "baseline",
        "total_tasks": TOTAL_TASKS,
        "patches_generated": patches_generated,
        "evaluated": evaluated,
        "resolved": resolved,
        "not_resolved": not_resolved,
        "resolve_rate_evaluated": round(resolved / evaluated, 4) if evaluated else 0,
        "resolve_rate_total": round(resolved / TOTAL_TASKS, 4),
        "cost": {
            "total_usd": round(total_cost, 2),
            "mean_usd": round(mean_cost, 4),
            "median_usd": round(median_cost, 4),
        },
        "wall_time": {
            "total_s": round(total_wall_time, 1),
            "mean_s": round(mean_wall_time, 1),
        },
        "eval_runs": EVAL_RUNS,
    }

    output_path = RESULTS_DIR / "baseline_summary.json"
    with open(output_path, "w") as f:
        json.dump(summary, f, indent=2)
        f.write("\n")

    print(f"Written: {output_path}")
    print(f"  patches_generated: {patches_generated}")
    print(f"  evaluated: {evaluated}")
    print(f"  resolved: {resolved}")
    print(f"  resolve_rate_evaluated: {summary['resolve_rate_evaluated']}")
    print(f"  resolve_rate_total: {summary['resolve_rate_total']}")
    print(f"  total_cost_usd: {summary['cost']['total_usd']}")


if __name__ == "__main__":
    main()
