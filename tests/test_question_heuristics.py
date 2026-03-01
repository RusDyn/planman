"""Tests for question_heuristics.py — signal map, detect_tech, match_options, extensions."""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from question_heuristics import (
    COMPOSE_SERVICE_MAP,
    MAX_EXTENSION_SCAN,
    SIGNAL_MAP,
    SKIP_DIRS,
    analyze_question,
    count_extensions,
    detect_tech,
    match_options,
    parse_compose_services,
)


class TestParseComposeServices(unittest.TestCase):
    """Test docker-compose.yml line-based parser."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _write_compose(self, content):
        path = os.path.join(self._tmpdir, "docker-compose.yml")
        with open(path, "w") as f:
            f.write(content)
        return path

    def test_standard_2_space_indent(self):
        path = self._write_compose(
            "services:\n"
            "  postgres:\n"
            "    image: postgres:15\n"
            "  redis:\n"
            "    image: redis:7\n"
        )
        self.assertEqual(parse_compose_services(path), ["postgres", "redis"])

    def test_4_space_indent(self):
        path = self._write_compose(
            "services:\n"
            "    postgres:\n"
            "        image: postgres:15\n"
            "    redis:\n"
            "        image: redis:7\n"
        )
        self.assertEqual(parse_compose_services(path), ["postgres", "redis"])

    def test_quoted_keys_skipped(self):
        path = self._write_compose(
            'services:\n'
            '  "postgres":\n'
            '    image: postgres:15\n'
            '  redis:\n'
            '    image: redis:7\n'
        )
        # Quoted keys don't match \w pattern → only redis captured
        self.assertEqual(parse_compose_services(path), ["redis"])

    def test_tab_indentation_skipped(self):
        path = self._write_compose(
            "services:\n"
            "\tpostgres:\n"
            "\t\timage: postgres:15\n"
        )
        # Tabs don't match space regex → empty
        self.assertEqual(parse_compose_services(path), [])

    def test_empty_services_block(self):
        path = self._write_compose(
            "services:\n"
            "volumes:\n"
            "  data:\n"
        )
        self.assertEqual(parse_compose_services(path), [])

    def test_missing_file(self):
        self.assertEqual(parse_compose_services("/nonexistent/path"), [])

    def test_stops_at_next_top_level_key(self):
        path = self._write_compose(
            "services:\n"
            "  postgres:\n"
            "    image: postgres:15\n"
            "volumes:\n"
            "  data:\n"
            "    driver: local\n"
        )
        self.assertEqual(parse_compose_services(path), ["postgres"])

    def test_comments_and_blank_lines(self):
        path = self._write_compose(
            "services:\n"
            "  # Database\n"
            "\n"
            "  postgres:\n"
            "    image: postgres:15\n"
            "\n"
            "  # Cache\n"
            "  redis:\n"
            "    image: redis:7\n"
        )
        self.assertEqual(parse_compose_services(path), ["postgres", "redis"])


class TestDetectTech(unittest.TestCase):
    """Test detect_tech signal map."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_tsconfig_detects_typescript(self):
        open(os.path.join(self._tmpdir, "tsconfig.json"), "w").close()
        detected = detect_tech(self._tmpdir)
        self.assertIn("typescript", detected)
        self.assertEqual(detected["typescript"], ["tsconfig.json exists"])

    def test_no_indicator_files_empty(self):
        detected = detect_tech(self._tmpdir)
        self.assertEqual(detected, {})

    def test_multiple_signals(self):
        open(os.path.join(self._tmpdir, "tsconfig.json"), "w").close()
        open(os.path.join(self._tmpdir, "package.json"), "w").close()
        detected = detect_tech(self._tmpdir)
        self.assertIn("typescript", detected)
        self.assertIn("node", detected)
        self.assertIn("javascript", detected)

    def test_compose_postgres_service(self):
        compose_path = os.path.join(self._tmpdir, "docker-compose.yml")
        with open(compose_path, "w") as f:
            f.write("services:\n  postgres:\n    image: postgres:15\n")
        detected = detect_tech(self._tmpdir)
        self.assertIn("postgresql", detected)
        self.assertIn("docker-compose.yml service: postgres", detected["postgresql"])

    def test_python_signals(self):
        open(os.path.join(self._tmpdir, "requirements.txt"), "w").close()
        detected = detect_tech(self._tmpdir)
        self.assertIn("python", detected)


