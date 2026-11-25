"""Offline regression tests: python3 -m unittest discover -s tests -v."""

from __future__ import annotations

import hashlib
import html
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from support import inputs

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".github/scripts/kernel_build"
sys.path.insert(0, str(SCRIPTS))
import installer  # noqa: E402
import metadata  # noqa: E402
import prepare  # noqa: E402
import telegram  # noqa: E402

spec = importlib.util.spec_from_file_location("kernel_package", SCRIPTS / "package.py")
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class MatrixTests(unittest.TestCase):
    def test_single_defaults(self):
        (entry,) = prepare.make_matrix(inputs())["include"]
        self.assertEqual(entry["branch"], "staging")
        self.assertEqual(entry["defconfig"], "vendor/xiaomi/miatoll_defconfig")
        self.assertEqual(entry["image_type"], "Image.gz.dtb")

    def test_multi_fallbacks_and_shared_toolchain(self):
        entries = [
            {"branch": "17.0/modern-sched"},
            {
                "branch": "17.0/upstream-susfs",
                "defconfig": "custom_defconfig",
                "image_type": prepare.IMAGE_TYPES[2],
            },
        ]
        matrix = prepare.make_matrix(
            inputs(
                build_mode="multi",
                toolchain="neutron-clang",
                multi_branches_json=json.dumps(entries),
            )
        )["include"]
        self.assertEqual(matrix[0]["defconfig"], "vendor/xiaomi/miatoll_defconfig")
        self.assertEqual(matrix[0]["image_type"], "Image.gz.dtb")
        self.assertEqual(matrix[1]["defconfig"], "custom_defconfig")
        self.assertEqual(matrix[1]["image_type"], prepare.IMAGE_TYPES[2])
        self.assertTrue(all("toolchain" not in entry for entry in matrix))
        self.assertEqual(matrix[0]["branch"], "17.0/modern-sched")
        self.assertTrue(matrix[0]["id"].startswith("build-"))

    def test_reject_bad_matrix_shapes(self):
        for value in (
            "null",
            "{}",
            "[]",
            "not json",
            '["staging"]',
            "[{}]",
            '[{"branch":"staging","toolchain":"other"}]',
            '[{"branch":"wrong","branch":"staging"}]',
            json.dumps([{"branch": f"branch-{i}"} for i in range(33)]),
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                prepare.make_matrix(inputs(build_mode="multi", multi_branches_json=value))

    def test_reject_unsafe_values(self):
        for value in ("-bad", "bad\nbranch", "refs/../oops", "@{-1}"):
            with self.subTest(branch=value), self.assertRaises(ValueError):
                prepare.make_matrix(inputs(single_branch=value))
        for value in ("../defconfig", "/tmp/config", "vendor/../../config", "x;whoami", "a//b"):
            with self.subTest(config=value), self.assertRaises(ValueError):
                prepare.make_matrix(inputs(defconfig=value))
        for value in (
            "https://token@github.com/a/b",
            "http://github.com/a/b",
            "https://example.org/a/b",
            "https://github.com/a/b?token=x",
        ):
            with self.subTest(url=value), self.assertRaises(ValueError):
                prepare.make_matrix(inputs(kernel_repository=value))

    def test_ids_prevent_sanitized_branch_and_config_collisions(self):
        entries = [
            {"branch": "a/b"},
            {"branch": "a-b"},
            {"branch": "a/b", "defconfig": "other_defconfig"},
        ]
        matrix = prepare.make_matrix(
            inputs(build_mode="multi", multi_branches_json=json.dumps(entries))
        )
        self.assertEqual(len({entry["id"] for entry in matrix["include"]}), 3)

    def test_reject_duplicate_and_null_overrides(self):
        for entries in (
            [{"branch": "staging"}] * 2,
            [{"branch": "staging", "defconfig": None}],
            [{"branch": "staging", "image_type": "unknown"}],
        ):
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                prepare.make_matrix(
                    inputs(build_mode="multi", multi_branches_json=json.dumps(entries))
                )

    def test_missing_private_pat_fails_before_work_starts(self):
        with patch.dict(
            os.environ, {"INPUTS_JSON": json.dumps(inputs(repo_visibility="private"))}, clear=True
        ):
            with (
                patch.object(sys, "argv", ["prepare.py", "matrix"]),
                self.assertRaisesRegex(ValueError, "GH_PAT"),
            ):
                prepare.main()

    def test_missing_telegram_credentials_fail_early(self):
        with patch.dict(
            os.environ,
            {
                "INPUTS_JSON": json.dumps(inputs(publish_to="personal only")),
                "TELEGRAM_CHANNEL_CONFIRMED": "true",
            },
            clear=True,
        ):
            with (
                patch.object(sys, "argv", ["prepare.py", "matrix"]),
                self.assertRaisesRegex(ValueError, "TELEGRAM_BOT_TOKEN"),
            ):
                prepare.main()


class IdentityTests(unittest.TestCase):
    def test_names_reject_shell_html_path_and_output_injection(self):
        for field in ("kernel_name", "device_name"):
            for value in (
                "",
                "../bad",
                "Other/Kernel",
                "bad\\nname",
                "bad'name",
                'bad"name',
                "$(command)",
                "<b>Kernel</b>",
                " name",
                "name ",
                "x" * 49,
                None,
            ):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    prepare.make_matrix(inputs(**{field: value}))

    def test_custom_identity_outputs_use_safe_filename_components(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "output"
            environment = dict(
                INPUTS_JSON=json.dumps(
                    inputs(kernel_name="Nova Kernel", device_name="other-device")
                ),
                GITHUB_OUTPUT=str(output),
                TELEGRAM_BOT_TOKEN="123:test",
                TELEGRAM_CHANNEL_ID="-100123",
            )
            with (
                patch.dict(os.environ, environment, clear=True),
                patch.object(sys, "argv", ["prepare.py", "matrix"]),
            ):
                prepare.main()
            values = dict(line.split("=", 1) for line in output.read_text().splitlines())
            self.assertEqual(values["kernel_slug"], "Nova-Kernel")
            self.assertEqual(values["device_slug"], "other-device")
            self.assertEqual(json.loads(values["matrix"])["include"][0]["branch"], "staging")

    def test_supported_android_version_forms_and_validation(self):
        for value in ("12.0-17.0", "12-17", "13.0", "12.0,13.0,17.0"):
            with self.subTest(value=value):
                self.assertEqual(metadata.supported_android_versions(value), value)
                prepare.make_matrix(inputs(supported_versions=value))
        for value in ("", "17.0-12.0", "12.0,13.0-17.0", "12.0;command", "12.0\n17.0", "*"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                prepare.make_matrix(inputs(supported_versions=value))

    def test_alias_validation_runs_before_builds_are_allocated(self):
        for value in ("", "curtana,curtana", "curtana,", "curtana;command", "Other Device"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                prepare.make_matrix(inputs(device_codenames=value))
        aliases = "curtana,gram,excalibur,joyeuse"
        prepare.make_matrix(inputs(device_codenames=aliases))
        self.assertEqual(
            metadata.supported_devices(aliases),
            ("curtana", "gram", "excalibur", "joyeuse"),
        )

    def test_template_source_rejects_credentials(self):
        with self.assertRaisesRegex(ValueError, "anykernel_repository"):
            prepare.make_matrix(
                inputs(anykernel_repository="https://secret@github.com/owner/template")
            )


def installer_config(**changes):
    values = dict(
        kernel_name="Nova",
        device_name="phone",
        release="6.1.0-custom",
        toolchain="android-clang",
        actor="TestBuilder",
        supported_versions="12.0-17.0",
        device_codenames="phone",
    )
    values.update(changes)
    return installer.InstallerConfig(**values)


def installer_template(name_property="kernel.name"):
    return f"""properties() {{ '
{name_property}=Old brand
kernel.version=0.0.0
kernel.toolchain=Old compiler
kernel.maintainer=Old maintainer
do.devicecheck=0
do.modules=0
device.name1=old-device
device.name2=stale-device
supported.versions=1-2
'; }}
BLOCK=/dev/block/bootdevice/by-name/boot;
. tools/ak3-core.sh;
split_boot;
flash_boot;
"""


class InstallerTests(unittest.TestCase):
    def test_upstream_and_fork_properties_configure_all_selected_metadata(self):
        for property_name in ("kernel.string", "kernel.name"):
            with self.subTest(property=property_name), tempfile.TemporaryDirectory() as temporary:
                script = Path(temporary) / "anykernel.sh"
                script.write_text(installer_template(property_name))
                config = installer_config(device_codenames="phone,phone-pro,phone-max")
                installer.configure_installer(script, config)
                result = script.read_text()
                for key, value in config.properties().items():
                    self.assertIn(f"{key}={value}\n", result)
                self.assertIn("kernel.name=Nova Kernel\n", result)
                if property_name == "kernel.string":
                    self.assertIn("kernel.string=\n", result)
                else:
                    self.assertNotIn("kernel.string=", result)
                self.assertIn("do.modules=0", result)
                self.assertIn("BLOCK=/dev/block/bootdevice/by-name/boot;", result)
                self.assertIn("device.name2=phone-pro", result)
                self.assertIn("device.name3=phone-max", result)
                self.assertNotIn("stale-device", result)
                self.assertNotIn(installer.BANNER_START, result)
                subprocess.run(["sh", "-n", str(script)], check=True)

    def test_fresh_blank_metadata_is_filled_without_changing_installer_controls(self):
        content = """properties() { '
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
do.systemless=0
do.cleanup=1
do.cleanuponabort=1
'; } # end properties
BLOCK=boot;
IS_SLOT_DEVICE=0;
. tools/ak3-core.sh;
split_boot;
flash_boot;
"""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "anykernel.sh"
            path.write_text(content)
            config = installer_config(kernel_name="Alecto", device_name="miatoll")
            installer.configure_installer(path, config)
            result = path.read_text()
            for key, value in config.properties().items():
                self.assertIn(f"{key}={value}\n", result)
            self.assertNotIn("device.name2=", result)
            for setting in (
                "do.modules=0",
                "do.systemless=0",
                "do.cleanup=1",
                "do.cleanuponabort=1",
                "BLOCK=boot;",
                "IS_SLOT_DEVICE=0;",
            ):
                self.assertIn(setting, result)
            subprocess.run(["sh", "-n", str(path)], check=True)

    def test_original_template_banner_and_ui_are_preserved_on_repeated_packaging(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            banner_path = root / "banner"
            original_banner = b"=== ORIGINAL TEMPLATE BANNER ===\nKeep $symbols and ASCII art.\n"
            banner_path.write_bytes(original_banner)
            tools = root / "tools"
            tools.mkdir()
            ui_path = tools / "install-ui.sh"
            original_ui = b'ak_ui_start() { ui_print "ORIGINAL TEMPLATE UI"; }\n'
            ui_path.write_bytes(original_ui)
            original_tail = (
                "\n# Template recovery UI\n"
                '. "$AKHOME/tools/install-ui.sh";\n'
                'ak_ui_start;\nui_print "Original inline banner";\n'
                "split_boot;\nflash_boot;\n"
            )
            content = installer_template().split(". tools/ak3-core.sh;", 1)[0]
            script = root / "anykernel.sh"
            script.write_text(content + ". tools/ak3-core.sh;" + original_tail)
            for name in ("Alecto", "Nova"):
                installer.configure_installer(script, installer_config(kernel_name=name))
                result = script.read_text()
                self.assertEqual(banner_path.read_bytes(), original_banner)
                self.assertEqual(ui_path.read_bytes(), original_ui)
                self.assertTrue(result.endswith(original_tail))
                self.assertNotIn(installer.BANNER_START, result)
                self.assertNotIn(installer.BANNER_END, result)
                self.assertNotIn("kernel.string=", result)
                subprocess.run(["sh", "-n", str(script)], check=True)

    def test_kernel_suffix_is_added_exactly_once(self):
        for name, expected in (
            ("Alecto", "Alecto Kernel"),
            ("Nova Kernel", "Nova Kernel"),
            ("Nova kernel", "Nova kernel"),
        ):
            self.assertEqual(
                installer_config(kernel_name=name).properties()["kernel.name"], expected
            )

    def test_selected_toolchain_name_and_triggering_user(self):
        self.assertEqual(installer_config().properties()["kernel.toolchain"], "Android Clang")
        config = installer_config(toolchain="neutron-clang", actor="github-actions[bot]")
        self.assertEqual(config.properties()["kernel.toolchain"], "Neutron Clang")
        self.assertEqual(config.properties()["kernel.maintainer"], "github-actions[bot]@github")

    def test_no_implicit_old_device_codenames_are_retained(self):
        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / "anykernel.sh"
            script.write_text(installer_template())
            installer.configure_installer(script, installer_config())
            result = script.read_text()
            self.assertIn("device.name1=phone", result)
            self.assertNotIn("device.name2=", result)
            self.assertNotIn("stale-device", result)
            self.assertIn("do.devicecheck=1", result)

    def test_bad_template_structure_and_duplicates_do_not_modify_template(self):
        examples = [
            "no-properties",
            installer_template().replace(
                "kernel.name=Old brand", "kernel.name=One\nkernel.name=Two"
            ),
            installer_template().replace(
                "kernel.version=0.0.0", "kernel.string=One\nkernel.string=Two\nkernel.version=0.0.0"
            ),
            installer_template() + "kernel.name=outside-properties\n",
            installer_template() + "device.name99=stale-outside-alias\n",
        ]
        for content in examples:
            with self.subTest(content=content), tempfile.TemporaryDirectory() as temporary:
                script = Path(temporary) / "anykernel.sh"
                script.write_text(content)
                with self.assertRaises(ValueError):
                    installer.configure_installer(script, installer_config())
                self.assertEqual(script.read_text(), content)

    def test_unsafe_release_actor_and_compatibility_inputs_are_rejected(self):
        for changes in (
            dict(release="6.1.0'"),
            dict(release="$(command)"),
            dict(actor="user@evil"),
            dict(actor="user\nname"),
            dict(supported_versions="17.0-12.0"),
            dict(device_name="Other Device"),
            dict(device_codenames="phone,phone"),
            dict(toolchain="unknown"),
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                installer_config(**changes)

    def test_metadata_updates_are_idempotent_without_banner_injection(self):
        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / "anykernel.sh"
            script.write_text(installer_template())
            installer.configure_installer(script, installer_config())
            installer.configure_installer(script, installer_config(kernel_name="Zenith"))
            result = script.read_text()
            self.assertNotIn(installer.BANNER_START, result)
            self.assertNotIn(installer.BANNER_END, result)
            self.assertIn("Zenith Kernel", result)
            self.assertNotIn("Nova Kernel", result)

    def test_legacy_generated_banner_is_removed_without_touching_template_ui(self):
        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / "anykernel.sh"
            original = installer_template() + 'ui_print "KEEP ORIGINAL UI";\n'
            block = (
                f"{installer.BANNER_START}\nui_print OLD_GENERATED_ART;\n{installer.BANNER_END}\n"
            )
            script.write_text(original.replace("split_boot;", block + "split_boot;"))
            installer.configure_installer(script, installer_config())
            self.assertNotIn("OLD_GENERATED_ART", script.read_text())
            self.assertNotIn(installer.BANNER_START, script.read_text())
            self.assertTrue(script.read_text().endswith('ui_print "KEEP ORIGINAL UI";\n'))
            first = script.read_text()
            installer.configure_installer(script, installer_config())
            self.assertEqual(script.read_text(), first)

    def test_malformed_legacy_banner_fails_without_modifying_script(self):
        for tail in (
            installer.BANNER_START,
            installer.BANNER_END,
            f"{installer.BANNER_START}\nx\n{installer.BANNER_END}\n" * 2,
        ):
            with self.subTest(tail=tail), tempfile.TemporaryDirectory() as temporary:
                script = Path(temporary) / "anykernel.sh"
                original = installer_template() + tail
                script.write_text(original)
                with self.assertRaisesRegex(ValueError, "legacy managed banner"):
                    installer.configure_installer(script, installer_config())
                self.assertEqual(script.read_text(), original)

    def test_installer_emits_only_original_core_and_flash_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / "anykernel.sh"
            script.write_text(installer_template())
            installer.configure_installer(script, installer_config())
            (root / "tools").mkdir()
            (root / "tools/ak3-core.sh").write_text(
                "printf 'CORE_READY\\n'; split_boot() { printf 'SPLIT_BOOT\\n'; }; flash_boot() { printf 'FLASH_BOOT\\n'; };\n"
            )
            result = subprocess.run(
                ["sh", "anykernel.sh"], cwd=root, capture_output=True, text=True, check=True
            )
            self.assertEqual(result.stdout.splitlines(), ["CORE_READY", "SPLIT_BOOT", "FLASH_BOOT"])


class HeaderTests(unittest.TestCase):
    def test_only_final_redirect_headers_are_used(self):
        headers = (
            "HTTP/1.1 200 Connection established\r\n\r\nHTTP/2 302\r\n"
            "Content-Length: 0\r\nETag: redirect\r\n\r\nHTTP/2 200\r\n"
            'ETag: "release-v1"\r\nLast-Modified: Tue, 24 Nov 2026 12:00:00 GMT\r\n'
            "Content-Length: 1234\r\n\r\n"
        )
        result = subprocess.CompletedProcess([], 0, headers, "")
        with patch.object(prepare.subprocess, "run", return_value=result):
            value = prepare.release_headers("https://github.com/a/b")
        self.assertEqual(value["etag"], '"release-v1"')
        self.assertEqual(value["content-length"], "1234")
        self.assertIn("last-modified", value)

    def test_fingerprint_changes_with_url_and_each_header(self):
        original = {"etag": "v1", "last-modified": "today", "content-length": "100"}
        old = prepare.fingerprint("url", original)
        self.assertEqual(old, prepare.fingerprint("url", dict(reversed(list(original.items())))))
        self.assertNotEqual(old, prepare.fingerprint("other-url", original))
        for name in original:
            changed = dict(original, **{name: "different"})
            self.assertNotEqual(old, prepare.fingerprint("url", changed))

    def test_missing_headers_or_curl_failure_fail_closed(self):
        for result in (
            subprocess.CompletedProcess([], 0, "HTTP/2 200\n\n", ""),
            subprocess.CompletedProcess([], 22, "", "private error"),
        ):
            with (
                patch.object(prepare.subprocess, "run", return_value=result),
                self.assertRaises(ValueError),
            ):
                prepare.release_headers("https://github.com/a/b")


class OutputsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.boot = self.root / "kernel/out/arch/arm64/boot"
        self.boot.mkdir(parents=True)
        self.trees = self.boot / "dts/qcom"
        self.trees.mkdir(parents=True)
        (self.trees / "z.dtb").write_bytes(b"last-dtb")
        (self.trees / "cust-atoll-test.dtb").write_bytes(b"cust-dtb")
        (self.boot / "Image.gz").write_bytes(b"gzip-image")
        (self.boot / "Image.lz4").write_bytes(b"lz4-image")
        (self.boot / "dtbo.img").write_bytes(b"dtbo-image")
        self.artifacts = self.root / "artifacts"

    def test_combined_from_deterministic_concatenation(self):
        payload = package.collect_outputs(self.boot, self.artifacts, "Image.gz.dtb")
        self.assertEqual(
            (self.artifacts / "Image.gz.dtb").read_bytes(), b"gzip-imagecust-dtblast-dtb"
        )
        self.assertEqual(payload["Image.gz.dtb"], "Image.gz-dtb")
        self.assertNotIn("cust-atoll-test.dtb", payload)
        self.assertTrue((self.artifacts / "cust-atoll-test.dtb").is_file())
        self.assertNotIn("dtbo.img", payload)

    def test_unrelated_custom_dtbs_are_not_exposed_or_packaged(self):
        (self.trees / "cust-other-device.dtb").write_bytes(b"other-dtb")
        payload = package.collect_outputs(self.boot, self.artifacts, "Image.gz.dtb")
        self.assertNotIn("cust-other-device.dtb", payload)
        self.assertFalse((self.artifacts / "cust-other-device.dtb").exists())

    def test_existing_combined_output_is_preferred(self):
        (self.boot / "Image.gz-dtb").write_bytes(b"native-combined")
        package.collect_outputs(self.boot, self.artifacts, "Image.gz.dtb")
        self.assertEqual((self.artifacts / "Image.gz.dtb").read_bytes(), b"native-combined")

    def test_both_split_modes_and_native_dtb_image(self):
        for index, mode in enumerate(prepare.IMAGE_TYPES[1:]):
            with self.subTest(mode=mode):
                (self.boot / "dtb.img").write_bytes(b"native-dtb-image")
                directory = self.root / f"split-{index}"
                payload = package.collect_outputs(self.boot, directory, mode)
                self.assertEqual(payload["dtbo.img"], "dtbo")
                self.assertEqual(payload["dtb.img"], "dtb")
                self.assertEqual((directory / "dtb.img").read_bytes(), b"native-dtb-image")
                self.assertIn("Image.lz4" if index else "Image.gz", payload)

    def test_empty_required_image_and_missing_device_trees_fail(self):
        (self.boot / "Image.gz").write_bytes(b"")
        with self.assertRaises(ValueError):
            package.collect_outputs(self.boot, self.artifacts, "Image.gz.dtb")
        (self.boot / "Image.gz").write_bytes(b"image")
        shutil.rmtree(self.boot / "dts")
        with self.assertRaisesRegex(ValueError, "No compiled DTBs"):
            package.collect_outputs(self.boot, self.artifacts, "Image.gz.dtb")

    def test_colliding_cust_dtb_basenames_fail(self):
        other = self.boot / "dts/other"
        other.mkdir()
        (other / "cust-atoll-test.dtb").write_bytes(b"not-the-same")
        with self.assertRaisesRegex(ValueError, "Duplicate output filename"):
            package.collect_outputs(self.boot, self.artifacts, "Image.gz.dtb")

    def test_packaging_default_identity(self):
        self.assert_packaging_identity("Alecto", "miatoll")

    def test_packaging_custom_identity(self):
        self.assert_packaging_identity("Nova Kernel", "Marble")

    def assert_packaging_identity(self, kernel_name, device_name):
        logs = self.root / "logs"
        logs.mkdir()
        (logs / "build-duration.txt").write_text("125")
        (logs / "clang-version.txt").write_text(
            "Neutron clang version 22.0.0 (compiler provenance)\nTarget: aarch64"
        )
        release_file = self.root / "kernel/out/include/config/kernel.release"
        release_file.parent.mkdir(parents=True)
        release_file.write_text("4.14.357-Alecto/8528922d\n")
        template = self.root / "template"
        template.mkdir()
        for filename in (
            "anykernel.sh",
            "README.md",
            "LICENSE",
            "LICENCE",
            ".gitignore",
            "Image.lz4",
        ):
            (template / filename).write_text("template")
        (template / "anykernel.sh").write_text("""properties() { '
kernel.name=Stale brand
kernel.version=0.0.0
kernel.toolchain=Unknown
do.devicecheck=1
device.name1=curtana
device.name2=obsolete-device
supported.versions=1-2
'; }
. tools/ak3-core.sh;
split_boot;
flash_boot;
""")
        (template / ".git").mkdir()
        (template / ".git/config").write_text("not-for-zip")
        (template / "tools").mkdir()
        (template / "tools/ak3-core.sh").write_text("installer")
        (template / "tools/README.md").write_text("exclude")
        original_banner = b"=== ORIGINAL ANYKERNEL3 BANNER ===\nNot generated by SyncroBot.\n"
        original_ui = b'ak_ui_start() { ui_print "ORIGINAL RECOVERY UI"; }\n'
        (template / "banner").write_bytes(original_banner)
        (template / "tools/install-ui.sh").write_bytes(original_ui)
        original_run = subprocess.run

        def mocked_run(command, **kwargs):
            if command[0] == "git" and "clone" in command:
                self.assertIn("https://github.com/test/custom-anykernel", command)
                shutil.copytree(template, Path(command[-1]))
                return subprocess.CompletedProcess(command, 0)
            return original_run(command, **kwargs)

        class FixedDate(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 11, 25, 16, 29, tzinfo=tz)

        previous = Path.cwd()
        try:
            os.chdir(self.root)
            with patch.dict(
                os.environ,
                IMAGE_TYPE="Image.gz.dtb",
                BRANCH="17.0/modern-sched",
                KERNEL_RC="rc1",
                GITHUB_STEP_SUMMARY=str(self.root / "summary.md"),
                KERNEL_NAME=kernel_name,
                DEVICE_NAME=device_name,
                ANYKERNEL_REPOSITORY="https://github.com/test/custom-anykernel",
                TOOLCHAIN="neutron-clang",
                BUILD_ACTOR="TestBuilder",
                SUPPORTED_VERSIONS="12.0-17.0",
                DEVICE_CODENAMES="additional-device",
            ):
                with (
                    patch.object(package.subprocess, "run", side_effect=mocked_run),
                    patch.object(package, "datetime", FixedDate),
                ):
                    package.main()
        finally:
            os.chdir(previous)
        archive = self.artifacts / (
            f"17.0/modern-sched-{metadata.filename_component(kernel_name)}-"
            f"{metadata.filename_component(device_name)}-rc1-25112026.zip"
        )
        with zipfile.ZipFile(archive) as zipped:
            names = zipped.namelist()
            self.assertIn("Image.gz-dtb", names)
            self.assertNotIn("cust-atoll-test.dtb", names)
            self.assertIn("tools/ak3-core.sh", names)
            self.assertEqual(zipped.read("banner"), original_banner)
            self.assertEqual(zipped.read("tools/install-ui.sh"), original_ui)
            self.assertEqual((template / "banner").read_bytes(), original_banner)
            self.assertEqual((template / "tools/install-ui.sh").read_bytes(), original_ui)
            installer = zipped.read("anykernel.sh").decode()
            self.assertIn(f"kernel.name={metadata.kernel_title(kernel_name)}\n", installer)
            self.assertNotIn("Non-GKI Modern Scheduler Kernel", installer)
            self.assertNotIn("kernel.string=", installer)
            self.assertIn("kernel.version=4.14.357\n", installer)
            self.assertIn("kernel.toolchain=Neutron Clang\n", installer)
            self.assertIn("do.devicecheck=1", installer)
            self.assertNotIn(f"device.name1={device_name}\n", installer)
            self.assertIn("device.name1=additional-device\n", installer)
            self.assertNotIn("device.name2=", installer)
            self.assertIn("kernel.maintainer=TestBuilder@github\n", installer)
            self.assertIn("supported.versions=12.0-17.0\n", installer)
            self.assertNotIn("obsolete-device", installer)
            self.assertNotIn("# BEGIN KERNEL_BUILD_FLASH_BANNER", installer)
            self.assertNotIn("KERNEL_BUILD_FLASH_BANNER_EOF", installer)
            self.assertNotIn("Stale brand", installer)
            self.assertNotIn("Image.lz4", names)
            self.assertFalse(
                any(
                    ".git" in name or Path(name).name in ("README.md", "LICENSE", "LICENCE")
                    for name in names
                )
            )
        meta = json.loads((logs / "package.json").read_text())
        self.assertEqual(meta["kernel_name"], kernel_name)
        self.assertEqual(meta["device_name"], device_name)
        self.assertEqual(meta["kernel_rc"], "rc1")
        self.assertEqual(meta["kernel_release"], "4.14.357-Alecto/8528922d")
        self.assertEqual(meta["installer"]["kernel.version"], "4.14.357")
        self.assertEqual(meta["zip_relative_path"], archive.relative_to(self.artifacts).as_posix())
        self.assertEqual(meta["zip_name"], archive.name)
        self.assertNotIn("0429", archive.name)
        self.assertEqual(archive.parent.name, "17.0")
        self.assertIn(f"{kernel_name} · {device_name}", (self.root / "summary.md").read_text())
        self.assertEqual(meta["date"], "25112026")
        self.assertEqual(meta["time"], "04:29 PM")
        self.assertEqual(meta["built_at"], "2026-11-25T16:29:00+06:00")
        self.assertFalse((logs / "flash-banner.txt").exists())
        self.assertEqual(len(meta["md5"]), 32)
        self.assertEqual(meta["sha256"], hashlib.sha256(archive.read_bytes()).hexdigest())


class TelegramTests(unittest.TestCase):
    def metadata(self):
        return dict(
            kernel_name="Nova Kernel",
            device_name="Marble",
            duration_seconds=3723,
            date="25112026",
            time="04:29 PM",
            built_at="2026-11-25T16:29:00+06:00",
            zip_bytes=1234567,
            md5="a" * 32,
        )

    def environment(self):
        return dict(
            TOOLCHAIN="android-clang",
            BRANCH="17.0/modern-sched",
            BUILD_MODE="multi",
            REPO_VISIBILITY="private",
            DEFCONFIG="vendor/xiaomi/miatoll_defconfig",
            IMAGE_TYPE="Image.gz.dtb",
        )

    def test_caption_has_latest_full_message_and_separate_checksum_code(self):
        message = "Fix scheduler issue\n\nKeep task placement stable.\nPreserve idle accounting.\n\nSigned-off-by: Developer <dev@example.org>"
        caption = telegram.make_caption(
            self.metadata(),
            self.environment(),
            "Android clang version 22.0.0",
            "LLD 22.0.0",
            message,
        )
        for value in (
            "Image.gz.dtb",
            "Android Clang 22.0.0",
            "LLD 22.0.0",
            "1h 2m 3s",
            "Wednesday, 25 November 2026",
            "MiB",
            "a" * 32,
        ):
            self.assertIn(value, caption)
        blocks = re.findall(r"<pre><code>(.*?)</code></pre>", caption, re.DOTALL)
        self.assertEqual([html.unescape(block) for block in blocks], ["a" * 32, message])
        self.assertEqual(caption.count("<code>"), 2)
        self.assertNotIn("<code>", caption.split("<b>🔑 MD5 hash</b>")[0])
        for value in ("Nova Kernel", "Marble", "17.0/modern-sched", "Branch:", "Local Time:"):
            self.assertNotIn(value, caption)
        self.assertIn("Latest commit", caption)
        self.assertNotIn("Recent commits", caption)
        self.assertNotIn("Alecto", caption)
        self.assertNotIn("MIATOLL", caption)
        self.assertNotIn("SHA:", caption)
        self.assertNotIn("MD5SUM", caption)
        self.assertNotIn(" AM", caption)
        self.assertNotIn(" PM", caption)
        self.assertLessEqual(telegram.utf16_length(caption), 1024)

    def test_pathological_message_remains_escaped_and_within_limit(self):
        environment = self.environment()
        environment.update(
            BRANCH="long-branch" * 12,
            DEFCONFIG="very-long-config" * 10,
            IMAGE_TYPE=prepare.IMAGE_TYPES[2],
        )
        caption = telegram.make_caption(
            self.metadata(),
            environment,
            "Android clang version " + "1" * 150,
            "LLD " + "1" * 100,
            "Fix <scheduler> & crashes\n\n" + '<&"😀' * 1000,
        )
        self.assertLessEqual(telegram.utf16_length(caption), 1024)
        self.assertIn("&lt;", caption)
        self.assertNotIn('<&"', caption)
        self.assertIn("trimmed; full message in build logs", caption)
        self.assertEqual(caption.count("<code>"), 2)

    def test_empty_commit_message_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "empty"):
            telegram.make_caption(
                self.metadata(), self.environment(), "Android clang version 22", "LLD 22", " \n\n"
            )

    def test_api_failure_does_not_leak_token(self):
        token = "123:never-print-this"
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "kernel.zip"
            archive.write_bytes(b"zip")
            with patch.object(
                telegram.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 6, "", token),
            ):
                with self.assertRaises(ValueError) as error:
                    telegram.send_document(archive, "caption", token, "-100123")
        self.assertNotIn(token, str(error.exception))


class CloneTests(unittest.TestCase):
    def test_public_and_private_clone_latest_message_and_remote_scrubbing(self):
        real_git = shutil.which("git")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            subprocess.run(
                [real_git, "init", "--initial-branch=staging", str(source)],
                check=True,
                capture_output=True,
            )
            for index in range(8):
                subprocess.run(
                    [
                        real_git,
                        "-C",
                        str(source),
                        "-c",
                        "user.name=Test",
                        "-c",
                        "user.email=test@example.org",
                        "commit",
                        "--allow-empty",
                        "-m",
                        f"Subject {index}",
                        "-m",
                        f"Body for commit {index}\n\nSigned-off-by: Test <test@example.org>",
                    ],
                    check=True,
                    capture_output=True,
                )
            binaries = root / "bin"
            binaries.mkdir()
            git_wrapper = binaries / "git"
            git_wrapper.write_text(
                "#!/usr/bin/env python3\nimport json, os, subprocess, sys\n"
                "args = sys.argv[1:]\n"
                "with open(os.environ['GIT_CALLS'], 'a') as f: f.write(json.dumps(args) + '\\n')\n"
                "args = [os.environ['SOURCE_URL'] if x.startswith('https://') and ('clone' in args or 'fetch' in args) else x for x in args]\n"
                "sys.exit(subprocess.call([os.environ['REAL_GIT'], *args]))\n"
            )
            git_wrapper.chmod(0o755)
            for mode in ("private", "public"):
                work = root / mode
                work.mkdir()
                calls = work / "git-calls.jsonl"
                environment = dict(
                    os.environ,
                    PATH=f"{binaries}:{os.environ['PATH']}",
                    REAL_GIT=real_git,
                    SOURCE_URL=source.as_uri(),
                    GIT_CALLS=str(calls),
                    BRANCH="staging",
                    REPO_VISIBILITY=mode,
                    GH_PAT="test-token",
                    KERNEL_REPOSITORY="https://github.com/test/kernel",
                )
                result = subprocess.run(
                    ["bash", str(SCRIPTS / "clone.sh")],
                    cwd=work,
                    env=environment,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                message = (work / "logs/commit-message.txt").read_text().strip()
                self.assertEqual(
                    message,
                    "Subject 7\n\nBody for commit 7\n\nSigned-off-by: Test <test@example.org>",
                )
                count = subprocess.check_output(
                    [real_git, "-C", str(work / "kernel"), "rev-list", "--count", "HEAD"], text=True
                ).strip()
                self.assertEqual(count, "1")
                config = (work / "kernel/.git/config").read_text()
                self.assertIn("https://github.com/test/kernel.git", config)
                self.assertNotIn("test-token", config)
                fetch_head = work / "kernel/.git/FETCH_HEAD"
                self.assertFalse(fetch_head.exists() and "test-token" in fetch_head.read_text())
                commands = [json.loads(line) for line in calls.read_text().splitlines()]
                clone = next(command for command in commands if "clone" in command)
                self.assertIn("--depth=1", clone)
                self.assertEqual(
                    any("x-access-token:test-token@" in value for value in clone), mode == "private"
                )
                self.assertFalse(any("fetch" in command for command in commands))
                self.assertLess(
                    next(i for i, cmd in enumerate(commands) if "set-url" in cmd),
                    next(i for i, cmd in enumerate(commands) if "log" in cmd),
                )

    def test_private_without_pat_never_invokes_git(self):
        with tempfile.TemporaryDirectory() as temporary:
            environment = dict(
                os.environ,
                REPO_VISIBILITY="private",
                GH_PAT="",
                BRANCH="staging",
                KERNEL_REPOSITORY="https://github.com/test/kernel",
            )
            result = subprocess.run(
                ["bash", str(SCRIPTS / "clone.sh")],
                cwd=temporary,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("GH_PAT", result.stderr)
            self.assertFalse((Path(temporary) / "kernel").exists())


class BuildShellTests(unittest.TestCase):
    def fixture(self, root):
        binary = root / "toolchain/bin"
        binary.mkdir(parents=True)
        config = root / "kernel/arch/arm64/configs/vendor/xiaomi/miatoll_defconfig"
        config.parent.mkdir(parents=True)
        config.write_text("CONFIG_TEST=y\n")
        for name, body in {
            "clang": "echo 'Android clang version 22.0.0'",
            "ld.lld": "echo 'LLD 22.0.0'",
            "ccache": "exit 0",
            "nproc": "echo 2",
            "make": """python3 - "$@" <<'PY'
import json, os, pathlib, sys
assert os.environ['KBUILD_BUILD_USER'] == os.environ['BUILD_ACTOR']
assert os.environ['KBUILD_BUILD_HOST'] == 'GitHub'
with open(os.environ['MAKE_CALLS'], 'a') as stream:
    stream.write(json.dumps(sys.argv[1:]) + '\\n')
boot = pathlib.Path('kernel/out/arch/arm64/boot')
(boot / 'dts/qcom').mkdir(parents=True, exist_ok=True)
(boot / 'dts/qcom/cust-atoll-test.dtb').write_bytes(b'dtb')
(boot / 'Image.gz').write_bytes(b'image')
(boot / 'Image.lz4').write_bytes(b'image')
(boot / 'dtbo.img').write_bytes(b'dtbo')
PY""",
        }.items():
            path = binary / name
            path.write_text("#!/usr/bin/env bash\nset -eu\n" + body + "\n")
            path.chmod(0o755)
        return binary

    def test_compat_compiler_and_linker_remain_owned_by_kernel_makefiles(self):
        script = (SCRIPTS / "build.sh").read_text()
        self.assertNotIn("CC_COMPAT=", script)
        self.assertNotIn("LD_COMPAT=", script)

    def test_make_receives_exact_compiler_environment_in_every_image_mode(self):
        required = [
            "KBUILD_BUILD_USER=WorkflowTrigger",
            "KBUILD_BUILD_HOST=GitHub",
            "CC=ccache clang",
            "CXX=ccache clang++",
            "HOSTCC=ccache /usr/bin/clang",
            "HOSTCXX=ccache /usr/bin/clang++",
            "CLANG_TRIPLE=aarch64-linux-gnu-",
            "CROSS_COMPILE=aarch64-linux-gnu-",
            "CROSS_COMPILE_ARM32=arm-linux-gnueabi-",
            "CROSS_COMPILE_COMPAT=arm-linux-gnueabi-",
            "LD=ld.lld",
            "AR=llvm-ar",
            "NM=llvm-nm",
            "STRIP=llvm-strip",
            "OBJCOPY=llvm-objcopy",
            "OBJDUMP=llvm-objdump",
            "READELF=llvm-readelf",
            "OBJSIZE=llvm-size",
        ]
        for mode in prepare.IMAGE_TYPES:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                binary = self.fixture(root)
                calls = root / "make-calls.jsonl"
                environment = dict(
                    os.environ,
                    DEFCONFIG="vendor/xiaomi/miatoll_defconfig",
                    BRANCH="staging",
                    IMAGE_TYPE=mode,
                    TOOLCHAIN_DIR=str(binary.parent),
                    TOOLCHAIN_BIN_REL="bin",
                    MAKE_CALLS=str(calls),
                    BUILD_ACTOR="WorkflowTrigger",
                    KBUILD_BUILD_USER="runner",
                    KBUILD_BUILD_HOST="hosted-runner",
                )
                result = subprocess.run(
                    ["bash", str(SCRIPTS / "build.sh")],
                    cwd=root,
                    env=environment,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                for command in (json.loads(line) for line in calls.read_text().splitlines()):
                    for argument in required:
                        self.assertIn(argument, command)
                self.assertTrue((root / "logs/build-duration.txt").is_file())

    def test_make_failure_is_not_hidden_by_tee(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = self.fixture(root)
            (binary / "make").write_text("#!/bin/sh\necho intentional-compiler-error >&2\nexit 2\n")
            environment = dict(
                os.environ,
                DEFCONFIG="vendor/xiaomi/miatoll_defconfig",
                BRANCH="staging",
                IMAGE_TYPE="Image.gz.dtb",
                BUILD_ACTOR="WorkflowTrigger",
                TOOLCHAIN_DIR=str(binary.parent),
                TOOLCHAIN_BIN_REL="bin",
            )
            result = subprocess.run(
                ["bash", str(SCRIPTS / "build.sh")],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("intentional-compiler-error", (root / "logs/defconfig.log").read_text())
            self.assertFalse((root / "logs/build-duration.txt").exists())

    def test_incomplete_cached_compiler_fails_before_path_export_or_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / "toolchain/bin"
            binary.mkdir(parents=True)
            clang = binary / "clang"
            clang.write_text("#!/bin/sh\necho MUST-NOT-EXECUTE\n")
            clang.chmod(0o755)
            environment = dict(
                os.environ,
                TOOLCHAIN_DIR=str(binary.parent),
                TOOLCHAIN_BIN_REL="bin",
                TOOLCHAIN_URL="https://invalid.example/no-network",
                TOOLCHAIN_HASH="fixture",
                GITHUB_PATH=str(root / "github-path"),
            )
            result = subprocess.run(
                ["bash", str(SCRIPTS / "toolchain.sh")],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("toolchain is incomplete", result.stdout + result.stderr)
            self.assertNotIn("MUST-NOT-EXECUTE", result.stdout)
            self.assertFalse((root / "github-path").exists())

    def test_cached_toolchain_requires_no_network_or_archive_download(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / "toolchain/bin"
            binary.mkdir(parents=True)
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
            ):
                tool = binary / name
                tool.write_text("#!/bin/sh\necho cached-tool\n")
                tool.chmod(0o755)
            environment = dict(
                os.environ,
                TOOLCHAIN_DIR=str(binary.parent),
                TOOLCHAIN_BIN_REL="bin",
                TOOLCHAIN_URL="https://invalid.example/no-network-allowed",
                TOOLCHAIN_HASH="hash",
                GITHUB_PATH=str(root / "github-path"),
            )
            result = subprocess.run(
                ["bash", str(SCRIPTS / "toolchain.sh")],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("cache hit", result.stdout)
            self.assertEqual(
                (root / "github-path").read_text().splitlines(), ["/usr/bin", str(binary)]
            )


if __name__ == "__main__":
    unittest.main()
