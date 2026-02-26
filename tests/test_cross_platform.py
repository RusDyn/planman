"""Cross-platform compatibility tests for planman."""

import glob
import json
import os
import sys
import tempfile
import unittest
from pathlib import PurePath, PurePosixPath, PureWindowsPath
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


class TestTempPathConsistency(unittest.TestCase):
    """Verify all modules use tempfile.gettempdir() — no hardcoded /tmp."""

    def test_no_hardcoded_tmp_in_source(self):
        """Grep-style check: no Python source files should contain '"/tmp"'."""
        scripts_dir = os.path.join(os.path.dirname(__file__), "..", "scripts")
        violations = []
        for fname in os.listdir(scripts_dir):
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(scripts_dir, fname)
            with open(fpath, "r", encoding="utf-8") as f:
                for i, line in enumerate(f, 1):
                    if '"/tmp"' in line or "'/tmp'" in line:
                        violations.append(f"{fname}:{i}: {line.strip()}")
        self.assertEqual(violations, [], f"Hardcoded /tmp found:\n" + "\n".join(violations))

    def test_state_path_uses_tempdir(self):
        """state._state_path() should return path under tempfile.gettempdir()."""
        from state import _state_path
        path = _state_path("test-session")
        self.assertTrue(
            path.startswith(tempfile.gettempdir()),
            f"Expected {path} to start with {tempfile.gettempdir()}",
        )

    def test_marker_template_uses_tempdir(self):
        """post_tool_hook and pre_exit_plan_hook markers should use tempdir."""
        import post_tool_hook
        import pre_exit_plan_hook
        tmpdir = tempfile.gettempdir()
        for mod_name, mod in [("post_tool_hook", post_tool_hook),
                               ("pre_exit_plan_hook", pre_exit_plan_hook)]:
            template = mod._MARKER_TEMPLATE
            # Template has {session_id} placeholder — format it first
            resolved = template.format(session_id="test")
            self.assertTrue(
                resolved.startswith(tmpdir),
                f"{mod_name}._MARKER_TEMPLATE resolved to {resolved}, "
                f"expected to start with {tmpdir}",
            )


class TestPathSegmentDetection(unittest.TestCase):
    """Test that plan file detection works with different path formats."""

    def _is_plan_path(self, file_path):
        """Replicate the segment-based check from post_tool_hook.py."""
        parts = PurePath(file_path).parts
        lower_parts = tuple(p.casefold() for p in parts)
        try:
            idx = lower_parts.index(".claude")
            return idx + 1 < len(lower_parts) and lower_parts[idx + 1] == "plans"
        except ValueError:
            return False

    def test_unix_plan_path(self):
        self.assertTrue(self._is_plan_path("/home/user/.claude/plans/my-plan.md"))

    def test_unix_nested_plan_path(self):
        self.assertTrue(self._is_plan_path("/home/user/project/.claude/plans/test.md"))

    def test_windows_plan_path(self):
        """Windows-style paths with backslashes."""
        # Use PureWindowsPath to simulate Windows path parsing
        path = "C:\\Users\\user\\.claude\\plans\\my-plan.md"
        parts = PureWindowsPath(path).parts
        lower_parts = tuple(p.casefold() for p in parts)
        try:
            idx = lower_parts.index(".claude")
            result = idx + 1 < len(lower_parts) and lower_parts[idx + 1] == "plans"
        except ValueError:
            result = False
        self.assertTrue(result)

    def test_case_insensitive_detection(self):
        """macOS/Windows case-insensitive filesystems."""
        self.assertTrue(self._is_plan_path("/home/user/.Claude/Plans/test.md"))
        self.assertTrue(self._is_plan_path("/home/user/.CLAUDE/PLANS/test.md"))

    def test_non_plan_path_rejected(self):
        self.assertFalse(self._is_plan_path("/home/user/project/src/main.ts"))

    def test_partial_match_rejected(self):
        """Should not match 'plans_backup' or other partial matches."""
        self.assertFalse(self._is_plan_path("/home/user/.claude/plans_backup/test.md"))

    def test_claude_without_plans_rejected(self):
        self.assertFalse(self._is_plan_path("/home/user/.claude/config.json"))


