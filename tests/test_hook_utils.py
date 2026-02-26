"""Tests for hook_utils.py — run_evaluation() direct tests."""

import json
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from config import Config
from state import clear_state

VALID_RESULT = {
    "score": 8,
    "breakdown": {
        "completeness": 2,
        "correctness": 2,
        "sequencing": 1,
        "risk_awareness": 1,
        "clarity": 2,
    },
    "weaknesses": ["Minor: missing rollback plan"],
    "suggestions": ["Add error handling"],
    "strengths": ["Clear ordering"],
    "is_plan": True,
}

LOW_SCORE_RESULT = {
    "score": 4,
    "breakdown": {
        "completeness": 1,
        "correctness": 1,
        "sequencing": 0,
        "risk_awareness": 1,
        "clarity": 1,
    },
    "weaknesses": ["Steps out of order"],
    "suggestions": ["Reorder steps"],
    "strengths": ["Good references"],
    "is_plan": True,
}

NOT_A_PLAN_RESULT = {
    "score": 1,
    "breakdown": {
        "completeness": 0,
        "correctness": 0,
        "sequencing": 0,
        "risk_awareness": 0,
        "clarity": 1,
    },
    "weaknesses": [],
    "suggestions": [],
    "strengths": [],
    "is_plan": False,
}

PLAN_TEXT = "# My Plan\n1. Do X\n2. Do Y\n3. Do Z"


def _make_config(**overrides):
    defaults = {
        "threshold": 7,
        "max_rounds": 3,
        "model": "",
        "fail_open": True,
        "enabled": True,
        "rubric": "Score it 1-10.",
        "codex_path": "codex",
        "verbose": False,
        "timeout": 90,
        "stress_test": False,
        "stress_test_prompt": "Stress-test default prompt",
    }
    defaults.update(overrides)
    return Config(**defaults)


