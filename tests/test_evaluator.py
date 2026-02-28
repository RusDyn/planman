"""Tests for evaluator.py — mocked subprocess for codex exec."""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from evaluator import (
    build_prompt,
    check_codex_installed,
    evaluate_plan,
    parse_codex_output,
    reset_codex_cache,
)
from config import Config


def _make_config(**overrides):
    defaults = {
        "threshold": 7,
        "max_rounds": 3,
        "model": "",
        "fail_open": True,
        "enabled": True,
        "rubric": "Score it 1-10.",
        "verbose": False,
        "stress_test": False,
        "context": "",
        "source_verify": True,
    }
    defaults.update(overrides)
    return Config(**defaults)


VALID_RESULT = {
    "score": 8,
    "breakdown": {
        "completeness": 2,
        "correctness": 2,
        "sequencing": 1,
        "risk_awareness": 1,
        "clarity": 2,
    },
    "weaknesses": ["Missing rollback plan"],
    "suggestions": ["Add error handling for step 3"],
    "strengths": ["Clear step ordering", "Good file references"],
}


class TestBuildPrompt(unittest.TestCase):
    def test_basic_prompt(self):
        prompt = build_prompt("My plan", "Score it.", round_number=1)
        self.assertIn("My plan", prompt)
        self.assertIn("Score it.", prompt)
        self.assertIn("Round 1", prompt)
        self.assertNotIn("Previous Feedback", prompt)

    def test_prompt_with_previous_feedback(self):
        prompt = build_prompt("My plan", "Score it.", "Fix X", round_number=2)
        self.assertIn("Previous Feedback (Round 1)", prompt)
        self.assertIn("Fix X", prompt)
        self.assertIn("Round 2", prompt)
        self.assertIn("Which feedback items were addressed", prompt)

    def test_prompt_with_context(self):
        prompt = build_prompt("My plan", "Score it.", context="Python CLI tool, no web framework")
        self.assertIn("## Project Context", prompt)
        self.assertIn("Python CLI tool, no web framework", prompt)
        # Context should appear before the rubric
        ctx_pos = prompt.index("Project Context")
        rubric_pos = prompt.index("Score it.")
        self.assertLess(ctx_pos, rubric_pos)

    def test_prompt_without_context(self):
        prompt = build_prompt("My plan", "Score it.")
        self.assertNotIn("Project Context", prompt)

    def test_prompt_empty_context_excluded(self):
        prompt = build_prompt("My plan", "Score it.", context="")
        self.assertNotIn("Project Context", prompt)

    def test_source_verify_enabled_by_default(self):
        prompt = build_prompt("My plan", "Score it.", round_number=1)
        self.assertIn("## Source Verification", prompt)
        self.assertIn("read-only access to the project filesystem", prompt)
        self.assertIn("cat <path>", prompt)

    def test_source_verify_disabled(self):
        prompt = build_prompt("My plan", "Score it.", round_number=1, source_verify=False)
        self.assertNotIn("Source Verification", prompt)

    def test_source_verify_before_feedback(self):
        """Source verification should appear before previous feedback."""
        prompt = build_prompt("My plan", "Score it.", "Fix X", round_number=2, source_verify=True)
        verify_pos = prompt.index("Source Verification")
        feedback_pos = prompt.index("Previous Feedback")
        self.assertLess(verify_pos, feedback_pos)


