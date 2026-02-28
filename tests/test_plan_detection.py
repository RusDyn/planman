"""Tests for _find_plan_file() — TTL, scan fallback, marker handling."""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from config import Config
from hook_utils import MARKER_TEMPLATE, safe_session_id
from pre_exit_plan_hook import _find_plan_file, _is_plan_filename


def _make_config(**overrides):
    defaults = {
        "threshold": 7,
        "max_rounds": 3,
        "model": "",
        "fail_open": True,
        "enabled": True,
        "rubric": "Score it 1-10.",
        "verbose": False,
        "stress_test": False,
        "source_verify": True,
    }
    defaults.update(overrides)
    return Config(**defaults)


def _write_marker(session_id, plan_path, timestamp=None):
    """Write a marker file for the given session."""
    safe_id = safe_session_id(session_id)
    marker_path = MARKER_TEMPLATE.format(session_id=safe_id)
    marker = {"plan_file_path": plan_path}
    if timestamp is not None:
        marker["timestamp"] = timestamp
    with open(marker_path, "w", encoding="utf-8") as f:
        json.dump(marker, f)
    return marker_path


def _cleanup_marker(session_id):
    safe_id = safe_session_id(session_id)
    marker_path = MARKER_TEMPLATE.format(session_id=safe_id)
    try:
        os.unlink(marker_path)
    except OSError:
        pass


class TestStaleMarkerFallback(unittest.TestCase):
    """Test 1: Stale marker (expired TTL) → scan fallback."""

    def setUp(self):
        self._session_id = f"test-stale-{os.getpid()}-{id(self)}"
        self._tmpdir = tempfile.mkdtemp()
        self._plans_dir = os.path.join(self._tmpdir, ".claude", "plans")
        os.makedirs(self._plans_dir)

    def tearDown(self):
        _cleanup_marker(self._session_id)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_expired_marker_falls_to_scan(self):
        """Marker timestamp >2h ago → falls through to scan."""
        # File A: referenced by marker (old)
        file_a = os.path.join(self._plans_dir, "old-plan.md")
        with open(file_a, "w") as f:
            f.write("# Old Plan")

        # File B: newer file in plans dir
        file_b = os.path.join(self._plans_dir, "new-plan.md")
        with open(file_b, "w") as f:
            f.write("# New Plan")
        # Ensure file_b is newer
        os.utime(file_b, (time.time(), time.time()))
        os.utime(file_a, (time.time() - 100, time.time() - 100))

        # Write expired marker (3 hours ago)
        _write_marker(self._session_id, file_a, timestamp=time.time() - 10800)

        config = _make_config()
        path, text, _ = _find_plan_file(self._session_id, self._tmpdir, config)
        self.assertEqual(path, file_b)
        self.assertIn("New Plan", text)


class TestFreshMarkerTrusted(unittest.TestCase):
    """Test 2: Fresh marker trusted over newer scanned file."""

    def setUp(self):
        self._session_id = f"test-fresh-{os.getpid()}-{id(self)}"
        self._tmpdir = tempfile.mkdtemp()
        self._plans_dir = os.path.join(self._tmpdir, ".claude", "plans")
        os.makedirs(self._plans_dir)

    def tearDown(self):
        _cleanup_marker(self._session_id)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_fresh_marker_wins_over_newer_file(self):
        """Fresh marker → returns marker's file even if scan finds newer."""
        file_a = os.path.join(self._plans_dir, "marker-plan.md")
        with open(file_a, "w") as f:
            f.write("# Marker Plan")

        file_b = os.path.join(self._plans_dir, "newer-plan.md")
        with open(file_b, "w") as f:
            f.write("# Newer Plan")
        # Make file_b newer
        os.utime(file_b, (time.time() + 10, time.time() + 10))

        _write_marker(self._session_id, file_a, timestamp=time.time())

        config = _make_config()
        path, text, _ = _find_plan_file(self._session_id, self._tmpdir, config)
        self.assertEqual(os.path.realpath(path), os.path.realpath(file_a))
        self.assertIn("Marker Plan", text)


