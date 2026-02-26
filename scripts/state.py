"""Multi-round session state tracking.

State file per session at <tempdir>/planman-{session_id}.json.
Tracks round count, last score/feedback, and plan hash for change detection.
"""

import hashlib
import json
import os
import tempfile
import time

_STALE_TTL = 1800  # 30 minutes — reset round counter after inactivity


def _state_path(session_id):
    """Return the state file path for a session."""
    safe_id = "".join(c for c in session_id if c.isalnum() or c in "-_")
    return os.path.join(tempfile.gettempdir(), f"planman-{safe_id}.json")


def compute_plan_hash(plan_text):
    """Compute a short hash of the plan text for change detection.

    Normalizes whitespace so minor formatting changes don't reset rounds.
    """
    normalized = " ".join(plan_text.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def load_state(session_id):
    """Load session state, returning a dict.

    Returns default state if file doesn't exist or is corrupt.
    """
    path = _state_path(session_id)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "session_id" in data:
            return data
    except (OSError, json.JSONDecodeError, ValueError):
        pass

    return {
        "session_id": session_id,
        "round_count": 0,
        "last_score": None,
        "last_feedback": None,
        "plan_hash": None,
    }


def save_state(state):
    """Save session state atomically via write-to-temp + os.replace()."""
    session_id = state.get("session_id", "unknown")
    path = _state_path(session_id)

    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=tempfile.gettempdir(), prefix="planman-tmp-"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, allow_nan=False)
        os.replace(tmp_path, path)
    except (OSError, ValueError):
        # Best-effort cleanup
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def clear_state(session_id):
    """Remove session state file."""
    path = _state_path(session_id)
    try:
        os.unlink(path)
    except OSError:
        pass


def _is_stale(state):
    """Return True if last evaluation was long enough ago to indicate a new planning context."""
    last_time = state.get("last_eval_time")
    if not isinstance(last_time, (int, float)):
        return False
    try:
        return (time.time() - last_time) > _STALE_TTL
    except (TypeError, ValueError):
        return False


def _compute_plan_fingerprint(plan_text):
    """Compute a deterministic identity fingerprint for an inline plan.

    Combines the first heading/line (stable across revisions) with a normalized
    prefix hash (first 500 chars, catches same-title/different-plan collisions).
    Returns a string like "# My Plan|a3b2c1d0".
    """
    title = ""
    for line in plan_text.strip().splitlines():
        line = line.strip()
        if line.startswith("#"):
            title = line
            break
        if line:
            title = line[:120]
            break

    # Prefix hash: first 500 chars normalized — stable across minor revisions
    # but changes when the plan body is fundamentally different
    prefix = " ".join(plan_text[:500].split())
    prefix_hash = hashlib.sha256(prefix.encode("utf-8")).hexdigest()[:8]
    return f"{title}|{prefix_hash}"


def update_for_plan(state, plan_text, plan_path=None):
    """Update state for a new plan evaluation.

    Resets round counter when:
    - plan_path differs from stored path (new plan file — plan-mode signal)
    - plan_path absent AND fingerprint differs (new inline plan — stop-hook signal)
    - plan_path absent AND no stored fingerprint (first inline eval — reset)
    - Last evaluation was > 30 min ago (stale session fallback)
    Otherwise increments.
    """
    new_hash = compute_plan_hash(plan_text)

    if plan_path and plan_path != state.get("plan_file_path"):
        # Plan-mode: different file (or first file) = new plan
        state["round_count"] = 1
        state["plan_fingerprint"] = _compute_plan_fingerprint(plan_text)
    elif not plan_path:
        # Active plan-mode session: don't let stop hook corrupt round counter
        if state.get("plan_file_path") and not _is_stale(state):
            return state
        # Stop-hook path: fingerprint = title + prefix hash
        new_fp = _compute_plan_fingerprint(plan_text)
        old_fp = state.get("plan_fingerprint")
        if old_fp is None:
            # First inline evaluation in this session — reset to 1
            state["round_count"] = 1
        elif new_fp != old_fp:
            # Different plan (title or body prefix changed substantially)
            state["round_count"] = 1
        elif _is_stale(state):
            state["round_count"] = 1
        else:
            state["round_count"] = state.get("round_count", 0) + 1
        state["plan_fingerprint"] = new_fp
    else:
        # Plan-mode: same file = revision
        state["round_count"] = state.get("round_count", 0) + 1
        state["plan_fingerprint"] = _compute_plan_fingerprint(plan_text)

    state["plan_hash"] = new_hash
    state["last_eval_time"] = time.time()
    if plan_path:
        state["plan_file_path"] = plan_path
    return state


def record_feedback(state, score, feedback, breakdown=None):
    """Record evaluation results in state."""
    state["last_score"] = score
    state["last_feedback"] = feedback
    if breakdown:
        state["last_breakdown"] = breakdown
    return state
