"""Tests for the packaged Codex plugin wrapper."""

import filecmp
import json
import os
import unittest


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PACKAGE_ROOT = os.path.join(ROOT, "plugins", "planman")


class TestCodexPluginPackage(unittest.TestCase):
    def test_marketplace_points_to_packaged_plugin(self):
        path = os.path.join(ROOT, ".agents", "plugins", "marketplace.json")
        with open(path, encoding="utf-8") as f:
            marketplace = json.load(f)

        plugin = marketplace["plugins"][0]
        self.assertEqual(plugin["name"], "planman")
        self.assertEqual(plugin["source"]["path"], "./plugins/planman")

    def test_packaged_manifest_matches_root_manifest(self):
        self.assertTrue(
            filecmp.cmp(
                os.path.join(ROOT, ".codex-plugin", "plugin.json"),
                os.path.join(PACKAGE_ROOT, ".codex-plugin", "plugin.json"),
                shallow=False,
            )
        )

    def test_packaged_hook_config_matches_root_hook_config(self):
        self.assertTrue(
            filecmp.cmp(
                os.path.join(ROOT, "hooks.json"),
                os.path.join(PACKAGE_ROOT, "hooks.json"),
                shallow=False,
            )
        )

    def test_packaged_runtime_files_match_sources(self):
        files = [
            ("scripts/codex_stop_hook.py", "scripts/codex_stop_hook.py"),
            ("scripts/config.py", "scripts/config.py"),
            ("scripts/evaluator.py", "scripts/evaluator.py"),
            ("scripts/hook_utils.py", "scripts/hook_utils.py"),
            ("scripts/path_utils.py", "scripts/path_utils.py"),
            ("scripts/state.py", "scripts/state.py"),
            ("schemas/evaluation.json", "schemas/evaluation.json"),
        ]
        for source, packaged in files:
            with self.subTest(source=source):
                self.assertTrue(
                    filecmp.cmp(
                        os.path.join(ROOT, source),
                        os.path.join(PACKAGE_ROOT, packaged),
                        shallow=False,
                    )
                )