class TestMarkerAndScanAgree(unittest.TestCase):
    """Test 3: Marker and scan agree → returns file."""

    def setUp(self):
        self._session_id = f"test-agree-{os.getpid()}-{id(self)}"
        self._tmpdir = tempfile.mkdtemp()
        self._plans_dir = os.path.join(self._tmpdir, ".claude", "plans")
        os.makedirs(self._plans_dir)

    def tearDown(self):
        _cleanup_marker(self._session_id)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_marker_and_scan_same_file(self):
        plan_file = os.path.join(self._plans_dir, "the-plan.md")
        with open(plan_file, "w") as f:
            f.write("# The Plan")

        _write_marker(self._session_id, plan_file, timestamp=time.time())

        config = _make_config()
        path, text, _ = _find_plan_file(self._session_id, self._tmpdir, config)
        self.assertIsNotNone(path)
        self.assertIn("The Plan", text)


class TestMarkerDeletedFileFallback(unittest.TestCase):
    """Test 4: Marker points to deleted file → scan fallback."""

    def setUp(self):
        self._session_id = f"test-deleted-{os.getpid()}-{id(self)}"
        self._tmpdir = tempfile.mkdtemp()
        self._plans_dir = os.path.join(self._tmpdir, ".claude", "plans")
        os.makedirs(self._plans_dir)

    def tearDown(self):
        _cleanup_marker(self._session_id)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_marker_file_deleted_falls_to_scan(self):
        # Marker points to non-existent file
        _write_marker(
            self._session_id,
            os.path.join(self._plans_dir, "deleted.md"),
            timestamp=time.time(),
        )

        # But another file exists
        fallback = os.path.join(self._plans_dir, "fallback-plan.md")
        with open(fallback, "w") as f:
            f.write("# Fallback Plan")

        config = _make_config()
        path, text, _ = _find_plan_file(self._session_id, self._tmpdir, config)
        self.assertEqual(path, fallback)
        self.assertIn("Fallback Plan", text)


class TestTTLBoundary(unittest.TestCase):
    """Test 5: TTL boundary — exactly at cutoff."""

    def setUp(self):
        self._session_id = f"test-ttl-{os.getpid()}-{id(self)}"
        self._tmpdir = tempfile.mkdtemp()
        self._plans_dir = os.path.join(self._tmpdir, ".claude", "plans")
        os.makedirs(self._plans_dir)

    def tearDown(self):
        _cleanup_marker(self._session_id)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_just_within_ttl_is_fresh(self):
        """Marker at TTL + tolerance - 1s → still fresh."""
        plan_file = os.path.join(self._plans_dir, "fresh.md")
        with open(plan_file, "w") as f:
            f.write("# Fresh Plan")

        # Hardcoded: TTL=7200, tolerance=2
        ts = time.time() - (7200 + 2 - 1)
        _write_marker(self._session_id, plan_file, timestamp=ts)

        config = _make_config()
        path, text, _ = _find_plan_file(self._session_id, self._tmpdir, config)
        self.assertEqual(os.path.realpath(path), os.path.realpath(plan_file))
        self.assertIn("Fresh Plan", text)

    def test_just_beyond_ttl_is_expired(self):
        """Marker at TTL + tolerance + 1s → expired, falls to scan."""
        marker_file = os.path.join(self._plans_dir, "marker-file.md")
        with open(marker_file, "w") as f:
            f.write("# Marker File")
        os.utime(marker_file, (time.time() - 100, time.time() - 100))

        scan_file = os.path.join(self._plans_dir, "scan-file.md")
        with open(scan_file, "w") as f:
            f.write("# Scan File")
        os.utime(scan_file, (time.time(), time.time()))

        # Hardcoded: TTL=7200, tolerance=2
        ts = time.time() - (7200 + 2 + 1)
        _write_marker(self._session_id, marker_file, timestamp=ts)

        config = _make_config()
        path, text, _ = _find_plan_file(self._session_id, self._tmpdir, config)
        # Should pick the scan file (newer mtime), not the marker file
        self.assertEqual(path, scan_file)
        self.assertIn("Scan File", text)


