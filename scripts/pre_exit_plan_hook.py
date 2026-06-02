"""Planman PreToolUse hook — evaluates plan when Claude calls ExitPlanMode.

This is the PRIMARY plan evaluation path. It fires once when Claude is
done writing the plan and ready to present it to the user. The plan file
path is discovered via the session marker left by PostToolUse(Write).

PreToolUse input (stdin JSON):
  tool_name: "ExitPlanMode"
  tool_input: {...}
  session_id: str
  cwd: str (project directory)
  ...

PreToolUse output (stdout JSON):
  {"hookSpecificOutput":{"permissionDecision":"deny","permissionDecisionReason":"..."}} → Claude revises
  {"hookSpecificOutput":{"permissionDecision":"allow"}} or no output → ExitPlanMode proceeds
"""

import json
import os
import sys

# Add scripts directory to path for sibling imports
_scripts_dir = os.path.dirname(os.path.abspath(__file__))
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

from config import load_config
from evaluator import check_evaluator_installed
from hook_utils import find_plan_file, log, run_evaluation, safe_session_id


def _output_block(reason, system_message=None):
    """Output a block decision and exit."""
    result = {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
    }}
    if reason:
        result["hookSpecificOutput"]["permissionDecisionReason"] = reason
    if system_message:
        result["systemMessage"] = system_message
    json.dump(result, sys.stdout, ensure_ascii=True)
    sys.exit(0)


def _output_allow(system_message=None):
    """Output an allow decision and exit."""
    result = {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
    }}
    if system_message:
        result["systemMessage"] = system_message
    json.dump(result, sys.stdout, ensure_ascii=True)
    sys.exit(0)


def _main():
    if os.environ.get("_PLANMAN_EVALUATOR"):
        _output_allow()
        return

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

    # Extract context early so log() can write to project-local file
    cwd = hook_input.get("cwd")

    # Load config (pass cwd so file config resolves from project root, not hook cwd)
    config = load_config(cwd=cwd)

    if not config.enabled:
        log("disabled via config", config, cwd)
        _output_allow()
        return

    if not check_evaluator_installed(config, host="claude"):
        log("evaluator CLI not installed — passing through", config, cwd)
        _output_allow()
        return

    session_id = hook_input.get("session_id", "default")

    # Find the plan file
    plan_path, plan_text, skip_reason = find_plan_file(session_id, cwd, config)

    if not plan_text:
        if skip_reason:
            log(f"plan skipped: {skip_reason}", config, cwd)
            _output_allow(system_message=f"Planman: {skip_reason}")
        else:
            log("no plan file found — blocking ExitPlanMode", config, cwd)
            _output_block(
                reason="No plan file found. Write your plan to the plan file before calling ExitPlanMode.",
            )
        return

    log(f"evaluating plan from {plan_path} (session={session_id})", config, cwd)

    # Run evaluation
    result = run_evaluation(
        plan_text, session_id, config, cwd=cwd, plan_path=plan_path, host="claude"
    )

    action = result["action"]
    reason = result.get("reason")
    sys_msg = result.get("system_message")

    if action == "block":
        _output_block(reason=reason, system_message=sys_msg)
        return
    elif action == "pass":
        _output_allow(system_message=sys_msg)
        return
    else:
        # "skip" — not detected as a plan (unlikely for .claude/plans/ files)
        _output_allow(system_message=sys_msg)
        return


def main():
    try:
        _main()
    except Exception as e:
        print(f"[planman] FATAL: {e}", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
