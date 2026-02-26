"""Tests for config.py — env overrides, file config, type coercion."""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from config import Config, DEFAULT_RUBRIC, DEFAULTS, load_config, _coerce_bool, _coerce_int, _validate_codex_path


class TestCoerceBool(unittest.TestCase):
    def test_truthy_strings(self):
        for val in ("true", "True", "TRUE", "1", "yes", "Yes", "on", "ON"):
            self.assertTrue(_coerce_bool(val), f"Expected True for {val!r}")

    def test_falsy_strings(self):
        for val in ("false", "False", "FALSE", "0", "no", "No", "off", "OFF"):
            self.assertFalse(_coerce_bool(val), f"Expected False for {val!r}")

    def test_bool_passthrough(self):
        self.assertTrue(_coerce_bool(True))
        self.assertFalse(_coerce_bool(False))

    def test_unknown_falls_back(self):
        # Unknown string falls back to default fail_open (True)
        self.assertTrue(_coerce_bool("maybe"))


class TestCoerceInt(unittest.TestCase):
    def test_valid_int(self):
        self.assertEqual(_coerce_int("7", "threshold"), 7)
        self.assertEqual(_coerce_int("0", "threshold"), 0)

    def test_invalid_falls_back(self):
        self.assertEqual(_coerce_int("abc", "threshold"), DEFAULTS["threshold"])
        self.assertEqual(_coerce_int("", "max_rounds"), DEFAULTS["max_rounds"])

    def test_none_falls_back(self):
        self.assertEqual(_coerce_int(None, "threshold"), DEFAULTS["threshold"])


class TestConfigDefaults(unittest.TestCase):
    @patch("config._load_file_config", return_value={})
    def test_default_values(self, _mock_file):
        # Clear env vars that might interfere
        env_keys = [k for k in os.environ if k.startswith("PLANMAN_")]
        saved = {k: os.environ.pop(k) for k in env_keys}
        try:
            cfg = load_config()
            self.assertEqual(cfg.threshold, 7)
            self.assertEqual(cfg.max_rounds, 3)
            self.assertEqual(cfg.model, "")
            self.assertTrue(cfg.fail_open)
            self.assertTrue(cfg.enabled)
            self.assertEqual(cfg.rubric, DEFAULT_RUBRIC)
            self.assertEqual(cfg.codex_path, "codex")
            self.assertFalse(cfg.verbose)
            self.assertEqual(cfg.timeout, 90)
        finally:
            os.environ.update(saved)

    def test_config_slots(self):
        cfg = Config()
        with self.assertRaises(AttributeError):
            cfg.nonexistent = "oops"


class TestEnvOverrides(unittest.TestCase):
    def setUp(self):
        self._saved = {}
        for k in list(os.environ):
            if k.startswith("PLANMAN_"):
                self._saved[k] = os.environ.pop(k)

    def tearDown(self):
        for k in list(os.environ):
            if k.startswith("PLANMAN_"):
                del os.environ[k]
        os.environ.update(self._saved)

    def test_threshold_override(self):
        os.environ["PLANMAN_THRESHOLD"] = "9"
        cfg = load_config()
        self.assertEqual(cfg.threshold, 9)

    def test_enabled_false(self):
        os.environ["PLANMAN_ENABLED"] = "false"
        cfg = load_config()
        self.assertFalse(cfg.enabled)

    def test_model_override(self):
        os.environ["PLANMAN_MODEL"] = "gpt-4o"
        cfg = load_config()
        self.assertEqual(cfg.model, "gpt-4o")

    def test_codex_path_override(self):
        os.environ["PLANMAN_CODEX_PATH"] = "/usr/local/bin/codex"
        cfg = load_config()
        self.assertEqual(cfg.codex_path, "/usr/local/bin/codex")

    def test_verbose_true(self):
        os.environ["PLANMAN_VERBOSE"] = "1"
        cfg = load_config()
        self.assertTrue(cfg.verbose)

    def test_custom_rubric(self):
        os.environ["PLANMAN_RUBRIC"] = "Score it 1-10 on vibes."
        cfg = load_config()
        self.assertEqual(cfg.rubric, "Score it 1-10 on vibes.")


