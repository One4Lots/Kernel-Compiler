"""Regression coverage for compact dispatch, numeric versions, and device ordering."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github/scripts/kernel_build"))

import prepare
from configuration import resolve_dispatch
from installer import InstallerConfig
from metadata import numeric_kernel_version, supported_devices


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "configs").mkdir()
        self.path = self.root / "configs/miatoll.json"
        self.original = (ROOT / "configs/miatoll.json").read_text()
        self.path.write_text(self.original)

    def test_production_defaults_and_small_dispatch_override(self):
        settings = resolve_dispatch({"config_file": "configs/miatoll.json"}, self.root)
        self.assertEqual(settings["defconfig"], "vendor/xiaomi/miatoll_defconfig")
        self.assertEqual(settings["image_type"], "Image.gz + dtbo.img + dtb.img")
        self.assertEqual(settings["device_codenames"], "curtana,gram,excalibur,joyeuse")
        changed = resolve_dispatch(
            {
                "kernel_rc": "rc9",
                "toolchain": "neutron-clang",
                "build_mode": "multi",
                "publish_to": "artifacts only",
            },
            self.root,
        )
        self.assertEqual(changed["kernel_rc"], "rc9")
        self.assertEqual(changed["toolchain"], "neutron-clang")
        self.assertEqual(changed["publish_to"], "artifacts only")
        self.assertEqual(len(prepare.make_matrix(changed)["include"]), 2)
        self.assertEqual(self.path.read_text(), self.original)

    def test_other_device_profiles_are_isolated_and_desktop_overrides_are_run_local(self):
        for device, aliases, mode in (
            ("surya", "surya,karna", "Image.gz.dtb"),
            ("alioth", "alioth,aliothin", "Image.lz4 + dtbo.img + dtb.img"),
        ):
            with self.subTest(device=device):
                profile = dict(
                    json.loads(self.original),
                    kernel_name="Example",
                    device_name=device,
                    device_codenames=aliases,
                    kernel_repository=f"https://github.com/example/kernel_{device}",
                    repo_visibility="public",
                    single_branch="device-branch",
                    multi_branches_json=[{"branch": "device-branch"}],
                    defconfig=f"vendor/{device}_defconfig",
                    anykernel_repository=f"https://github.com/example/AnyKernel3_{device}",
                    image_type=mode,
                )
                path = self.root / "configs" / f"{device}.json"
                text = json.dumps(profile)
                path.write_text(text)
                mobile = resolve_dispatch({"config_file": f"configs/{device}.json"}, self.root)
                self.assertEqual(mobile["device_name"], device)
                self.assertEqual(mobile["device_codenames"], aliases)
                self.assertEqual(mobile["anykernel_repository"], profile["anykernel_repository"])
                self.assertEqual(mobile["publish_to"], "artifacts only")
                entry = prepare.make_matrix(mobile)["include"][0]
                self.assertEqual(entry["branch"], "device-branch")
                self.assertEqual(entry["defconfig"], f"vendor/{device}_defconfig")
                self.assertEqual(entry["image_type"], mode)
                desktop = resolve_dispatch(
                    {
                        "config_file": f"configs/{device}.json",
                        "single_branch": "desktop-branch",
                        "device_codenames": device,
                    },
                    self.root,
                )
                self.assertEqual(
                    prepare.make_matrix(desktop)["include"][0]["branch"], "desktop-branch"
                )
                self.assertEqual(desktop["device_codenames"], device)
                self.assertEqual(mobile["single_branch"], "device-branch")
                self.assertEqual(path.read_text(), text)
                self.assertEqual(self.path.read_text(), self.original)

    def test_non_object_dispatch_fails_with_actionable_error(self):
        for value in (None, [], ["publish_to"], "personal only", True, 1):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "JSON object"):
                resolve_dispatch(value, self.root)

    def test_rejects_unsafe_paths_missing_files_and_symlinks(self):
        for name in (
            "../file.json",
            "configs/../file.json",
            "configs/nested/file.json",
            "configs/missing.json",
            "configs/.hidden.json",
            "/configs/miatoll.json",
            None,
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                resolve_dispatch({"config_file": name}, self.root)
        other = self.root / "other.json"
        other.write_text(self.original)
        self.path.unlink()
        self.path.symlink_to(other)
        with self.assertRaisesRegex(ValueError, "symlink"):
            resolve_dispatch({}, self.root)

    def test_rejects_symlinked_configs_directory(self):
        self.path.unlink()
        (self.root / "configs").rmdir()
        other = self.root / "other"
        other.mkdir()
        (other / "miatoll.json").write_text(self.original)
        (self.root / "configs").symlink_to(other, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            resolve_dispatch({}, self.root)

    def test_rejects_duplicate_unknown_missing_and_invalid_profile_fields(self):
        baseline = json.loads(self.original)
        cases = [
            "[]",
            "null",
            "{bad",
            self.original.replace(
                '"kernel_name": "Alecto",', '"kernel_name": "Alecto", "kernel_name": "Other",'
            ),
            json.dumps(dict(baseline, secret="not-allowed")),
            json.dumps({k: v for k, v in baseline.items() if k != "defconfig"}),
            json.dumps(dict(baseline, image_type=None)),
            json.dumps(dict(baseline, multi_branches_json="[]")),
            " " * 32769,
        ]
        for text in cases:
            with self.subTest(text=text[:80]), self.assertRaises(ValueError):
                self.path.write_text(text)
                resolve_dispatch({}, self.root)

    def test_rejects_unknown_or_empty_dispatch_values(self):
        for dispatch in ({"GH_PAT": "not-a-setting"}, {"kernel_rc": ""}, {"toolchain": None}):
            with self.subTest(dispatch=dispatch), self.assertRaises(ValueError):
                resolve_dispatch(dispatch, self.root)

    def test_compact_dispatch_emits_consistent_settings_and_matrix(self):
        output = self.root / "output"
        with (
            patch.dict(
                os.environ,
                {
                    "INPUTS_JSON": json.dumps(
                        {"config_file": "configs/miatoll.json", "publish_to": "artifacts only"}
                    ),
                    "GH_PAT": "fixture",
                    "GITHUB_OUTPUT": str(output),
                },
                clear=True,
            ),
            patch.object(sys, "argv", ["prepare.py", "matrix"]),
        ):
            prepare.main()
        values = dict(line.split("=", 1) for line in output.read_text().splitlines())
        settings = json.loads(values["settings"])
        matrix = json.loads(values["matrix"])
        self.assertEqual(settings["publish_to"], "artifacts only")
        self.assertEqual(matrix, prepare.make_matrix(settings))
        self.assertNotIn("fixture", output.read_text())


class NumericVersionTests(unittest.TestCase):
    def test_reported_slash_release_and_common_local_versions(self):
        for value, expected in (
            ("4.14.357-Alecto/8528922d", "4.14.357"),
            ("6.1.0", "6.1.0"),
            ("6.1.12+", "6.1.12"),
            ("6.12.0-rc1", "6.12.0"),
        ):
            with self.subTest(value=value):
                self.assertEqual(numeric_kernel_version(value), expected)

    def test_rejects_invalid_or_multiline_versions(self):
        for value in (
            "",
            "version-4.14.357",
            "4.14",
            "4.14.357garbage",
            "4.14.357\nother",
            "4.14.357\x00",
            "4.14.357-" + "a" * 4096,
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                numeric_kernel_version(value)


class DeviceListTests(unittest.TestCase):
    def test_package_label_never_becomes_implicit_device(self):
        config = InstallerConfig(
            kernel_name="Alecto",
            device_name="miatoll",
            release="4.14.357",
            toolchain="android-clang",
            actor="Builder",
            supported_versions="12.0-17.0",
            device_codenames="curtana, gram, excalibur, joyeuse",
        )
        properties = config.properties()
        self.assertEqual(
            {key: value for key, value in properties.items() if key.startswith("device.name")},
            {
                "device.name1": "curtana",
                "device.name2": "gram",
                "device.name3": "excalibur",
                "device.name4": "joyeuse",
            },
        )
        self.assertNotIn("miatoll", properties.values())

    def test_device_list_boundaries_and_order(self):
        devices = ",".join(f"phone-{n}" for n in range(32))
        self.assertEqual(len(supported_devices(devices)), 32)
        self.assertEqual(supported_devices("gram,curtana"), ("gram", "curtana"))
        for value in ("", "curtana,curtana", "curtana,", devices + ",phone-33"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                supported_devices(value)
