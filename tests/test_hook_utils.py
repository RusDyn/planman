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
        "timeout": 180,
        "stress_test": False,
        "stress_test_prompt": "Stress-test default prompt",
        "source_verify": True,
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
    def test_max_rounds_passes_through(self, mock_eval):
        """Exceeding max rounds → pass through + system message informs user."""
        from hook_utils import run_evaluation
        mock_eval.return_value = (LOW_SCORE_RESULT, None)
        config = _make_config(max_rounds=1)

        # Round 1: mandatory rejection
        run_evaluation(PLAN_TEXT, self._session_id, config, plan_path="/test.md")
        # Round 2: over limit → passes through
        r2 = run_evaluation(PLAN_TEXT, self._session_id, config, plan_path="/test.md")
        self.assertEqual(r2["action"], "pass")
        self.assertIn("Max evaluation rounds", r2["system_message"])
        self.assertIn("threshold", r2["system_message"])
        self.assertIsNone(r2["reason"])

        # Round 3 (retry): should still pass through
        r3 = run_evaluation(PLAN_TEXT, self._session_id, config, plan_path="/test.md")
        self.assertEqual(r3["action"], "pass")
        self.assertIn("Max evaluation rounds", r3["system_message"])

    @patch("hook_utils.evaluate_plan")
    def test_round_continues_after_pass(self, mock_eval):
        """After plan passes on round 2, round 3 is NOT reset to 1."""
        from hook_utils import run_evaluation
        mock_eval.return_value = (VALID_RESULT, None)
        config = _make_config()

        r1 = run_evaluation(PLAN_TEXT, self._session_id, config, plan_path="/test.md")
        self.assertEqual(r1["action"], "block")  # Round 1: mandatory

        r2 = run_evaluation(PLAN_TEXT, self._session_id, config, plan_path="/test.md")
        self.assertEqual(r2["action"], "pass")   # Round 2: pass

        r3 = run_evaluation(PLAN_TEXT, self._session_id, config, plan_path="/test.md")
        self.assertEqual(r3["action"], "pass")   # Round 3: still passes (NOT first-round)
        self.assertNotIn("First-round", r3.get("reason") or "")

    @patch("hook_utils.evaluate_plan")
    def test_pass_clears_feedback_not_state(self, mock_eval):
        """Pass preserves round count but nulls last_feedback."""
        from hook_utils import run_evaluation
        mock_eval.return_value = (VALID_RESULT, None)
        config = _make_config()

        run_evaluation(PLAN_TEXT, self._session_id, config, plan_path="/test.md")  # R1
        run_evaluation(PLAN_TEXT, self._session_id, config, plan_path="/test.md")  # R2: pass

        from state import load_state
        state = load_state(self._session_id)
        self.assertEqual(state["round_count"], 2)        # preserved
        self.assertEqual(state["last_score"], 8)          # preserved
        self.assertIsNone(state["last_feedback"])          # nulled (no approval pollution)

    def test_empty_plan_text_returns_skip(self):
        from hook_utils import run_evaluation
        config = _make_config()

        for text in ["", "   ", "\n\t  ", None]:
            result = run_evaluation(text, self._session_id, config)
            self.assertEqual(result["action"], "skip", f"Failed for: {text!r}")
            self.assertIsNotNone(result["system_message"], f"Expected system_message for: {text!r}")
            self.assertIn("empty", result["system_message"].lower())

    @patch("hook_utils.evaluate_plan")
    def test_codex_error_fail_open(self, mock_eval):
        from hook_utils import run_evaluation
        mock_eval.return_value = (None, "codex timed out")
        config = _make_config(fail_open=True)

        result = run_evaluation(PLAN_TEXT, self._session_id, config, plan_path="/test.md")
        self.assertEqual(result["action"], "pass")
        self.assertIn("fail-open", result["system_message"].lower())

    @patch("hook_utils.evaluate_plan")
    def test_codex_error_fail_closed(self, mock_eval):
        from hook_utils import run_evaluation
        mock_eval.return_value = (None, "codex timed out")
        config = _make_config(fail_open=False)

        result = run_evaluation(PLAN_TEXT, self._session_id, config, plan_path="/test.md")
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


