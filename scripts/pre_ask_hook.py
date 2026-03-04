#!/usr/bin/env python3
"""PreToolUse(AskUserQuestion) hook — auto-answer obvious questions.

PreToolUse input (stdin JSON):
  tool_name: "AskUserQuestion"
  tool_input: {questions: [{question, header, options, multiSelect}]}
  session_id: str
  cwd: str (project directory)

PreToolUse output (stdout):
  {"hookSpecificOutput":{"permissionDecision":"deny","permissionDecisionReason":"..."}} → Claude reads answer
  empty stdout (no output) → question reaches user normally
  {"hookSpecificOutput":{"permissionDecision":"allow"}} → question reaches user
"""

import json
import os
import sys

# Sibling-import bootstrap (matches pre_exit_plan_hook.py:25-28)
_scripts_dir = os.path.dirname(os.path.abspath(__file__))
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

from config import load_config
from hook_utils import log
from question_heuristics import analyze_question


def _output_block(reason, system_message=None):
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
    result = {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
    }}
    if system_message:
        result["systemMessage"] = system_message
    json.dump(result, sys.stdout, ensure_ascii=True)
    sys.exit(0)


def _main():
    try:
        raw = sys.stdin.read()
    except Exception:
        _output_allow()
        return

    try:
        hook_input = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, ValueError):
        _output_allow()
        return

    cwd = hook_input.get("cwd") or os.getcwd()
    config = load_config(cwd=cwd)

    # Gate checks (ordered: cheapest first)
    if not config.enabled:
        _output_allow()
        return
    if not config.auto_answer:
        _output_allow()
        return

    # Extract questions — validate schema variations
    tool_input = hook_input.get("tool_input", {})
    if not isinstance(tool_input, dict):
        _output_allow()
        return
    questions = tool_input.get("questions", [])
    if not isinstance(questions, list) or not questions:
        _output_allow()
        return

    # Analyze each question
    answers = []
    for q in questions:
        if not isinstance(q, dict):
            _output_allow()  # Malformed question → bail entirely
            return
        question_text = q.get("question", "")
        options = q.get("options", [])
        if not isinstance(options, list) or len(options) < 2:
            _output_allow()  # Can't analyze → bail
            return
        result = analyze_question(question_text, options, cwd)
        if result is None:
            _output_allow()  # ANY unanswerable → pass ALL through
            return
        answers.append((q, result))

    # All questions answered — format block reason
    reason_lines = ["Planman auto-answer: Clear answer found from project context.", ""]
    for q, result in answers:
        header = q.get("header", q.get("question", "Question"))
        reason_lines.append(f"**{header}**: {result['answer']}")
        for ev in result.get("evidence", []):
            reason_lines.append(f"- Evidence: {ev}")
        reason_lines.append("")
    reason_lines.append(
        "Continue planning with these choices. "
        "If any answer seems wrong, the user can run /planman:clear and re-enter plan mode."
    )

    system_msg = f"Planman: Auto-answered {len(answers)} question(s) from project context."
    log(f"auto-answer: answered {len(answers)} questions", config, cwd)
    _output_block("\n".join(reason_lines), system_msg)


if __name__ == "__main__":
    try:
        _main()
    except Exception:
        _output_allow()  # Uncaught exception → always fail-open
