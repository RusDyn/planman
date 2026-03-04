"""Shared evaluation helpers for planman hooks (PreToolUse(ExitPlanMode)).

Contains the common evaluation flow:
  detect plan -> load state -> check round limit -> assess -> format output

Used by pre_exit_plan_hook.py and post_tool_hook.py.
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

# ── Shared constants used by both hooks ──────────────────────────────

MARKER_TEMPLATE = os.path.join(tempfile.gettempdir(), "planman-plan-{session_id}.json")


def safe_session_id(session_id):
    """Sanitize session_id for use in file paths. Falls back to 'default' if empty."""
    safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
    safe = safe[:100]
    return safe or "default"


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
    """Always log to file; also print to stderr if verbose."""
    _log_to_file(msg, cwd)
    if config and config.verbose:
        print(f"[planman] {msg}", file=sys.stderr)


def format_trend(history, current_score):
    """Format score trend from history + current score.

    Called BEFORE record_feedback() persists current_score to history,
    so history contains only previous rounds' data.
    """
    if not history:
        return ""
    prev_score = history[-1].get("score")
    if prev_score is None or current_score is None:
        return ""
    delta = current_score - prev_score
    sign = "+" if delta > 0 else ""
    return f"- **Previous**: {prev_score}/10 → {current_score}/10 ({sign}{delta})"


def format_feedback(data, threshold, round_num, max_rounds, first_round=False, trend=""):
    """Format evaluation result into structured, actionable feedback.

    Args:
        data: Evaluation result dict with score, breakdown, weaknesses, etc.
        threshold: Minimum score to pass.
        round_num: Current round number.
        max_rounds: Maximum evaluation rounds.
        first_round: Whether this is the first round (mandatory rejection).
        trend: Trend line string from format_trend() (empty on round 1).
    """
    score = data.get("score", "?")
    breakdown = data.get("breakdown") or {}
    weaknesses = data.get("weaknesses") or []
    suggestions = data.get("suggestions") or []
    strengths = data.get("strengths") or []

    # Header
    lines = ["## Evaluation Result"]
    if first_round:
        lines.append(
            f"- **Score**: {score}/10 (threshold: {threshold}) | "
            f"**First-round review** — Round 1/{max_rounds}"
        )
    else:
        lines.append(
            f"- **Score**: {score}/10 (threshold: {threshold}) | "
            f"Round {round_num}/{max_rounds}"
        )
    if trend:
        lines.append(trend)

    # Breakdown sorted by lowest scores first
    lines.append("")
    lines.append("## Breakdown (lowest scores first)")
    lines.append("| Criterion | Score |")
    lines.append("|-----------|-------|")
    sorted_breakdown = sorted(breakdown.items(), key=lambda x: x[1])
    for criterion, value in sorted_breakdown:
        lines.append(f"| {criterion} | {value}/2 |")
    # Show missing criteria as ?
    all_criteria = ["completeness", "correctness", "sequencing", "risk_awareness", "clarity"]
    for c in all_criteria:
        if c not in breakdown:
            lines.append(f"| {c} | ?/2 |")

    # Issues — must fix
    if weaknesses:
        lines.append("")
        lines.append("## Issues (must fix)")
        for w in weaknesses:
            lines.append(f"- {w}")

    # Suggestions — improvements
    if suggestions:
        lines.append("")
        lines.append("## Suggestions (improvements)")
        for s in suggestions:
            lines.append(f"- {s}")

    # Call to action
    lines.append("")
    lines.append("## What To Do")
    lines.append("Revise the plan addressing the issues above. Then call ExitPlanMode.")

    return "\n".join(lines)


def truncate_for_system_message(score, round_num, max_rounds, trend,
                                 issues, suggestions, strengths, limit=2000):
    """Build systemMessage: score + round + trend only (details are in reason)."""
    parts = [f"Planman: {score}/10 | Round {round_num}/{max_rounds}"]
    if trend:
        parts.append(trend)
    return "\n".join(parts)


def format_approval(data):
    """Format approval message."""
    score = data.get("score", "?")
    return f"Plan approved (score: {score}/10)."


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

    # Check round limit — pass through with clear proceed signal
    if state["round_count"] > config.max_rounds:
        log("max rounds exceeded — auto-approving", config, cwd)
        try:
            save_state(state)
        except (OSError, ValueError) as e:
            log(f"failed to save state: {e}", config, cwd)
        last_score = state.get('last_score')
        if last_score is not None:
            score_msg = f"Last score was {last_score}/10 (threshold: {config.threshold}). "
        else:
            score_msg = ""
        return {
            "action": "pass",
            "reason": None,
            "system_message": (
                f"Planman: Max evaluation rounds ({config.max_rounds}) reached. "
                f"{score_msg}"
                "Plan accepted — proceed with implementation."
            ),
        }

    # Stress-test mode: skip Codex on round 1, block with prompt as reason.
    # Round 2+ continues with normal Codex evaluation.
    if config.stress_test and state["round_count"] == 1:
        prompt = config.stress_test_prompt
        state = record_feedback(state, None, prompt, None)
        try:
            save_state(state)
        except (OSError, ValueError) as e:
            log(f"failed to save state: {e}", config, cwd)
        log("stress-test mode: first plan rejected without evaluation", config, cwd)
        return {
            "action": "block",
            "reason": prompt,
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
        # Persist state so round_count advances (prevents infinite retry loops)
        try:
            save_state(state)
        except (OSError, ValueError) as e:
            log(f"failed to save state: {e}", config, cwd)
        error_brief = error[:500] if len(error) > 500 else error
        if config.fail_open:
            return {
                "action": "pass",
                "reason": None,
                "system_message": f"Planman: Evaluation failed ({error_brief}). Passing through (fail-open).",
            }
        else:
            return {
                "action": "block",
                "reason": f"Planman evaluation failed: {error_brief}. Set PLANMAN_FAIL_OPEN=true to pass through on errors.",
                "system_message": None,
            }

    assessment_score = result["score"]
    weaknesses = result.get("weaknesses") or []
    suggestions = result.get("suggestions") or []
    strengths = result.get("strengths") or []

    # Compute trend BEFORE recording feedback (history has prev rounds only)
    trend = format_trend(state.get("history", []), assessment_score)

    # First-round mandatory rejection
    if state["round_count"] == 1:
        feedback_text = format_feedback(
            result, config.threshold, state["round_count"], config.max_rounds,
            first_round=True, trend=trend,
        )
        sys_msg = truncate_for_system_message(
            assessment_score, state["round_count"], config.max_rounds,
            trend, weaknesses, suggestions, strengths,
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
            "system_message": sys_msg,
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
            result, config.threshold, state["round_count"], config.max_rounds,
            trend=trend,
        )
        sys_msg = truncate_for_system_message(
            assessment_score, state["round_count"], config.max_rounds,
            trend, weaknesses, suggestions, strengths,
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
            "system_message": sys_msg,
        }
