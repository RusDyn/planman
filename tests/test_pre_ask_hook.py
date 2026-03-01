"""Tests for pre_ask_hook.py — hook contract, input validation, fail-open."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

HOOK_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "pre_ask_hook.py")


def _run_hook(stdin_data, env_overrides=None, cwd=None):
    """Run pre_ask_hook.py as subprocess and return (stdout, stderr, returncode)."""
    env = os.environ.copy()
    # Clear PLANMAN_ env vars to avoid test interference
    for k in list(env):
        if k.startswith("PLANMAN_"):
            del env[k]
    if env_overrides:
        env.update(env_overrides)

    proc = subprocess.run(
        [sys.executable, HOOK_SCRIPT],
        input=json.dumps(stdin_data) if isinstance(stdin_data, dict) else stdin_data,
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        timeout=10,
    )
    return proc.stdout, proc.stderr, proc.returncode


class TestAutoAnswerDisabled(unittest.TestCase):
    """When auto_answer=false, hook should always allow."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self._tmpdir, ".claude"), exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_auto_answer_false_allows(self):
        """auto_answer=false → no JSON output (allow)."""
        with open(os.path.join(self._tmpdir, ".claude", "planman.jsonc"), "w") as f:
            json.dump({"auto_answer": False}, f)
        stdin = {
            "cwd": self._tmpdir,
            "tool_input": {"questions": [{"question": "Q?", "options": [{"label": "A"}, {"label": "B"}]}]},
        }
        stdout, stderr, rc = _run_hook(stdin, cwd=self._tmpdir)
        self.assertEqual(rc, 0)
        # No JSON with decision=block
        if stdout.strip():
            data = json.loads(stdout)
            self.assertNotEqual(data.get("decision"), "block")


