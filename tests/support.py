"""Shared deterministic fixtures; no credentials, network, or kernel builds."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github/scripts/kernel_build"))

import publication
from naming import package_relative_path
from publication_settings import PublicationSettings


def inputs(**changes):
    value = dict(
        kernel_rc="rc1",
        kernel_name="Alecto",
        device_name="miatoll",
        device_codenames="curtana,gram,excalibur,joyeuse",
        supported_versions="12.0-17.0",
        anykernel_repository="https://github.com/One4Lots/AnyKernel3",
        build_mode="single",
        repo_visibility="public",
        toolchain="android-clang",
        kernel_repository="https://github.com/One4Lots/Alecto_kernel_qcom",
        single_branch="staging",
        defconfig="vendor/xiaomi/miatoll_defconfig",
        image_type="Image.gz.dtb",
        multi_branches_json="[]",
    )
    value.update(changes)
    return value


def settings(policy="fail", **changes):
    values = dict(
        publish_to="personal+github",
        changelog_mode="latest-message",
        commit_count=3,
        release_repository="owner/releases",
        kernel_rc="v1.0",
        release_target="main",
        duplicate_release=policy,
    )
    values.update(changes)
    return PublicationSettings.from_inputs(values)


class BundleFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.downloads = self.root / "downloads"
        self.expected = []

    def create_bundle(self, name, branch="staging"):
        identity = f"{name}-123456789012"
        bundle = self.downloads / f"release-bundle-{name}"
        (bundle / "logs").mkdir(parents=True)
        (bundle / "artifacts").mkdir()
        relative = package_relative_path(branch, "Nova", "phone", settings().tag, "25112026")
        archive = bundle / "artifacts" / relative.as_posix()
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(f"zip-{name}".encode())
        entry = dict(
            id=identity, branch=branch, defconfig="vendor/defconfig", image_type="Image.gz.dtb"
        )
        self.expected.append(entry)
        meta = dict(
            schema=3,
            matrix_id=identity,
            branch=branch,
            defconfig=entry["defconfig"],
            image_type=entry["image_type"],
            source_repository="https://github.com/owner/kernel",
            kernel_name="Nova",
            device_name="phone",
            kernel_rc=settings().tag,
            zip_relative_path=relative.as_posix(),
            zip_name=archive.name,
            zip_bytes=archive.stat().st_size,
            sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
            md5="a" * 32,
            date="25112026",
            time="04:29 PM",
            built_at="2026-11-25T16:29:00+06:00",
            duration_seconds=123,
            commits=[dict(sha=str(i) * 40, subject=f"Fix issue {i}") for i in range(1, 4)],
        )
        manifest = bundle / "logs/release-manifest.json"
        manifest.write_text(json.dumps(meta))
        return manifest, archive

    def load(self):
        return publication.load_bundles(
            self.downloads,
            self.expected,
            settings(),
            "Nova",
            "phone",
            "https://github.com/owner/kernel",
        )
