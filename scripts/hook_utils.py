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

import glob

from config import load_config
from evaluator import check_codex_installed, evaluate_plan
from path_utils import normalize_path
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


def is_plan_filename(basename):
    """Return True if basename looks like an actual plan file (not metadata)."""
    lower = basename.lower()
    if lower.startswith("."):
        return False
    skip_prefixes = ("readme", "template", "sample", "example", "backup")
    for prefix in skip_prefixes:
        if lower.startswith(prefix):
            return False
    return True


def read_plan_text(path):
    """Read plan file, return (text, skip_reason).

    text is None when the file is empty/oversized/unreadable.
    skip_reason is set only when the file was found but explicitly rejected.
    """
    _MAX_PLAN_SIZE = 1_000_000  # 1 MB
    try:
        size = os.path.getsize(path)
        if size > _MAX_PLAN_SIZE:
            return None, f"Plan file too large (>{_MAX_PLAN_SIZE // 1_000_000} MB): {path}"
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        return (text, None) if text.strip() else (None, None)
    except (OSError, UnicodeDecodeError):
        return None, None


def read_marker_metadata(session_id):
    """Read marker file and return (normalized_path_or_None, timestamp_float).

    Returns (None, 0) for: missing file, corrupt JSON, missing keys,
    non-numeric timestamp, future timestamp (clamped to 0).
    """
    safe_id = safe_session_id(session_id)
    marker_path = MARKER_TEMPLATE.format(session_id=safe_id)
    try:
        with open(marker_path, "r", encoding="utf-8") as f:
            marker = json.load(f)
    except (OSError, json.JSONDecodeError):
        return (None, 0)

    if not isinstance(marker, dict):
        return (None, 0)

    plan_path = marker.get("plan_file_path")
    if not plan_path or not isinstance(plan_path, str):
        return (None, 0)

    ts = marker.get("timestamp")
    if ts is None:
        return (None, 0)
    try:
        ts = float(ts)
    except (ValueError, TypeError):
        return (None, 0)

    # Future timestamp → clamp to 0 (marker still trusted if file exists)
    if ts > time.time():
        ts = 0

    return (normalize_path(plan_path), ts)


def scan_plan_dirs(cwd, plan_dirs=None, project_local_only=False):
    """Scan plan directories for most recently modified .md plan file.

    Checks {cwd}/.claude/plans/, configured plan_dirs (resolved relative
    to cwd), and optionally ~/.claude/plans/.
    Returns the path of the best candidate or None.
    """
    home_plans = os.path.expanduser("~/.claude/plans")
    cwd_plans = os.path.join(cwd, ".claude", "plans") if cwd else None

    scan_dirs_list = []
    if cwd_plans and os.path.isdir(cwd_plans):
        scan_dirs_list.append(cwd_plans)

    # Additional plan_dirs (resolved relative to cwd)
    if plan_dirs and cwd:
        for d in plan_dirs:
            resolved = os.path.join(cwd, d) if not os.path.isabs(d) else d
            resolved = os.path.realpath(resolved)
            if os.path.isdir(resolved) and resolved not in scan_dirs_list:
                scan_dirs_list.append(resolved)

    if not project_local_only:
        if os.path.isdir(home_plans):
            real_home = os.path.realpath(home_plans)
            if real_home not in [os.path.realpath(d) for d in scan_dirs_list]:
                scan_dirs_list.append(home_plans)

    md_files = []
    for plans_dir in scan_dirs_list:
        for f in glob.glob(os.path.join(plans_dir, "*.md")):
            if is_plan_filename(os.path.basename(f)):
                md_files.append(f)

    if not md_files:
        return None

    return max(md_files, key=os.path.getmtime)