class TestFileConfig(unittest.TestCase):
    def setUp(self):
        self._saved = {}
        for k in list(os.environ):
            if k.startswith("PLANMAN_"):
                self._saved[k] = os.environ.pop(k)
        self._orig_dir = os.getcwd()
        self._tmpdir = tempfile.mkdtemp()
        os.chdir(self._tmpdir)

    def tearDown(self):
        os.chdir(self._orig_dir)
        for k in list(os.environ):
            if k.startswith("PLANMAN_"):
                del os.environ[k]
        os.environ.update(self._saved)
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_file_config_loads(self):
        os.makedirs(".claude", exist_ok=True)
        with open(".claude/planman.json", "w") as f:
            json.dump({"threshold": 8, "max_rounds": 5}, f)
        cfg = load_config()
        self.assertEqual(cfg.threshold, 8)
        self.assertEqual(cfg.max_rounds, 5)

    def test_env_overrides_file(self):
        os.makedirs(".claude", exist_ok=True)
        with open(".claude/planman.json", "w") as f:
            json.dump({"threshold": 8}, f)
        os.environ["PLANMAN_THRESHOLD"] = "3"
        cfg = load_config()
        self.assertEqual(cfg.threshold, 3)  # env wins

    def test_corrupt_file_ignored(self):
        os.makedirs(".claude", exist_ok=True)
        with open(".claude/planman.json", "w") as f:
            f.write("not json{{{")
        cfg = load_config()
        self.assertEqual(cfg.threshold, DEFAULTS["threshold"])

    def test_non_dict_file_ignored(self):
        os.makedirs(".claude", exist_ok=True)
        with open(".claude/planman.json", "w") as f:
            json.dump([1, 2, 3], f)
        cfg = load_config()
        self.assertEqual(cfg.threshold, DEFAULTS["threshold"])


class TestValidateCodexPath(unittest.TestCase):
    def test_codex_path_rejects_traversal(self):
        self.assertEqual(_validate_codex_path("../evil/codex"), "codex")

    def test_codex_path_allows_absolute(self):
        self.assertEqual(_validate_codex_path("/usr/local/bin/codex"), "/usr/local/bin/codex")

    def test_codex_path_allows_basename(self):
        self.assertEqual(_validate_codex_path("codex"), "codex")


class TestRangeClamping(unittest.TestCase):
    def setUp(self):
        self._saved = {}
        for k in list(os.environ):
            if k.startswith("PLANMAN_"):
                self._saved[k] = os.environ.pop(k)

    def tearDown(self):
        for k in list(os.environ):
            if k.startswith("PLANMAN_"):
                del os.environ[k]
        os.environ.update(self._saved)

    def test_threshold_clamped_low(self):
        os.environ["PLANMAN_THRESHOLD"] = "-5"
        cfg = load_config()
        self.assertEqual(cfg.threshold, 0)

    def test_threshold_clamped_high(self):
        os.environ["PLANMAN_THRESHOLD"] = "999"
        cfg = load_config()
        self.assertEqual(cfg.threshold, 10)

    def test_timeout_clamped_low(self):
        os.environ["PLANMAN_TIMEOUT"] = "0"
        cfg = load_config()
        self.assertEqual(cfg.timeout, 1)

    def test_timeout_clamped_high(self):
        os.environ["PLANMAN_TIMEOUT"] = "9999"
        cfg = load_config()
        self.assertEqual(cfg.timeout, 600)


class TestCoerceBoolKey(unittest.TestCase):
    def test_coerce_bool_uses_correct_default_for_field(self):
        # "maybe" for enabled field should return DEFAULTS["enabled"] (True)
        result = _coerce_bool("maybe", key="enabled")
        self.assertEqual(result, DEFAULTS["enabled"])

    def test_coerce_bool_uses_correct_default_for_verbose(self):
        # "maybe" for verbose field should return DEFAULTS["verbose"] (False)
        result = _coerce_bool("maybe", key="verbose")
        self.assertEqual(result, DEFAULTS["verbose"])


if __name__ == "__main__":
    unittest.main()