class TestParseCodexOutput(unittest.TestCase):
    def test_valid_output(self):
        stdout = json.dumps(VALID_RESULT)
        result, error = parse_codex_output(stdout)
        self.assertIsNone(error)
        self.assertEqual(result["score"], 8)

    def test_empty_output(self):
        result, error = parse_codex_output("")
        self.assertIsNone(result)
        self.assertIn("empty", error)

    def test_invalid_json(self):
        result, error = parse_codex_output("{not json}")
        self.assertIsNone(result)
        self.assertIn("malformed output", error)

    def test_not_object(self):
        result, error = parse_codex_output("[1,2,3]")
        self.assertIsNone(result)
        self.assertIn("not a JSON object", error)

    def test_missing_fields(self):
        result, error = parse_codex_output('{"score": 5}')
        self.assertIsNone(result)
        self.assertIn("missing fields", error)

    def test_score_out_of_range(self):
        data = dict(VALID_RESULT, score=11)
        result, error = parse_codex_output(json.dumps(data))
        self.assertIsNone(result)
        self.assertIn("invalid score", error)

    def test_score_zero(self):
        data = dict(VALID_RESULT, score=0)
        result, error = parse_codex_output(json.dumps(data))
        self.assertIsNone(result)
        self.assertIn("invalid score", error)

    def test_breakdown_value_out_of_range(self):
        data = dict(VALID_RESULT)
        data["breakdown"] = dict(VALID_RESULT["breakdown"], completeness=3)
        result, error = parse_codex_output(json.dumps(data))
        self.assertIsNone(result)
        self.assertIn("breakdown.completeness", error)

    def test_score_mismatch_rejected(self):
        """Score must equal sum of breakdown values."""
        data = dict(VALID_RESULT, score=9)  # sum is 8
        result, error = parse_codex_output(json.dumps(data))
        self.assertIsNone(result)
        self.assertIn("score mismatch", error)

    def test_score_matches_breakdown_accepted(self):
        """Score matching breakdown sum passes."""
        result, error = parse_codex_output(json.dumps(VALID_RESULT))
        self.assertIsNone(error)
        self.assertEqual(result["score"], 8)

    def test_empty_strengths_rejected(self):
        """No strengths → rejected."""
        data = dict(VALID_RESULT, strengths=[])
        result, error = parse_codex_output(json.dumps(data))
        self.assertIsNone(result)
        self.assertIn("no strengths listed", error)

    def test_empty_weaknesses_with_low_score_rejected(self):
        """Score < 10 with no weaknesses → rejected."""
        data = dict(VALID_RESULT, weaknesses=[])
        result, error = parse_codex_output(json.dumps(data))
        self.assertIsNone(result)
        self.assertIn("no weaknesses listed", error)

    def test_empty_weaknesses_with_perfect_score_accepted(self):
        """Score = 10 with no weaknesses → accepted."""
        data = {
            "score": 10,
            "breakdown": {
                "completeness": 2, "correctness": 2, "sequencing": 2,
                "risk_awareness": 2, "clarity": 2,
            },
            "weaknesses": [],
            "suggestions": [],
            "strengths": ["Perfect plan"],
        }
        result, error = parse_codex_output(json.dumps(data))
        self.assertIsNone(error)
        self.assertEqual(result["score"], 10)


class TestEvaluatePlan(unittest.TestCase):
    def setUp(self):
        reset_codex_cache()

    def tearDown(self):
        reset_codex_cache()

    @patch("evaluator.subprocess.run")
    @patch("evaluator.check_codex_installed", return_value=True)
    def test_successful_evaluation(self, mock_check, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(VALID_RESULT),
            stderr="",
        )
        config = _make_config()
        result, error = evaluate_plan("My plan", config)
        self.assertIsNone(error)
        self.assertEqual(result["score"], 8)

    @patch("evaluator.subprocess.run")
    @patch("evaluator.check_codex_installed", return_value=True)
    def test_stdin_mode_used(self, mock_check, mock_run):
        """Prompt is passed via stdin, not as CLI arg."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(VALID_RESULT),
            stderr="",
        )
        config = _make_config()
        evaluate_plan("My plan", config)
        cmd = mock_run.call_args[0][0]
        # Should use 'exec -' (stdin mode), not 'exec <prompt>'
        self.assertIn("-", cmd)
        self.assertNotIn("My plan", cmd)
        # Prompt passed via input kwarg
        call_kwargs = mock_run.call_args[1]
        self.assertIn("My plan", call_kwargs.get("input", ""))

    @patch("evaluator.subprocess.run")
    @patch("evaluator.check_codex_installed", return_value=True)
    def test_ephemeral_flag_passed(self, mock_check, mock_run):
        """--ephemeral flag prevents session file accumulation."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(VALID_RESULT),
            stderr="",
        )
        config = _make_config()
        evaluate_plan("My plan", config)
        cmd = mock_run.call_args[0][0]
        self.assertIn("--ephemeral", cmd)

    @patch("evaluator.subprocess.run")
    @patch("evaluator.check_codex_installed", return_value=True)
    def test_model_flag_passed(self, mock_check, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(VALID_RESULT),
            stderr="",
        )
        config = _make_config(model="gpt-4o")
        evaluate_plan("My plan", config)
        cmd = mock_run.call_args[0][0]
        self.assertIn("-m", cmd)
        self.assertIn("gpt-4o", cmd)

    @patch("evaluator.subprocess.run")
    @patch("evaluator.check_codex_installed", return_value=True)
    def test_no_model_flag_when_empty(self, mock_check, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(VALID_RESULT),
            stderr="",
        )
        config = _make_config(model="")
        evaluate_plan("My plan", config)
        cmd = mock_run.call_args[0][0]
        self.assertNotIn("-m", cmd)

    @patch("evaluator.check_codex_installed", return_value=False)
    def test_codex_not_installed(self, mock_check):
        config = _make_config()
        result, error = evaluate_plan("My plan", config)
        self.assertIsNone(result)
        self.assertIn("not found", error)
        self.assertIn("npm install", error)

    @patch("evaluator.subprocess.run")
    @patch("evaluator.check_codex_installed", return_value=True)
    def test_timeout(self, mock_check, mock_run):
        import subprocess as sp
        mock_run.side_effect = sp.TimeoutExpired(cmd="codex", timeout=90)
        config = _make_config()
        result, error = evaluate_plan("My plan", config)
        self.assertIsNone(result)
        self.assertIn("timed out", error)
        self.assertIn("Try a shorter plan", error)

    @patch("evaluator.subprocess.run")
    @patch("evaluator.check_codex_installed", return_value=True)
    def test_nonzero_exit(self, mock_check, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="auth failed",
        )
        config = _make_config()
        result, error = evaluate_plan("My plan", config)
        self.assertIsNone(result)
        self.assertIn("exit 1", error)

    @patch("evaluator.subprocess.run")
    @patch("evaluator.check_codex_installed", return_value=True)
    def test_malformed_output(self, mock_check, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="thinking about your plan...",
            stderr="",
        )
        config = _make_config()
        result, error = evaluate_plan("My plan", config)
        self.assertIsNone(result)
        self.assertIn("malformed output", error)


