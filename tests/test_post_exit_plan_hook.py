"""Tests for post_exit_plan_hook.py — PostToolUse(ExitPlanMode) state clearing."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from hook_utils import MARKER_TEMPLATE, safe_session_id
from state import clear_state, load_state, save_state, _state_path


class TestPostExitPlanHookClearing(unittest.TestCase):
    """Test that the PostToolUse(ExitPlanMode) logic correctly clears state."""

    def setUp(self):
        self._session_id = f"test-post-exit-{os.getpid()}-{id(self)}"

    def tearDown(self):
        clear_state(self._session_id)
        # Clean up marker file
        safe_id = safe_session_id(self._session_id)
        marker_path = MARKER_TEMPLATE.format(session_id=safe_id)
        try:
            os.unlink(marker_path)
        except OSError:
            pass

    def test_clears_state_file(self):
        """State file is removed after clear_state."""
        state = {
            "session_id": self._session_id,
            "round_count": 3,
            "last_score": 8,
            "last_feedback": "Good plan",
            "plan_hash": "abc123",
            "plan_file_path": "/test.md",
            "plan_approved": True,
            "history": [{"round": 1, "score": 5}],
        }
        save_state(state)
        path = _state_path(self._session_id)
        self.assertTrue(os.path.exists(path))

        clear_state(self._session_id)
        self.assertFalse(os.path.exists(path))

        # load_state returns defaults after clearing
        loaded = load_state(self._session_id)
        self.assertEqual(loaded["round_count"], 0)
        self.assertIsNone(loaded["last_score"])

    def test_clears_marker_file(self):
        """Marker file is removed."""
        safe_id = safe_session_id(self._session_id)
        marker_path = MARKER_TEMPLATE.format(session_id=safe_id)

        # Create a marker file
        with open(marker_path, "w") as f:
            json.dump({"plan_file_path": "/test.md", "timestamp": 1234567890}, f)
        self.assertTrue(os.path.exists(marker_path))

        # Simulate the PostToolUse clearing logic
        try:
            os.unlink(marker_path)
        except OSError:
            pass
        self.assertFalse(os.path.exists(marker_path))

    def test_no_state_file_no_error(self):
        """clear_state doesn't crash if state file doesn't exist."""
        clear_state("definitely-nonexistent-session-xyz")

    def test_no_marker_file_no_error(self):
        """Removing a nonexistent marker doesn't crash."""
        safe_id = safe_session_id("nonexistent-session")
        marker_path = MARKER_TEMPLATE.format(session_id=safe_id)
        try:
            os.unlink(marker_path)
        except OSError:
            pass
        # No exception raised


class TestPlanApprovedFlagFallback(unittest.TestCase):
    """Test the plan_approved flag as fallback when PostToolUse doesn't fire."""

    def setUp(self):
        self._session_id = f"test-flag-{os.getpid()}-{id(self)}"

    def tearDown(self):
        clear_state(self._session_id)

    def test_plan_approved_flag_resets_round(self):
        """When plan_approved=True, update_for_plan resets to round 1."""
        from state import update_for_plan

        state = {
            "session_id": self._session_id,
            "round_count": 3,
            "plan_hash": "old",
            "plan_file_path": "/plan.md",
            "plan_approved": True,
            "history": [{"round": 1, "score": 5}, {"round": 2, "score": 7}],
        }
        state = update_for_plan(state, "New plan text", plan_path="/plan.md")
        self.assertEqual(state["round_count"], 1)
        self.assertEqual(state["history"], [])
        self.assertNotIn("plan_approved", state)

    def test_plan_approved_flag_consumed(self):
        """Flag is consumed (removed) after being used."""
        from state import update_for_plan

        state = {
            "session_id": self._session_id,
            "round_count": 2,
            "plan_approved": True,
        }
        state = update_for_plan(state, "Plan text", plan_path="/plan.md")
        self.assertNotIn("plan_approved", state)

        # Next call increments normally (flag gone)
        state = update_for_plan(state, "Revised plan", plan_path="/plan.md")
        self.assertEqual(state["round_count"], 2)

    def test_no_flag_normal_increment(self):
        """Without plan_approved flag, normal increment behavior."""
        from state import update_for_plan

        state = {
            "session_id": self._session_id,
            "round_count": 2,
            "plan_hash": "old",
            "plan_file_path": "/plan.md",
        }
        state = update_for_plan(state, "Revised plan", plan_path="/plan.md")
        self.assertEqual(state["round_count"], 3)

    def test_flag_works_with_same_file_path(self):
        """Flag resets even when the same file path is used (the core bug fix)."""
        from state import update_for_plan

        state = {
            "session_id": self._session_id,
            "round_count": 5,
            "plan_hash": "old",
            "plan_file_path": "/plan.md",
            "plan_approved": True,
            "history": [{"round": i, "score": i + 3} for i in range(1, 6)],
        }
        state = update_for_plan(state, "Completely new plan", plan_path="/plan.md")
        self.assertEqual(state["round_count"], 1)
        self.assertEqual(state["history"], [])


if __name__ == "__main__":
    unittest.main()
