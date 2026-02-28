"""Multi-round session state tracking.

State file per session at <tempdir>/planman-{session_id}.json.
Tracks round count, last score/feedback, and plan hash for change detection.
"""

import hashlib
import json
import os
import tempfile
import time

from path_utils import normalize_path as _normalize


def _state_path(session_id):
    """Return the state file path for a session."""
    safe_id = "".join(c for c in session_id if c.isalnum() or c in "-_")
    return os.path.join(tempfile.gettempdir(), f"planman-{safe_id or 'default'}.json")


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


def update_for_plan(state, plan_text, plan_path=None):
    """Update state for a new plan evaluation.

    Resets round counter when:
    - plan_path differs from stored path (new plan file)
    Otherwise increments.
    """
    new_hash = compute_plan_hash(plan_text)

    normalized_plan_path = _normalize(plan_path) if plan_path else None
    stored_path = _normalize(state.get("plan_file_path")) if state.get("plan_file_path") else None

    if normalized_plan_path and normalized_plan_path != stored_path:
        # New plan file (or first file) = new plan
        state["round_count"] = 1
    else:
        # Same file = revision
        state["round_count"] = state.get("round_count", 0) + 1

    state["plan_hash"] = new_hash
    state["last_eval_time"] = time.time()
    if normalized_plan_path:
        state["plan_file_path"] = normalized_plan_path
    return state


def record_feedback(state, score, feedback, breakdown=None):
    """Record evaluation results in state."""
    state["last_score"] = score
    state["last_feedback"] = feedback
    if breakdown:
        state["last_breakdown"] = breakdown
    return state