class TestPromptLengthLimit(unittest.TestCase):
    def setUp(self):
        reset_codex_cache()

    def tearDown(self):
        reset_codex_cache()

    @patch("evaluator.check_codex_installed", return_value=True)
    def test_oversized_prompt_returns_error(self, mock_check):
        """Prompt > 2MB → error without calling subprocess."""
        config = _make_config()
        huge_plan = "x" * 2_100_000
        result, error = evaluate_plan(huge_plan, config)
        self.assertIsNone(result)
        self.assertIn("too large", error)
        self.assertIn("2MB", error)

    @patch("evaluator.subprocess.run")
    @patch("evaluator.check_codex_installed", return_value=True)
    def test_large_prompt_uses_hook_budget(self, mock_check, mock_run):
        """Prompt > 500KB but < 2MB → proceeds with scaled timeout."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(VALID_RESULT),
            stderr="",
        )
        config = _make_config()
        large_plan = "x" * 600_000
        result, error = evaluate_plan(large_plan, config)
        self.assertIsNone(error)
        # Verify timeout is capped at hook budget (600s hook − 30s margin)
        call_kwargs = mock_run.call_args[1]
        self.assertEqual(call_kwargs["timeout"], 570)

    @patch("evaluator.subprocess.run")
    @patch("evaluator.check_codex_installed", return_value=True)
    def test_normal_prompt_proceeds(self, mock_check, mock_run):
        """Prompt under 500KB → proceeds to subprocess."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(VALID_RESULT),
            stderr="",
        )
        config = _make_config()
        result, error = evaluate_plan("Normal plan", config)
        self.assertIsNone(error)
        mock_run.assert_called_once()


class TestCheckCodexInstalled(unittest.TestCase):
    def setUp(self):
        reset_codex_cache()

    def tearDown(self):
        reset_codex_cache()

    @patch("evaluator.shutil.which", return_value="/usr/local/bin/codex")
    def test_found(self, mock_which):
        self.assertTrue(check_codex_installed())

    @patch("evaluator.shutil.which", return_value=None)
    def test_not_found(self, mock_which):
        self.assertFalse(check_codex_installed())

    @patch("evaluator.shutil.which", return_value="/usr/local/bin/codex")
    def test_caching(self, mock_which):
        check_codex_installed()
        check_codex_installed()
        mock_which.assert_called_once()


class TestPluginRoot(unittest.TestCase):
    def test_empty_plugin_root_uses_file_based_fallback(self):
        """When CLAUDE_PLUGIN_ROOT is empty string, should use file-based fallback."""
        import evaluator
        saved = os.environ.get("CLAUDE_PLUGIN_ROOT")
        try:
            os.environ["CLAUDE_PLUGIN_ROOT"] = ""
            # Re-evaluate PLUGIN_ROOT logic
            plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "") or os.path.dirname(
                os.path.dirname(os.path.abspath(evaluator.__file__))
            )
            self.assertNotEqual(plugin_root, "")
            self.assertTrue(os.path.isabs(plugin_root))
        finally:
            if saved is not None:
                os.environ["CLAUDE_PLUGIN_ROOT"] = saved
            elif "CLAUDE_PLUGIN_ROOT" in os.environ:
                del os.environ["CLAUDE_PLUGIN_ROOT"]


class TestMissingSchema(unittest.TestCase):
    def setUp(self):
        reset_codex_cache()

    def tearDown(self):
        reset_codex_cache()

    @patch("evaluator.PLUGIN_ROOT", "/nonexistent/path")
    @patch("evaluator.check_codex_installed", return_value=True)
    def test_missing_schema_returns_error(self, mock_check):
        config = _make_config()
        result, error = evaluate_plan("My plan", config)
        self.assertIsNone(result)
        self.assertIn("schema file not found", error)
        self.assertIn("CLAUDE_PLUGIN_ROOT", error)


if __name__ == "__main__":
    unittest.main()
