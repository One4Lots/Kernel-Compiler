"""Exercise CLI wiring without network calls or destination mutations."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github/scripts/kernel_build"))

import prepare
import publication
from delivery_modes import MODES, delivery_plan
from support import BundleFixture, inputs

SHA = "a" * 40


class EntrypointTests(BundleFixture, unittest.TestCase):
    def test_command_line_help_and_invalid_commands_are_actionable(self):
        for filename in ("prepare.py", "publication.py"):
            script = ROOT / ".github/scripts/kernel_build" / filename
            for arguments, status in ((["--help"], 0), ([], 2), (["invalid-command"], 2)):
                with self.subTest(script=filename, arguments=arguments):
                    result = subprocess.run(
                        [sys.executable, str(script), *arguments], capture_output=True, text=True
                    )
                    self.assertEqual(result.returncode, status)
                    self.assertIn("usage:", result.stdout + result.stderr)
                    self.assertNotIn("Traceback", result.stderr)

    def test_toolchain_command_emits_selected_fingerprint_and_cache_epoch(self):
        headers = {"etag": '"pinned-release"', "content-length": "1234"}
        target = self.root / "outputs"
        with (
            patch.dict(os.environ, TOOLCHAIN="android-clang", GITHUB_OUTPUT=str(target)),
            patch.object(sys, "argv", ["prepare.py", "toolchain"]),
            patch.object(prepare, "release_headers", return_value=headers),
            redirect_stdout(io.StringIO()),
        ):
            prepare.main()
        records = dict(line.split("=", 1) for line in target.read_text().splitlines())
        url, binary = prepare.TOOLCHAINS["android-clang"]
        self.assertEqual(records["hash"], prepare.fingerprint(url, headers))
        self.assertEqual(records["url"], url)
        self.assertEqual(records["bin"], binary)
        self.assertRegex(records["apt_epoch"], r"^\d{4}-W\d{2}$")

    def test_changed_toolchain_release_fails_verification(self):
        headers = {"etag": "current"}
        environment = dict(
            TOOLCHAIN_URL="https://github.com/owner/compiler/releases/download/v1/clang.tar.zst",
            TOOLCHAIN_HASH="not-the-current-hash",
        )
        with (
            patch.dict(os.environ, environment),
            patch.object(sys, "argv", ["prepare.py", "verify-toolchain"]),
            patch.object(prepare, "release_headers", return_value=headers),
        ):
            with self.assertRaisesRegex(ValueError, "changed during this run"):
                prepare.main()
            os.environ["TOOLCHAIN_HASH"] = prepare.fingerprint(
                environment["TOOLCHAIN_URL"], headers
            )
            prepare.main()

    def test_github_preflight_writes_frozen_target_and_serialization_key(self):
        target = self.root / "outputs"
        values = inputs(publish_to="personal+github", release_repository="owner/releases")
        client = Mock()
        client.api.side_effect = [
            {"default_branch": "main", "permissions": {"push": True}},
            None,
            None,
            {"sha": SHA},
        ]
        environment = dict(
            INPUTS_JSON=json.dumps(values), GH_TOKEN="test-token", GITHUB_OUTPUT=str(target)
        )
        with (
            patch.dict(os.environ, environment, clear=True),
            patch.object(sys, "argv", ["publication.py", "preflight"]),
            patch.object(publication, "GithubClient", return_value=client),
            redirect_stdout(io.StringIO()),
        ):
            publication.main()
        records = dict(line.split("=", 1) for line in target.read_text().splitlines())
        self.assertEqual(records["release_repository"], "owner/releases")
        self.assertEqual(records["release_target_sha"], SHA)
        self.assertEqual(records["release_skip"], "false")
        self.assertRegex(records["release_lock"], r"^[a-f0-9]{64}$")
        client.upload.assert_not_called()
        self.assertTrue(all("POST" not in call.args for call in client.api.call_args_list))

    def test_publish_entrypoint_verifies_stages_and_summarizes_a_complete_build_set(self):
        manifest, _ = self.create_bundle("first", "17.0/modern-sched")
        bundle = manifest.parent.parent
        for child in list(bundle.iterdir()):
            child.rename(self.downloads / child.name)
        bundle.rmdir()
        self.downloads.rename(self.root / "release-downloads")
        values = inputs(
            publish_to="personal+github",
            release_repository="owner/releases",
            kernel_name="Nova",
            device_name="phone",
            kernel_rc="v1.0",
            kernel_repository="https://github.com/owner/kernel",
            commit_count=3,
        )
        values.update(single_branch="17.0/modern-sched", defconfig="vendor/defconfig")
        self.expected = prepare.make_matrix(values)["include"]
        saved = self.root / "release-downloads/logs/release-manifest.json"
        meta = json.loads(saved.read_text())
        meta["matrix_id"] = self.expected[0]["id"]
        saved.write_text(json.dumps(meta))
        summary = self.root / "summary"
        environment = dict(
            INPUTS_JSON=json.dumps(values),
            MATRIX_JSON=json.dumps({"include": self.expected}),
            RELEASE_TARGET_SHA=SHA,
            GH_TOKEN="test-token",
            GITHUB_STEP_SUMMARY=str(summary),
        )
        previous = Path.cwd()
        try:
            os.chdir(self.root)
            with (
                patch.dict(os.environ, environment, clear=True),
                patch.object(sys, "argv", ["publication.py", "publish"]),
                patch.object(publication, "GithubClient"),
                patch.object(
                    publication,
                    "publish",
                    return_value="https://github.com/owner/releases/releases/tag/v1.0",
                ) as publish,
                redirect_stdout(io.StringIO()),
            ):
                publication.main()
            args = publish.call_args.args
            self.assertEqual(args[2], SHA)
            self.assertEqual(args[3], "Nova")
            self.assertIn("modern", args[4])
            self.assertEqual(
                [path.name for path in args[5]],
                ["modern-sched-Nova-phone-v1.0-25112026.zip", "SHA256SUMS.txt"],
            )
            self.assertTrue(all(path.is_file() for path in args[5]))
            self.assertIn(
                "https://github.com/owner/releases/releases/tag/v1.0", summary.read_text()
            )
            self.assertTrue((self.root / "release-assets/release-notes.md").is_file())
        finally:
            os.chdir(previous)

    def test_github_publisher_rejects_changed_or_partial_matrix_before_staging(self):
        values = inputs(
            publish_to="personal+github",
            release_repository="owner/releases",
            build_mode="multi",
            multi_branches_json=json.dumps([{"branch": "staging"}, {"branch": "other"}]),
        )
        expected = prepare.make_matrix(values)["include"]
        for matrix in (
            [],
            expected[:1],
            list(reversed(expected)),
            [dict(expected[0], branch="changed")],
        ):
            with (
                self.subTest(matrix=matrix),
                patch.dict(
                    os.environ,
                    INPUTS_JSON=json.dumps(values),
                    MATRIX_JSON=json.dumps({"include": matrix}),
                    GH_TOKEN="fixture",
                ),
                patch.object(sys, "argv", ["publication.py", "publish"]),
                patch.object(publication, "GithubClient"),
                patch.object(publication, "stage_assets") as stage,
                patch.object(publication, "publish") as publish,
                self.assertRaisesRegex(ValueError, "matrix differs"),
            ):
                publication.main()
            stage.assert_not_called()
            publish.assert_not_called()

    def test_all_modes_gate_github_before_any_api_client_is_created(self):
        for mode in MODES:
            selected = delivery_plan(mode).github
            for command in ("preflight", "publish"):
                with (
                    self.subTest(mode=mode, command=command),
                    patch.dict(
                        os.environ,
                        INPUTS_JSON=json.dumps(
                            inputs(publish_to=mode, release_repository="owner/releases")
                        ),
                        GH_TOKEN="",
                    ),
                    patch.object(sys, "argv", ["publication.py", command]),
                    patch.object(publication, "GithubClient") as client,
                    self.assertRaisesRegex(
                        ValueError, "GH_RELEASE_TOKEN" if selected else "not selected"
                    ),
                ):
                    publication.main()
                client.assert_not_called()

    def test_publication_requires_selected_destination_and_credentials(self):
        for values, expected in (
            (inputs(publish_to="artifacts only"), "not selected"),
            (
                inputs(publish_to="personal+github", release_repository="owner/releases"),
                "GH_RELEASE_TOKEN",
            ),
        ):
            with (
                self.subTest(values=values),
                patch.dict(os.environ, INPUTS_JSON=json.dumps(values), GH_TOKEN=""),
                patch.object(sys, "argv", ["publication.py", "preflight"]),
                self.assertRaisesRegex(ValueError, expected),
            ):
                publication.main()


if __name__ == "__main__":
    unittest.main()
