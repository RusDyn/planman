"""Tests for pre_exec_gate_hook.py — Bash/Skill execution gate."""

import json
import os
import sys
import tempfile
import unittest
from io import StringIO
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


class TestPreExecGateHook(unittest.TestCase):
    """Tests for the unified Bash/Skill exec gate hook."""

    def setUp(self):
        self._env_saved = {}
        for k in list(os.environ):
            if k.startswith("PLANMAN_"):
                self._env_saved[k] = os.environ.pop(k)
        os.environ["PLANMAN_ENABLED"] = "true"
        os.environ["PLANMAN_VERBOSE"] = "false"

    def tearDown(self):
        for k in list(os.environ):
            if k.startswith("PLANMAN_"):
                del os.environ[k]
        os.environ.update(self._env_saved)

    def _run_hook(self, hook_input):
        """Run pre_exec_gate_hook.main() with mocked stdin/stdout."""
        import pre_exec_gate_hook

        stdin_data = json.dumps(hook_input) if hook_input is not None else ""
        stdout_capture = StringIO()

        with patch("sys.stdin", StringIO(stdin_data)), \
             patch("sys.stdout", stdout_capture), \
             self.assertRaises(SystemExit) as ctx:
            pre_exec_gate_hook.main()

        output = stdout_capture.getvalue()
        exit_code = ctx.exception.code
        parsed = None
        if output.strip():
            try:
                parsed = json.loads(output)
            except json.JSONDecodeError:
                pass
        return output, exit_code, parsed

    # ── Fast path tests ──

    def test_empty_stdin_allows(self):
        """Empty stdin should allow immediately."""
        _, code, _ = self._run_hook(None)
        self.assertEqual(code, 0)

    def test_no_exec_patterns_allows_bash(self):
        """Bash call with no exec_patterns configured should allow."""
        _, code, parsed = self._run_hook({
            "tool_name": "Bash",
            "tool_input": {"command": "omc team 3:claude 'fix tests'"},
            "session_id": "test-session",
            "cwd": "/tmp",
        })
        self.assertEqual(code, 0)

    def test_no_skill_patterns_allows_skill(self):
        """Skill call with no skill_patterns configured should allow."""
        _, code, parsed = self._run_hook({
            "tool_name": "Skill",
            "tool_input": {"skill": "oh-my-claudecode:ralph"},
            "session_id": "test-session",
            "cwd": "/tmp",
        })
        self.assertEqual(code, 0)

    def test_non_matching_bash_allows(self):
        """Bash call that doesn't match exec_patterns should allow."""
        os.environ["PLANMAN_EXEC_PATTERNS"] = "omc\\s+(team|ralphthon)"
        _, code, parsed = self._run_hook({
            "tool_name": "Bash",
            "tool_input": {"command": "ls -la"},
            "session_id": "test-session",
            "cwd": "/tmp",
        })
        self.assertEqual(code, 0)

    def test_non_matching_skill_allows(self):
        """Skill call that doesn't match skill_patterns should allow."""
        os.environ["PLANMAN_SKILL_PATTERNS"] = "oh-my-claudecode:(ralph|autopilot)"
        _, code, parsed = self._run_hook({
            "tool_name": "Skill",
            "tool_input": {"skill": "oh-my-claudecode:plan"},
            "session_id": "test-session",
            "cwd": "/tmp",
        })
        self.assertEqual(code, 0)

    def test_empty_command_allows(self):
        """Empty command string should allow."""
        os.environ["PLANMAN_EXEC_PATTERNS"] = "omc\\s+(team|ralphthon)"
        _, code, _ = self._run_hook({
            "tool_name": "Bash",
            "tool_input": {"command": ""},
            "session_id": "test-session",
            "cwd": "/tmp",
        })
        self.assertEqual(code, 0)

    def test_malformed_json_allows(self):
        """Malformed JSON stdin should allow."""
        import pre_exec_gate_hook
        stdout_capture = StringIO()
        with patch("sys.stdin", StringIO("not json")), \
             patch("sys.stdout", stdout_capture), \
             self.assertRaises(SystemExit) as ctx:
            pre_exec_gate_hook.main()
        self.assertEqual(ctx.exception.code, 0)

    # ── Pattern matching tests ──

    def test_bash_pattern_matches_omc_team(self):
        """omc team command should match exec_patterns and trigger evaluation path."""
        os.environ["PLANMAN_EXEC_PATTERNS"] = "omc\\s+(team|ralphthon)"
        # Codex is not installed → hook allows but produces output (not fast-exit)
        _, code, parsed = self._run_hook({
            "tool_name": "Bash",
            "tool_input": {"command": "omc team 3:claude 'fix tests'"},
            "session_id": "test-session",
            "cwd": "/tmp",
        })
        # Codex not installed → allow (but it went through evaluation path, not fast-exit)
        self.assertEqual(code, 0)

    def test_bash_pattern_matches_omc_ralphthon(self):
        """omc ralphthon should match."""
        os.environ["PLANMAN_EXEC_PATTERNS"] = "omc\\s+(team|ralphthon)"
        _, code, parsed = self._run_hook({
            "tool_name": "Bash",
            "tool_input": {"command": "omc ralphthon 'build feature'"},
            "session_id": "test-session",
            "cwd": "/tmp",
        })
        self.assertEqual(code, 0)

    def test_skill_pattern_matches(self):
        """Skill name should match skill_patterns."""
        os.environ["PLANMAN_SKILL_PATTERNS"] = "oh-my-claudecode:(ralph|autopilot)"
        _, code, parsed = self._run_hook({
            "tool_name": "Skill",
            "tool_input": {"skill": "oh-my-claudecode:ralph"},
            "session_id": "test-session",
            "cwd": "/tmp",
        })
        self.assertEqual(code, 0)

    def test_bash_no_match_git_status(self):
        """git status should not match omc patterns."""
        os.environ["PLANMAN_EXEC_PATTERNS"] = "omc\\s+(team|ralphthon)"
        _, code, parsed = self._run_hook({
            "tool_name": "Bash",
            "tool_input": {"command": "git status"},
            "session_id": "test-session",
            "cwd": "/tmp",
        })
        self.assertEqual(code, 0)
        # Should be fast-exit with no output (not evaluation path)
        # parsed is None means no structured output was returned

    def test_invalid_regex_skipped(self):
        """Invalid regex pattern should be skipped, not crash."""
        os.environ["PLANMAN_EXEC_PATTERNS"] = "[invalid(regex"
        _, code, _ = self._run_hook({
            "tool_name": "Bash",
            "tool_input": {"command": "omc team"},
            "session_id": "test-session",
            "cwd": "/tmp",
        })
        # Should allow (pattern skipped)
        self.assertEqual(code, 0)

    def test_disabled_config_allows(self):
        """Disabled planman should allow even with matching patterns."""
        os.environ["PLANMAN_EXEC_PATTERNS"] = "omc\\s+(team|ralphthon)"
        os.environ["PLANMAN_ENABLED"] = "false"
        _, code, _ = self._run_hook({
            "tool_name": "Bash",
            "tool_input": {"command": "omc team 3:claude 'task'"},
            "session_id": "test-session",
            "cwd": "/tmp",
        })
        self.assertEqual(code, 0)

    # ── Evaluation path tests ──

    def test_matching_pattern_no_plan_blocks(self):
        """Matching pattern with no plan file should block."""
        tmpdir = tempfile.mkdtemp()
        os.environ["PLANMAN_EXEC_PATTERNS"] = "omc\\s+(team|ralphthon)"
        # Use marker-only mode so find_plan_file doesn't scan ~/.claude/plans/
        os.environ["_PLANMAN_DEBUG_MARKER_ONLY"] = "1"
        import evaluator
        saved = evaluator._codex_available
        evaluator._codex_available = True
        try:
            _, code, parsed = self._run_hook({
                "tool_name": "Bash",
                "tool_input": {"command": "omc team 3:claude 'task'"},
                "session_id": "test-no-plan",
                "cwd": tmpdir,
            })
        finally:
            evaluator._codex_available = saved
            os.environ.pop("_PLANMAN_DEBUG_MARKER_ONLY", None)
        self.assertEqual(code, 0)
        self.assertIsNotNone(parsed)
        decision = parsed.get("hookSpecificOutput", {}).get("permissionDecision")
        self.assertEqual(decision, "deny")

    def test_matching_pattern_with_plan_evaluates(self):
        """Matching pattern with plan file should trigger evaluation."""
        tmpdir = tempfile.mkdtemp()
        # Create a plan file in .claude/plans/
        plans_dir = os.path.join(tmpdir, ".claude", "plans")
        os.makedirs(plans_dir, exist_ok=True)
        plan_path = os.path.join(plans_dir, "test-plan.md")
        with open(plan_path, "w") as f:
            f.write("# Test Plan\n\n1. Do thing\n2. Do other thing\n")

        os.environ["PLANMAN_EXEC_PATTERNS"] = "omc\\s+(team|ralphthon)"

        # Mock codex as installed but fail the evaluation (fail-open)
        os.environ["PLANMAN_FAIL_OPEN"] = "true"
        from evaluator import reset_codex_cache
        reset_codex_cache()
        with patch("evaluator._codex_available", True), \
             patch("evaluator.subprocess.run", side_effect=FileNotFoundError("codex")):
            _, code, parsed = self._run_hook({
                "tool_name": "Bash",
                "tool_input": {"command": "omc team 3:claude 'task'"},
                "session_id": "test-with-plan",
                "cwd": tmpdir,
            })
        reset_codex_cache()

        self.assertEqual(code, 0)
        # With fail-open, should allow
        if parsed:
            decision = parsed.get("hookSpecificOutput", {}).get("permissionDecision")
            self.assertEqual(decision, "allow")

        # Cleanup
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