class TestEncodingRoundtrip(unittest.TestCase):
    """Verify state files handle non-ASCII characters correctly."""

    def setUp(self):
        self._session_id = f"test-encoding-{os.getpid()}"

    def tearDown(self):
        from state import clear_state
        clear_state(self._session_id)

    def test_unicode_feedback_roundtrip(self):
        """State with emoji and accented characters survives save/load."""
        from state import load_state, save_state, record_feedback

        state = load_state(self._session_id)
        state["round_count"] = 1
        state = record_feedback(
            state,
            score=5,
            feedback="Plan needs work: caf\u00e9 endpoint missing \U0001f680 launch sequence",
            breakdown={"completeness": 1, "correctness": 1, "sequencing": 1,
                       "risk_awareness": 1, "clarity": 1},
        )
        save_state(state)

        loaded = load_state(self._session_id)
        self.assertIn("caf\u00e9", loaded["last_feedback"])
        self.assertIn("\U0001f680", loaded["last_feedback"])

    def test_state_save_load_roundtrip(self):
        """State saves and loads correctly."""
        from state import load_state, save_state
        state = load_state(self._session_id)
        state["round_count"] = 2
        save_state(state)
        loaded = load_state(self._session_id)
        self.assertEqual(loaded["round_count"], 2)


class TestFcntlFallback(unittest.TestCase):
    """Verify logging works when fcntl is unavailable (Windows)."""

    def test_log_to_file_without_fcntl(self):
        """_log_to_file should still write when fcntl is None."""
        import hook_utils

        original_fcntl = hook_utils.fcntl
        try:
            hook_utils.fcntl = None  # Simulate Windows
            log_path = os.path.join(tempfile.gettempdir(), "planman-test-nofcntl.log")
            # Remove if exists
            try:
                os.unlink(log_path)
            except OSError:
                pass

            # Patch cwd to use temp dir so the fallback path is used
            hook_utils._log_to_file("test message from fcntl fallback", cwd=None)

            # Verify the log file was written (in tempdir fallback)
            fallback_log = os.path.join(tempfile.gettempdir(), "planman.log")
            self.assertTrue(os.path.exists(fallback_log))
            with open(fallback_log, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn("test message from fcntl fallback", content)
        finally:
            hook_utils.fcntl = original_fcntl


class TestClearStateScript(unittest.TestCase):
    """Test the clear_state.py utility."""

    def test_clear_removes_files(self):
        """clear() should remove planman state files."""
        # Create a temp state file
        test_file = os.path.join(tempfile.gettempdir(), "planman-test-clear-dummy.json")
        with open(test_file, "w", encoding="utf-8") as f:
            json.dump({"test": True}, f)

        from clear_state import clear
        removed, total = clear()
        self.assertGreaterEqual(total, 1)
        self.assertGreaterEqual(removed, 1)
        self.assertFalse(os.path.exists(test_file))

    def test_list_sessions_returns_files(self):
        """list_sessions() should find state files."""
        test_file = os.path.join(tempfile.gettempdir(), "planman-test-list-dummy.json")
        with open(test_file, "w", encoding="utf-8") as f:
            json.dump({"test": True}, f)

        from clear_state import list_sessions
        files = list_sessions()
        self.assertIn(test_file, files)

        # Cleanup
        os.remove(test_file)

    def test_clear_handles_no_files(self):
        """clear() should handle case with no files gracefully."""
        from clear_state import clear
        # May or may not have files — just verify no crash
        removed, total = clear()
        self.assertGreaterEqual(removed, 0)
        self.assertGreaterEqual(total, 0)


if __name__ == "__main__":
    unittest.main()
