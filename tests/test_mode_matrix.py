"""Cross-product handoff scenarios: real files/checksums, no external writes or compilation."""

from __future__ import annotations

import hashlib
import html
import io
import itertools
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github/scripts/kernel_build"))

import prepare
import publication
import telegram_delivery
import telegram_release
from configuration import resolve_dispatch
from naming import package_relative_path
from telegram import utf16_length

# Independent expectation: changing production routing must not change this oracle.
DESTINATIONS = {
    "artifacts only": (False, False, False),
    "personal only": (True, False, False),
    "personal+channel": (True, True, False),
    "personal+github": (True, False, True),
    "channel+github": (False, True, True),
    "personal+channel+github": (True, True, True),
}
VARIANTS = ("single-flat", "single-nested", "multi-nested")


def write_bundle(root, entry, values):
    logs = root / "logs"
    logs.mkdir(parents=True)
    relative = package_relative_path(
        entry["branch"],
        values["kernel_name"],
        values["device_name"],
        values["kernel_rc"],
        "25112026",
    )
    archive = root / "artifacts" / relative
    archive.parent.mkdir(parents=True)
    with ZipFile(archive, "w") as stream:
        stream.writestr("FIXTURE-NOT-FLASHABLE.txt", entry["image_type"])
    commits = [
        {"sha": str(index) * 40, "subject": f"Fix {index}: <scheduler> & Unicode 😀"}
        for index in range(1, 4)
    ]
    meta = dict(
        schema=3,
        matrix_id=entry["id"],
        branch=entry["branch"],
        defconfig=entry["defconfig"],
        image_type=entry["image_type"],
        kernel_name=values["kernel_name"],
        device_name=values["device_name"],
        kernel_rc=values["kernel_rc"],
        source_repository=values["kernel_repository"],
        zip_relative_path=relative.as_posix(),
        zip_name=archive.name,
        zip_bytes=archive.stat().st_size,
        sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        date="25112026",
        time="04:29 PM",
        built_at="2026-11-25T16:29:00+06:00",
        duration_seconds=123,
        commits=commits,
    )
    (logs / "release-manifest.json").write_text(json.dumps(meta), encoding="utf-8")
    compiler = "Android" if values["toolchain"] == "android-clang" else "Neutron"
    (logs / "clang-version.txt").write_text(f"{compiler} clang version 22.0.0\n")
    (logs / "lld-version.txt").write_text("LLD 22.0.0\n")
    (logs / "commit-message.txt").write_text(
        "Fix scheduling\n\nFull body with <tags> & Unicode 😀\n", encoding="utf-8"
    )
    (logs / "recent-commits.json").write_text(json.dumps(commits), encoding="utf-8")