class TestInputValidation(unittest.TestCase):
    """Test that malformed inputs always allow (fail-open)."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self._tmpdir, ".claude"), exist_ok=True)
        with open(os.path.join(self._tmpdir, ".claude", "planman.jsonc"), "w") as f:
            json.dump({"auto_answer": True}, f)

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _assert_allows(self, stdin_data):
        stdout, stderr, rc = _run_hook(stdin_data, cwd=self._tmpdir)
        self.assertEqual(rc, 0)
        if stdout.strip():
            data = json.loads(stdout)
            self.assertNotEqual(data.get("decision"), "block")

    def test_missing_tool_input(self):
        self._assert_allows({"cwd": self._tmpdir})

    def test_tool_input_not_dict(self):
        self._assert_allows({"cwd": self._tmpdir, "tool_input": "string"})

    def test_missing_questions_key(self):
        self._assert_allows({"cwd": self._tmpdir, "tool_input": {}})

    def test_questions_not_list(self):
        self._assert_allows({"cwd": self._tmpdir, "tool_input": {"questions": "string"}})

    def test_empty_questions_list(self):
        self._assert_allows({"cwd": self._tmpdir, "tool_input": {"questions": []}})

    def test_question_not_dict(self):
        self._assert_allows({"cwd": self._tmpdir, "tool_input": {"questions": ["string"]}})

    def test_options_less_than_two(self):
        self._assert_allows({
            "cwd": self._tmpdir,
            "tool_input": {"questions": [{"question": "Q?", "options": [{"label": "Only one"}]}]},
        })

    def test_invalid_json_input(self):
        stdout, stderr, rc = _run_hook("not json at all", cwd=self._tmpdir)
        self.assertEqual(rc, 0)
        if stdout.strip():
            data = json.loads(stdout)
            self.assertNotEqual(data.get("decision"), "block")

    def test_empty_input(self):
        stdout, stderr, rc = _run_hook("", cwd=self._tmpdir)
        self.assertEqual(rc, 0)


class TestAutoAnswerBlocking(unittest.TestCase):
    """Test that answerable questions produce correct block output."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self._tmpdir, ".claude"), exist_ok=True)
        with open(os.path.join(self._tmpdir, ".claude", "planman.jsonc"), "w") as f:
            json.dump({"auto_answer": True}, f)
        # Create a TypeScript indicator
        open(os.path.join(self._tmpdir, "tsconfig.json"), "w").close()

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_answerable_question_blocks(self):
        stdin = {
            "cwd": self._tmpdir,
            "tool_input": {
                "questions": [{
                    "question": "What language should we use?",
                    "header": "Language",
                    "options": [{"label": "TypeScript"}, {"label": "Python"}, {"label": "Go"}],
                }],
            },
        }
        stdout, stderr, rc = _run_hook(stdin, cwd=self._tmpdir)
        self.assertEqual(rc, 0)
        data = json.loads(stdout)
        self.assertEqual(data["decision"], "block")
        self.assertIn("Planman auto-answer", data["reason"])
        self.assertIn("TypeScript", data["reason"])
        self.assertIn("Evidence", data["reason"])
        self.assertIn("systemMessage", data)

    def test_unanswerable_allows(self):
        """No matching tech → allow."""
        stdin = {
            "cwd": self._tmpdir,
            "tool_input": {
                "questions": [{
                    "question": "Which deployment strategy?",
                    "header": "Deploy",
                    "options": [{"label": "Blue-green"}, {"label": "Canary"}, {"label": "Rolling"}],
                }],
            },
        }
        stdout, stderr, rc = _run_hook(stdin, cwd=self._tmpdir)
        self.assertEqual(rc, 0)
        if stdout.strip():
            data = json.loads(stdout)
            self.assertNotEqual(data.get("decision"), "block")

    def test_any_unanswerable_allows_all(self):
        """If ANY question is unanswerable, ALL pass through."""
        stdin = {
            "cwd": self._tmpdir,
            "tool_input": {
                "questions": [
                    {
                        "question": "What language?",
                        "header": "Language",
                        "options": [{"label": "TypeScript"}, {"label": "Python"}],
                    },
                    {
                        "question": "Which deployment?",
                        "header": "Deploy",
                        "options": [{"label": "Blue-green"}, {"label": "Canary"}],
                    },
                ],
            },
        }
        stdout, stderr, rc = _run_hook(stdin, cwd=self._tmpdir)
        self.assertEqual(rc, 0)
        if stdout.strip():
            data = json.loads(stdout)
            self.assertNotEqual(data.get("decision"), "block")


class TestFailOpen(unittest.TestCase):
    """Verify fail-open on uncaught exceptions."""

    def test_exception_in_heuristic_allows(self):
        """Even if analyze_question throws, hook exits 0 with no block."""
        # Use invalid cwd that will cause issues
        stdin = {
            "cwd": "/nonexistent/path/that/should/not/exist",
            "tool_input": {
                "questions": [{
                    "question": "Q?",
                    "options": [{"label": "A"}, {"label": "B"}],
                }],
            },
        }
        env = {"PLANMAN_AUTO_ANSWER": "true", "PLANMAN_ENABLED": "true"}
        stdout, stderr, rc = _run_hook(stdin, env_overrides=env)
        self.assertEqual(rc, 0)
        if stdout.strip():
            data = json.loads(stdout)
            self.assertNotEqual(data.get("decision"), "block")


class TestSiblingImport(unittest.TestCase):
    """Verify the script can import siblings without CLAUDE_PLUGIN_ROOT."""

    def test_import_works(self):
        """Script runs without CLAUDE_PLUGIN_ROOT set."""
        env = os.environ.copy()
        env.pop("CLAUDE_PLUGIN_ROOT", None)
        for k in list(env):
            if k.startswith("PLANMAN_"):
                del env[k]
        proc = subprocess.run(
            [sys.executable, HOOK_SCRIPT],
            input="{}",
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )
        # Should not crash with ImportError
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn("ImportError", proc.stderr)


if __name__ == "__main__":
    unittest.main()