class TestNonPlanFilesSkipped(unittest.TestCase):
    """Test 6: Non-plan files skipped by scan."""

    def setUp(self):
        self._session_id = f"test-skip-{os.getpid()}-{id(self)}"
        self._tmpdir = tempfile.mkdtemp()
        self._plans_dir = os.path.join(self._tmpdir, ".claude", "plans")
        os.makedirs(self._plans_dir)

    def tearDown(self):
        _cleanup_marker(self._session_id)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_readme_and_template_skipped(self):
        # Non-plan files (should be skipped)
        for name in ["README.md", "TEMPLATE.md", "sample-plan.md", "example.md"]:
            with open(os.path.join(self._plans_dir, name), "w") as f:
                f.write("# Not a plan")

        # Actual plan file
        plan = os.path.join(self._plans_dir, "actual-plan.md")
        with open(plan, "w") as f:
            f.write("# Actual Plan")

        config = _make_config()
        # No marker → scan fallback
        path, text, _ = _find_plan_file(self._session_id, self._tmpdir, config)
        self.assertEqual(path, plan)
        self.assertIn("Actual Plan", text)

    def test_is_plan_filename_rejects_metadata(self):
        self.assertFalse(_is_plan_filename("README.md"))
        self.assertFalse(_is_plan_filename("template.md"))
        self.assertFalse(_is_plan_filename("SAMPLE-plan.md"))
        self.assertFalse(_is_plan_filename("example.md"))
        self.assertFalse(_is_plan_filename("backup-old.md"))
        self.assertFalse(_is_plan_filename(".hidden.md"))
        self.assertTrue(_is_plan_filename("my-cool-plan.md"))
        self.assertTrue(_is_plan_filename("parsed-herding-honey.md"))


class TestMarkerOnlyMode(unittest.TestCase):
    """Test 7: _PLANMAN_DEBUG_MARKER_ONLY env var disables scan."""

    def setUp(self):
        self._session_id = f"test-monly-{os.getpid()}-{id(self)}"
        self._tmpdir = tempfile.mkdtemp()
        self._plans_dir = os.path.join(self._tmpdir, ".claude", "plans")
        os.makedirs(self._plans_dir)
        os.environ["_PLANMAN_DEBUG_MARKER_ONLY"] = "1"

    def tearDown(self):
        os.environ.pop("_PLANMAN_DEBUG_MARKER_ONLY", None)
        _cleanup_marker(self._session_id)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_marker_only_no_marker_returns_none(self):
        """No marker + marker_only debug env → (None, None, None)."""
        # Plan file exists but shouldn't be found
        plan = os.path.join(self._plans_dir, "plan.md")
        with open(plan, "w") as f:
            f.write("# A Plan")

        config = _make_config()
        path, text, skip = _find_plan_file(self._session_id, self._tmpdir, config)
        self.assertIsNone(path)
        self.assertIsNone(text)
        self.assertIsNone(skip)

    def test_marker_only_with_valid_marker(self):
        """Valid marker + marker_only debug env → returns marker's file."""
        plan = os.path.join(self._plans_dir, "plan.md")
        with open(plan, "w") as f:
            f.write("# Marker Plan")

        _write_marker(self._session_id, plan, timestamp=time.time())

        config = _make_config()
        path, text, _ = _find_plan_file(self._session_id, self._tmpdir, config)
        self.assertIsNotNone(path)
        self.assertIn("Marker Plan", text)


class TestConcurrentSessions(unittest.TestCase):
    """Test 8: Two concurrent sessions → each gets its own file."""

    def setUp(self):
        self._session_a = f"test-concurrent-a-{os.getpid()}"
        self._session_b = f"test-concurrent-b-{os.getpid()}"
        self._tmpdir = tempfile.mkdtemp()
        self._plans_dir = os.path.join(self._tmpdir, ".claude", "plans")
        os.makedirs(self._plans_dir)

    def tearDown(self):
        _cleanup_marker(self._session_a)
        _cleanup_marker(self._session_b)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_each_session_gets_own_file(self):
        file_a = os.path.join(self._plans_dir, "plan-a.md")
        file_b = os.path.join(self._plans_dir, "plan-b.md")
        with open(file_a, "w") as f:
            f.write("# Plan A")
        with open(file_b, "w") as f:
            f.write("# Plan B")

        _write_marker(self._session_a, file_a, timestamp=time.time())
        _write_marker(self._session_b, file_b, timestamp=time.time())

        config = _make_config()
        path_a, text_a, _ = _find_plan_file(self._session_a, self._tmpdir, config)
        path_b, text_b, _ = _find_plan_file(self._session_b, self._tmpdir, config)

        self.assertEqual(os.path.realpath(path_a), os.path.realpath(file_a))
        self.assertEqual(os.path.realpath(path_b), os.path.realpath(file_b))
        self.assertIn("Plan A", text_a)
        self.assertIn("Plan B", text_b)


