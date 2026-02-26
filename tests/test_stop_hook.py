"""Integration tests for stop_hook.py — full hook with mocked codex."""

import json
import os
import sys
import tempfile
import unittest
from io import StringIO
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from state import clear_state, _state_path

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
    "weaknesses": ["Steps out of order", "No error handling"],
    "suggestions": ["Reorder steps", "Add error handling"],
    "strengths": ["Good file references"],
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


def _make_transcript(plan_text):
    """Create a temp JSONL transcript with a plan as the last assistant message."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    entries = [
        {"role": "user", "content": "Plan this feature"},
        {"role": "assistant", "content": plan_text},
    ]
    for entry in entries:
        f.write(json.dumps(entry) + "\n")
    f.close()
    return f.name


PLAN_TEXT = """\
## Implementation Plan

Here's my plan to add authentication:

1. Create the auth middleware in `src/middleware/auth.ts`
2. Add JWT token validation in `src/utils/jwt.ts`
3. Implement session management
4. Update the API routes in `src/routes/api.ts`
5. Add error handling

### Step 1: Auth Middleware
- Create the middleware file
- Implement token extraction
- Add role-based access control
"""


def _clear_recent_eval_marker():
    """Remove the recent-evaluation marker file to prevent cross-test interference."""
    marker_path = os.path.join("/tmp", "planman-recent-eval.json")
    try:
        os.unlink(marker_path)
    except OSError:
        pass


class TestStopHookIntegration(unittest.TestCase):
    """Integration tests that exercise the full stop_hook flow."""

    def setUp(self):
        self._session_id = f"test-hook-{os.getpid()}-{id(self)}"
        self._env_saved = {}
        for k in list(os.environ):
            if k.startswith("PLANMAN_"):
                self._env_saved[k] = os.environ.pop(k)
        os.environ["PLANMAN_ENABLED"] = "true"
        os.environ["PLANMAN_VERBOSE"] = "false"
        _clear_recent_eval_marker()
        # Reset evaluator cache
        import evaluator
        evaluator.reset_codex_cache()

    def tearDown(self):
        clear_state(self._session_id)
        for k in list(os.environ):
            if k.startswith("PLANMAN_"):
                del os.environ[k]
        os.environ.update(self._env_saved)

    def _run_hook(self, hook_input):
        """Run stop_hook.main() with mocked stdin/stdout."""
        import stop_hook

        stdin_data = json.dumps(hook_input)
        stdout_capture = StringIO()

        with patch("sys.stdin", StringIO(stdin_data)), \
             patch("sys.stdout", stdout_capture), \
             self.assertRaises(SystemExit) as ctx:
            stop_hook.main()

        output = stdout_capture.getvalue()
        exit_code = ctx.exception.code
        return output, exit_code

    @patch("stop_hook.check_codex_installed", return_value=False)
    def test_codex_not_installed_passes_through(self, mock_check):
        transcript = _make_transcript(PLAN_TEXT)
        try:
            output, code = self._run_hook({
                "session_id": self._session_id,
                "transcript_path": transcript,
            })
            self.assertEqual(code, 0)
            self.assertEqual(output, "")  # pass through
        finally:
            os.unlink(transcript)

    def test_disabled_passes_through(self):
        os.environ["PLANMAN_ENABLED"] = "false"
        transcript = _make_transcript(PLAN_TEXT)
        try:
            output, code = self._run_hook({
                "session_id": self._session_id,
                "transcript_path": transcript,
            })
            self.assertEqual(code, 0)
            self.assertEqual(output, "")
        finally:
            os.unlink(transcript)

    @patch("hook_utils.evaluate_plan")
    @patch("stop_hook.check_codex_installed", return_value=True)
    def test_non_plan_passes_through(self, mock_check, mock_eval):
        """LLM returns is_plan=false → pass through."""
        mock_eval.return_value = (NOT_A_PLAN_RESULT, None)
        output, code = self._run_hook({
            "session_id": self._session_id,
            "last_assistant_message": "Done! The file has been updated successfully.",
        })
        self.assertEqual(code, 0)
        self.assertEqual(output, "")  # pass through, no systemMessage

    @patch("stop_hook.was_recently_evaluated", return_value=False)
    @patch("hook_utils.evaluate_plan")
    @patch("stop_hook.check_codex_installed", return_value=True)
    def test_plan_passes_threshold(self, mock_check, mock_eval, mock_recent):
        """Plan needs 2 rounds: round 1 = mandatory rejection, round 2 = approval."""
        mock_eval.return_value = (VALID_RESULT, None)
        transcript = _make_transcript(PLAN_TEXT)
        try:
            # Round 1: mandatory first-round rejection
            output1, code1 = self._run_hook({
                "session_id": self._session_id,
                "transcript_path": transcript,
            })
            self.assertEqual(code1, 0)
            parsed1 = json.loads(output1)
            self.assertEqual(parsed1["decision"], "block")
            self.assertIn("First-round review", parsed1["reason"])

            # Round 2: approval
            output2, code2 = self._run_hook({
                "session_id": self._session_id,
                "transcript_path": transcript,
            })
            self.assertEqual(code2, 0)
            parsed2 = json.loads(output2)
            self.assertIn("systemMessage", parsed2)
            self.assertIn("approved", parsed2["systemMessage"].lower())
        finally:
            os.unlink(transcript)

    @patch("stop_hook.was_recently_evaluated", return_value=False)
    @patch("hook_utils.evaluate_plan")
    @patch("stop_hook.check_codex_installed", return_value=True)
    def test_plan_rejected_below_threshold(self, mock_check, mock_eval, mock_recent):
        mock_eval.return_value = (LOW_SCORE_RESULT, None)
        transcript = _make_transcript(PLAN_TEXT)
        try:
            # Round 1: first-round rejection
            self._run_hook({
                "session_id": self._session_id,
                "transcript_path": transcript,
            })
            # Round 2: rejected on score
            output, code = self._run_hook({
                "session_id": self._session_id,
                "transcript_path": transcript,
            })
            self.assertEqual(code, 0)
            parsed = json.loads(output)
            self.assertEqual(parsed["decision"], "block")
            self.assertIn("4/10", parsed["reason"])
            self.assertIn("needs 7", parsed["reason"])
        finally:
            os.unlink(transcript)

    @patch("stop_hook.was_recently_evaluated", return_value=False)
    @patch("hook_utils.evaluate_plan")
    @patch("stop_hook.check_codex_installed", return_value=True)
    def test_max_rounds_exceeded(self, mock_check, mock_eval, mock_recent):
        os.environ["PLANMAN_MAX_ROUNDS"] = "1"
        mock_eval.return_value = (LOW_SCORE_RESULT, None)
        transcript = _make_transcript(PLAN_TEXT)
        try:
            # Round 1: first-round rejection (mandatory)
            self._run_hook({
                "session_id": self._session_id,
                "transcript_path": transcript,
            })
            # Round 2: over limit → block for human decision
            output, code = self._run_hook({
                "session_id": self._session_id,
                "transcript_path": transcript,
            })
            self.assertEqual(code, 0)
            parsed = json.loads(output)
            self.assertEqual(parsed["decision"], "block")
            self.assertIn("Max evaluation rounds", parsed["reason"])
        finally:
            os.unlink(transcript)

    @patch("hook_utils.evaluate_plan")
    @patch("stop_hook.check_codex_installed", return_value=True)
    def test_codex_error_fail_open(self, mock_check, mock_eval):
        mock_eval.return_value = (None, "codex exec timed out after 90s")
        os.environ["PLANMAN_FAIL_OPEN"] = "true"
        transcript = _make_transcript(PLAN_TEXT)
        try:
            output, code = self._run_hook({
                "session_id": self._session_id,
                "transcript_path": transcript,
            })
            self.assertEqual(code, 0)
            parsed = json.loads(output)
            self.assertIn("systemMessage", parsed)
            self.assertIn("fail-open", parsed["systemMessage"].lower())
        finally:
            os.unlink(transcript)

    @patch("hook_utils.evaluate_plan")
    @patch("stop_hook.check_codex_installed", return_value=True)
    def test_codex_error_fail_closed(self, mock_check, mock_eval):
        mock_eval.return_value = (None, "codex exec timed out after 90s")
        os.environ["PLANMAN_FAIL_OPEN"] = "false"
        transcript = _make_transcript(PLAN_TEXT)
        try:
            output, code = self._run_hook({
                "session_id": self._session_id,
                "transcript_path": transcript,
            })
            self.assertEqual(code, 0)
            parsed = json.loads(output)
            self.assertEqual(parsed["decision"], "block")
            self.assertIn("PLANMAN_FAIL_OPEN", parsed["reason"])
        finally:
            os.unlink(transcript)

    def test_no_transcript_passes_through(self):
        output, code = self._run_hook({
            "session_id": self._session_id,
        })
        self.assertEqual(code, 0)
        self.assertEqual(output, "")

    def test_empty_stdin_passes_through(self):
        import stop_hook
        stdout_capture = StringIO()
        with patch("sys.stdin", StringIO("")), \
             patch("sys.stdout", stdout_capture), \
             self.assertRaises(SystemExit) as ctx:
            stop_hook.main()
        self.assertEqual(ctx.exception.code, 0)


class TestOutputExits(unittest.TestCase):
    """Test that _output() always calls sys.exit."""

    def test_output_block_calls_sys_exit(self):
        from stop_hook import _output
        with self.assertRaises(SystemExit) as ctx:
            _output("block", reason="test")
        self.assertEqual(ctx.exception.code, 0)

    def test_output_allow_with_message_calls_sys_exit(self):
        from stop_hook import _output
        with self.assertRaises(SystemExit):
            _output("allow", system_message="test")


class TestStopHookActive(unittest.TestCase):
    """Test that stop_hook_active=true bypasses evaluation."""

    def setUp(self):
        self._env_saved = {}
        for k in list(os.environ):
            if k.startswith("PLANMAN_"):
                self._env_saved[k] = os.environ.pop(k)
        os.environ["PLANMAN_ENABLED"] = "true"
        import evaluator
        evaluator.reset_codex_cache()

    def tearDown(self):
        for k in list(os.environ):
            if k.startswith("PLANMAN_"):
                del os.environ[k]
        os.environ.update(self._env_saved)

    def test_stop_hook_active_passes_through(self):
        """Hook input with stop_hook_active=true should exit 0 with no output."""
        import stop_hook
        stdin_data = json.dumps({"stop_hook_active": True})
        stdout_capture = StringIO()
        with patch("sys.stdin", StringIO(stdin_data)), \
             patch("sys.stdout", stdout_capture), \
             self.assertRaises(SystemExit) as ctx:
            stop_hook.main()
        self.assertEqual(ctx.exception.code, 0)
        self.assertEqual(stdout_capture.getvalue(), "")


class TestLastAssistantMessage(unittest.TestCase):
    """Test that last_assistant_message is used as primary source."""

    def setUp(self):
        self._session_id = f"test-lam-{os.getpid()}-{id(self)}"
        self._env_saved = {}
        for k in list(os.environ):
            if k.startswith("PLANMAN_"):
                self._env_saved[k] = os.environ.pop(k)
        os.environ["PLANMAN_ENABLED"] = "true"
        os.environ["PLANMAN_VERBOSE"] = "false"
        _clear_recent_eval_marker()
        import evaluator
        evaluator.reset_codex_cache()

    def tearDown(self):
        clear_state(self._session_id)
        for k in list(os.environ):
            if k.startswith("PLANMAN_"):
                del os.environ[k]
        os.environ.update(self._env_saved)

    def _run_hook(self, hook_input):
        import stop_hook
        stdin_data = json.dumps(hook_input)
        stdout_capture = StringIO()
        with patch("sys.stdin", StringIO(stdin_data)), \
             patch("sys.stdout", stdout_capture), \
             self.assertRaises(SystemExit) as ctx:
            stop_hook.main()
        output = stdout_capture.getvalue()
        exit_code = ctx.exception.code
        return output, exit_code

    @patch("stop_hook.was_recently_evaluated", return_value=False)
    @patch("hook_utils.evaluate_plan")
    @patch("stop_hook.check_codex_installed", return_value=True)
    def test_uses_last_assistant_message_when_present(self, mock_check, mock_eval, mock_recent):
        """When last_assistant_message is provided, it should be used directly."""
        mock_eval.return_value = (VALID_RESULT, None)
        # Round 1: mandatory rejection
        output1, code1 = self._run_hook({
            "session_id": self._session_id,
            "last_assistant_message": PLAN_TEXT,
        })
        self.assertEqual(code1, 0)
        parsed1 = json.loads(output1)
        self.assertEqual(parsed1["decision"], "block")

        # Round 2: approval
        output2, code2 = self._run_hook({
            "session_id": self._session_id,
            "last_assistant_message": PLAN_TEXT,
        })
        self.assertEqual(code2, 0)
        parsed2 = json.loads(output2)
        self.assertIn("systemMessage", parsed2)
        self.assertIn("approved", parsed2["systemMessage"].lower())

    @patch("stop_hook.was_recently_evaluated", return_value=False)
    @patch("hook_utils.evaluate_plan")
    @patch("stop_hook.check_codex_installed", return_value=True)
    def test_falls_back_to_transcript_when_no_last_assistant_message(self, mock_check, mock_eval, mock_recent):
        """When last_assistant_message is absent, should fall back to transcript_path."""
        mock_eval.return_value = (VALID_RESULT, None)
        transcript = _make_transcript(PLAN_TEXT)
        try:
            # Round 1: mandatory rejection
            self._run_hook({
                "session_id": self._session_id,
                "transcript_path": transcript,
            })
            # Round 2: approval
            output, code = self._run_hook({
                "session_id": self._session_id,
                "transcript_path": transcript,
            })
            self.assertEqual(code, 0)
            parsed = json.loads(output)
            self.assertIn("systemMessage", parsed)
            self.assertIn("approved", parsed["systemMessage"].lower())
        finally:
            os.unlink(transcript)

    @patch("stop_hook.was_recently_evaluated", return_value=False)
    @patch("hook_utils.evaluate_plan")
    @patch("stop_hook.check_codex_installed", return_value=True)
    def test_last_assistant_message_takes_priority_over_transcript(self, mock_check, mock_eval, mock_recent):
        """last_assistant_message should be used even when transcript_path is also present."""
        mock_eval.return_value = (VALID_RESULT, None)
        # Transcript has different text (non-plan)
        transcript = _make_transcript("Done! The file has been updated.")
        try:
            # Round 1
            self._run_hook({
                "session_id": self._session_id,
                "last_assistant_message": PLAN_TEXT,
                "transcript_path": transcript,
            })
            # Round 2: approval
            output, code = self._run_hook({
                "session_id": self._session_id,
                "last_assistant_message": PLAN_TEXT,
                "transcript_path": transcript,
            })
            self.assertEqual(code, 0)
            parsed = json.loads(output)
            self.assertIn("systemMessage", parsed)
            self.assertIn("approved", parsed["systemMessage"].lower())
        finally:
            os.unlink(transcript)

    @patch("stop_hook.check_codex_installed", return_value=True)
    def test_no_last_assistant_message_and_no_transcript_passes_through(self, mock_check):
        """With neither last_assistant_message nor transcript_path, should pass through."""
        output, code = self._run_hook({
            "session_id": self._session_id,
        })
        self.assertEqual(code, 0)
        self.assertEqual(output, "")

    @patch("hook_utils.evaluate_plan")
    @patch("stop_hook.check_codex_installed", return_value=True)
    def test_empty_last_assistant_message_falls_back_to_transcript(self, mock_check, mock_eval):
        """Empty string last_assistant_message should trigger transcript fallback."""
        mock_eval.return_value = (NOT_A_PLAN_RESULT, None)
        transcript = _make_transcript("Done! Simple response.")
        try:
            output, code = self._run_hook({
                "session_id": self._session_id,
                "last_assistant_message": "",
                "transcript_path": transcript,
            })
            self.assertEqual(code, 0)
            # LLM classifies as non-plan → pass through
            self.assertEqual(output, "")
        finally:
            os.unlink(transcript)


class TestConcisePlan(unittest.TestCase):
    """Test that even concise plans reach LLM evaluation."""

    def setUp(self):
        self._session_id = f"test-concise-{os.getpid()}-{id(self)}"
        self._env_saved = {}
        for k in list(os.environ):
            if k.startswith("PLANMAN_"):
                self._env_saved[k] = os.environ.pop(k)
        os.environ["PLANMAN_ENABLED"] = "true"
        os.environ["PLANMAN_VERBOSE"] = "false"
        _clear_recent_eval_marker()
        import evaluator
        evaluator.reset_codex_cache()

    def tearDown(self):
        clear_state(self._session_id)
        for k in list(os.environ):
            if k.startswith("PLANMAN_"):
                del os.environ[k]
        os.environ.update(self._env_saved)

    def _run_hook(self, hook_input):
        import stop_hook
        stdin_data = json.dumps(hook_input)
        stdout_capture = StringIO()
        with patch("sys.stdin", StringIO(stdin_data)), \
             patch("sys.stdout", stdout_capture), \
             self.assertRaises(SystemExit) as ctx:
            stop_hook.main()
        return stdout_capture.getvalue(), ctx.exception.code

    @patch("hook_utils.evaluate_plan")
    @patch("stop_hook.check_codex_installed", return_value=True)
    def test_concise_plan_reaches_evaluation(self, mock_check, mock_eval):
        """Short but valid plan (~60 chars) reaches LLM, gets first-round rejection."""
        concise_plan = "# Fix\n1. Edit X\n2. Test"
        mock_eval.return_value = (VALID_RESULT, None)
        output, code = self._run_hook({
            "session_id": self._session_id,
            "last_assistant_message": concise_plan,
        })
        self.assertEqual(code, 0)
        mock_eval.assert_called_once()
        parsed = json.loads(output)
        self.assertEqual(parsed["decision"], "block")
        self.assertIn("First-round review", parsed["reason"])


class TestContractActions(unittest.TestCase):
    """Test that stop_hook correctly maps run_evaluation() actions to hook output."""

    def setUp(self):
        self._session_id = f"test-contract-{os.getpid()}-{id(self)}"
        self._env_saved = {}
        for k in list(os.environ):
            if k.startswith("PLANMAN_"):
                self._env_saved[k] = os.environ.pop(k)
        os.environ["PLANMAN_ENABLED"] = "true"
        os.environ["PLANMAN_VERBOSE"] = "false"
        _clear_recent_eval_marker()
        import evaluator
        evaluator.reset_codex_cache()

    def tearDown(self):
        clear_state(self._session_id)
        for k in list(os.environ):
            if k.startswith("PLANMAN_"):
                del os.environ[k]
        os.environ.update(self._env_saved)

    def _run_hook(self, hook_input):
        import stop_hook
        stdin_data = json.dumps(hook_input)
        stdout_capture = StringIO()
        with patch("sys.stdin", StringIO(stdin_data)), \
             patch("sys.stdout", stdout_capture), \
             self.assertRaises(SystemExit) as ctx:
            stop_hook.main()
        return stdout_capture.getvalue(), ctx.exception.code

    @patch("stop_hook.run_evaluation")
    @patch("stop_hook.check_codex_installed", return_value=True)
    def test_contract_skip(self, mock_check, mock_run_eval):
        """run_evaluation returns skip → exit 0 with no output."""
        mock_run_eval.return_value = {"action": "skip", "reason": None, "system_message": None}
        output, code = self._run_hook({
            "session_id": self._session_id,
            "last_assistant_message": "some text",
        })
        self.assertEqual(code, 0)
        self.assertEqual(output, "")

    @patch("stop_hook.run_evaluation")
    @patch("stop_hook.check_codex_installed", return_value=True)
    def test_contract_pass(self, mock_check, mock_run_eval):
        """run_evaluation returns pass → allow + system_message."""
        mock_run_eval.return_value = {
            "action": "pass",
            "reason": None,
            "system_message": "Planman: Plan approved.",
        }
        output, code = self._run_hook({
            "session_id": self._session_id,
            "last_assistant_message": "some text",
        })
        self.assertEqual(code, 0)
        parsed = json.loads(output)
        self.assertNotIn("decision", parsed)
        self.assertIn("systemMessage", parsed)
        self.assertIn("approved", parsed["systemMessage"].lower())

    @patch("stop_hook.run_evaluation")
    @patch("stop_hook.check_codex_installed", return_value=True)
    def test_contract_block(self, mock_check, mock_run_eval):
        """run_evaluation returns block → block + reason."""
        mock_run_eval.return_value = {
            "action": "block",
            "reason": "Plan needs work",
            "system_message": "Planman: Rejected.",
        }
        output, code = self._run_hook({
            "session_id": self._session_id,
            "last_assistant_message": "some text",
        })
        self.assertEqual(code, 0)
        parsed = json.loads(output)
        self.assertEqual(parsed["decision"], "block")
        self.assertEqual(parsed["reason"], "Plan needs work")
        self.assertIn("systemMessage", parsed)

    @patch("stop_hook.check_codex_installed", return_value=True)
    def test_empty_text_skips(self, mock_check):
        """Empty/whitespace text → run_evaluation returns skip → exit 0."""
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
        entry = {"role": "assistant", "content": "   \n\t  \n  "}
        f.write(json.dumps(entry) + "\n")
        f.close()
        try:
            output, code = self._run_hook({
                "session_id": self._session_id,
                "transcript_path": f.name,
            })
            self.assertEqual(code, 0)
            self.assertEqual(output, "")
        finally:
            os.unlink(f.name)


class TestVerboseSystemMessage(unittest.TestCase):
    """Test that verbose mode emits systemMessage on pass-through."""

    def setUp(self):
        self._session_id = f"test-verbose-{os.getpid()}-{id(self)}"
        self._env_saved = {}
        for k in list(os.environ):
            if k.startswith("PLANMAN_"):
                self._env_saved[k] = os.environ.pop(k)
        os.environ["PLANMAN_ENABLED"] = "true"
        os.environ["PLANMAN_VERBOSE"] = "true"
        _clear_recent_eval_marker()
        import evaluator
        evaluator.reset_codex_cache()

    def tearDown(self):
        clear_state(self._session_id)
        for k in list(os.environ):
            if k.startswith("PLANMAN_"):
                del os.environ[k]
        os.environ.update(self._env_saved)

    def _run_hook(self, hook_input):
        import stop_hook
        stdin_data = json.dumps(hook_input)
        stdout_capture = StringIO()
        with patch("sys.stdin", StringIO(stdin_data)), \
             patch("sys.stdout", stdout_capture), \
             self.assertRaises(SystemExit) as ctx:
            stop_hook.main()
        output = stdout_capture.getvalue()
        exit_code = ctx.exception.code
        return output, exit_code

    @patch("hook_utils.evaluate_plan")
    @patch("stop_hook.check_codex_installed", return_value=True)
    def test_verbose_non_plan_no_system_message(self, mock_check, mock_eval):
        """Verbose mode: LLM classifies as non-plan → no systemMessage output."""
        mock_eval.return_value = (NOT_A_PLAN_RESULT, None)
        output, code = self._run_hook({
            "session_id": self._session_id,
            "last_assistant_message": "Done! The file has been updated.",
        })
        self.assertEqual(code, 0)
        self.assertEqual(output, "")

    @patch("stop_hook.check_codex_installed", return_value=False)
    def test_verbose_codex_not_installed_no_system_message(self, mock_check):
        """Verbose mode should NOT emit systemMessage when codex not found (logs to file only)."""
        output, code = self._run_hook({
            "session_id": self._session_id,
            "last_assistant_message": PLAN_TEXT,
        })
        self.assertEqual(code, 0)
        self.assertEqual(output, "")

    def test_verbose_disabled_no_system_message(self):
        """Verbose mode should NOT emit systemMessage when disabled (logs to file only)."""
        os.environ["PLANMAN_ENABLED"] = "false"
        output, code = self._run_hook({
            "session_id": self._session_id,
            "last_assistant_message": PLAN_TEXT,
        })
        self.assertEqual(code, 0)
        self.assertEqual(output, "")

    @patch("hook_utils.evaluate_plan")
    @patch("stop_hook.check_codex_installed", return_value=True)
    def test_non_verbose_non_plan_no_system_message(self, mock_check, mock_eval):
        """Non-verbose mode: LLM classifies as non-plan → no systemMessage."""
        os.environ["PLANMAN_VERBOSE"] = "false"
        mock_eval.return_value = (NOT_A_PLAN_RESULT, None)
        output, code = self._run_hook({
            "session_id": self._session_id,
            "last_assistant_message": "Done! The file has been updated.",
        })
        self.assertEqual(code, 0)
        self.assertEqual(output, "")


if __name__ == "__main__":
    unittest.main()