class TestPlanFileSizeLimit(unittest.TestCase):
    """Test that oversized plan files are rejected by _find_plan_file."""

    def test_oversized_plan_via_marker_returns_none(self):
        """Plan file > 1MB referenced by marker → returns (None, None, reason)."""
        import tempfile as _tmpmod
        from pre_exit_plan_hook import _find_plan_file
        from hook_utils import safe_session_id, MARKER_TEMPLATE

        session_id = f"test-size-{os.getpid()}"
        safe_id = safe_session_id(session_id)

        # Create an oversized plan file
        with _tmpmod.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# Plan\n" + "x" * 1_100_000)
            oversized_path = f.name

        # Create marker pointing to it
        marker_path = MARKER_TEMPLATE.format(session_id=safe_id)
        with open(marker_path, "w") as f:
            json.dump({"plan_file_path": oversized_path}, f)

        try:
            plan_path, plan_text, skip_reason = _find_plan_file(session_id, None)
            self.assertIsNone(plan_path)
            self.assertIsNone(plan_text)
            self.assertIsNotNone(skip_reason)
            self.assertIn("too large", skip_reason)
        finally:
            os.unlink(oversized_path)
            try:
                os.unlink(marker_path)
            except OSError:
                pass

    def test_normal_size_plan_accepted(self):
        """Plan file under 1MB → returns content."""
        import tempfile as _tmpmod
        from pre_exit_plan_hook import _find_plan_file
        from hook_utils import safe_session_id, MARKER_TEMPLATE

        session_id = f"test-size-ok-{os.getpid()}"
        safe_id = safe_session_id(session_id)

        with _tmpmod.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# Plan\n1. Step one\n2. Step two")
            plan_file = f.name

        marker_path = MARKER_TEMPLATE.format(session_id=safe_id)
        with open(marker_path, "w") as f:
            json.dump({"plan_file_path": plan_file}, f)

        try:
            plan_path, plan_text, skip_reason = _find_plan_file(session_id, None)
            self.assertEqual(plan_path, plan_file)
            self.assertIn("Step one", plan_text)
            self.assertIsNone(skip_reason)
        finally:
            os.unlink(plan_file)
            try:
                os.unlink(marker_path)
            except OSError:
                pass


class TestScoreMismatchRejected(unittest.TestCase):
    """Test that score mismatch is caught by parse_codex_output validation."""

    def test_score_mismatch_returns_error(self):
        """score=7 with breakdown sum=8 → parse error (v0.3.0+)."""
        from evaluator import parse_codex_output
        mismatched_result = {
            "score": 7,
            "breakdown": {
                "completeness": 2,
                "correctness": 2,
                "sequencing": 2,
                "risk_awareness": 1,
                "clarity": 1,
            },
            "weaknesses": ["Minor issue"],
            "suggestions": ["Fix it"],
            "strengths": ["Good plan"],
        }
        result, error = parse_codex_output(json.dumps(mismatched_result))
        self.assertIsNone(result)
        self.assertIn("score mismatch", error)
        self.assertIn("score=7", error)
        self.assertIn("sum=8", error)


class TestSafeSessionId(unittest.TestCase):
    """Test shared safe_session_id from hook_utils."""

    def test_normal_id(self):
        from hook_utils import safe_session_id
        self.assertEqual(safe_session_id("abc-123_def"), "abc-123_def")

    def test_empty_string_returns_default(self):
        from hook_utils import safe_session_id
        self.assertEqual(safe_session_id(""), "default")

    def test_special_chars_returns_default(self):
        from hook_utils import safe_session_id
        self.assertEqual(safe_session_id("///..."), "default")

    def test_strips_dangerous_chars(self):
        from hook_utils import safe_session_id
        self.assertEqual(safe_session_id("a/b/../c"), "abc")

    def test_long_input_truncated(self):
        from hook_utils import safe_session_id
        long_id = "a" * 10_000
        result = safe_session_id(long_id)
        self.assertEqual(len(result), 100)
        self.assertEqual(result, "a" * 100)


class TestLogAlwaysToFile(unittest.TestCase):
    """Test that log() always writes to file, even when verbose=false."""

    def test_log_writes_to_file_when_not_verbose(self):
        from hook_utils import log
        import tempfile as _tmpmod

        tmpdir = _tmpmod.mkdtemp()
        claude_dir = os.path.join(tmpdir, ".claude")
        os.makedirs(claude_dir, exist_ok=True)

        config = _make_config(verbose=False)
        log("test non-verbose log entry", config, cwd=tmpdir)

        log_path = os.path.join(claude_dir, "planman.log")
        self.assertTrue(os.path.exists(log_path))
        with open(log_path) as f:
            content = f.read()
        self.assertIn("test non-verbose log entry", content)

        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