class TestRunEvaluation(unittest.TestCase):
    """Direct tests for run_evaluation()."""

    def setUp(self):
        self._session_id = f"test-eval-{os.getpid()}-{id(self)}"

    def tearDown(self):
        clear_state(self._session_id)

    @patch("hook_utils.evaluate_plan")
    def test_first_round_rejection_even_high_score(self, mock_eval):
        """Round 1 always blocks, even with score=8."""
        from hook_utils import run_evaluation
        mock_eval.return_value = (VALID_RESULT, None)
        config = _make_config()

        result = run_evaluation(PLAN_TEXT, self._session_id, config, plan_path="/test.md")
        self.assertEqual(result["action"], "block")
        self.assertIn("First-round review", result["reason"])
        self.assertIn("8/10", result["reason"])
        self.assertIn("system_message", result)

    @patch("hook_utils.evaluate_plan")
    def test_round_two_approval(self, mock_eval):
        """Round 2 with high score → pass."""
        from hook_utils import run_evaluation
        mock_eval.return_value = (VALID_RESULT, None)
        config = _make_config()

        # Round 1: mandatory rejection
        r1 = run_evaluation(PLAN_TEXT, self._session_id, config, plan_path="/test.md")
        self.assertEqual(r1["action"], "block")

        # Round 2: approval
        r2 = run_evaluation(PLAN_TEXT, self._session_id, config, plan_path="/test.md")
        self.assertEqual(r2["action"], "pass")
        self.assertIn("approved", r2["system_message"].lower())

    @patch("hook_utils.evaluate_plan")
    def test_round_two_rejection(self, mock_eval):
        """Round 2 with low score → block."""
        from hook_utils import run_evaluation
        mock_eval.return_value = (LOW_SCORE_RESULT, None)
        config = _make_config()

        # Round 1
        run_evaluation(PLAN_TEXT, self._session_id, config, plan_path="/test.md")
        # Round 2
        r2 = run_evaluation(PLAN_TEXT, self._session_id, config, plan_path="/test.md")
        self.assertEqual(r2["action"], "block")
        self.assertIn("4/10", r2["reason"])
        self.assertIn("needs 7", r2["reason"])

    @patch("hook_utils.evaluate_plan")
    def test_max_rounds_blocks_and_clears_state(self, mock_eval):
        """Exceeding max rounds → block + reason mentions 'human' + state cleared."""
        from hook_utils import run_evaluation
        mock_eval.return_value = (LOW_SCORE_RESULT, None)
        config = _make_config(max_rounds=1)

        # Round 1: mandatory rejection
        run_evaluation(PLAN_TEXT, self._session_id, config, plan_path="/test.md")
        # Round 2: over limit
        r2 = run_evaluation(PLAN_TEXT, self._session_id, config, plan_path="/test.md")
        self.assertEqual(r2["action"], "block")
        self.assertIn("Max evaluation rounds", r2["reason"])
        self.assertIn("Please review", r2["reason"])

    def test_empty_plan_text_returns_skip(self):
        from hook_utils import run_evaluation
        config = _make_config()

        for text in ["", "   ", "\n\t  ", None]:
            result = run_evaluation(text, self._session_id, config)
            self.assertEqual(result["action"], "skip", f"Failed for: {text!r}")

    @patch("hook_utils.evaluate_plan")
    def test_codex_error_fail_open(self, mock_eval):
        from hook_utils import run_evaluation
        mock_eval.return_value = (None, "codex timed out")
        config = _make_config(fail_open=True)

        result = run_evaluation(PLAN_TEXT, self._session_id, config)
        self.assertEqual(result["action"], "pass")
        self.assertIn("fail-open", result["system_message"].lower())

    @patch("hook_utils.evaluate_plan")
    def test_codex_error_fail_closed(self, mock_eval):
        from hook_utils import run_evaluation
        mock_eval.return_value = (None, "codex timed out")
        config = _make_config(fail_open=False)

        result = run_evaluation(PLAN_TEXT, self._session_id, config)
        self.assertEqual(result["action"], "block")
        self.assertIn("PLANMAN_FAIL_OPEN", result["reason"])

    @patch("hook_utils.evaluate_plan")
    def test_plan_path_resets_round(self, mock_eval):
        """Different plan_path → round 1 (reset)."""
        from hook_utils import run_evaluation
        mock_eval.return_value = (VALID_RESULT, None)
        config = _make_config()

        # Round 1 with path A
        r1 = run_evaluation(PLAN_TEXT, self._session_id, config, plan_path="/a.md")
        self.assertEqual(r1["action"], "block")
        self.assertIn("First-round", r1["reason"])

        # Round 2 with path A → should pass (round 2)
        r2 = run_evaluation(PLAN_TEXT, self._session_id, config, plan_path="/a.md")
        self.assertEqual(r2["action"], "pass")

        # New path B → resets to round 1
        r3 = run_evaluation(PLAN_TEXT, self._session_id, config, plan_path="/b.md")
        self.assertEqual(r3["action"], "block")
        self.assertIn("First-round", r3["reason"])

    @patch("hook_utils.evaluate_plan")
    def test_not_a_plan_passes_through(self, mock_eval):
        """is_plan=false → pass, no state modification."""
        from hook_utils import run_evaluation
        mock_eval.return_value = (NOT_A_PLAN_RESULT, None)
        config = _make_config()

        result = run_evaluation("Some code output", self._session_id, config)
        self.assertEqual(result["action"], "pass")
        self.assertIsNone(result["reason"])
        self.assertIsNone(result["system_message"])

    @patch("hook_utils.evaluate_plan")
    def test_not_a_plan_skips_first_round_block(self, mock_eval):
        """is_plan=false on round 1 → still pass (not blocked)."""
        from hook_utils import run_evaluation
        mock_eval.return_value = (NOT_A_PLAN_RESULT, None)
        config = _make_config()

        result = run_evaluation("Some explanation text", self._session_id, config)
        self.assertEqual(result["action"], "pass")

    @patch("hook_utils.mark_recent_evaluation")
    @patch("hook_utils.evaluate_plan")
    def test_not_a_plan_does_not_mark_evaluation(self, mock_eval, mock_mark):
        """is_plan=false → mark_recent_evaluation NOT called."""
        from hook_utils import run_evaluation
        mock_eval.return_value = (NOT_A_PLAN_RESULT, None)
        config = _make_config()

        run_evaluation("Some code output", self._session_id, config)
        mock_mark.assert_not_called()

    @patch("hook_utils.mark_recent_evaluation")
    @patch("hook_utils.evaluate_plan")
    def test_confirmed_plan_marks_evaluation(self, mock_eval, mock_mark):
        """is_plan=true → mark_recent_evaluation IS called."""
        from hook_utils import run_evaluation
        mock_eval.return_value = (VALID_RESULT, None)
        config = _make_config()

        run_evaluation(PLAN_TEXT, self._session_id, config, plan_path="/test.md")
        mock_mark.assert_called_once_with(self._session_id)

    @patch("hook_utils.evaluate_plan")
    def test_contract_pass_fields(self, mock_eval):
        """Pass result has system_message."""
        from hook_utils import run_evaluation
        mock_eval.return_value = (VALID_RESULT, None)
        config = _make_config()

        # Round 1
        run_evaluation(PLAN_TEXT, self._session_id, config, plan_path="/test.md")
        # Round 2 → pass
        result = run_evaluation(PLAN_TEXT, self._session_id, config, plan_path="/test.md")
        self.assertEqual(result["action"], "pass")
        self.assertIsNotNone(result["system_message"])
        self.assertIn("reason", result)

    @patch("hook_utils.evaluate_plan")
    def test_contract_block_fields(self, mock_eval):
        """Block result has reason + system_message."""
        from hook_utils import run_evaluation
        mock_eval.return_value = (LOW_SCORE_RESULT, None)
        config = _make_config()

        # Round 1 → block
        result = run_evaluation(PLAN_TEXT, self._session_id, config, plan_path="/test.md")
        self.assertEqual(result["action"], "block")
        self.assertIsNotNone(result["reason"])
        self.assertIsNotNone(result["system_message"])


