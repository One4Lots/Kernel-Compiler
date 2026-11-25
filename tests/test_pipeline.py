"""Local workflow handoff checks; fixture make targets do not compile a real kernel."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".github/scripts/kernel_build"
sys.path.insert(0, str(SCRIPTS))

import prepare
import publication
import telegram
import telegram_delivery
import telegram_release
from configuration import resolve_dispatch
from metadata import kernel_title
from publication_settings import PublicationSettings
from support import inputs

spec = importlib.util.spec_from_file_location("pipeline_package", SCRIPTS / "package.py")
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)

MAKEFILE = r""".PHONY: all vendor/xiaomi/miatoll_defconfig Image.gz Image.lz4 dtbs dtbo.img
record = mkdir -p out; printf '%s\n' '$@' >> out/targets; printf '%s\n' '$(KBUILD_BUILD_USER)@$(KBUILD_BUILD_HOST)' >> out/identity
all:
	@$(record)
	@mkdir -p out/include/config
	@printf '4.14.357-Alecto/8528922d\n' > out/include/config/kernel.release
vendor/xiaomi/miatoll_defconfig:
	@$(record)
	@cp arch/arm64/configs/vendor/xiaomi/miatoll_defconfig out/.config
Image.gz Image.lz4:
	@$(record)
	@mkdir -p out/arch/arm64/boot
	@printf 'SYNTHETIC IMAGE - NOT FLASHABLE\n' > out/arch/arm64/boot/$@
dtbs:
	@$(record)
	@mkdir -p out/arch/arm64/boot/dts/qcom
	@printf 'SYNTHETIC DTB\n' > out/arch/arm64/boot/dts/qcom/cust-atoll-test.dtb
dtbo.img:
	@$(record)
	@mkdir -p out/arch/arm64/boot
	@printf 'SYNTHETIC DTBO\n' > out/arch/arm64/boot/dtbo.img
