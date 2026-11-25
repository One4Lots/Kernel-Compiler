"""Shared workflow output, diagnostics, and streaming checksum tests."""

from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github/scripts/kernel_build"))

import runtime


class RuntimeTests(unittest.TestCase):
    def test_streaming_hashes_match_reference_for_large_and_empty_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "kernel.zip"
            for data in (b"", b"kernel-image" * 300_000):
                path.write_bytes(data)
                with patch.object(
                    Path, "read_bytes", side_effect=AssertionError("Do not buffer the entire ZIP")
                ):
                    for algorithm in ("md5", "sha256"):
                        expected = hashlib.new(algorithm, data, usedforsecurity=False).hexdigest()
                        self.assertEqual(runtime.file_digest(path, algorithm), expected)

    def test_single_line_outputs_append_without_injecting_extra_records(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "output"
            with patch.dict(os.environ, GITHUB_OUTPUT=str(target)):
                runtime.output("matrix", '{"include":[]}')
                runtime.output("history_depth", "6")
                for name, value in (
                    ("invalid\nname", "value"),
                    ("", "value"),
                    ("name=other", "value"),
                    ("name", "value\nother=bad"),
                    ("name", "value\rother=bad"),
                ):
                    with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                        runtime.output(name, value)
            self.assertEqual(target.read_text(), 'matrix={"include":[]}\nhistory_depth=6\n')

    def test_expected_failures_are_escaped_and_exit_nonzero(self):
        for error in (
            ValueError("bad%\n::warning::untrusted\r"),
            KeyError("missing"),
            TypeError("bad value"),
            OSError("missing file"),
            subprocess.CalledProcessError(1, ["zip", "archive"]),
        ):

            def fail(error=error):
                raise error

            diagnostics = io.StringIO()
            with (
                self.subTest(error=type(error)),
                redirect_stderr(diagnostics),
                self.assertRaises(SystemExit) as result,
            ):
                runtime.run(fail)
            self.assertEqual(result.exception.code, 1)
            text = diagnostics.getvalue()
            self.assertEqual(len(text.splitlines()), 1)
            self.assertTrue(text.startswith("::error::"))
            if isinstance(error, ValueError):
                self.assertIn("%25%0A::warning::untrusted%0D", text)

    def test_success_does_not_exit_or_emit_errors(self):
        diagnostics = io.StringIO()
        with redirect_stderr(diagnostics):
            runtime.run(lambda: None)
        self.assertEqual(diagnostics.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
