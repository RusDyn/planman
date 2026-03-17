"""Assemble SWE-bench leaderboard submission folder from benchmark results.

Usage:
    python3 benchmark/swebench/prepare_submission.py --run-id pilot_1_rep0 --output /tmp/test_sub
"""

import argparse
import glob
import os
import shutil
import sys

_swebench_dir = os.path.dirname(os.path.abspath(__file__))
if _swebench_dir not in sys.path:
    sys.path.insert(0, _swebench_dir)

from config_swe import RESULTS_DIR, REPO_ROOT

METADATA_TEMPLATE = """\
name: planman
org: RusDyn
site: https://github.com/RusDyn/planman
report: <REPORT_URL>
authors:
  - <AUTHOR_NAME>
model:
  - claude-opus-4-6
os_model: false
os_system: true
attempts: "1"
"""

README_TEMPLATE = """\
# planman

**planman** is an open-source Claude Code plugin that adds structured planning
and stress-test critique to Claude's problem-solving workflow.

## How it works

1. **Plan phase** — Claude enters plan mode and produces a structured
   implementation plan for the task.
2. **Stress-test critique** — The planman plugin reviews the plan with
   adversarial self-critique, probing for missed edge cases, incorrect
   assumptions, and incomplete reasoning. The plan is revised based on
   the critique.
3. **Implementation phase** — Claude exits plan mode and implements the
   fix following the stress-tested plan.

## Difference from base Claude Code

Base Claude Code solves tasks in a single pass. planman adds a mandatory
planning phase with automated critique before implementation begins. The
plugin runs entirely within Claude Code's plugin system — no external
models or tools are used beyond Claude itself.

## Links

- Source code: https://github.com/RusDyn/planman
- Technical report: <REPORT_URL>
"""


def _is_resolved(report_path: str) -> bool:
    """Check if a report.json indicates the instance was resolved."""
    try:
        import json
        with open(report_path) as f:
            data = json.load(f)
        # report.json maps instance_id -> {resolved: bool, ...}
        for val in data.values():
            if isinstance(val, dict):
                return val.get("resolved", False)
    except (OSError, json.JSONDecodeError, StopIteration):
        pass
    return False