class ModeMatrixTests(unittest.TestCase):
    def test_all_432_delivery_build_image_compiler_history_visibility_and_layout_scenarios(self):
        executed = 0
        scenarios = itertools.product(
            DESTINATIONS,
            VARIANTS,
            prepare.IMAGE_TYPES,
            prepare.TOOLCHAINS,
            ("latest-message", "recent-commits"),
            ("public", "private"),
        )
        for mode, variant, image, compiler, history, visibility in scenarios:
            with (
                self.subTest(
                    mode=mode,
                    variant=variant,
                    image=image,
                    compiler=compiler,
                    history=history,
                    visibility=visibility,
                ),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                personal, channel, github = DESTINATIONS[mode]
                values = resolve_dispatch(
                    dict(
                        publish_to=mode,
                        build_mode="multi" if variant.startswith("multi") else "single",
                        single_branch="17.0/modern-sched",
                        multi_branches_json=json.dumps(
                            [{"branch": "17.0/modern-sched"}, {"branch": "staging"}]
                        ),
                        image_type=image,
                        toolchain=compiler,
                        changelog_mode=history,
                        repo_visibility=visibility,
                        commit_count="3",
                        release_repository="fixture/releases",
                    )
                )
                matrix = prepare.make_matrix(values)
                environment = dict(
                    INPUTS_JSON=json.dumps(values),
                    MATRIX_JSON=json.dumps(matrix),
                    GITHUB_REPOSITORY="fixture/workflow",
                    GITHUB_OUTPUT=str(root / "outputs"),
                    GITHUB_STEP_SUMMARY=str(root / "summary"),
                    RELEASE_TARGET_SHA="a" * 40,
                    TELEGRAM_DELIVERY_CONFIRMED="true" if personal else "false",
                    TELEGRAM_CHANNEL_CONFIRMED="true" if channel else "false",
                )
                if visibility == "private":
                    environment["GH_PAT"] = "source-fixture"
                if github:
                    environment.update(GH_RELEASE_TOKEN="write-fixture", GH_TOKEN="write-fixture")
                if personal or channel:
                    environment["TELEGRAM_BOT_TOKEN"] = "123:fixture"
                if personal:
                    environment["TELEGRAM_CHAT_ID"] = "123456789"
                if channel:
                    environment["TELEGRAM_CHANNEL_ID"] = "-100123"
                previous = Path.cwd()
                try:
                    os.chdir(root)
                    with (
                        patch.dict(os.environ, environment, clear=True),
                        redirect_stdout(io.StringIO()),
                    ):
                        with patch.object(sys, "argv", ["prepare.py", "matrix"]):
                            prepare.main()
                        records = dict(
                            line.split("=", 1)
                            for line in (root / "outputs").read_text().splitlines()
                        )
                        self.assertEqual(json.loads(records["matrix"]), matrix)
                        self.assertEqual(
                            json.loads(records["delivery"]),
                            dict(
                                personal=personal,
                                channel=channel,
                                github=github,
                                bundles=personal or channel or github,
                            ),
                        )
                        if personal or channel or github:
                            for entry in reversed(matrix["include"]):
                                destination = root / "release-downloads"
                                if variant != "single-flat":
                                    destination /= f"release-bundle-{entry['id']}"
                                write_bundle(destination, entry, values)
                        with (
                            patch.object(telegram_release, "send_document") as send,
                            patch.object(telegram_release, "send_changelog"),
                        ):
                            telegram_delivery.main()
                            telegram_release.main()
                        expected_destinations = (
                            ["123456789"] * len(matrix["include"]) if personal else []
                        ) + (["-100123"] * len(matrix["include"]) if channel else [])
                        self.assertEqual(
                            [call.args[3] for call in send.call_args_list], expected_destinations
                        )
                        for call in send.call_args_list:
                            self.assertTrue(call.args[0].is_file())
                            self.assertLessEqual(utf16_length(call.args[1]), 1024)
                            caption = call.args[1]
                            self.assertTrue(
                                caption.startswith("<b>✅ Kernel Compiled Successfully</b>\n\n")
                            )
                            for label in (
                                "🔩 <b>ToolChain:</b>",
                                "🧶 <b>Linker:</b>",
                                "⏳ <b>Build Time:</b>",
                                "〽️ <b>Zip Size:</b>",
                                "📅 <b>Date:</b>",
                                "📦 <b>Image:</b>",
                            ):
                                self.assertIn(label, caption)
                            for removed in ("Branch:", "Local Time:", "⚡", " • "):
                                self.assertNotIn(removed, caption)
                            self.assertIn("\n\n<b>🔑 MD5 hash</b>\n<pre><code>", caption)
                            blocks = re.findall(
                                r"<pre><code>(.*?)</code></pre>", caption, re.DOTALL
                            )
                            self.assertEqual(len(blocks), 2)
                            self.assertEqual(
                                blocks[0], hashlib.md5(call.args[0].read_bytes()).hexdigest()
                            )
                            self.assertIn("Fix", html.unescape(blocks[1]))
                            self.assertIn(
                                "Neutron" if compiler == "neutron-clang" else "Android",
                                call.args[1],
                            )
                        with (
                            patch.object(sys, "argv", ["publication.py", "publish"]),
                            patch.object(publication, "GithubClient") as client,
                            patch.object(
                                publication,
                                "publish",
                                return_value="https://github.com/fixture/releases/releases/tag/rc1",
                            ) as publish,
                        ):
                            if github:
                                publication.main()
                                publish.assert_called_once()
                                self.assertEqual(
                                    len(publish.call_args.args[5]), len(matrix["include"]) + 1
                                )
                                checksums = (
                                    (root / "release-assets/SHA256SUMS.txt")
                                    .read_text()
                                    .splitlines()
                                )
                                self.assertEqual(len(checksums), len(matrix["include"]))
                                for record in checksums:
                                    digest, name = record.split("  ", 1)
                                    self.assertEqual(
                                        digest,
                                        hashlib.sha256(
                                            (root / "release-assets" / name).read_bytes()
                                        ).hexdigest(),
                                    )
                            else:
                                with self.assertRaisesRegex(ValueError, "not selected"):
                                    publication.main()
                                client.assert_not_called()
                                publish.assert_not_called()
                finally:
                    os.chdir(previous)
                executed += 1
        self.assertEqual(executed, 432)

    def test_75_invalid_bundle_scenarios_block_every_selected_destination(self):
        faults = ("missing", "corrupt", "identity", "duplicate", "path")
        executed = 0
        for mode, variant, fault in itertools.product(list(DESTINATIONS)[1:], VARIANTS, faults):
            with (
                self.subTest(mode=mode, variant=variant, fault=fault),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                personal, channel, github = DESTINATIONS[mode]
                values = resolve_dispatch(
                    dict(
                        publish_to=mode,
                        build_mode="multi" if variant.startswith("multi") else "single",
                        single_branch="staging",
                        multi_branches_json=json.dumps(
                            [{"branch": "staging"}, {"branch": "17.0/modern-sched"}]
                        ),
                        release_repository="fixture/releases",
                    )
                )
                matrix = prepare.make_matrix(values)
                destinations = []
                for entry in matrix["include"]:
                    destination = root / "release-downloads"
                    if variant != "single-flat":
                        destination /= f"release-bundle-{entry['id']}"
                    write_bundle(destination, entry, values)
                    destinations.append(destination)
                target = destinations[-1]
                manifest = target / "logs/release-manifest.json"
                meta = json.loads(manifest.read_text())
                if fault == "missing":
                    manifest.unlink()
                elif fault == "corrupt":
                    (target / "artifacts" / meta["zip_relative_path"]).write_bytes(b"corrupt")
                elif fault == "identity":
                    manifest.write_text(json.dumps(dict(meta, branch="not-this-build")))
                elif fault == "path":
                    manifest.write_text(
                        json.dumps(dict(meta, zip_relative_path="../../outside.zip"))
                    )
                else:
                    duplicate = root / "release-downloads/duplicate"
                    shutil.copytree(target / "logs", duplicate / "logs")
                    shutil.copytree(target / "artifacts", duplicate / "artifacts")
                environment = dict(
                    INPUTS_JSON=json.dumps(values),
                    MATRIX_JSON=json.dumps(matrix),
                    TELEGRAM_BOT_TOKEN="123:fixture",
                    TELEGRAM_CHAT_ID="123456789",
                    TELEGRAM_CHANNEL_ID="-100123",
                    TELEGRAM_DELIVERY_CONFIRMED="true",
                    TELEGRAM_CHANNEL_CONFIRMED="true",
                    GH_TOKEN="write-fixture",
                    RELEASE_TARGET_SHA="a" * 40,
                    GITHUB_STEP_SUMMARY=str(root / "summary"),
                )
                previous = Path.cwd()
                try:
                    os.chdir(root)
                    with (
                        patch.dict(os.environ, environment, clear=True),
                        redirect_stdout(io.StringIO()),
                    ):
                        for enabled, publisher in (
                            (personal, telegram_delivery.main),
                            (channel, telegram_release.main),
                        ):
                            if not enabled:
                                continue
                            with (
                                patch.object(telegram_release, "send_document") as send,
                                patch.object(telegram_release, "send_changelog") as followup,
                                self.assertRaises(ValueError),
                            ):
                                publisher()
                            send.assert_not_called()
                            followup.assert_not_called()
                        if github:
                            with (
                                patch.object(sys, "argv", ["publication.py", "publish"]),
                                patch.object(publication, "GithubClient"),
                                patch.object(publication, "publish") as publish,
                                self.assertRaises(ValueError),
                            ):
                                publication.main()
                            publish.assert_not_called()
                            self.assertFalse((root / "release-assets").exists())
                finally:
                    os.chdir(previous)
                executed += 1
        self.assertEqual(executed, 75)


if __name__ == "__main__":
    unittest.main()