class TestNonPlanCaching(unittest.TestCase):
    """Tests for non-plan hash caching."""

    def setUp(self):
        self._session_id = f"test-nonplan-{os.getpid()}-{id(self)}"

    def tearDown(self):
        clear_state(self._session_id)

    @patch("hook_utils.evaluate_plan")
    def test_nonplan_hash_cached(self, mock_eval):
        """Second call with same non-plan text skips LLM entirely."""
        from hook_utils import run_evaluation
        mock_eval.return_value = (NOT_A_PLAN_RESULT, None)
        config = _make_config()

        non_plan_text = "Done! The file has been updated successfully."

        # First call: LLM classifies as non-plan
        r1 = run_evaluation(non_plan_text, self._session_id, config)
        self.assertEqual(r1["action"], "pass")
        self.assertEqual(mock_eval.call_count, 1)

        # Second call: same text, cached, LLM NOT called
        r2 = run_evaluation(non_plan_text, self._session_id, config)
        self.assertEqual(r2["action"], "pass")
        self.assertEqual(mock_eval.call_count, 1)  # still 1, cache hit

    @patch("hook_utils.evaluate_plan")
    def test_nonplan_cache_cleared_on_plan_eval(self, mock_eval):
        """Non-plan cache is invalidated when a real plan arrives and is approved."""
        from hook_utils import run_evaluation
        config = _make_config()

        non_plan_text = "Done! The file has been updated successfully."

        # First: classify as non-plan
        mock_eval.return_value = (NOT_A_PLAN_RESULT, None)
        run_evaluation(non_plan_text, self._session_id, config)
        self.assertEqual(mock_eval.call_count, 1)

        # Second: real plan arrives via plan-mode (sets plan_file_path, clears nonplan cache)
        mock_eval.return_value = (VALID_RESULT, None)
        run_evaluation(PLAN_TEXT, self._session_id, config, plan_path="/a.md")
        self.assertEqual(mock_eval.call_count, 2)

        # Simulate plan approval: clear_state removes plan_file_path
        clear_state(self._session_id)

        # Third: same non-plan text again — cache was cleared by plan eval, LLM called
        mock_eval.return_value = (NOT_A_PLAN_RESULT, None)
        r3 = run_evaluation(non_plan_text, self._session_id, config)
        self.assertEqual(r3["action"], "pass")
        self.assertEqual(mock_eval.call_count, 3)

    @patch("hook_utils.evaluate_plan")
    def test_nonplan_cache_only_applies_to_stop_hook(self, mock_eval):
        """Non-plan cache is only checked when plan_path is absent (stop-hook path)."""
        from hook_utils import run_evaluation
        from state import load_state, save_state, compute_plan_hash
        config = _make_config()

        # Pre-seed state with a non-plan hash matching PLAN_TEXT
        state = load_state(self._session_id)
        state["last_nonplan_hash"] = compute_plan_hash(PLAN_TEXT)
        save_state(state)

        # plan_path present, cache should NOT be checked, LLM should be called
        mock_eval.return_value = (VALID_RESULT, None)
        r = run_evaluation(PLAN_TEXT, self._session_id, config, plan_path="/a.md")
        self.assertEqual(r["action"], "block")  # first-round mandatory rejection
        mock_eval.assert_called_once()


