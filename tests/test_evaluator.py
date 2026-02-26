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
        "codex_path": "codex",
        "verbose": False,
        "timeout": 90,
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
    "is_plan": True,
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


class TestIsPlanField(unittest.TestCase):
    def test_valid_output_with_is_plan(self):
        stdout = json.dumps(VALID_RESULT)
        result, error = parse_codex_output(stdout)
        self.assertIsNone(error)
        self.assertTrue(result["is_plan"])

    def test_missing_is_plan(self):
        data = dict(VALID_RESULT)
        del data["is_plan"]
        result, error = parse_codex_output(json.dumps(data))
        self.assertIsNone(result)
        self.assertIn("missing", error.lower())

    def test_is_plan_false(self):
        data = dict(VALID_RESULT, is_plan=False)
        result, error = parse_codex_output(json.dumps(data))
        self.assertIsNone(error)
        self.assertFalse(result["is_plan"])

    def test_is_plan_non_boolean(self):
        data = dict(VALID_RESULT, is_plan="yes")
        result, error = parse_codex_output(json.dumps(data))
        self.assertIsNone(result)
        self.assertIn("is_plan", error)


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
        self.assertIn("PLANMAN_TIMEOUT", error)

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
