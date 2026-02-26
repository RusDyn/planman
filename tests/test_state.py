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
    _compute_plan_fingerprint,
    _is_stale,
    _state_path,
    _STALE_TTL,
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
        """First inline evaluation (no stored fingerprint) → round 1."""
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

    def test_no_plan_path_fingerprint_change_resets(self):
        """No path, different title → round 1."""
        state = {
            "session_id": "test",
            "round_count": 3,
            "plan_hash": compute_plan_hash("Old plan"),
            "plan_fingerprint": _compute_plan_fingerprint("# Old Plan\n1. Do X\n2. Do Y"),
            "last_eval_time": __import__("time").time(),
        }
        state = update_for_plan(state, "# New Plan\n1. Do A\n2. Do B")
        self.assertEqual(state["round_count"], 1)

    def test_no_plan_path_same_title_different_body_resets(self):
        """Same title but prefix hash differs (>500 char prefix change) → round 1."""
        plan_a = "# My Plan\n" + "A" * 500
        plan_b = "# My Plan\n" + "B" * 500
        state = {
            "session_id": "test",
            "round_count": 3,
            "plan_hash": compute_plan_hash(plan_a),
            "plan_fingerprint": _compute_plan_fingerprint(plan_a),
            "last_eval_time": __import__("time").time(),
        }
        state = update_for_plan(state, plan_b)
        self.assertEqual(state["round_count"], 1)

    def test_no_plan_path_same_fingerprint_increments(self):
        """No path, same title + prefix → increment."""
        # Plan must be >500 chars so appending beyond prefix doesn't change fingerprint
        plan = "# My Plan\n" + ("1. Step with enough detail to fill space. " * 15)
        self.assertGreater(len(plan), 500)
        state = {
            "session_id": "test",
            "round_count": 2,
            "plan_hash": compute_plan_hash(plan),
            "plan_fingerprint": _compute_plan_fingerprint(plan),
            "last_eval_time": __import__("time").time(),
        }
        # Append beyond 500-char prefix — fingerprint unchanged
        revised = plan + "\n\nExtra details appended well past prefix boundary"
        self.assertEqual(
            _compute_plan_fingerprint(plan),
            _compute_plan_fingerprint(revised),
        )
        state = update_for_plan(state, revised)
        self.assertEqual(state["round_count"], 3)

    def test_no_plan_path_first_eval_resets(self):
        """No path, no stored fingerprint → round 1 (first inline eval)."""
        state = {
            "session_id": "test",
            "round_count": 0,
            "plan_hash": None,
        }
        state = update_for_plan(state, "# Plan\n1. Step one")
        self.assertEqual(state["round_count"], 1)
        self.assertIn("plan_fingerprint", state)

    def test_plan_path_to_inline_transition_resets(self):
        """Prior state has plan_file_path but no plan_fingerprint, new eval has no plan_path → round 1."""
        state = {
            "session_id": "test",
            "round_count": 3,
            "plan_hash": compute_plan_hash("old"),
            "plan_file_path": "/a.md",
            # no plan_fingerprint
        }
        state = update_for_plan(state, "# Inline Plan\n1. Step")
        self.assertEqual(state["round_count"], 1)

    def test_stale_session_resets(self):
        """last_eval_time > 30 min ago → round 1."""
        plan = "# My Plan\n1. Do X"
        state = {
            "session_id": "test",
            "round_count": 3,
            "plan_hash": compute_plan_hash(plan),
            "plan_fingerprint": _compute_plan_fingerprint(plan),
            "last_eval_time": __import__("time").time() - _STALE_TTL - 1,
        }
        state = update_for_plan(state, plan)
        self.assertEqual(state["round_count"], 1)

    def test_fresh_session_increments(self):
        """last_eval_time recent → increment."""
        plan = "# My Plan\n1. Do X"
        state = {
            "session_id": "test",
            "round_count": 2,
            "plan_hash": compute_plan_hash(plan),
            "plan_fingerprint": _compute_plan_fingerprint(plan),
            "last_eval_time": __import__("time").time(),
        }
        state = update_for_plan(state, plan)
        self.assertEqual(state["round_count"], 3)

    def test_different_plan_same_fingerprint_increments(self):
        """No plan_path, same title+prefix (hash changes but fingerprint doesn't) → increment."""
        # Plan must be >500 chars so differences beyond prefix don't affect fingerprint
        plan_a = "# My Plan\n" + ("1. Step with enough detail to fill space. " * 15)
        self.assertGreater(len(plan_a), 500)
        plan_b = plan_a + "\n\nExtra details appended well past prefix boundary"
        fp_a = _compute_plan_fingerprint(plan_a)
        fp_b = _compute_plan_fingerprint(plan_b)
        self.assertEqual(fp_a, fp_b)  # fingerprints should match

        state = {
            "session_id": "test",
            "round_count": 3,
            "plan_hash": compute_plan_hash(plan_a),
            "plan_fingerprint": fp_a,
            "last_eval_time": __import__("time").time(),
        }
        state = update_for_plan(state, plan_b)
        self.assertEqual(state["round_count"], 4)


class TestRecordFeedback(unittest.TestCase):
    def test_records_score_and_feedback(self):
        state = {"session_id": "test"}
        state = record_feedback(state, 5, "Needs work")
        self.assertEqual(state["last_score"], 5)
        self.assertEqual(state["last_feedback"], "Needs work")


class TestComputePlanFingerprint(unittest.TestCase):
    def test_heading(self):
        fp = _compute_plan_fingerprint("# My Plan\n1. Step one")
        self.assertTrue(fp.startswith("# My Plan|"))

    def test_no_heading(self):
        fp = _compute_plan_fingerprint("This is my plan\n1. Step one")
        self.assertTrue(fp.startswith("This is my plan|"))

    def test_empty(self):
        fp = _compute_plan_fingerprint("")
        self.assertTrue(fp.startswith("|"))
        self.assertEqual(len(fp.split("|")[1]), 8)  # 8-char hex hash

    def test_whitespace_only(self):
        fp = _compute_plan_fingerprint("   \n\t  ")
        self.assertTrue(fp.startswith("|"))


class TestIsStale(unittest.TestCase):
    def test_no_last_eval_time(self):
        self.assertFalse(_is_stale({}))

    def test_recent(self):
        import time
        self.assertFalse(_is_stale({"last_eval_time": time.time()}))

    def test_stale(self):
        import time
        self.assertTrue(_is_stale({"last_eval_time": time.time() - _STALE_TTL - 1}))


class TestStatePath(unittest.TestCase):
    def test_sanitizes_session_id(self):
        path = _state_path("a/b/../c")
        self.assertIn("planman-abc", path)
        self.assertNotIn("/", os.path.basename(path).replace("planman-", "").replace(".json", ""))


if __name__ == "__main__":
    unittest.main()