class TestPlanModeGuardInRunEvaluation(unittest.TestCase):
    """Tests for plan-mode guard short-circuit in run_evaluation."""

    def setUp(self):
        self._session_id = f"test-guard-{os.getpid()}-{id(self)}"

    def tearDown(self):
        clear_state(self._session_id)

    @patch("hook_utils.evaluate_plan")
    def test_guard_skips_llm_entirely(self, mock_eval):
        """When plan-mode is active, stop-hook run_evaluation skips LLM call."""
        from hook_utils import run_evaluation
        from state import load_state, save_state, update_for_plan
        config = _make_config()

        # Simulate plan-mode round 1 (sets plan_file_path)
        state = load_state(self._session_id)
        state = update_for_plan(state, PLAN_TEXT, plan_path="/a.md")
        save_state(state)

        # Stop-hook path (no plan_path): guard should short-circuit
        r = run_evaluation(PLAN_TEXT, self._session_id, config)
        self.assertEqual(r["action"], "pass")
        mock_eval.assert_not_called()

    @patch("hook_utils.evaluate_plan")
    def test_guard_does_not_contaminate_state(self, mock_eval):
        """Stop-hook during active plan-mode must NOT add last_nonplan_hash to state."""
        from hook_utils import run_evaluation
        from state import load_state, save_state, update_for_plan
        config = _make_config()

        # Simulate plan-mode round 1
        state = load_state(self._session_id)
        state = update_for_plan(state, PLAN_TEXT, plan_path="/a.md")
        save_state(state)

        # Stop-hook fires with non-plan text
        mock_eval.return_value = (NOT_A_PLAN_RESULT, None)
        run_evaluation("Done! File updated.", self._session_id, config)

        # State must NOT have last_nonplan_hash
        state_after = load_state(self._session_id)
        self.assertNotIn("last_nonplan_hash", state_after)
        self.assertEqual(state_after["round_count"], 1)  # unchanged

    @patch("hook_utils.evaluate_plan")
    def test_guard_releases_after_stale(self, mock_eval):
        """After 30 min of inactivity, guard releases and stop-hook proceeds."""
        from hook_utils import run_evaluation
        from state import load_state, save_state, update_for_plan, _STALE_TTL
        import time as time_mod
        config = _make_config()

        # Simulate plan-mode from 31 minutes ago
        state = load_state(self._session_id)
        state = update_for_plan(state, PLAN_TEXT, plan_path="/a.md")
        state["last_eval_time"] = time_mod.time() - _STALE_TTL - 1
        save_state(state)

        # Stop-hook should now proceed (session stale)
        mock_eval.return_value = (VALID_RESULT, None)
        r = run_evaluation(PLAN_TEXT, self._session_id, config)
        # LLM IS called — guard released
        mock_eval.assert_called_once()

    @patch("hook_utils.evaluate_plan")
    def test_nonplan_cache_different_texts(self, mock_eval):
        """Cache stores only the most recent non-plan hash — different text causes re-evaluation."""
        from hook_utils import run_evaluation
        config = _make_config()
        mock_eval.return_value = (NOT_A_PLAN_RESULT, None)

        text_a = "Done! Updated config file."
        text_b = "Finished! All tests pass."

        # Text A: classified as non-plan, cache = hash(A)
        run_evaluation(text_a, self._session_id, config)
        self.assertEqual(mock_eval.call_count, 1)

        # Text A again: cache hit, LLM NOT called
        run_evaluation(text_a, self._session_id, config)
        self.assertEqual(mock_eval.call_count, 1)

        # Text B: different hash, LLM called, cache = hash(B)
        run_evaluation(text_b, self._session_id, config)
        self.assertEqual(mock_eval.call_count, 2)

        # Text A again: cache has hash(B), miss, LLM called, cache = hash(A)
        run_evaluation(text_a, self._session_id, config)
        self.assertEqual(mock_eval.call_count, 3)


