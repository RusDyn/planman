"""Planman PostToolUse(ExitPlanMode) — clears session state after plan approval.

Fires AFTER ExitPlanMode executes (all PreToolUse hooks approved, user
approved). Resets the round counter so the next plan cycle starts fresh.

This is the PRIMARY reset mechanism. A fallback flag (plan_approved) in
the session state handles the case where PostToolUse doesn't fire (e.g.
user accepted with "clear context").

PostToolUse input (stdin JSON):
  tool_name: "ExitPlanMode"
  session_id: str
  cwd: str (project directory)
  ...

PostToolUse output: empty (never blocks)
"""

import json
import os
import sys

# Add scripts directory to path for sibling imports
_scripts_dir = os.path.dirname(os.path.abspath(__file__))
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

from config import load_config
from hook_utils import MARKER_TEMPLATE, log, safe_session_id
from state import clear_state


def _main():
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

    session_id = hook_input.get("session_id", "default")
    cwd = hook_input.get("cwd")
    config = load_config(cwd=cwd)

    # Clear session state (round counter, scores, history)
    clear_state(session_id)

    # Clear marker file (plan file path tracker)
    safe_id = safe_session_id(session_id)
    marker_path = MARKER_TEMPLATE.format(session_id=safe_id)
    try:
        os.unlink(marker_path)
    except OSError:
        pass

    log("state cleared after ExitPlanMode", config, cwd)
    sys.exit(0)


def main():
    try:
        _main()
    except Exception as e:
        print(f"[planman] FATAL: {e}", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
