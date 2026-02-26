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
  {"decision":"block","reason":"..."} → Claude revises and tries again
  {} or no output → ExitPlanMode proceeds normally
"""

import glob
import json
import os
import sys
import tempfile
import time

# Add scripts directory to path for sibling imports
_scripts_dir = os.path.dirname(os.path.abspath(__file__))
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

from config import load_config
from evaluator import check_codex_installed
from hook_utils import log, run_evaluation

# Session marker path template (written by post_tool_hook.py)
_MARKER_TEMPLATE = os.path.join(tempfile.gettempdir(), "planman-plan-{session_id}.json")


def _safe_session_id(session_id):
    """Sanitize session_id for use in file paths."""
    return "".join(c for c in session_id if c.isalnum() or c in "-_")


def _output_block(reason, system_message=None):
    """Output a block decision and exit."""
    result = {"decision": "block"}
    if reason:
        result["reason"] = reason
    if system_message:
        result["systemMessage"] = system_message
    json.dump(result, sys.stdout, ensure_ascii=True)
    sys.exit(0)


def _output_allow(system_message=None):
    """Output an allow decision (empty or with system message) and exit."""
    if system_message:
        json.dump({"systemMessage": system_message}, sys.stdout, ensure_ascii=True)
    sys.exit(0)


def _find_plan_file(session_id, cwd):
    """Find the plan file path via session marker or fallback scan.

    Returns (plan_file_path, plan_text) or (None, None).
    """
    safe_id = _safe_session_id(session_id)
    marker_path = _MARKER_TEMPLATE.format(session_id=safe_id)

    _MAX_PLAN_SIZE = 1_000_000  # 1 MB — reject absurdly large files

    # Primary: read session marker left by PostToolUse(Write)
    try:
        with open(marker_path, "r", encoding="utf-8") as f:
            marker = json.load(f)
        plan_path = marker.get("plan_file_path", "")
        if plan_path and os.path.isfile(plan_path):
            if os.path.getsize(plan_path) > _MAX_PLAN_SIZE:
                return None, None
            with open(plan_path, "r", encoding="utf-8") as f:
                text = f.read()
            if text.strip():
                return plan_path, text
    except (OSError, json.JSONDecodeError, KeyError):
        pass

    # Fallback: scan .claude/plans/ under cwd for most recently modified .md
    if cwd:
        plans_dir = os.path.join(cwd, ".claude", "plans")
        if os.path.isdir(plans_dir):
            md_files = glob.glob(os.path.join(plans_dir, "*.md"))
            if md_files:
                latest = max(md_files, key=os.path.getmtime)
                try:
                    if os.path.getsize(latest) > _MAX_PLAN_SIZE:
                        return None, None
                    with open(latest, "r", encoding="utf-8") as f:
                        text = f.read()
                    if text.strip():
                        return latest, text
                except OSError:
                    pass

    return None, None


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

    # Extract context early so log() can write to project-local file
    cwd = hook_input.get("cwd")

    # Load config (pass cwd so file config resolves from project root, not hook cwd)
    config = load_config(cwd=cwd)

    if not config.enabled:
        log("disabled via config", config, cwd)
        _output_allow()

    if not check_codex_installed(config.codex_path):
        log("codex CLI not installed — passing through", config, cwd)
        _output_allow()

    session_id = hook_input.get("session_id", "default")

    # Find the plan file
    plan_path, plan_text = _find_plan_file(session_id, cwd)

    if not plan_text:
        log("no plan file found — allowing ExitPlanMode", config, cwd)
        _output_allow()

    log(f"evaluating plan from {plan_path}", config, cwd)

    # Run evaluation
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
        # "skip" — not detected as a plan (unlikely for .claude/plans/ files)
        _output_allow(system_message=sys_msg)


def main():
    try:
        _main()
    except Exception as e:
        print(f"[planman] FATAL: {e}", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
