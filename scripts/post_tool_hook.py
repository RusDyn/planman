"""Planman PostToolUse hook — plan file tracker + advisory review.

For native plan files (.claude/plans/): records path for ExitPlanMode eval.
For third-party plan files (plan_dirs, e.g. .omc/plans/): records path AND
injects advisory feedback via systemMessage (stress-test prompt).

PostToolUse input (stdin JSON):
  tool_name: "Write" or "Edit"
  tool_input: {file_path: "...", ...}
  session_id: str
  cwd: str
  ...

PostToolUse output:
  Native plans: empty (evaluation happens at ExitPlanMode)
  plan_dirs plans: {"systemMessage": "..."} with review feedback
"""

import json
import os
import sys
import tempfile
import time
from pathlib import PurePath

# Add scripts directory to path for sibling imports
_scripts_dir = os.path.dirname(os.path.abspath(__file__))
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

from config import load_config
from hook_utils import MARKER_TEMPLATE, safe_session_id
from path_utils import normalize_path


def _is_plan_dir_path(file_path, plan_dirs):
    """Check if file_path is inside any of the configured plan_dirs.

    Uses PurePath segment matching for cross-platform support.
    """
    if not plan_dirs:
        return False
    path_parts = PurePath(file_path).parts
    lower_path = tuple(p.casefold() for p in path_parts)
    for plan_dir in plan_dirs:
        dir_parts = PurePath(plan_dir).parts
        lower_dir = tuple(p.casefold() for p in dir_parts)
        for i in range(len(lower_path) - len(lower_dir) + 1):
            if lower_path[i:i + len(lower_dir)] == lower_dir:
                return True
    return False


def _write_marker(file_path, session_id):
    """Record plan file path in session marker."""
    marker_path = MARKER_TEMPLATE.format(session_id=session_id)
    try:
        marker = {"plan_file_path": normalize_path(file_path), "timestamp": time.time()}
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=os.path.dirname(marker_path), prefix="planman-marker-tmp-"
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(marker, f)
            os.replace(tmp_path, marker_path)
        except (OSError, ValueError):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except OSError as e:
        print(f"[planman] warning: failed to write plan marker: {e}", file=sys.stderr)


def _main():
    if os.environ.get("_PLANMAN_EVALUATOR"):
        sys.exit(0)

    # Read hook input from stdin
    try:
        raw = sys.stdin.read()
    except Exception:
        raw = ""

    hook_input = {}
    if raw.strip():
        try:
            hook_input = json.loads(raw)
        except json.JSONDecodeError:
            sys.exit(0)

    # Only handle Write and Edit tools
    tool_name = hook_input.get("tool_name", "")
    if tool_name not in ("Write", "Edit"):
        sys.exit(0)

    tool_input = hook_input.get("tool_input", {})
    file_path = tool_input.get("file_path", "")

    # Fast path: check .claude/plans/ (no config load needed)
    parts = PurePath(file_path).parts
    lower_parts = tuple(p.casefold() for p in parts)
    is_native_plan = False
    try:
        idx = lower_parts.index(".claude")
        if idx + 1 < len(lower_parts) and lower_parts[idx + 1] == "plans":
            is_native_plan = True
    except ValueError:
        pass

    is_plan_dirs_plan = False
    config = None
    if not is_native_plan:
        # Check plan_dirs (requires config load)
        cwd = hook_input.get("cwd")
        config = load_config(cwd=cwd)
        if config.plan_dirs and _is_plan_dir_path(file_path, config.plan_dirs):
            is_plan_dirs_plan = True
        else:
            sys.exit(0)

    session_id = safe_session_id(hook_input.get("session_id", "default"))

    # Record plan file path for evaluation hooks
    _write_marker(file_path, session_id)

    # For native plans: stop here (evaluation happens at ExitPlanMode)
    if is_native_plan:
        sys.exit(0)

    # For plan_dirs plans: inject advisory review via systemMessage
    if is_plan_dirs_plan and config and config.enabled:
        stress_prompt = config.stress_test_prompt
        result = {
            "systemMessage": (
                f"Planman: Plan file detected at {file_path}. "
                f"Review this plan before proceeding to execution.\n\n"
                f"{stress_prompt}"
            ),
        }
        json.dump(result, sys.stdout, ensure_ascii=True)

    sys.exit(0)


def main():
    try:
        _main()
    except Exception as e:
        print(f"[planman] FATAL: {e}", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
