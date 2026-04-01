"""Planman PreToolUse(Bash/Skill) hook — gates execution via regex matching.

Enables planman to evaluate plans from third-party planning tools (e.g.,
oh-my-claudecode) that transition from planning to execution via Bash
commands or Skill invocations rather than ExitPlanMode.

Performance: fires on EVERY Bash/Skill call. When no patterns are
configured (the default), exits in <1ms with minimal I/O.

PreToolUse input (stdin JSON):
  tool_name: "Bash" or "Skill"
  tool_input: {command: "..."} (Bash) or {skill: "..."} (Skill)
  session_id: str
  cwd: str

PreToolUse output (stdout JSON):
  deny/allow (same schema as pre_exit_plan_hook.py)
"""

import json
import os
import re
import sys

# Add scripts directory to path for sibling imports
_scripts_dir = os.path.dirname(os.path.abspath(__file__))
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)


def _fast_exit_allow():
    """Exit immediately with no output (implicit allow)."""
    sys.exit(0)


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


def _extract_matchable_text(tool_name, tool_input):
    """Extract the text to match against patterns from tool_input.

    Returns the matchable string, or None if not applicable.
    """
    if not isinstance(tool_input, dict):
        return None
    if tool_name == "Bash":
        return tool_input.get("command", "")
    elif tool_name == "Skill":
        return tool_input.get("skill", "")
    return None


def _get_patterns_for_tool(tool_name, config):
    """Return the pattern list for the given tool, or empty list."""
    if tool_name == "Bash":
        return config.exec_patterns
    elif tool_name == "Skill":
        return config.skill_patterns
    return []


def _main():
    # Read stdin
    try:
        raw = sys.stdin.read()
    except Exception:
        _fast_exit_allow()
        return

    if not raw.strip():
        _fast_exit_allow()
        return

    try:
        hook_input = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        _fast_exit_allow()
        return

    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})
    cwd = hook_input.get("cwd")

    # Extract matchable text (command for Bash, skill name for Skill)
    matchable = _extract_matchable_text(tool_name, tool_input)
    if not matchable:
        _fast_exit_allow()
        return

    # Load config
    from config import load_config
    config = load_config(cwd=cwd)

    # Get patterns for this tool type
    patterns = _get_patterns_for_tool(tool_name, config)
    if not patterns:
        _fast_exit_allow()
        return

    if not config.enabled:
        _fast_exit_allow()
        return

    # Match command/skill against patterns
    matched = False
    for pattern in patterns:
        try:
            if re.search(pattern, matchable):
                matched = True
                break
        except re.error:
            from hook_utils import log
            log(f"invalid exec/skill pattern regex: {pattern!r}", config, cwd)
            continue

    if not matched:
        _fast_exit_allow()
        return

    # ── Evaluation path: pattern matched ──
    from hook_utils import find_plan_file, log, run_evaluation
    from evaluator import check_codex_installed

    log(f"exec gate: {tool_name} pattern matched, text={matchable!r}", config, cwd)

    if not check_codex_installed():
        log("codex CLI not installed — allowing", config, cwd)
        _output_allow()
        return

    session_id = hook_input.get("session_id", "default")

    # Find the plan file (marker + scan fallback across plan_dirs)
    plan_path, plan_text, skip_reason = find_plan_file(session_id, cwd, config)

    if not plan_text:
        if skip_reason:
            log(f"plan skipped: {skip_reason}", config, cwd)
            _output_allow(system_message=f"Planman: {skip_reason}")
        else:
            log("no plan file found — blocking exec command", config, cwd)
            _output_block(
                reason=(
                    "No plan file found. Write your plan before executing. "
                    "Planman checked: .claude/plans/ and configured plan_dirs."
                ),
            )
        return

    log(f"evaluating plan from {plan_path} before exec (session={session_id})", config, cwd)

    result = run_evaluation(
        plan_text, session_id, config, cwd=cwd, plan_path=plan_path
    )

    action = result["action"]
    reason = result.get("reason")
    sys_msg = result.get("system_message")

    if action == "block":
        _output_block(reason=reason, system_message=sys_msg)
    elif action == "pass":
        _output_allow(system_message=sys_msg)
    else:
        _output_allow(system_message=sys_msg)


def main():
    try:
        _main()
    except Exception as e:
        print(f"[planman] FATAL in pre_exec_gate_hook: {e}", file=sys.stderr)
        sys.exit(0)  # Fail-open


if __name__ == "__main__":
    main()
