#!/usr/bin/env python3
"""Codex Stop hook — evaluate latest plan-like content.

This adapter is intentionally conservative. It only evaluates text that looks
like a plan; if the Codex hook payload shape does not expose plan text, it
fails open and logs the skip.
"""

import json
import os
import re
import sys

_scripts_dir = os.path.dirname(os.path.abspath(__file__))
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

from config import load_config
from hook_utils import log, run_evaluation, safe_session_id
from evaluator import check_evaluator_installed


_PLAN_RE = re.compile(
    r"(?ims)(<proposed_plan>|implementation plan|^# .{0,80}plan\b|^\*\*summary\*\*|^\s*1\.\s+.+\n\s*2\.)"
)


def _walk_strings(value, path=""):
    """Yield (path, string) pairs from nested JSON-like hook input."""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            yield from _walk_strings(child, child_path)
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            yield from _walk_strings(child, f"{path}[{idx}]")


def _extract_plan_text(hook_input):
    """Extract the latest clearly plan-like text from a Codex hook payload."""
    candidates = []
    order = 0
    for path, text in _walk_strings(hook_input):
        order += 1
        stripped = text.strip()
        if len(stripped) < 80:
            continue
        lower_path = path.lower()
        score = 0
        if "plan" in lower_path:
            score += 3
        if any(part in lower_path for part in ("message", "content", "text", "markdown", "summary")):
            score += 1
        if _PLAN_RE.search(stripped):
            score += 4
        if score >= 4:
            candidates.append((score, order, stripped))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[-1][2]


def _output_allow(system_message=None):
    result = {}
    if system_message:
        result["systemMessage"] = system_message
    if result:
        json.dump(result, sys.stdout, ensure_ascii=True)
    sys.exit(0)


def _output_block(reason, system_message=None):
    result = {
        "decision": "block",
        "reason": reason,
    }
    if system_message:
        result["systemMessage"] = system_message
    json.dump(result, sys.stdout, ensure_ascii=True)
    sys.exit(0)


def _main():
    if os.environ.get("_PLANMAN_EVALUATOR"):
        _output_allow()
        return

    try:
        raw = sys.stdin.read()
    except Exception:
        raw = ""

    try:
        hook_input = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, ValueError):
        hook_input = {}

    cwd = hook_input.get("cwd") or os.getcwd()
    config = load_config(cwd=cwd)
    if not config.enabled:
        _output_allow()
        return

    plan_text = _extract_plan_text(hook_input)
    if not plan_text:
        log("codex host: no plan-like content found in Stop payload", config, cwd)
        _output_allow()
        return

    if not check_evaluator_installed(config, host="codex"):
        log("codex host: evaluator CLI not installed — allowing", config, cwd)
        _output_allow()
        return

    session_id = safe_session_id(hook_input.get("session_id") or hook_input.get("thread_id") or "codex")
    log(f"codex host: evaluating plan-like content (session={session_id})", config, cwd)

    result = run_evaluation(
        plan_text,
        session_id,
        config,
        cwd=cwd,
        plan_path="codex:stop",
        host="codex",
    )

    action = result["action"]
    reason = result.get("reason")
    sys_msg = result.get("system_message")

    if action == "block":
        _output_block(reason=reason, system_message=sys_msg)
    else:
        _output_allow(system_message=sys_msg)


def main():
    try:
        _main()
    except Exception as e:
        print(f"[planman] FATAL in codex_stop_hook: {e}", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
