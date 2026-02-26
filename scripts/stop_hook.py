"""Planman Stop hook — catches inline plans (non-plan-mode).

This is the SECONDARY plan interception path. It fires after Claude's turn
ends, catching plans presented outside of plan mode. For plan mode, the
PostToolUse hook (post_tool_hook.py) is the primary interceptor.

Flow:
  stdin JSON -> load config -> enabled? -> codex installed?
    -> recently evaluated by PostToolUse? -> skip (avoid double-eval)
    -> extract text (last_assistant_message or transcript) -> detect plan?
      -> not a plan: exit 0 (pass through)
      -> is a plan: load state -> check round limit
        -> over limit: exit 0 + systemMessage (human decides)
        -> under limit: call codex exec
          -> codex error + fail_open: exit 0 + warning
          -> score >= threshold: exit 0 + approval message, clear state
          -> score < threshold: block with feedback
"""

import json
import os
import sys

# Add scripts directory to path for sibling imports
_scripts_dir = os.path.dirname(os.path.abspath(__file__))
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

from config import load_config
from evaluator import check_codex_installed
from hook_utils import log, run_evaluation, was_recently_evaluated
from plan_detector import extract_last_assistant_text
from state import load_state, _is_stale


def _output(decision, reason=None, system_message=None):
    """Write hook decision JSON to stdout and exit."""
    result = {}
    if decision == "block":
        result["decision"] = "block"
        if reason:
            result["reason"] = reason
    # For "allow", output nothing (empty = pass through)
    if system_message:
        result["systemMessage"] = system_message
    if result:
        json.dump(result, sys.stdout, ensure_ascii=True)
    sys.exit(0)


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
        except json.JSONDecodeError as e:
            print(f"[planman] warning: malformed hook input: {e}", file=sys.stderr)

    # Prevent infinite loops: if another stop hook already blocked, pass through
    if hook_input.get("stop_hook_active"):
        sys.exit(0)

    # Extract cwd early so log() can write to project-local file
    cwd = hook_input.get("cwd")

    # Load config
    config = load_config()

    # Master switch
    if not config.enabled:
        log("disabled via config", config, cwd)
        sys.exit(0)

    # Check codex is installed
    if not check_codex_installed(config.codex_path):
        log("codex CLI not installed — passing through", config, cwd)
        sys.exit(0)

    session_id = hook_input.get("session_id", "default")

    # Skip if PostToolUse hook already evaluated this plan recently
    if was_recently_evaluated(session_id):
        log("plan was recently evaluated by PostToolUse hook — skipping", config, cwd)
        sys.exit(0)

    # Extract plan text — prefer last_assistant_message, fall back to transcript
    plan_text = hook_input.get("last_assistant_message", "")
    if not plan_text or not plan_text.strip():
        # Primary extraction: parse transcript file
        transcript_path = hook_input.get("transcript_path", "")
        if transcript_path:
            log("using transcript for plan text extraction", config, cwd)
            plan_text = extract_last_assistant_text(transcript_path)

    if not plan_text or not plan_text.strip():
        log("no assistant text found", config, cwd)
        sys.exit(0)

    # Skip if plan-mode already owns this session
    state = load_state(session_id)
    if state.get("plan_file_path") and not _is_stale(state):
        log("plan-mode session active — skipping stop hook", config, cwd)
        sys.exit(0)

    result = run_evaluation(plan_text, session_id, config, cwd=cwd)

    action = result["action"]
    reason = result.get("reason")
    sys_msg = result.get("system_message")

    if action == "block":
        _output("block", reason=reason, system_message=sys_msg)
    elif action == "pass":
        _output("allow", system_message=sys_msg)
    else:  # "skip"
        sys.exit(0)


def main():
    try:
        _main()
    except Exception as e:
        print(f"[planman] FATAL: {e}", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
