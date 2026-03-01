"""Tests for state.py — round-trip, corruption, hash change."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from state import (
    clear_state,
    compute_plan_hash,
    load_state,
    record_feedback,
    save_state,
    update_for_plan,
    _state_path,
)


class TestComputePlanHash(unittest.TestCase):
    def test_deterministic(self):
        h1 = compute_plan_hash("My plan is to do X")
        h2 = compute_plan_hash("My plan is to do X")
        self.assertEqual(h1, h2)

    def test_whitespace_normalized(self):
        h1 = compute_plan_hash("My  plan\n  is   to do X")
        h2 = compute_plan_hash("My plan is to do X")
        self.assertEqual(h1, h2)

    def test_different_plans_different_hash(self):
        h1 = compute_plan_hash("Plan A")
        h2 = compute_plan_hash("Plan B")
        self.assertNotEqual(h1, h2)

    def test_length(self):
        h = compute_plan_hash("test")
        self.assertEqual(len(h), 16)


class TestLoadSaveState(unittest.TestCase):
    def setUp(self):
        self._session_id = f"test-{os.getpid()}-{id(self)}"

    def tearDown(self):
        clear_state(self._session_id)

    def test_roundtrip(self):
        state = {
            "session_id": self._session_id,
            "round_count": 2,
            "last_score": 5,
            "last_feedback": "Needs improvement",
            "plan_hash": "abc123",
        }
        save_state(state)
        loaded = load_state(self._session_id)
        self.assertEqual(loaded["round_count"], 2)
        self.assertEqual(loaded["last_score"], 5)
        self.assertEqual(loaded["last_feedback"], "Needs improvement")
        self.assertEqual(loaded["plan_hash"], "abc123")

    def test_missing_file_returns_default(self):
        state = load_state("nonexistent-session-id-xyz")
        self.assertEqual(state["round_count"], 0)
        self.assertIsNone(state["last_score"])

    def test_corrupt_file_returns_default(self):
        path = _state_path(self._session_id)
        with open(path, "w") as f:
            f.write("{bad json")
        state = load_state(self._session_id)
        self.assertEqual(state["round_count"], 0)

    def test_non_dict_file_returns_default(self):
        path = _state_path(self._session_id)
        with open(path, "w") as f:
            json.dump([1, 2, 3], f)
        state = load_state(self._session_id)
        self.assertEqual(state["round_count"], 0)


class TestClearState(unittest.TestCase):
    def test_clear_removes_file(self):
        session_id = f"test-clear-{os.getpid()}"
        save_state({"session_id": session_id, "round_count": 1})
        path = _state_path(session_id)
        self.assertTrue(os.path.exists(path))
        clear_state(session_id)
        self.assertFalse(os.path.exists(path))

    def test_clear_nonexistent_no_error(self):
        clear_state("definitely-does-not-exist")


class TestUpdateForPlan(unittest.TestCase):
    def test_first_round_no_path(self):
        """No plan_path → increments from 0 to 1."""
        state = {
            "session_id": "test",
            "round_count": 0,
            "plan_hash": None,
        }
        state = update_for_plan(state, "My plan")
        self.assertEqual(state["round_count"], 1)
        self.assertIsNotNone(state["plan_hash"])
        self.assertIn("last_eval_time", state)

    def test_plan_path_first_plan_resets(self):
        """plan_path provided, no stored path → round 1."""
        state = {"session_id": "test", "round_count": 0, "plan_hash": None}
        state = update_for_plan(state, "My plan", plan_path="/a.md")
        self.assertEqual(state["round_count"], 1)
        self.assertEqual(state["plan_file_path"], "/a.md")

    def test_plan_path_changed_resets(self):
        """plan_path differs from stored → round 1."""
        state = {
            "session_id": "test",
            "round_count": 3,
            "plan_hash": compute_plan_hash("Old plan"),
            "plan_file_path": "/a.md",
        }
        state = update_for_plan(state, "New plan", plan_path="/b.md")
        self.assertEqual(state["round_count"], 1)
        self.assertEqual(state["plan_file_path"], "/b.md")

    def test_plan_path_same_increments(self):
        """Same plan_path → increment."""
        state = {
            "session_id": "test",
            "round_count": 2,
            "plan_hash": compute_plan_hash("My plan"),
            "plan_file_path": "/a.md",
        }
        state = update_for_plan(state, "My revised plan", plan_path="/a.md")
        self.assertEqual(state["round_count"], 3)

    def test_no_plan_path_increments(self):
        """No plan_path, existing state → increment."""
        state = {
            "session_id": "test",
            "round_count": 2,
            "plan_hash": compute_plan_hash("My plan"),
        }
        state = update_for_plan(state, "My revised plan")
        self.assertEqual(state["round_count"], 3)

    def test_plan_hash_updated(self):
        """plan_hash is updated on each call."""
        state = {"session_id": "test", "round_count": 0, "plan_hash": None}
        state = update_for_plan(state, "Plan version 1", plan_path="/a.md")
        hash1 = state["plan_hash"]
        state = update_for_plan(state, "Plan version 2", plan_path="/a.md")
        hash2 = state["plan_hash"]
        self.assertNotEqual(hash1, hash2)

    def test_last_eval_time_set(self):
        """last_eval_time is always set."""
        import time
        state = {"session_id": "test", "round_count": 0, "plan_hash": None}
        before = time.time()
        state = update_for_plan(state, "My plan", plan_path="/a.md")
        after = time.time()
        self.assertGreaterEqual(state["last_eval_time"], before)
        self.assertLessEqual(state["last_eval_time"], after)


class TestRecordFeedback(unittest.TestCase):
    def test_records_score_and_feedback(self):
        state = {"session_id": "test", "round_count": 1}
        state = record_feedback(state, 5, "Needs work")
        self.assertEqual(state["last_score"], 5)
        self.assertEqual(state["last_feedback"], "Needs work")

    def test_records_breakdown(self):
        state = {"session_id": "test", "round_count": 1}
        breakdown = {"completeness": 2, "correctness": 1}
        state = record_feedback(state, 7, "Good", breakdown)
        self.assertEqual(state["last_breakdown"], breakdown)

    def test_none_breakdown_not_recorded(self):
        state = {"session_id": "test", "round_count": 1}
        state = record_feedback(state, 5, "OK", None)
        self.assertNotIn("last_breakdown", state)


class TestHistoryAppend(unittest.TestCase):
    """Test history array management in record_feedback and update_for_plan."""

    def test_history_appends(self):
        state = {"session_id": "test", "round_count": 1, "history": []}
        state = record_feedback(state, 5, "Round 1 feedback")
        self.assertEqual(len(state["history"]), 1)
        self.assertEqual(state["history"][0]["score"], 5)
        self.assertEqual(state["history"][0]["round"], 1)

    def test_history_caps_at_20(self):
        state = {"session_id": "test", "round_count": 1, "history": []}
        for i in range(25):
            state["round_count"] = i + 1
            state = record_feedback(state, i % 10 + 1, f"Feedback {i}")
        self.assertEqual(len(state["history"]), 20)
        # Oldest entries should be trimmed
        self.assertEqual(state["history"][0]["round"], 6)

    def test_history_resets_on_new_plan_file(self):
        state = {
            "session_id": "test", "round_count": 2,
            "plan_hash": compute_plan_hash("Old plan"),
            "plan_file_path": "/a.md",
            "history": [{"round": 1, "score": 5}],
        }
        state = update_for_plan(state, "New plan", plan_path="/b.md")
        self.assertEqual(state["round_count"], 1)
        self.assertEqual(state["history"], [])

    def test_history_preserved_on_same_plan_file(self):
        state = {
            "session_id": "test", "round_count": 1,
            "plan_hash": compute_plan_hash("Plan"),
            "plan_file_path": "/a.md",
            "history": [{"round": 1, "score": 5}],
        }
        state = update_for_plan(state, "Revised plan", plan_path="/a.md")
        self.assertEqual(state["round_count"], 2)
        self.assertEqual(len(state["history"]), 1)

    def test_history_persists_across_save_load(self):
        session_id = f"test-history-persist-{os.getpid()}"
        state = load_state(session_id)
        state = update_for_plan(state, "Plan", plan_path="/a.md")
        state = record_feedback(state, 6, "Feedback")
        save_state(state)

        loaded = load_state(session_id)
        self.assertEqual(len(loaded["history"]), 1)
        self.assertEqual(loaded["history"][0]["score"], 6)
        clear_state(session_id)

    def test_history_backward_compat(self):
        """Old state file without history key loads fine."""
        session_id = f"test-compat-{os.getpid()}"
        # Save old-format state (no history key)
        state = {
            "session_id": session_id,
            "round_count": 2,
            "last_score": 5,
            "last_feedback": "Old feedback",
            "plan_hash": "abc",
        }
        save_state(state)

        loaded = load_state(session_id)
        # history defaults to [] via .get()
        self.assertEqual(loaded.get("history", []), [])
        clear_state(session_id)

    def test_history_has_timestamp(self):
        import time
        state = {"session_id": "test", "round_count": 1, "history": []}
        before = time.time()
        state = record_feedback(state, 7, "Feedback")
        after = time.time()
        self.assertGreaterEqual(state["history"][0]["timestamp"], before)
        self.assertLessEqual(state["history"][0]["timestamp"], after)

    def test_history_has_breakdown(self):
        state = {"session_id": "test", "round_count": 1, "history": []}
        breakdown = {"completeness": 2, "correctness": 1}
        state = record_feedback(state, 7, "Feedback", breakdown)
        self.assertEqual(state["history"][0]["breakdown"], breakdown)


class TestSaveStateNanRejection(unittest.TestCase):
    def test_nan_score_rejected(self):
        """save_state must reject NaN values (invalid JSON)."""
        state = {"session_id": "test-nan", "last_score": float("nan")}
        with self.assertRaises(ValueError):
            save_state(state)

    def test_infinity_score_rejected(self):
        """save_state must reject Infinity values (invalid JSON)."""
        state = {"session_id": "test-inf", "last_score": float("inf")}
        with self.assertRaises(ValueError):
            save_state(state)


class TestStatePath(unittest.TestCase):
    def test_sanitizes_session_id(self):
        path = _state_path("a/b/../c")
        self.assertIn("planman-abc", path)
        self.assertNotIn("/", os.path.basename(path).replace("planman-", "").replace(".json", ""))

    def test_empty_session_id_uses_default(self):
        """Empty session_id should use 'default' to avoid collision."""
        path = _state_path("")
        self.assertIn("planman-default", path)

    def test_special_chars_session_id_uses_default(self):
        """Session ID with only special chars should use 'default'."""
        path = _state_path("///...")
        self.assertIn("planman-default", path)


class TestPathNormalization(unittest.TestCase):
    """Test that tilde and absolute paths are treated as the same file."""

    def test_tilde_vs_absolute_same_file(self):
        """~/plans/x.md and /home/user/plans/x.md → round increments, not reset."""
        home = os.path.expanduser("~")
        tilde_path = "~/plans/x.md"
        absolute_path = os.path.join(home, "plans", "x.md")

        state = {"session_id": "test", "round_count": 0, "plan_hash": None}
        state = update_for_plan(state, "My plan v1", plan_path=tilde_path)
        self.assertEqual(state["round_count"], 1)

        # Same file referenced with absolute path → should increment, not reset
        state = update_for_plan(state, "My plan v2", plan_path=absolute_path)
        self.assertEqual(state["round_count"], 2)


if __name__ == "__main__":
    unittest.main()