class TestStressTestEvaluation(unittest.TestCase):
    """Tests for stress-test mode in run_evaluation()."""

    def setUp(self):
        self._session_id = f"test-stress-{os.getpid()}-{id(self)}"

    def tearDown(self):
        clear_state(self._session_id)

    @patch("hook_utils.evaluate_plan")
    def test_stress_test_skips_codex_round_one(self, mock_eval):
        """stress_test=true, round 1 → evaluate_plan NOT called."""
        from hook_utils import run_evaluation
        config = _make_config(stress_test=True)

        result = run_evaluation(PLAN_TEXT, self._session_id, config, plan_path="/test.md")
        self.assertEqual(result["action"], "block")
        mock_eval.assert_not_called()

    @patch("hook_utils.evaluate_plan")
    def test_stress_test_uses_custom_prompt(self, mock_eval):
        """Rejection reason matches config.stress_test_prompt."""
        from hook_utils import run_evaluation
        config = _make_config(stress_test=True, stress_test_prompt="Make it better!")

        result = run_evaluation(PLAN_TEXT, self._session_id, config, plan_path="/test.md")
        self.assertEqual(result["reason"], "Make it better!")
        self.assertIn("Stress-test mode", result["system_message"])

    @patch("hook_utils.evaluate_plan")
    def test_stress_test_round_two_uses_codex(self, mock_eval):
        """stress_test=true, round 2 → Codex is called normally."""
        from hook_utils import run_evaluation
        mock_eval.return_value = (VALID_RESULT, None)
        config = _make_config(stress_test=True)

        # Round 1: stress-test skip
        r1 = run_evaluation(PLAN_TEXT, self._session_id, config, plan_path="/test.md")
        self.assertEqual(r1["action"], "block")
        mock_eval.assert_not_called()

        # Round 2: Codex evaluates
        r2 = run_evaluation(PLAN_TEXT, self._session_id, config, plan_path="/test.md")
        mock_eval.assert_called_once()

    @patch("hook_utils.evaluate_plan")
    def test_stress_test_custom_prompt_in_rejection(self, mock_eval):
        """Override prompt appears in the rejection."""
        from hook_utils import run_evaluation
        custom = "Deep dive: find all edge cases and fix them."
        config = _make_config(stress_test=True, stress_test_prompt=custom)

        result = run_evaluation(PLAN_TEXT, self._session_id, config, plan_path="/test.md")
        self.assertEqual(result["reason"], custom)

    @patch("hook_utils.mark_recent_evaluation")
    @patch("hook_utils.evaluate_plan")
    def test_stress_test_marks_recent_evaluation(self, mock_eval, mock_mark):
        """Stress-test round 1 still marks recent evaluation (double-eval prevention)."""
        from hook_utils import run_evaluation
        config = _make_config(stress_test=True)

        run_evaluation(PLAN_TEXT, self._session_id, config, plan_path="/test.md")
        mock_mark.assert_called_once_with(self._session_id)

    @patch("hook_utils.evaluate_plan")
    def test_no_stress_test_uses_codex_round_one(self, mock_eval):
        """Regression: stress_test=false still calls Codex on round 1."""
        from hook_utils import run_evaluation
        mock_eval.return_value = (VALID_RESULT, None)
        config = _make_config(stress_test=False)

        run_evaluation(PLAN_TEXT, self._session_id, config, plan_path="/test.md")
        mock_eval.assert_called_once()


