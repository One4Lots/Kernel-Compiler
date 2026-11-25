"""RC versioning, literal branch directories, and publisher path safety."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github/scripts/kernel_build"))
import naming
import prepare
import telegram
from publication_settings import PublicationSettings
from support import BundleFixture, inputs


class NamingTests(unittest.TestCase):
    def test_rc_replaces_time_and_branch_slash_is_a_directory(self):
        path = naming.package_relative_path(
            "17.0/modern-sched", "Alecto", "miatoll", "rc1", "25112026"
        )
        self.assertEqual(path.as_posix(), "17.0/modern-sched-Alecto-miatoll-rc1-25112026.zip")
        self.assertEqual(path.parent.as_posix(), "17.0")
        self.assertEqual(path.name, "modern-sched-Alecto-miatoll-rc1-25112026.zip")
        self.assertNotIn("0429", path.name)
        self.assertNotIn("17.0-modern-sched", path.as_posix())

    def test_multi_level_branch_is_preserved_without_changing_zip_payload_root(self):
        path = naming.package_relative_path(
            "feature/security/next", "Nova Kernel", "phone", "v2.0-rc3", "25112026"
        )
        self.assertEqual(
            path.as_posix(), "feature/security/next-Nova-Kernel-phone-v2.0-rc3-25112026.zip"
        )
        self.assertEqual(
            naming.package_relative_path(
                "staging", "Nova", "phone", "rc2", "25112026"
            ).parent.as_posix(),
            ".",
        )

    def test_one_rc_is_used_as_the_release_tag_in_every_publication_mode(self):
        for destination in (
            "personal only",
            "artifacts only",
            "personal+channel",
            "personal+github",
            "personal+channel+github",
            "channel+github",
        ):
            value = PublicationSettings.from_inputs(
                dict(
                    publish_to=destination,
                    kernel_rc="v1.0-rc2",
                    release_repository="owner/releases",
                )
            )
            self.assertEqual(value.tag, "v1.0-rc2")
        self.assertEqual(PublicationSettings.from_inputs({}).tag, "rc1")

    def test_invalid_rc_is_rejected_even_when_github_publishing_is_disabled(self):
        for rc in (
            "",
            "rc/1",
            "../rc1",
            "rc..1",
            "rc1.lock",
            "rc1.",
            "-rc1",
            "rc 1",
            "rc1;command",
            "x" * 49,
            None,
        ):
            with self.subTest(rc=rc), self.assertRaises(ValueError):
                prepare.make_matrix(inputs(kernel_rc=rc))

    def test_unsafe_branches_are_rejected_not_silently_slugified(self):
        for branch in (
            "/absolute",
            "../escape",
            "a/../escape",
            "a//b",
            "feature/$(command)",
            "branch;other",
            ".hidden/branch",
            "a\\b",
        ):
            with self.subTest(branch=branch), self.assertRaises(ValueError):
                naming.branch_path(branch)

    def test_impossible_dates_and_overlong_basenames_fail_before_build(self):
        for date in ("31022026", "251126", "25-11-2026"):
            with self.subTest(date=date), self.assertRaises(ValueError):
                naming.package_relative_path("staging", "Alecto", "miatoll", "rc1", date)
        with self.assertRaisesRegex(ValueError, "255-byte"):
            prepare.make_matrix(
                inputs(
                    single_branch="x" * 120,
                    kernel_name="K" * 48,
                    device_name="d" * 48,
                    kernel_rc="r" * 48,
                )
            )

    def test_telegram_omits_rc_and_branch_without_changing_package_naming(self):
        meta = dict(
            kernel_name="Nova",
            device_name="phone",
            kernel_rc="rc7",
            duration_seconds=60,
            date="25112026",
            time="04:29 PM",
            built_at="2026-11-25T16:29:00+06:00",
            zip_bytes=1234,
            md5="a" * 32,
        )
        env = dict(
            TOOLCHAIN="android-clang",
            BRANCH="17.0/modern-sched",
            BUILD_MODE="single",
            REPO_VISIBILITY="public",
            DEFCONFIG="vendor/defconfig",
            IMAGE_TYPE="Image.gz.dtb",
        )
        caption = telegram.make_caption(
            meta, env, "Android clang version 22", "LLD 22", "Commit message"
        )
        self.assertNotIn("RC:", caption)
        self.assertNotIn("17.0/modern-sched", caption)
        self.assertEqual(caption.count("<code>"), 2)


class NestedReleasePathTests(BundleFixture, unittest.TestCase):
    def test_nested_branch_bundle_loads_and_github_asset_is_only_the_basename(self):
        manifest, archive = self.create_bundle("nested", "17.0/modern-sched")
        manifests = self.load()
        self.assertEqual(manifests[0]["archive"], archive)
        import publication

        assets = publication.stage_assets(manifests, self.root / "assets")
        self.assertEqual(assets[0].name, "modern-sched-Nova-phone-v1.0-25112026.zip")
        self.assertNotIn("/", assets[0].name)
        self.assertEqual(
            json.loads(manifest.read_text())["zip_relative_path"],
            "17.0/modern-sched-Nova-phone-v1.0-25112026.zip",
        )

    def test_traversal_cross_branch_path_and_wrong_rc_are_rejected(self):
        manifest, _ = self.create_bundle("nested", "17.0/modern-sched")
        original = json.loads(manifest.read_text())
        for changes in (
            dict(zip_relative_path="../../outside.zip"),
            dict(zip_relative_path="18.0/modern-sched-Nova-phone-v1.0-25112026.zip"),
            dict(kernel_rc="rc-other"),
        ):
            manifest.write_text(json.dumps(dict(original, **changes)))
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.load()

    def test_symlinked_branch_directory_cannot_escape_the_bundle(self):
        _, archive = self.create_bundle("nested", "17.0/modern-sched")
        outside = self.root / "outside"
        archive.parent.rename(outside)
        archive.parent.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "unsafe release ZIP"):
            self.load()


if __name__ == "__main__":
    unittest.main()