"""

TEMPLATE = """properties() { '
kernel.name=
kernel.string=
kernel.version=
kernel.toolchain=
kernel.maintainer=
device.name1=
device.name2=
supported.versions=
do.devicecheck=1
do.modules=0
do.cleanup=1
do.cleanuponabort=1
'; }
BLOCK=boot;
IS_SLOT_DEVICE=0;
. tools/ak3-core.sh;
split_boot;
flash_boot;
"""


class PipelineTests(unittest.TestCase):
    def initialize_repository(self, path, branch, commits=1):
        self.git(path.parent, "init", "--initial-branch", branch, str(path))
        self.git(path, "add", ".")
        for index in range(commits):
            self.git(
                path,
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.org",
                "commit",
                "--allow-empty",
                "-m",
                f"Fixture subject {index}",
                "-m",
                f"Fixture body {index}",
            )

    def git(self, cwd, *arguments):
        return subprocess.run(
            [self.real_git, *arguments], cwd=cwd, capture_output=True, text=True, check=True
        )

    def run_script(self, work, environment, script):
        result = subprocess.run(
            ["bash", str(SCRIPTS / script)],
            cwd=work,
            env=environment,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def exercise(
        self,
        mode,
        visibility,
        toolchain="android-clang",
        *,
        kernel_name="Alecto",
        device_name="miatoll",
        device_codenames="curtana,gram,excalibur,joyeuse",
        from_profile=False,
    ):
        self.real_git = shutil.which("git")
        with tempfile.TemporaryDirectory(prefix="kernel-pipeline-") as temporary:
            root = Path(temporary)
            source = root / "source"
            (source / "arch/arm64/configs/vendor/xiaomi").mkdir(parents=True)
            (source / "arch/arm64/configs/vendor/xiaomi/miatoll_defconfig").write_text(
                "CONFIG_FIXTURE=y\n"
            )
            (source / "Makefile").write_text(MAKEFILE)
            self.initialize_repository(source, "17.0/modern-sched", commits=3)
            template = root / "template"
            (template / "tools").mkdir(parents=True)
            (template / "anykernel.sh").write_text(TEMPLATE)
            original_banner = b"ORIGINAL TEMPLATE BANNER\n"
            (template / "banner").write_bytes(original_banner)
            (template / "tools/ak3-core.sh").write_text("# fixture core; never executed\n")
            self.initialize_repository(template, "main")
            work = root / "work"
            binary = work / "toolchain/bin"
            binary.mkdir(parents=True)
            commands = {
                "clang": "echo '"
                + ("Android" if toolchain == "android-clang" else "Neutron")
                + " clang version 22.0.0'",
                "ld.lld": "echo 'LLD 22.0.0'",
            }
            for name in (
                "clang",
                "clang++",
                "ld.lld",
                "llvm-ar",
                "llvm-nm",
                "llvm-strip",
                "llvm-objcopy",
                "llvm-objdump",
                "llvm-readelf",
                "llvm-size",
                "ccache",
            ):
                path = binary / name
                path.write_text("#!/bin/sh\n" + commands.get(name, "exit 0") + "\n")
                path.chmod(0o755)
            wrapper = binary / "git"
            wrapper.write_text("""#!/usr/bin/env python3
import os, subprocess, sys
args = sys.argv[1:]
if 'clone' in args or 'fetch' in args:
    for i, value in enumerate(args):
        if value.startswith('https://'):
            if '/test/fixture-kernel' in value:
                args[i] = os.environ['FIXTURE_SOURCE_URL']
            elif '/test/fixture-anykernel' in value:
                args[i] = os.environ['FIXTURE_TEMPLATE_URL']
            else:
                raise SystemExit('Unexpected external clone URL in offline test')
sys.exit(subprocess.call([os.environ['REAL_GIT'], *args]))
""")
            wrapper.chmod(0o755)
            values = inputs(
                kernel_name=kernel_name,
                device_name=device_name,
                device_codenames=device_codenames,
                single_branch="17.0/modern-sched",
                image_type=mode,
                toolchain=toolchain,
                repo_visibility=visibility,
                kernel_rc="rc7",
                publish_to="personal+channel+github",
                changelog_mode="recent-commits",
                commit_count=3,
                release_repository="test/releases",
                kernel_repository="https://github.com/test/fixture-kernel",
                anykernel_repository="https://github.com/test/fixture-anykernel",
            )
            if from_profile:
                profile = json.loads((ROOT / "configs/miatoll.json").read_text())
                profile.update(values)
                profile["multi_branches_json"] = json.loads(values["multi_branches_json"])
                (root / "configs").mkdir()
                (root / "configs/custom.json").write_text(json.dumps(profile))
                values = resolve_dispatch(
                    {"config_file": "configs/custom.json", "publish_to": values["publish_to"]}, root
                )
                self.assertEqual(values["kernel_name"], kernel_name)
                self.assertEqual(values["device_name"], device_name)
            (entry,) = prepare.make_matrix(values)["include"]
            settings = PublicationSettings.from_inputs(values)
            environment = dict(
                os.environ,
                PATH=f"{binary}:{os.environ['PATH']}",
                REAL_GIT=self.real_git,
                FIXTURE_SOURCE_URL=source.as_uri(),
                FIXTURE_TEMPLATE_URL=template.as_uri(),
                GH_PAT="fixture-token",
                KERNEL_REPOSITORY=values["kernel_repository"],
                BRANCH=entry["branch"],
                REPO_VISIBILITY=visibility,
                DEFCONFIG=entry["defconfig"],
                IMAGE_TYPE=mode,
                TOOLCHAIN=toolchain,
                TOOLCHAIN_DIR=str(binary.parent),
                TOOLCHAIN_BIN_REL="bin",
                TOOLCHAIN_URL="https://github.com/test/compiler",
                TOOLCHAIN_HASH="fixture-hash",
                GITHUB_PATH=str(work / "github-path"),
                KERNEL_NAME=values["kernel_name"],
                DEVICE_NAME=values["device_name"],
                KERNEL_RC="rc7",
                ANYKERNEL_REPOSITORY=values["anykernel_repository"],
                BUILD_ACTOR="IntegrationBuilder",
                DEVICE_CODENAMES=values["device_codenames"],
                SUPPORTED_VERSIONS="12.0-17.0",
                HISTORY_DEPTH="3",
                MATRIX_ID=entry["id"],
                GITHUB_STEP_SUMMARY=str(work / "summary"),
                BUILD_MODE="single",
                CHANGELOG_MODE="recent-commits",
                COMMIT_COUNT="3",
                TELEGRAM_CHANNEL_CONFIRMED="true",
                TELEGRAM_DELIVERY_CONFIRMED="true",
                TELEGRAM_CHAT_ID="123456789",
                TELEGRAM_BOT_TOKEN="123:fixture",
                TELEGRAM_CHANNEL_ID="-100123",
            )
            self.run_script(work, environment, "toolchain.sh")
            self.run_script(work, environment, "clone.sh")
            self.assertNotIn("fixture-token", (work / "kernel/.git/config").read_text())
            history = json.loads((work / "logs/recent-commits.json").read_text())
            self.assertEqual(len(history), 3)
            self.run_script(work, environment, "build.sh")
            targets = (work / "kernel/out/targets").read_text().splitlines()
            image = "Image.lz4" if mode.startswith("Image.lz4") else "Image.gz"
            self.assertEqual(targets[:2], ["vendor/xiaomi/miatoll_defconfig", "all"])
            self.assertIn(image, targets)
            self.assertIn("dtbs", targets)
            self.assertEqual("dtbo.img" in targets, mode != "Image.gz.dtb")
            self.assertEqual(
                set((work / "kernel/out/identity").read_text().splitlines()),
                {"IntegrationBuilder@GitHub"},
            )
            previous = Path.cwd()
            try:
                os.chdir(work)
                with patch.dict(os.environ, environment):
                    package.main()
                    publication.bundle()
                    meta = json.loads((work / "logs/package.json").read_text())
                    with ZipFile(meta["zip_path"]) as archive:
                        self.assertIsNone(archive.testzip())
                        self.assertFalse(any(name.endswith(".dtb") for name in archive.namelist()))
                        self.assertTrue((work / "artifacts/cust-atoll-test.dtb").is_file())
                        script = archive.read("anykernel.sh").decode()
                        self.assertIn("kernel.version=4.14.357\n", script)
                        from installer import TOOLCHAIN_NAMES

                        self.assertIn(f"kernel.toolchain={TOOLCHAIN_NAMES[toolchain]}\n", script)
                        self.assertIn(f"kernel.name={kernel_title(kernel_name)}\n", script)
                        self.assertIn("kernel.maintainer=IntegrationBuilder@github\n", script)
                        self.assertIn("supported.versions=12.0-17.0\n", script)
                        self.assertEqual(
                            len(re.findall(r"(?m)^device\.name[0-9]+=", script)),
                            len(device_codenames.split(",")),
                        )
                        for index, device in enumerate(device_codenames.split(","), 1):
                            self.assertIn(f"device.name{index}={device}\n", script)
                        if mode != "Image.gz.dtb":
                            self.assertIn("dtb", archive.namelist())
                            self.assertIn("dtbo", archive.namelist())
                        self.assertEqual(archive.read("banner"), original_banner)
                        self.assertIn(
                            b"kernel.string=\n",
                            archive.read("anykernel.sh"),
                        )
                    # A sole artifact is extracted flat by the pinned download action.
                    download = work / "release-downloads"
                    # Match the real upload: no duplicate raw images in publisher transport.
                    source_zip = work / "artifacts" / meta["zip_relative_path"]
                    destination_zip = download / "artifacts" / meta["zip_relative_path"]
                    destination_zip.parent.mkdir(parents=True)
                    shutil.copyfile(source_zip, destination_zip)
                    self.assertEqual(list((download / "artifacts").rglob("*.img")), [])
                    (download / "logs").mkdir()
                    for filename in (
                        "release-manifest.json",
                        "clang-version.txt",
                        "lld-version.txt",
                        "commit-message.txt",
                        "recent-commits.json",
                    ):
                        shutil.copyfile(work / "logs" / filename, download / "logs" / filename)
                    manifests = publication.load_bundles(
                        work / "release-downloads",
                        [entry],
                        settings,
                        kernel_name,
                        device_name,
                        values["kernel_repository"],
                    )
                    assets = publication.stage_assets(manifests, work / "release-assets")
                    notes = publication.release_notes(manifests, settings, kernel_name, "a" * 40)
                    self.assertIn("Fixture subject 2", notes)
                    self.assertEqual(len(assets), 2)
                    client = Mock()
                    client.api.side_effect = [
                        None,
                        None,
                        {"object": {"type": "commit", "sha": "a" * 40}},
                        {"id": 42, "draft": True},
                        {"html_url": "https://github.com/test/releases/releases/tag/rc7"},
                    ]
                    url = publication.publish(
                        client, settings, "a" * 40, kernel_name, notes, assets
                    )
                    self.assertTrue(url.endswith("/rc7"))
                    client.upload.assert_called_once_with(
                        "test/releases", "rc7", assets, clobber=False
                    )
                    with patch.object(telegram, "telegram_request") as delivery:
                        telegram.main()
                    delivery.assert_called_once()
                    self.assertEqual(delivery.call_args.args[0], "sendDocument")
                    self.assertEqual(delivery.call_args.args[4], Path(meta["zip_path"]))
                    self.assertIn(
                        "✅ Kernel Compiled Successfully", delivery.call_args.args[1]["caption"]
                    )
                    with (
                        patch.dict(
                            os.environ,
                            INPUTS_JSON=json.dumps(values),
                            MATRIX_JSON=json.dumps({"include": [entry]}),
                        ),
                        patch.object(telegram_release, "send_document") as channel,
                    ):
                        telegram_release.main()
                    channel.assert_called_once()
                    self.assertEqual(
                        channel.call_args.args[0].resolve(),
                        (download / "artifacts" / meta["zip_relative_path"]).resolve(),
                    )
                    self.assertIn("✅ Kernel Compiled Successfully", channel.call_args.args[1])
                    with (
                        patch.dict(
                            os.environ,
                            INPUTS_JSON=json.dumps(dict(values, publish_to="personal+github")),
                            MATRIX_JSON=json.dumps({"include": [entry]}),
                        ),
                        patch.object(telegram_release, "send_document") as personal,
                    ):
                        telegram_delivery.main()
                    personal.assert_called_once()
                    self.assertEqual(personal.call_args.args[3], "123456789")
                    for rendered in (
                        delivery.call_args.args[1]["caption"],
                        channel.call_args.args[1],
                        personal.call_args.args[1],
                    ):
                        self.assertTrue(
                            rendered.startswith("<b>✅ Kernel Compiled Successfully</b>\n\n")
                        )
                        self.assertNotIn(kernel_name, rendered)
                        self.assertNotIn(device_name, rendered)
                        self.assertNotIn("Branch:", rendered)
                        self.assertNotIn("Local Time:", rendered)
                        self.assertIn("🔩 <b>ToolChain:</b>", rendered)
                        self.assertIn("📦 <b>Image:</b>", rendered)
                        self.assertIn(
                            f"<b>🔑 MD5 hash</b>\n<pre><code>{meta['md5']}</code></pre>", rendered
                        )
                        self.assertEqual(rendered.count("<pre><code>"), 2)
                    if from_profile:
                        self.assertEqual(
                            meta["installer"]["kernel.name"], kernel_title(kernel_name)
                        )
                        self.assertEqual(meta["device_name"], device_name)
                        self.assertIn(kernel_name.replace(" ", "-"), meta["zip_name"])
                        self.assertNotIn("KERNEL_BUILD_FLASH_BANNER_EOF", script)

            finally:
                os.chdir(previous)
            self.assertEqual((template / "banner").read_bytes(), original_banner)
            self.assertEqual((template / "anykernel.sh").read_text(), TEMPLATE)

    def test_public_combined_image_pipeline(self):
        self.exercise("Image.gz.dtb", "public")

    def test_private_split_gzip_pipeline(self):
        self.exercise("Image.gz + dtbo.img + dtb.img", "private")

    def test_private_split_lz4_pipeline(self):
        self.exercise("Image.lz4 + dtbo.img + dtb.img", "private")

    def test_custom_profiles_preserve_installer_identity_with_compact_telegram_captions(self):
        for name, device, compatible in (
            ("Orion", "surya", "surya,karna"),
            ("Nova Kernel", "marble", "marble"),
            ("Zenith kernel", "alioth", "alioth,aliothin"),
        ):
            for compiler in prepare.TOOLCHAINS:
                with self.subTest(name=name, device=device, compiler=compiler):
                    self.exercise(
                        "Image.gz + dtbo.img + dtb.img",
                        "public",
                        compiler,
                        kernel_name=name,
                        device_name=device,
                        device_codenames=compatible,
                        from_profile=True,
                    )

    def test_all_image_compiler_and_visibility_combinations(self):
        # Exercise actual shell/Git/make/package handoffs, not real compiler binaries.
        for image in prepare.IMAGE_TYPES:
            for toolchain in prepare.TOOLCHAINS:
                for visibility in ("public", "private"):
                    with self.subTest(image=image, toolchain=toolchain, visibility=visibility):
                        self.exercise(image, visibility, toolchain)


if __name__ == "__main__":
    unittest.main()