class TestFormatFeedback(unittest.TestCase):
    """Test format_feedback first_round parameter."""

    def test_first_round_header(self):
        from hook_utils import format_feedback
        text = format_feedback(VALID_RESULT, 7, 1, 3, first_round=True)
        self.assertIn("First-round review", text)
        self.assertIn("8/10", text)
        self.assertIn("Revise your plan and resubmit", text)

    def test_normal_round_header(self):
        from hook_utils import format_feedback
        text = format_feedback(LOW_SCORE_RESULT, 7, 2, 3, first_round=False)
        self.assertIn("needs 7", text)
        self.assertIn("4/10", text)
        self.assertIn("Revise your plan addressing these issues", text)
        self.assertNotIn("First-round", text)

    def test_missing_breakdown_keys(self):
        """format_feedback with incomplete breakdown must not crash."""
        from hook_utils import format_feedback
        partial = {"score": 5, "breakdown": {"completeness": 2}}
        text = format_feedback(partial, 7, 1, 3)
        self.assertIn("5/10", text)
        self.assertIn("?/2", text)  # missing keys show as ?

    def test_missing_score(self):
        """format_feedback with missing score uses '?'."""
        from hook_utils import format_feedback
        text = format_feedback({"breakdown": {}}, 7, 1, 3)
        self.assertIn("?/10", text)

    def test_null_lists(self):
        """format_feedback with None lists must not crash."""
        from hook_utils import format_feedback
        data = {**VALID_RESULT, "strengths": None, "weaknesses": None, "suggestions": None}
        text = format_feedback(data, 7, 1, 3)
        self.assertIn("8/10", text)
        self.assertNotIn("**Issues:**", text)

    def test_format_approval_missing_score(self):
        """format_approval with missing score uses '?'."""
        from hook_utils import format_approval
        text = format_approval({"strengths": ["Good"]})
        self.assertIn("?/10", text)

    def test_format_approval_null_strengths(self):
        """format_approval with None strengths must not crash."""
        from hook_utils import format_approval
        text = format_approval({"score": 8, "strengths": None})
        self.assertIn("8/10", text)


class TestSessionAwareRecentEval(unittest.TestCase):
    """Tests for session-aware was_recently_evaluated()."""

    def setUp(self):
        from hook_utils import _RECENT_EVAL_PATH
        self._marker_path = _RECENT_EVAL_PATH
        try:
            os.unlink(self._marker_path)
        except OSError:
            pass

    def tearDown(self):
        try:
            os.unlink(self._marker_path)
        except OSError:
            pass

    def test_same_session_returns_true(self):
        from hook_utils import mark_recent_evaluation, was_recently_evaluated
        mark_recent_evaluation("session-A")
        self.assertTrue(was_recently_evaluated("session-A"))

    def test_different_session_returns_false(self):
        from hook_utils import mark_recent_evaluation, was_recently_evaluated
        mark_recent_evaluation("session-A")
        self.assertFalse(was_recently_evaluated("session-B"))

    def test_no_session_id_returns_true_for_any(self):
        """Backwards compat: no session_id → returns True if any recent eval."""
        from hook_utils import mark_recent_evaluation, was_recently_evaluated
        mark_recent_evaluation("session-A")
        self.assertTrue(was_recently_evaluated())

    def test_no_marker_returns_false(self):
        from hook_utils import was_recently_evaluated
        self.assertFalse(was_recently_evaluated("session-A"))


