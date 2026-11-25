"""Dependency caching and font-free runtime regression checks."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".github/scripts/kernel_build"
sys.path.insert(0, str(SCRIPTS))


class RuntimeIndependenceTests(unittest.TestCase):
    def test_runtime_imports_without_site_packages_or_font_renderer(self):
        result = subprocess.run(
            [
                sys.executable,
                "-S",
                "-c",
                "import sys; sys.path.insert(0, sys.argv[1]); import installer, package, telegram, telegram_release, presentation; print('stdlib runtime ready')",
                str(SCRIPTS),
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("stdlib runtime ready", result.stdout)

    def test_font_assets_renderer_and_dependency_are_removed(self):
        self.assertFalse((SCRIPTS / "fonts").exists())
        self.assertFalse((SCRIPTS / "banner.py").exists())
        for path in (
            SCRIPTS / "packages.txt",
            ROOT / "tests/requirements.txt",
            ROOT / ".github/workflows/validate.yml",
        ):
            self.assertNotIn("pyfiglet", path.read_text().lower())


class DependencyPhaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        binary = self.root / "bin"
        binary.mkdir()
        sudo = binary / "sudo"
        sudo.write_text("""#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
with open(os.environ['APT_CALLS'], 'a') as output:
    output.write(json.dumps(args) + '\\n')
if 'apt-get' in args:
    if args[-1] == 'update':
        sys.exit(100 if os.environ.get('FAIL_UPDATE') else 0)
    if 'install' in args:
        marker = pathlib.Path(os.environ['APT_CALLS'] + '.install-attempted')
        fail = bool(os.environ.get('FAIL_INSTALL_ALWAYS') or
                    (os.environ.get('FAIL_INSTALL_ONCE') and not marker.exists()))
        marker.touch()
        sys.exit(100 if fail else 0)
elif args and args[0] == 'chown':
    sys.exit(0)
sys.exit(90)
""")
        sudo.chmod(0o755)
        ccache = binary / "ccache"
        ccache.write_text("#!/bin/sh\nexit 0\n")
        ccache.chmod(0o755)
        self.cache = self.root / "apt-cache"
        self.calls_path = self.root / "apt-calls.jsonl"
        self.environment.update(
            PATH=f"{binary}:{Path(sys.executable).parent}:{os.environ['PATH']}",
            APT_CACHE_DIR=str(self.cache),
            APT_CALLS=str(self.calls_path),
        )

    def execute(self, **changes):
        return subprocess.run(
            ["bash", str(SCRIPTS / "dependencies.sh")],
            cwd=self.root,
            env=dict(self.environment, **changes),
            capture_output=True,
            text=True,
        )

    def calls(self, action):
        if not self.calls_path.exists():
            return []
        return [
            args
            for line in self.calls_path.read_text().splitlines()
            if "apt-get" in (args := json.loads(line)) and action in args
        ]

    def stamp(self, value):
        self.cache.mkdir(exist_ok=True)
        (self.cache / "updated-at").write_text(str(value) + "\n")

    def test_cold_cache_installs_dependencies_without_font_renderer(self):
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls("update")), 1)
        self.assertEqual(len(self.calls("install")), 1)
        self.assertNotIn("python3-pyfiglet", self.calls("install")[0])
        self.assertIn("Dependencies ready", result.stdout)
        self.assertNotIn("Traceback", result.stderr)

    def test_fresh_cache_skips_refresh_but_still_installs(self):
        self.stamp(int(time.time()))
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls("update")), 0)
        self.assertEqual(len(self.calls("install")), 1)
        self.assertIn("Dependencies ready", result.stdout)

    def test_expired_cache_refreshes_indexes(self):
        self.stamp(int(time.time()) - 604801)
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls("update")), 1)

    def test_invalid_timestamp_refreshes_indexes(self):
        self.stamp("not-a-timestamp")
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls("update")), 1)

    def test_leading_zero_timestamp_is_decimal_and_reused(self):
        self.stamp(f"0{int(time.time())}")
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls("update")), 0)
        self.assertNotIn("value too great for base", result.stderr)

    def test_overlong_timestamp_is_discarded_without_arithmetic_overflow(self):
        self.stamp("9" * 100)
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls("update")), 1)

    def test_future_timestamp_refreshes_indexes(self):
        self.stamp(int(time.time()) + 86400)
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls("update")), 1)

    def test_install_failure_refreshes_once_then_retries(self):
        self.stamp(int(time.time()))
        result = self.execute(FAIL_INSTALL_ONCE="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls("update")), 1)
        self.assertEqual(len(self.calls("install")), 2)
        self.assertIn("Dependencies ready", result.stdout)

    def test_repeated_install_failure_does_not_report_success(self):
        self.stamp(int(time.time()))
        result = self.execute(FAIL_INSTALL_ALWAYS="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.calls("install")), 2)
        self.assertNotIn("Dependencies ready", result.stdout)

    def test_index_refresh_failure_stops_before_package_installation(self):
        result = self.execute(FAIL_UPDATE="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.calls("install")), 0)
        self.assertNotIn("Dependencies ready", result.stdout)


if __name__ == "__main__":
    unittest.main()
