"""Tests for Codex Stop hook adapter."""

import json
import os
import sys
import unittest
from io import StringIO
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


PLAN_TEXT = """\
# Implementation Plan

## Summary
Add cross-agent plan evaluation.

1. Refactor the evaluator provider.
2. Add Claude provider support.
3. Add Codex host adapter.
"""


class TestCodexStopHook(unittest.TestCase):
    def setUp(self):
        self._saved = {}
        for k in list(os.environ):
            if k.startswith("PLANMAN_") or k == "_PLANMAN_EVALUATOR":
                self._saved[k] = os.environ.pop(k)
        os.environ["PLANMAN_ENABLED"] = "true"

    def tearDown(self):
        for k in list(os.environ):
            if k.startswith("PLANMAN_") or k == "_PLANMAN_EVALUATOR":
                del os.environ[k]
        os.environ.update(self._saved)

    def _run_hook(self, hook_input):
        import codex_stop_hook

        stdout_capture = StringIO()
        with patch("sys.stdin", StringIO(json.dumps(hook_input))), \
             patch("sys.stdout", stdout_capture), \
             self.assertRaises(SystemExit) as ctx:
            codex_stop_hook.main()
        return stdout_capture.getvalue(), ctx.exception.code

    def test_extract_plan_text_from_payload(self):
        import codex_stop_hook

        text = codex_stop_hook._extract_plan_text({
            "hookEventName": "Stop",
            "assistant": {"message": PLAN_TEXT},
        })
        self.assertEqual(text, PLAN_TEXT.strip())

    def test_extract_plan_text_prefers_latest_candidate(self):
        import codex_stop_hook

        older_plan = PLAN_TEXT + "\n" + ("Extra detail.\n" * 20)
        latest_plan = """Here is the revised plan:

# Plan

1. Keep the Codex hook contract.
2. Use authenticated Claude print mode.
3. Verify package parity.
"""
        text = codex_stop_hook._extract_plan_text({
            "messages": [
                {"content": older_plan},
                {"content": latest_plan},
            ],
        })
        self.assertEqual(text, latest_plan.strip())

    def test_extract_plan_text_matches_multiline_heading(self):
        import codex_stop_hook

        text = codex_stop_hook._extract_plan_text({
            "assistant": {"message": "Intro text.\n\n" + PLAN_TEXT},
        })
        self.assertEqual(text, ("Intro text.\n\n" + PLAN_TEXT).strip())

    def test_no_plan_exits_silently(self):
        output, code = self._run_hook({"hookEventName": "Stop", "cwd": os.getcwd(), "message": "done"})
        self.assertEqual(code, 0)
        self.assertEqual(output, "")

    @patch("codex_stop_hook.check_evaluator_installed", return_value=True)
    @patch("codex_stop_hook.run_evaluation")
    def test_plan_found_blocks_with_feedback(self, mock_eval, mock_check):
        mock_eval.return_value = {
            "action": "block",
            "reason": "Fix the plan",
            "system_message": "Planman: 6/10",
        }
        output, code = self._run_hook({
            "hookEventName": "Stop",
            "thread_id": "abc",
            "cwd": os.getcwd(),
            "assistant": {"message": PLAN_TEXT},
        })
        self.assertEqual(code, 0)
        parsed = json.loads(output)
        self.assertEqual(parsed["decision"], "block")
        self.assertIn("Fix the plan", parsed["reason"])
        mock_eval.assert_called_once()
        self.assertEqual(mock_eval.call_args[1]["host"], "codex")

    def test_evaluator_sentinel_exits_silently(self):
        os.environ["_PLANMAN_EVALUATOR"] = "1"
        output, code = self._run_hook({"hookEventName": "Stop", "assistant": {"message": PLAN_TEXT}})
        self.assertEqual(code, 0)
        self.assertEqual(output, "")


if __name__ == "__main__":
    unittest.main()