class TestRunStopHookEvaluation(unittest.TestCase):
    """Dedicated tests for _run_stop_hook_evaluation (classify-only path)."""

    def setUp(self):
        self._session_id = f"test-stophook-eval-{os.getpid()}-{id(self)}"

    def tearDown(self):
        clear_state(self._session_id)

    @patch("hook_utils.evaluate_plan")
    def test_nonplan_caches_hash(self, mock_eval):
        """Non-plan classification saves last_nonplan_hash to state."""
        from hook_utils import _run_stop_hook_evaluation
        from state import load_state
        mock_eval.return_value = (NOT_A_PLAN_RESULT, None)
        config = _make_config()
        state = load_state(self._session_id)

        result = _run_stop_hook_evaluation("Hello world", state, self._session_id, config, None)
        self.assertEqual(result["action"], "pass")

        # Verify state was saved with hash
        reloaded = load_state(self._session_id)
        self.assertIn("last_nonplan_hash", reloaded)

    @patch("hook_utils.evaluate_plan")
    def test_plan_above_threshold_passes(self, mock_eval):
        """Plan with score >= threshold → pass with approval message."""
        from hook_utils import _run_stop_hook_evaluation
        from state import load_state
        mock_eval.return_value = (VALID_RESULT, None)  # score=8, threshold=7
        config = _make_config()
        state = load_state(self._session_id)

        result = _run_stop_hook_evaluation(PLAN_TEXT, state, self._session_id, config, None)
        self.assertEqual(result["action"], "pass")
        self.assertIn("Plan approved", result["system_message"])

    @patch("hook_utils.evaluate_plan")
    def test_plan_below_threshold_blocks(self, mock_eval):
        """Plan with score < threshold → block with feedback."""
        from hook_utils import _run_stop_hook_evaluation
        from state import load_state
        mock_eval.return_value = (LOW_SCORE_RESULT, None)  # score=4, threshold=7
        config = _make_config()
        state = load_state(self._session_id)

        result = _run_stop_hook_evaluation(PLAN_TEXT, state, self._session_id, config, None)
        self.assertEqual(result["action"], "block")
        self.assertIn("4/10", result["reason"])
        self.assertIn("Inline plan rejected", result["system_message"])

    @patch("hook_utils.evaluate_plan")
    def test_error_fail_open(self, mock_eval):
        """Evaluation error + fail_open → pass with warning."""
        from hook_utils import _run_stop_hook_evaluation
        from state import load_state
        mock_eval.return_value = (None, "timeout")
        config = _make_config(fail_open=True)
        state = load_state(self._session_id)

        result = _run_stop_hook_evaluation(PLAN_TEXT, state, self._session_id, config, None)
        self.assertEqual(result["action"], "pass")
        self.assertIn("fail-open", result["system_message"].lower())

    @patch("hook_utils.evaluate_plan")
    def test_error_fail_closed(self, mock_eval):
        """Evaluation error + fail_open=False → block."""
        from hook_utils import _run_stop_hook_evaluation
        from state import load_state
        mock_eval.return_value = (None, "timeout")
        config = _make_config(fail_open=False)
        state = load_state(self._session_id)

        result = _run_stop_hook_evaluation(PLAN_TEXT, state, self._session_id, config, None)
        self.assertEqual(result["action"], "block")
        self.assertIn("PLANMAN_FAIL_OPEN", result["reason"])

    @patch("hook_utils.evaluate_plan")
    def test_plan_clears_nonplan_hash(self, mock_eval):
        """When classified as plan, last_nonplan_hash is removed from state."""
        from hook_utils import _run_stop_hook_evaluation
        from state import load_state, save_state, compute_plan_hash
        config = _make_config()

        # Pre-seed nonplan hash
        state = load_state(self._session_id)
        state["last_nonplan_hash"] = compute_plan_hash("old text")
        save_state(state)

        mock_eval.return_value = (VALID_RESULT, None)
        state = load_state(self._session_id)
        result = _run_stop_hook_evaluation(PLAN_TEXT, state, self._session_id, config, None)
        self.assertEqual(result["action"], "pass")
        # state object should have last_nonplan_hash removed
        self.assertNotIn("last_nonplan_hash", state)

    @patch("hook_utils.evaluate_plan")
    def test_no_round_tracking(self, mock_eval):
        """Stop-hook path does NOT modify round_count in state."""
        from hook_utils import _run_stop_hook_evaluation
        from state import load_state
        mock_eval.return_value = (LOW_SCORE_RESULT, None)
        config = _make_config()
        state = load_state(self._session_id)
        original_round = state.get("round_count", 0)

        _run_stop_hook_evaluation(PLAN_TEXT, state, self._session_id, config, None)
        self.assertEqual(state.get("round_count", 0), original_round)

    @patch("hook_utils.evaluate_plan")
    def test_no_stress_test(self, mock_eval):
        """Stop-hook path ignores stress_test config — always calls LLM."""
        from hook_utils import _run_stop_hook_evaluation
        from state import load_state
        mock_eval.return_value = (VALID_RESULT, None)
        config = _make_config(stress_test=True)
        state = load_state(self._session_id)

        _run_stop_hook_evaluation(PLAN_TEXT, state, self._session_id, config, None)
        mock_eval.assert_called_once()  # LLM called despite stress_test=True

    @patch("hook_utils.evaluate_plan")
    def test_missing_is_plan_defaults_to_true(self, mock_eval):
        """Result without is_plan field defaults to True (plan)."""
        from hook_utils import _run_stop_hook_evaluation
        from state import load_state
        result_no_flag = {
            "score": 9,
            "breakdown": VALID_RESULT["breakdown"],
            "strengths": ["Good"],
        }
        mock_eval.return_value = (result_no_flag, None)
        config = _make_config()
        state = load_state(self._session_id)

        r = _run_stop_hook_evaluation(PLAN_TEXT, state, self._session_id, config, None)
        self.assertEqual(r["action"], "pass")
        self.assertIn("Plan approved", r["system_message"])


if __name__ == "__main__":
    unittest.main()
