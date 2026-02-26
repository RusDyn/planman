"""Tests for post_tool_hook.py — lightweight plan file path tracker."""

import json
import os
import sys
import tempfile
import unittest
from io import StringIO
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


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


class TestPostToolHookIntegration(unittest.TestCase):
    """Integration tests for PostToolUse hook (plan file tracker)."""

    def setUp(self):
        self._session_id = f"test-pth-{os.getpid()}-{id(self)}"
        self._env_saved = {}
        for k in list(os.environ):
            if k.startswith("PLANMAN_"):
                self._env_saved[k] = os.environ.pop(k)
        os.environ["PLANMAN_ENABLED"] = "true"
        os.environ["PLANMAN_VERBOSE"] = "false"
        # Clean up any leftover marker
        self._marker_path = os.path.join(tempfile.gettempdir(), f"planman-plan-{self._session_id}.json")
        self._cleanup_marker()

    def tearDown(self):
        self._cleanup_marker()
        for k in list(os.environ):
            if k.startswith("PLANMAN_"):
                del os.environ[k]
        os.environ.update(self._env_saved)

    def _cleanup_marker(self):
        try:
            os.unlink(self._marker_path)
        except OSError:
            pass

    def _run_hook(self, hook_input):
        """Run post_tool_hook.main() with mocked stdin/stdout."""
        import post_tool_hook

        stdin_data = json.dumps(hook_input)
        stdout_capture = StringIO()

        with patch("sys.stdin", StringIO(stdin_data)), \
             patch("sys.stdout", stdout_capture), \
             self.assertRaises(SystemExit) as ctx:
            post_tool_hook.main()

        output = stdout_capture.getvalue()
        exit_code = ctx.exception.code
        return output, exit_code

    def test_non_write_tool_passes_through(self):
        """Non-Write tool events should exit silently."""
        output, code = self._run_hook({
            "tool_name": "Read",
            "tool_input": {"file_path": "/some/file.txt"},
            "session_id": self._session_id,
        })
        self.assertEqual(code, 0)
        self.assertEqual(output, "")

    def test_non_plan_file_write_passes_through(self):
        """Write to a non-plan file should exit silently."""
        output, code = self._run_hook({
            "tool_name": "Write",
            "tool_input": {
                "file_path": "/home/user/project/src/main.ts",
                "content": "console.log('hello')",
            },
            "session_id": self._session_id,
        })
        self.assertEqual(code, 0)
        self.assertEqual(output, "")
        self.assertFalse(os.path.exists(self._marker_path))

    def test_plan_file_empty_content_still_tracks(self):
        """Write to a plan file with empty content should still record the path."""
        plan_path = "/home/user/.claude/plans/test.md"
        output, code = self._run_hook({
            "tool_name": "Write",
            "tool_input": {
                "file_path": plan_path,
                "content": "",
            },
            "session_id": self._session_id,
        })
        self.assertEqual(code, 0)
        self.assertEqual(output, "")
        # Tracker records the path regardless — content check happens at eval time
        self.assertTrue(os.path.exists(self._marker_path))

    def test_plan_file_records_marker(self):
        """Write to a plan file should record the path in a session marker."""
        plan_path = "/home/user/.claude/plans/test.md"
        output, code = self._run_hook({
            "tool_name": "Write",
            "tool_input": {
                "file_path": plan_path,
                "content": PLAN_TEXT,
            },
            "session_id": self._session_id,
        })
        self.assertEqual(code, 0)
        self.assertEqual(output, "")  # No stdout output — always allows

        # Verify marker was written
        self.assertTrue(os.path.exists(self._marker_path))
        with open(self._marker_path) as f:
            marker = json.load(f)
        self.assertEqual(marker["plan_file_path"], plan_path)
        self.assertIn("timestamp", marker)

    def test_marker_updates_on_subsequent_writes(self):
        """Subsequent plan writes should update the marker with latest path."""
        first_path = "/home/user/.claude/plans/v1.md"
        second_path = "/home/user/.claude/plans/v2.md"

        self._run_hook({
            "tool_name": "Write",
            "tool_input": {"file_path": first_path, "content": PLAN_TEXT},
            "session_id": self._session_id,
        })
        self._run_hook({
            "tool_name": "Write",
            "tool_input": {"file_path": second_path, "content": PLAN_TEXT},
            "session_id": self._session_id,
        })

        with open(self._marker_path) as f:
            marker = json.load(f)
        self.assertEqual(marker["plan_file_path"], second_path)

    def test_never_blocks(self):
        """The tracker should never output a block decision."""
        output, code = self._run_hook({
            "tool_name": "Write",
            "tool_input": {
                "file_path": "/home/user/.claude/plans/test.md",
                "content": PLAN_TEXT,
            },
            "session_id": self._session_id,
        })
        self.assertEqual(code, 0)
        # No output at all — never blocks
        self.assertEqual(output, "")

    def test_empty_stdin_passes_through(self):
        """Empty stdin should exit silently."""
        import post_tool_hook
        stdout_capture = StringIO()
        with patch("sys.stdin", StringIO("")), \
             patch("sys.stdout", stdout_capture), \
             self.assertRaises(SystemExit) as ctx:
            post_tool_hook.main()
        self.assertEqual(ctx.exception.code, 0)

    def test_malformed_json_passes_through(self):
        """Malformed JSON stdin should exit silently."""
        import post_tool_hook
        stdout_capture = StringIO()
        with patch("sys.stdin", StringIO("not json")), \
             patch("sys.stdout", stdout_capture), \
             self.assertRaises(SystemExit) as ctx:
            post_tool_hook.main()
        self.assertEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
