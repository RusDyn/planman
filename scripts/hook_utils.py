"""Shared evaluation helpers for planman hooks (PreToolUse(ExitPlanMode)).

Contains the common evaluation flow:
  detect plan -> load state -> check round limit -> assess -> format output

Used by pre_exit_plan_hook.py (plan mode).
"""

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone

try:
    import fcntl
except ImportError:
    fcntl = None

from config import load_config
from evaluator import check_codex_installed, evaluate_plan
from state import (
    compute_plan_hash,
    load_state,
    record_feedback,
    save_state,
    update_for_plan,
)


def _log_to_file(msg, cwd):
    """Append a timestamped message to planman.log (race-safe)."""
    if cwd:
        log_path = os.path.join(cwd, ".claude", "planman.log")
    else:
        log_path = os.path.join(tempfile.gettempdir(), "planman.log")
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            if fcntl:
                fcntl.flock(f, fcntl.LOCK_EX)
            try:
                ts = datetime.now(timezone.utc).isoformat()
                f.write(f"[{ts}] {msg}\n")
            finally:
                if fcntl:
                    fcntl.flock(f, fcntl.LOCK_UN)
    except Exception:
        pass  # Never crash on log failure


def log(msg, config, cwd=None):
    """Log to stderr if verbose; also append to planman.log file."""
    if config.verbose:
        print(f"[planman] {msg}", file=sys.stderr)
        _log_to_file(msg, cwd)


def format_feedback(data, threshold, round_num, max_rounds, first_round=False):
    """Format evaluation result into human-readable feedback."""
    score = data.get("score", "?")
    breakdown = data.get("breakdown") or {}
    weaknesses = data.get("weaknesses") or []
    suggestions = data.get("suggestions") or []
    strengths = data.get("strengths") or []

    if first_round:
        header = (
            f"**First-round review** — your plan scored **{score}/10**. "
            f"Round 1/{max_rounds}."
        )
    else:
        header = (
            f"Your plan scored **{score}/10** (needs {threshold}). "
            f"Round {round_num}/{max_rounds}."
        )

    lines = [
        header,
        "",
        f"**Breakdown**: completeness={breakdown.get('completeness', '?')}/2, "
        f"correctness={breakdown.get('correctness', '?')}/2, "
        f"sequencing={breakdown.get('sequencing', '?')}/2, "
        f"risk_awareness={breakdown.get('risk_awareness', '?')}/2, "
        f"clarity={breakdown.get('clarity', '?')}/2",
    ]

    if strengths:
        lines.append("")
        lines.append("**Strengths:**")
        for s in strengths:
            lines.append(f"- {s}")

    if weaknesses:
        lines.append("")
        lines.append("**Issues:**")
        for w in weaknesses:
            lines.append(f"- {w}")

    if suggestions:
        lines.append("")
        lines.append("**Suggestions:**")
        for s in suggestions:
            lines.append(f"- {s}")

    lines.append("")
    if first_round:
        lines.append("Revise your plan and resubmit.")
    else:
        lines.append("Revise your plan addressing these issues.")

    return "\n".join(lines)


def format_approval(data):
    """Format approval message."""
    score = data.get("score", "?")
    strengths = data.get("strengths") or []

    lines = [f"Plan approved (score: {score}/10)."]
    if strengths:
        lines.append("")
        lines.append("**Strengths:**")
        for s in strengths[:3]:
            lines.append(f"- {s}")
    return "\n".join(lines)


def run_evaluation(plan_text, session_id, config, cwd=None, plan_path=None):
    """Run the full plan evaluation flow.

    Returns a dict with keys:
        action: "pass" | "block" | "skip"
        reason: str or None (for block)
        system_message: str or None
    """
    # Empty text — skip
    if not plan_text or not plan_text.strip():
        return {"action": "skip", "reason": None, "system_message": "Planman: Plan is empty — nothing to evaluate."}

    # Load state and update round counter
    state = load_state(session_id)

    # ── Plan-mode path: full multi-round flow ────
    state = update_for_plan(state, plan_text, plan_path)
    log(f"round {state['round_count']}/{config.max_rounds}", config, cwd)

    # Check round limit — block for human decision
    if state["round_count"] > config.max_rounds:
        log("max rounds exceeded — blocking for human decision", config, cwd)
        return {
            "action": "block",
            "reason": (
                f"Planman: Max evaluation rounds ({config.max_rounds}) reached. "
                f"Last score was {state.get('last_score', '?')}/10. "
                "The plan has not met the quality threshold after multiple revisions. "
                "Please review and decide whether to proceed."
            ),
            "system_message": None,
        }

    # Stress-test mode: skip Codex on round 1, reject with custom prompt
    if config.stress_test and state["round_count"] == 1:
        state = record_feedback(state, None, config.stress_test_prompt, None)
        try:
            save_state(state)
        except (OSError, ValueError) as e:
            log(f"failed to save state: {e}", config, cwd)
        log("stress-test mode: first plan rejected without evaluation", config, cwd)
        return {
            "action": "block",
            "reason": config.stress_test_prompt,
            "system_message": (
                f"Planman: Stress-test mode — first plan rejected for deep revision. "
                f"Round 1/{config.max_rounds}."
            ),
        }

    # Assess via codex
    previous_feedback = state.get("last_feedback")
    result, error = evaluate_plan(
        plan_text, config, previous_feedback, state["round_count"], cwd=cwd
    )

    if error:
        log(f"evaluation error: {error}", config, cwd)
        if config.fail_open:
            return {
                "action": "pass",
                "reason": None,
                "system_message": f"Planman: Evaluation failed ({error}). Passing through (fail-open).",
            }
        else:
            return {
                "action": "block",
                "reason": f"Planman evaluation failed: {error}. Set PLANMAN_FAIL_OPEN=true to pass through on errors.",
                "system_message": None,
            }

    assessment_score = result["score"]

    # First-round mandatory rejection
    if state["round_count"] == 1:
        feedback_text = format_feedback(
            result, config.threshold, state["round_count"], config.max_rounds, first_round=True
        )
        state = record_feedback(state, assessment_score, feedback_text, result.get("breakdown"))
        try:
            save_state(state)
        except OSError as e:
            log(f"failed to save state: {e}", config, cwd)
        log(f"first round: mandatory review ({assessment_score}/10)", config, cwd)
        return {
            "action": "block",
            "reason": feedback_text,
            "system_message": (
                f"Planman: First-round review ({assessment_score}/10). "
                f"Revision required. Round 1/{config.max_rounds}."
            ),
        }

    if assessment_score >= config.threshold:
        # Plan passes (round >= 2) — preserve state but null feedback
        state = record_feedback(state, assessment_score, None, result.get("breakdown"))
        try:
            save_state(state)
        except OSError as e:
            log(f"failed to save state: {e}", config, cwd)
        log(f"plan accepted: {assessment_score}/10", config, cwd)
        return {
            "action": "pass",
            "reason": None,
            "system_message": f"Planman: {format_approval(result)}",
        }
    else:
        # Plan rejected
        feedback_text = format_feedback(
            result, config.threshold, state["round_count"], config.max_rounds
        )
        state = record_feedback(state, assessment_score, feedback_text, result.get("breakdown"))
        try:
            save_state(state)
        except OSError as e:
            log(f"failed to save state: {e}", config, cwd)

        log(f"plan rejected: {assessment_score}/{config.threshold}", config, cwd)
        return {
            "action": "block",
            "reason": feedback_text,
            "system_message": (
                f"Planman: Plan rejected ({assessment_score}/10, threshold {config.threshold}). "
                f"Round {state['round_count']}/{config.max_rounds}."
            ),
        }
