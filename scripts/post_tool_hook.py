"""Planman PostToolUse hook — lightweight plan file path tracker.

Records the path of plan files written to .claude/plans/ so the
PreToolUse(ExitPlanMode) hook knows which file to evaluate.

Does NOT evaluate plans — that happens in pre_exit_plan_hook.py when
Claude calls ExitPlanMode.

PostToolUse input (stdin JSON):
  tool_name: "Write" or "Edit"
  tool_input: {file_path: "...", ...}
  session_id: str
  ...

PostToolUse output: empty (always allows the write through)
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

from hook_utils import MARKER_TEMPLATE, safe_session_id


def _main():
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

    # Only track plan files (.claude/plans/) — segment check for cross-platform
    parts = PurePath(file_path).parts
    lower_parts = tuple(p.casefold() for p in parts)
    try:
        idx = lower_parts.index(".claude")
        if idx + 1 >= len(lower_parts) or lower_parts[idx + 1] != "plans":
            sys.exit(0)
    except ValueError:
        sys.exit(0)

    # Record plan file path for the PreToolUse(ExitPlanMode) hook
    session_id = safe_session_id(hook_input.get("session_id", "default"))
    marker_path = MARKER_TEMPLATE.format(session_id=session_id)

    try:
        marker = {"plan_file_path": file_path, "timestamp": time.time()}
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

    # Always allow — no blocking, no evaluation
    sys.exit(0)


def main():
    try:
        _main()
    except Exception as e:
        print(f"[planman] FATAL: {e}", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
