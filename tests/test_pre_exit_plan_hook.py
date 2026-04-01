"""Tests for pre_exit_plan_hook.py — plan file reading edge cases."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from hook_utils import read_plan_text as _read_plan_text


class TestReadPlanText(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_read_plan_text_invalid_utf8(self):
        """Plan file with invalid UTF-8 returns None gracefully."""
        plan_path = os.path.join(self.tmpdir, "bad.md")
        with open(plan_path, "wb") as f:
            f.write(b"# Plan\n\x80\x81\x82 invalid bytes\n")
        text, skip = _read_plan_text(plan_path)
        self.assertIsNone(text)

    def test_read_plan_text_valid(self):
        """Normal plan file is read successfully."""
        plan_path = os.path.join(self.tmpdir, "good.md")
        with open(plan_path, "w", encoding="utf-8") as f:
            f.write("# Plan\n1. Step one\n")
        text, skip = _read_plan_text(plan_path)
        self.assertIn("Step one", text)
        self.assertIsNone(skip)

    def test_read_plan_text_empty(self):
        """Empty plan file returns None."""
        plan_path = os.path.join(self.tmpdir, "empty.md")
        with open(plan_path, "w", encoding="utf-8") as f:
            f.write("")
        text, skip = _read_plan_text(plan_path)
        self.assertIsNone(text)

    def test_read_plan_text_missing_file(self):
        """Missing file returns None gracefully."""
        text, skip = _read_plan_text(os.path.join(self.tmpdir, "nonexistent.md"))
        self.assertIsNone(text)
        self.assertIsNone(skip)


if __name__ == "__main__":
    unittest.main()
