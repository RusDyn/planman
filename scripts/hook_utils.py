"""Shared evaluation helpers for planman hooks (PostToolUse + Stop).

Contains the common evaluation flow:
  detect plan -> load state -> check round limit -> assess -> format output

Used by both post_tool_hook.py (plan mode) and stop_hook.py (inline plans).
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
    _is_stale,
    clear_state,
    compute_plan_hash,
    load_state,
    record_feedback,
    save_state,
    update_for_plan,
)

# Path for recent-evaluation marker (prevents double-eval by Stop hook)
_RECENT_EVAL_PATH = os.path.join(tempfile.gettempdir(), "planman-recent-eval.json")
_RECENT_EVAL_TTL = 60  # seconds


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


def mark_recent_evaluation(session_id):
    """Write a marker so the Stop hook can skip double-evaluation."""
    try:
        with open(_RECENT_EVAL_PATH, "w", encoding="utf-8") as f:
            json.dump({"session_id": session_id, "timestamp": time.time()}, f)
    except OSError:
        pass


def was_recently_evaluated(session_id=None):
    """Check if a plan was recently evaluated by PostToolUse hook.

    When *session_id* is provided, only returns True if the marker
    belongs to the same session — prevents cross-session false skipping.
    """
    try:
        with open(_RECENT_EVAL_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        ts = data.get("timestamp", 0)
        if time.time() - ts < _RECENT_EVAL_TTL:
            if session_id is not None and data.get("session_id") != session_id:
                return False
            return True
    except (OSError, json.JSONDecodeError, KeyError):
        pass
    return False


def _run_stop_hook_evaluation(plan_text, state, session_id, config, cwd):
    """Stop-hook classify-only path — no round tracking, no stress-test.

    Asks the LLM whether *plan_text* is a plan.  If yes, evaluates quality
    and blocks/passes.  If no, caches the non-plan hash and passes through.
    No round counter, no first-round rejection, no stress-test rejection.
    """
    result, error = evaluate_plan(plan_text, config, None, 1, cwd=cwd)

    if error:
        log(f"stop-hook evaluation error: {error}", config, cwd)
        if config.fail_open:
            return {
                "action": "pass",
                "reason": None,
                "system_message": f"Planman: Evaluation failed ({error}). Passing through (fail-open).",
            }
        return {
            "action": "block",
            "reason": f"Planman evaluation failed: {error}. Set PLANMAN_FAIL_OPEN=true to pass through on errors.",
            "system_message": None,
        }

    is_plan_flag = result.get("is_plan", True)

    if not is_plan_flag:
        # Cache non-plan hash so next stop hook call can skip LLM
        state["last_nonplan_hash"] = compute_plan_hash(plan_text)
        save_state(state)
        log("LLM classified as non-plan — passing through", config, cwd)
        return {"action": "pass", "reason": None, "system_message": None}

    # It IS a plan — evaluate quality and act on score
    mark_recent_evaluation(session_id)
    state.pop("last_nonplan_hash", None)
    assessment_score = result["score"]

    if assessment_score >= config.threshold:
        log(f"stop-hook: inline plan accepted ({assessment_score}/10)", config, cwd)
        return {
            "action": "pass",
            "reason": None,
            "system_message": f"Planman: {format_approval(result)}",
        }

    feedback_text = format_feedback(
        result, config.threshold, 1, config.max_rounds
    )
    log(f"stop-hook: inline plan rejected ({assessment_score}/{config.threshold})", config, cwd)
    return {
        "action": "block",
        "reason": feedback_text,
        "system_message": (
            f"Planman: Inline plan rejected ({assessment_score}/10, "
            f"threshold {config.threshold})."
        ),
    }


def run_evaluation(plan_text, session_id, config, cwd=None, plan_path=None):
    """Run the full plan evaluation flow.

    Returns a dict with keys:
        action: "pass" | "block" | "skip"
        reason: str or None (for block)
        system_message: str or None
    """
    # Empty text — skip
    if not plan_text or not plan_text.strip():
        return {"action": "skip", "reason": None, "system_message": None}

    # Load state and update round counter
    state = load_state(session_id)

    # Skip LLM if this text was already classified as non-plan
    if not plan_path:
        text_hash = compute_plan_hash(plan_text)
        if text_hash == state.get("last_nonplan_hash"):
            return {"action": "pass", "reason": None, "system_message": None}

    # Check if plan-mode owns this session BEFORE updating state.
    # update_for_plan guards against this too, but we must short-circuit
    # the entire evaluation — otherwise run_evaluation would continue with
    # the guarded state and potentially save contaminated data (e.g.
    # last_nonplan_hash, record_feedback) to plan-mode's state file.
    if not plan_path and state.get("plan_file_path") and not _is_stale(state):
        log("plan-mode session active — skipping evaluation", config, cwd)
        return {"action": "pass", "reason": None, "system_message": None}

    # ── Stop-hook path (no plan_path): classify-only ──────────────
    # Skip all plan-mode machinery (stress-test, round tracking,
    # first-round rejection, max-rounds).  Just ask the LLM whether
    # this text is a plan, cache the answer, and act on the score.
    if not plan_path:
        return _run_stop_hook_evaluation(plan_text, state, session_id, config, cwd)

    # ── Plan-mode path (plan_path set): full multi-round flow ────
    state = update_for_plan(state, plan_text, plan_path)
    log(f"round {state['round_count']}/{config.max_rounds}", config, cwd)

    # Check round limit — block for human decision
    if state["round_count"] > config.max_rounds:
        log("max rounds exceeded — blocking for human decision", config, cwd)
        clear_state(session_id)
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
        mark_recent_evaluation(session_id)
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
    is_plan_flag = result.get("is_plan", True)

    # Non-plan text — pass through without touching state
    if not is_plan_flag:
        log("LLM classified as non-plan — passing through", config, cwd)
        return {"action": "pass", "reason": None, "system_message": None}

    # Only mark evaluation AFTER confirming it's a plan
    mark_recent_evaluation(session_id)

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
        # Plan passes (round >= 2)
        log(f"plan accepted: {assessment_score}/10", config, cwd)
        clear_state(session_id)
        log("session state cleared", config, cwd)
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
