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

        result = run_evaluation(PLAN_TEXT, self._session_id, config)
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
        r1 = run_evaluation(PLAN_TEXT, self._session_id, config)
        self.assertEqual(r1["action"], "block")

        # Round 2: approval
        r2 = run_evaluation(PLAN_TEXT, self._session_id, config)
        self.assertEqual(r2["action"], "pass")
        self.assertIn("approved", r2["system_message"].lower())

    @patch("hook_utils.evaluate_plan")
    def test_round_two_rejection(self, mock_eval):
        """Round 2 with low score → block."""
        from hook_utils import run_evaluation
        mock_eval.return_value = (LOW_SCORE_RESULT, None)
        config = _make_config()

        # Round 1
        run_evaluation(PLAN_TEXT, self._session_id, config)
        # Round 2
        r2 = run_evaluation(PLAN_TEXT, self._session_id, config)
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
        run_evaluation(PLAN_TEXT, self._session_id, config)
        # Round 2: over limit
        r2 = run_evaluation(PLAN_TEXT, self._session_id, config)
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

        run_evaluation(PLAN_TEXT, self._session_id, config)
        mock_mark.assert_called_once_with(self._session_id)

    @patch("hook_utils.evaluate_plan")
    def test_contract_pass_fields(self, mock_eval):
        """Pass result has system_message."""
        from hook_utils import run_evaluation
        mock_eval.return_value = (VALID_RESULT, None)
        config = _make_config()

        # Round 1
        run_evaluation(PLAN_TEXT, self._session_id, config)
        # Round 2 → pass
        result = run_evaluation(PLAN_TEXT, self._session_id, config)
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
        result = run_evaluation(PLAN_TEXT, self._session_id, config)
        self.assertEqual(result["action"], "block")
        self.assertIsNotNone(result["reason"])
        self.assertIsNotNone(result["system_message"])


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


if __name__ == "__main__":
    unittest.main()