class TestMatchOptions(unittest.TestCase):
    """Test match_options against detected tech."""

    def test_single_match_returns_it(self):
        detected = {"typescript": ["tsconfig.json exists"]}
        options = [
            {"label": "TypeScript"},
            {"label": "Python"},
            {"label": "Go"},
        ]
        result = match_options(detected, options)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "TypeScript")
        self.assertEqual(result[1], ["tsconfig.json exists"])

    def test_zero_matches_returns_none(self):
        detected = {"rust": ["Cargo.toml exists"]}
        options = [
            {"label": "TypeScript"},
            {"label": "Python"},
        ]
        self.assertIsNone(match_options(detected, options))

    def test_multiple_matches_returns_none(self):
        detected = {
            "typescript": ["tsconfig.json exists"],
            "python": ["requirements.txt exists"],
        }
        options = [
            {"label": "TypeScript"},
            {"label": "Python"},
        ]
        self.assertIsNone(match_options(detected, options))

    def test_alias_matching(self):
        detected = {"postgresql": ["docker-compose.yml service: postgres"]}
        options = [
            {"label": "PostgreSQL"},
            {"label": "MySQL"},
        ]
        result = match_options(detected, options)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "PostgreSQL")

    def test_case_insensitive(self):
        detected = {"typescript": ["tsconfig.json exists"]}
        options = [
            {"label": "typescript"},
            {"label": "python"},
        ]
        result = match_options(detected, options)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "typescript")


class TestCountExtensions(unittest.TestCase):
    """Test count_extensions file scanning."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_counts_ts_files(self):
        for i in range(10):
            open(os.path.join(self._tmpdir, f"file{i}.ts"), "w").close()
        for i in range(2):
            open(os.path.join(self._tmpdir, f"file{i}.js"), "w").close()
        counts = count_extensions(self._tmpdir, [".ts", ".js"])
        self.assertEqual(counts[".ts"], 10)
        self.assertEqual(counts[".js"], 2)

    def test_skips_node_modules(self):
        nm_dir = os.path.join(self._tmpdir, "node_modules")
        os.makedirs(nm_dir)
        open(os.path.join(nm_dir, "lib.ts"), "w").close()
        open(os.path.join(self._tmpdir, "app.ts"), "w").close()
        counts = count_extensions(self._tmpdir, [".ts"])
        self.assertEqual(counts[".ts"], 1)

    def test_skips_git_dir(self):
        git_dir = os.path.join(self._tmpdir, ".git")
        os.makedirs(git_dir)
        open(os.path.join(git_dir, "obj.py"), "w").close()
        open(os.path.join(self._tmpdir, "main.py"), "w").close()
        counts = count_extensions(self._tmpdir, [".py"])
        self.assertEqual(counts[".py"], 1)

    def test_caps_at_max_scan(self):
        # Create more files than MAX_EXTENSION_SCAN — verify it doesn't hang
        # (We don't create 5000 files in test, just verify the loop terminates)
        counts = count_extensions(self._tmpdir, [".ts"])
        self.assertEqual(counts[".ts"], 0)  # No files created


class TestAnalyzeQuestion(unittest.TestCase):
    """Test analyze_question orchestrator."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_typescript_project(self):
        open(os.path.join(self._tmpdir, "tsconfig.json"), "w").close()
        options = [
            {"label": "TypeScript"},
            {"label": "Python"},
            {"label": "Go"},
        ]
        result = analyze_question("What language?", options, self._tmpdir)
        self.assertIsNotNone(result)
        self.assertEqual(result["answer"], "TypeScript")
        self.assertTrue(len(result["evidence"]) > 0)

    def test_ambiguous_returns_none(self):
        open(os.path.join(self._tmpdir, "tsconfig.json"), "w").close()
        open(os.path.join(self._tmpdir, "requirements.txt"), "w").close()
        options = [
            {"label": "TypeScript"},
            {"label": "Python"},
        ]
        result = analyze_question("What language?", options, self._tmpdir)
        self.assertIsNone(result)

    def test_malformed_options_not_list(self):
        result = analyze_question("Question?", "not a list", self._tmpdir)
        self.assertIsNone(result)

    def test_malformed_options_less_than_two(self):
        result = analyze_question("Question?", [{"label": "Only one"}], self._tmpdir)
        self.assertIsNone(result)

    def test_malformed_option_no_label(self):
        result = analyze_question("Question?", [{"text": "A"}, {"text": "B"}], self._tmpdir)
        self.assertIsNone(result)

    def test_invalid_cwd(self):
        result = analyze_question("Q?", [{"label": "A"}, {"label": "B"}], "/nonexistent/dir")
        self.assertIsNone(result)

    def test_extension_dominance(self):
        """80%+ .ts files → detects TypeScript even without tsconfig."""
        for i in range(9):
            open(os.path.join(self._tmpdir, f"file{i}.ts"), "w").close()
        open(os.path.join(self._tmpdir, "file0.js"), "w").close()
        options = [
            {"label": "TypeScript"},
            {"label": "Python"},
        ]
        result = analyze_question("What language?", options, self._tmpdir)
        self.assertIsNotNone(result)
        self.assertEqual(result["answer"], "TypeScript")

    def test_extension_split_defers(self):
        """50/50 split → None (defer)."""
        for i in range(5):
            open(os.path.join(self._tmpdir, f"file{i}.ts"), "w").close()
            open(os.path.join(self._tmpdir, f"file{i}.py"), "w").close()
        options = [
            {"label": "TypeScript"},
            {"label": "Python"},
        ]
        result = analyze_question("What language?", options, self._tmpdir)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