class TestMalformedMarker(unittest.TestCase):
    """Tests 9-12: Malformed marker handling."""

    def setUp(self):
        self._session_id = f"test-malformed-{os.getpid()}-{id(self)}"
        self._tmpdir = tempfile.mkdtemp()
        self._plans_dir = os.path.join(self._tmpdir, ".claude", "plans")
        os.makedirs(self._plans_dir)
        # Create a plan file for scan fallback
        self._plan_file = os.path.join(self._plans_dir, "fallback.md")
        with open(self._plan_file, "w") as f:
            f.write("# Fallback Plan")

    def tearDown(self):
        _cleanup_marker(self._session_id)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_missing_timestamp_falls_to_scan(self):
        """Marker JSON has plan_file_path but no timestamp → scan fallback."""
        _write_marker(self._session_id, self._plan_file, timestamp=None)
        # Rewrite without timestamp
        safe_id = safe_session_id(self._session_id)
        marker_path = MARKER_TEMPLATE.format(session_id=safe_id)
        with open(marker_path, "w") as f:
            json.dump({"plan_file_path": self._plan_file}, f)

        config = _make_config()
        path, text, _ = _find_plan_file(self._session_id, self._tmpdir, config)
        # Falls to scan since marker has no timestamp
        self.assertIsNotNone(path)
        self.assertIn("Fallback Plan", text)

    def test_non_numeric_timestamp_falls_to_scan(self):
        """timestamp: "not-a-number" → scan fallback."""
        safe_id = safe_session_id(self._session_id)
        marker_path = MARKER_TEMPLATE.format(session_id=safe_id)
        with open(marker_path, "w") as f:
            json.dump({"plan_file_path": self._plan_file, "timestamp": "not-a-number"}, f)

        config = _make_config()
        path, text, _ = _find_plan_file(self._session_id, self._tmpdir, config)
        self.assertIsNotNone(path)
        self.assertIn("Fallback Plan", text)

    def test_corrupt_json_falls_to_scan(self):
        """Corrupt JSON marker → scan fallback."""
        safe_id = safe_session_id(self._session_id)
        marker_path = MARKER_TEMPLATE.format(session_id=safe_id)
        with open(marker_path, "w") as f:
            f.write("{bad json")

        config = _make_config()
        path, text, _ = _find_plan_file(self._session_id, self._tmpdir, config)
        self.assertIsNotNone(path)
        self.assertIn("Fallback Plan", text)

    def test_future_timestamp_clamped_marker_trusted(self):
        """Future timestamp → clamped to 0, marker trusted if file exists."""
        future_ts = time.time() + 3600
        _write_marker(self._session_id, self._plan_file, timestamp=future_ts)

        # Create a second plan file that scan would find
        scan_file = os.path.join(self._plans_dir, "scan-result.md")
        with open(scan_file, "w") as f:
            f.write("# Scan Result")
        os.utime(scan_file, (time.time(), time.time()))
        os.utime(self._plan_file, (time.time() - 100, time.time() - 100))

        config = _make_config()
        path, text, _ = _find_plan_file(self._session_id, self._tmpdir, config)
        # ts clamped to 0 → expired=(0>0)=False → marker used
        self.assertEqual(os.path.realpath(path), os.path.realpath(self._plan_file))


class TestScanScopeProjectLocalFirst(unittest.TestCase):
    """Test 13: Scan scope — project-local first."""

    def setUp(self):
        self._session_id = f"test-scope-{os.getpid()}-{id(self)}"
        self._tmpdir = tempfile.mkdtemp()
        self._plans_dir = os.path.join(self._tmpdir, ".claude", "plans")
        os.makedirs(self._plans_dir)

    def tearDown(self):
        _cleanup_marker(self._session_id)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_project_local_preferred(self):
        """Project-local plan preferred over home dir plan during scan."""
        local_plan = os.path.join(self._plans_dir, "local-plan.md")
        with open(local_plan, "w") as f:
            f.write("# Local Plan")

        config = _make_config()
        # No marker → scan fallback → finds project-local
        path, text, _ = _find_plan_file(self._session_id, self._tmpdir, config)
        self.assertEqual(path, local_plan)
        self.assertIn("Local Plan", text)


if __name__ == "__main__":
    unittest.main()