def prepare_submission(
    run_id: str,
    output_dir: str,
    condition: str = "stress_test",
    eval_dirs: list[str] | None = None,
):
    """Assemble submission folder from benchmark results."""
    os.makedirs(output_dir, exist_ok=True)

    # 1. Copy predictions
    pred_path = os.path.join(RESULTS_DIR, f"predictions_{condition}_{run_id}.jsonl")
    if not os.path.exists(pred_path):
        print(f"ERROR: predictions not found: {pred_path}", file=sys.stderr)
        sys.exit(1)
    shutil.copy2(pred_path, os.path.join(output_dir, "all_preds.jsonl"))
    print(f"Copied predictions from {pred_path}", file=sys.stderr)

    # 2. Write metadata.yaml
    with open(os.path.join(output_dir, "metadata.yaml"), "w") as f:
        f.write(METADATA_TEMPLATE)
    print("Wrote metadata.yaml", file=sys.stderr)

    # 3. Write README.md
    with open(os.path.join(output_dir, "README.md"), "w") as f:
        f.write(README_TEMPLATE)
    print("Wrote README.md", file=sys.stderr)

    # 4. Copy trajectories
    traj_src = os.path.join(RESULTS_DIR, "trajs", condition)
    traj_dst = os.path.join(output_dir, "trajs")
    if os.path.isdir(traj_src):
        shutil.copytree(traj_src, traj_dst, dirs_exist_ok=True)
        n_trajs = len(os.listdir(traj_dst))
        print(f"Copied {n_trajs} trajectories", file=sys.stderr)
    else:
        os.makedirs(traj_dst, exist_ok=True)
        print("WARNING: no trajectories found", file=sys.stderr)

    # 5. Copy evaluation logs from one or more eval directories
    logs_dst = os.path.join(output_dir, "logs")
    os.makedirs(logs_dst, exist_ok=True)
    eval_logs_base = os.path.join(REPO_ROOT, "logs", "run_evaluation")

    if eval_dirs:
        # Multi-dir mode: iterate over explicit eval dir list
        # Track which instances are already resolved to handle duplicates
        resolved_instances: set[str] = set()
        copied = 0

        for eval_dir_name in eval_dirs:
            eval_dir = os.path.join(eval_logs_base, eval_dir_name)
            if not os.path.isdir(eval_dir):
                print(f"WARNING: eval dir not found: {eval_dir}", file=sys.stderr)
                continue

            # swebench creates subdirs like planman-stress_test/instance_id/
            model_dir = os.path.join(eval_dir, f"planman-{condition}")
            source_dir = model_dir if os.path.isdir(model_dir) else eval_dir

            for instance_dir in os.listdir(source_dir):
                src = os.path.join(source_dir, instance_dir)
                if not os.path.isdir(src):
                    continue

                # If already copied and resolved, skip — don't overwrite a good result
                if instance_dir in resolved_instances:
                    continue

                dst = os.path.join(logs_dst, instance_dir)
                os.makedirs(dst, exist_ok=True)
                for fname in ["report.json", "patch.diff", "test_output.txt"]:
                    src_file = os.path.join(src, fname)
                    if os.path.exists(src_file):
                        shutil.copy2(src_file, dst)

                # Track resolved status
                report_dst = os.path.join(dst, "report.json")
                if os.path.exists(report_dst) and _is_resolved(report_dst):
                    resolved_instances.add(instance_dir)

                copied += 1

        # Deduplicated count
        unique_instances = len(os.listdir(logs_dst))
        print(
            f"Copied logs from {len(eval_dirs)} eval dirs: "
            f"{unique_instances} unique instances ({len(resolved_instances)} resolved)",
            file=sys.stderr,
        )
    else:
        # Single-dir mode: auto-detect from condition + run_id
        eval_dir = os.path.join(eval_logs_base, f"{condition}_{run_id}")
        if not os.path.isdir(eval_dir):
            candidates = glob.glob(os.path.join(eval_logs_base, f"*{run_id}*"))
            eval_dir = candidates[0] if candidates else None

        if eval_dir and os.path.isdir(eval_dir):
            model_dir = os.path.join(eval_dir, f"planman-{condition}")
            source_dir = model_dir if os.path.isdir(model_dir) else eval_dir

            copied = 0
            for instance_dir in os.listdir(source_dir):
                src = os.path.join(source_dir, instance_dir)
                if not os.path.isdir(src):
                    continue
                dst = os.path.join(logs_dst, instance_dir)
                os.makedirs(dst, exist_ok=True)
                for fname in ["report.json", "patch.diff", "test_output.txt"]:
                    src_file = os.path.join(src, fname)
                    if os.path.exists(src_file):
                        shutil.copy2(src_file, dst)
                copied += 1
            print(f"Copied logs for {copied} instances", file=sys.stderr)
        else:
            print("WARNING: no evaluation logs found", file=sys.stderr)

    print(f"\nSubmission assembled at: {output_dir}", file=sys.stderr)
    print("TODO: Fill in <REPORT_URL> and <AUTHOR_NAME> in metadata.yaml", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Prepare SWE-bench submission")
    parser.add_argument("--run-id", required=True, help="Run ID (e.g. pilot_1_rep0)")
    parser.add_argument("--output", required=True, help="Output directory")
    parser.add_argument("--condition", default="stress_test",
                        help="Condition to submit (default: stress_test)")
    parser.add_argument("--eval-dirs", nargs="+", default=None,
                        help="Eval run IDs to scan for logs (e.g. stress_test_final_500 stress_test_last25)")
    args = parser.parse_args()

    prepare_submission(args.run_id, args.output, args.condition, args.eval_dirs)


if __name__ == "__main__":
    main()
