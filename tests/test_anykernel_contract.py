"""Offline compatibility checks against facts recorded from the current AnyKernel3 template."""

from __future__ import annotations

import itertools
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github/scripts/kernel_build"))

from installer import InstallerConfig, configure_installer

CONTRACT = json.loads((ROOT / "tests/fixtures/anykernel3-contract.json").read_text())
TAIL = """
BLOCK=boot;
IS_SLOT_DEVICE=0;
RAMDISK_COMPRESSION=auto;
PATCH_VBMETA_FLAG=auto;
NO_BLOCK_DISPLAY=1;
. tools/ak3-core.sh;
. "$AKHOME/tools/install-ui.sh";
ak_ui_enable_core_messages;
split_boot;
flash_boot;
"""


def config(**changes):
    values = dict(
        kernel_name="Orion",
        device_name="surya",
        release="4.14.357",
        toolchain="android-clang",
        actor="FixtureBuilder",
        supported_versions="12.0-17.0",
        device_codenames="surya,karna",
    )
    values.update(changes)
    return InstallerConfig(**values)


def template(with_slot=True, old_description=""):
    properties = [
        f"{key}={old_description if key == 'kernel.string' else value}"
        for key, value in CONTRACT["properties"]
        if with_slot or key != "kernel.string"
    ]
    return (
        "# Current-template property contract fixture\nproperties() { '\n"
        + "\n".join(properties)
        + "\n'; } # end properties\n"
        + TAIL
    )


class CurrentAnyKernelContractTests(unittest.TestCase):
    def test_recorded_ui_reads_named_metadata_without_a_description_field(self):
        self.assertEqual(
            CONTRACT["ui_metadata_properties"],
            ["kernel.maintainer", "kernel.name", "kernel.toolchain", "kernel.version"],
        )
        self.assertEqual(CONTRACT["device_display_source"], "getprop ro.product.device")
        self.assertIn(["kernel.string", ""], CONTRACT["properties"])

    def test_twelve_name_compiler_and_optional_description_slot_combinations(self):
        for name, compiler, slot in itertools.product(
            ("Orion", "Nova Kernel", "Zenith kernel"),
            ("android-clang", "neutron-clang"),
            (False, True),
        ):
            with (
                self.subTest(name=name, compiler=compiler, slot=slot),
                tempfile.TemporaryDirectory() as temporary,
            ):
                path = Path(temporary) / "anykernel.sh"
                path.write_text(template(slot, old_description="STALE WORKFLOW DESCRIPTION"))
                selected = config(kernel_name=name, toolchain=compiler)
                configure_installer(path, selected)
                result = path.read_text()
                expected = selected.properties()
                self.assertNotIn("kernel.string", expected)
                self.assertNotIn("STALE WORKFLOW DESCRIPTION", result)
                self.assertTrue(result.endswith(TAIL))
                body = result.split("properties() { '\n", 1)[1].split("'; }", 1)[0]
                lines = body.splitlines()
                keys = [line.split("=", 1)[0] for line in lines]
                ordered = (
                    ["kernel.name"]
                    + (["kernel.string"] if slot else [])
                    + [
                        "kernel.version",
                        "kernel.toolchain",
                        "kernel.maintainer",
                        "device.name1",
                        "device.name2",
                        "supported.versions",
                    ]
                )
                self.assertEqual(keys[: len(ordered)], ordered)
                if slot:
                    self.assertIn("kernel.string=", lines)
                else:
                    self.assertNotIn("kernel.string=", result)
                for key, value in expected.items():
                    self.assertIn(f"{key}={value}", lines)
                for key, value in CONTRACT["properties"]:
                    if key.startswith("do."):
                        self.assertIn(f"{key}={value}", lines)
                self.assertEqual(len(re.findall(r"(?m)^device\.name[0-9]+=", result)), 2)
                subprocess.run(["sh", "-n", str(path)], check=True)
                configure_installer(path, selected)
                self.assertEqual(path.read_text(), result)

    def test_extra_device_aliases_remain_ordered_before_compatibility_and_controls(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "anykernel.sh"
            path.write_text(template())
            configure_installer(path, config(device_codenames="curtana,gram,excalibur,joyeuse"))
            self.assertIn(
                "device.name1=curtana\ndevice.name2=gram\ndevice.name3=excalibur\ndevice.name4=joyeuse\nsupported.versions=12.0-17.0\ndo.devicecheck=1\n",
                path.read_text(),
            )

    def test_description_outside_properties_is_rejected_without_partial_rewrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "anykernel.sh"
            original = template(False) + "kernel.string=ambiguous-outside-description\n"
            path.write_text(original)
            with self.assertRaisesRegex(ValueError, "ambiguous kernel.string"):
                configure_installer(path, config())
            self.assertEqual(path.read_text(), original)


if __name__ == "__main__":
    unittest.main()