def find_plan_file(session_id, cwd, config):
    """Find the plan file path via session marker or fallback scan.

    Returns (plan_file_path, plan_text, skip_reason) where skip_reason
    is a human-readable message when the plan was found but rejected
    (e.g. oversized), or None on success / when no plan exists at all.
    """
    _MARKER_TTL = 7200  # 2 hours
    _STALENESS_TOLERANCE = 2  # seconds

    plan_dirs = getattr(config, "plan_dirs", None)

    # ── Gate: debug marker-only mode (internal escape hatch) ──
    if os.environ.get("_PLANMAN_DEBUG_MARKER_ONLY"):
        marker_path, _ = read_marker_metadata(session_id)
        if marker_path and os.path.isfile(marker_path):
            text, skip = read_plan_text(marker_path)
            if text:
                return (marker_path, text, None)
            return (None, None, skip)
        return (None, None, None)

    # ── Step 1: Try marker (authoritative when fresh) ──
    marker_plan_path, marker_ts = read_marker_metadata(session_id)
    now = time.time()
    expired = marker_ts > 0 and (now - marker_ts) > (_MARKER_TTL + _STALENESS_TOLERANCE)

    if marker_plan_path and os.path.isfile(marker_plan_path) and not expired:
        text, skip = read_plan_text(marker_plan_path)
        if text:
            log(
                f"plan detection: source=marker, path={marker_plan_path}, "
                f"session={safe_session_id(session_id)}",
                config, cwd,
            )
            return (marker_plan_path, text, None)
        if skip:
            return (None, None, skip)

    # ── Step 2: Scan fallback (marker missing/expired/file deleted) ──
    scan_path = scan_plan_dirs(cwd, plan_dirs=plan_dirs, project_local_only=True)
    if not scan_path:
        scan_path = scan_plan_dirs(cwd, plan_dirs=plan_dirs, project_local_only=False)

    reason = (
        "marker_expired" if expired
        else "marker_file_deleted" if marker_plan_path
        else "no_marker"
    )
    if scan_path:
        text, skip = read_plan_text(scan_path)
        if text:
            log(
                f"plan detection: source=scan_fallback({reason}), "
                f"path={scan_path}, session={safe_session_id(session_id)}",
                config, cwd,
            )
            return (scan_path, text, None)
        if skip:
            return (None, None, skip)

    return (None, None, None)


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


def format_feedback(data, threshold, round_num, max_rounds, first_round=False, trend="",
                     min_rounds_remaining=None):
    """Format evaluation result into structured, actionable feedback.

    Args:
        data: Evaluation result dict with score, breakdown, weaknesses, etc.
        threshold: Minimum score to pass.
        round_num: Current round number.
        max_rounds: Maximum evaluation rounds.
        first_round: Whether this is the first round (mandatory rejection).
        trend: Trend line string from format_trend() (empty on round 1).
        min_rounds_remaining: If set, number of rounds still required before plan can pass.
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
    if min_rounds_remaining:
        lines.append(f"- **Min rounds**: {min_rounds_remaining} more round(s) required before plan can pass")

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
        state["plan_approved"] = True
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

    # Stress-test mode: skip Codex for the first N rounds, block with prompt as reason.
    # Round N+1 continues with normal Codex evaluation.
    if config.stress_test and state["round_count"] <= config.stress_test:
        prompt = config.stress_test_prompt
        state = record_feedback(state, None, prompt, None)
        try:
            save_state(state)
        except (OSError, ValueError) as e:
            log(f"failed to save state: {e}", config, cwd)
        log(f"stress-test mode: round {state['round_count']}/{config.stress_test} rejected without evaluation", config, cwd)
        return {
            "action": "block",
            "reason": prompt,
            "system_message": (
                f"Planman: Stress-test mode — plan rejected for deep revision. "
                f"Stress-test round {state['round_count']}/{config.stress_test} | "
                f"Round {state['round_count']}/{config.max_rounds}."
            ),
        }

    # Assess via codex
    previous_feedback = state.get("last_feedback")
    result, error = evaluate_plan(
        plan_text, config, previous_feedback, state["round_count"], cwd=cwd
    )

    if error:
        log(f"evaluation error: {error}", config, cwd)
        error_brief = error[:500] if len(error) > 500 else error
        if config.fail_open:
            state["plan_approved"] = True
            try:
                save_state(state)
            except (OSError, ValueError) as e:
                log(f"failed to save state: {e}", config, cwd)
            return {
                "action": "pass",
                "reason": None,
                "system_message": f"Planman: Evaluation failed ({error_brief}). Passing through (fail-open).",
            }
        else:
            # Persist state so round_count advances (prevents infinite retry loops)
            try:
                save_state(state)
            except (OSError, ValueError) as e:
                log(f"failed to save state: {e}", config, cwd)
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

    # Min rounds enforcement: block even if score would pass
    if config.min_rounds and state["round_count"] < config.min_rounds:
        feedback_text = format_feedback(
            result, config.threshold, state["round_count"], config.max_rounds,
            trend=trend, min_rounds_remaining=config.min_rounds - state["round_count"],
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
        log(f"min rounds: {state['round_count']}/{config.min_rounds} — blocking", config, cwd)
        return {"action": "block", "reason": feedback_text, "system_message": sys_msg}

    if assessment_score >= config.threshold:
        # Plan passes (round >= 2) — preserve state but null feedback
        state = record_feedback(state, assessment_score, None, result.get("breakdown"))
        state["plan_approved"] = True
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
