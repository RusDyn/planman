"""Planman PostToolUse hook — lightweight plan file path tracker.

Records the path of plan files written to .claude/plans/ so the
PreToolUse(ExitPlanMode) hook knows which file to evaluate.

Does NOT evaluate plans — that happens in pre_exit_plan_hook.py when
Claude calls ExitPlanMode.

PostToolUse input (stdin JSON):
  tool_name: "Write"
  tool_input: {file_path: "...", content: "..."}
  session_id: str
  ...

PostToolUse output: empty (always allows the write through)
"""

import json
import os
import sys
import time

# Session marker path template
_MARKER_TEMPLATE = os.path.join("/tmp", "planman-plan-{session_id}.json")


def _safe_session_id(session_id):
    """Sanitize session_id for use in file paths."""
    return "".join(c for c in session_id if c.isalnum() or c in "-_")


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

    # Only handle Write tool
    tool_name = hook_input.get("tool_name", "")
    if tool_name != "Write":
        sys.exit(0)

    tool_input = hook_input.get("tool_input", {})
    file_path = tool_input.get("file_path", "")

    # Only track plan files (.claude/plans/)
    if "/.claude/plans/" not in file_path:
        sys.exit(0)

    # Record plan file path for the PreToolUse(ExitPlanMode) hook
    session_id = _safe_session_id(hook_input.get("session_id", "default"))
    marker_path = _MARKER_TEMPLATE.format(session_id=session_id)

    try:
        marker = {"plan_file_path": file_path, "timestamp": time.time()}
        with open(marker_path, "w") as f:
            json.dump(marker, f)
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
