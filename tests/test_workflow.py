"""Validate the actual workflow structure and public artifact contract."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github/scripts/kernel_build"))

import prepare
from configuration import resolve_dispatch
from delivery_modes import MODES
from support import inputs


def load_workflow():
    # BaseLoader keeps the YAML 1.2 event key `on` as text, not a YAML 1.1 bool.
    return yaml.load(
        (ROOT / ".github/workflows/kernel-pipeline.yml").read_text(), Loader=yaml.BaseLoader
    )


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = load_workflow()
        self.mobile = yaml.load(
            (ROOT / ".github/workflows/kernel-build.yml").read_text(), Loader=yaml.BaseLoader
        )
        self.desktop = yaml.load(
            (ROOT / ".github/workflows/kernel-desktop.yml").read_text(), Loader=yaml.BaseLoader
        )
        self.build = self.workflow["jobs"]["build"]
        steps = {step.get("id"): step for step in self.build["steps"]}
        self.payload = steps["payload-artifact"]
        self.bundle = steps["publisher-bundle"]
        self.diagnostics = steps["diagnostics-artifact"]
        self.patterns = self.payload["with"]["path"].splitlines()

    def test_dispatch_is_small_and_every_step_has_a_short_display_name(self):
        declared = self.mobile["on"]["workflow_dispatch"]["inputs"]
        self.assertEqual(
            set(declared),
            {
                "config_file",
                "kernel_rc",
                "build_mode",
                "toolchain",
                "publish_to",
            },
        )
        for path in (ROOT / ".github/workflows").glob("*.yml"):
            workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
            for job in workflow["jobs"].values():
                for step in job.get("steps", []):
                    self.assertTrue(step.get("name"), step)
                    self.assertLessEqual(len(step["name"]), 30)

    def test_mobile_and_desktop_have_distinct_names_in_lists_runs_and_jobs(self):
        for workflow, label, emoji in (
            (self.mobile, "Mobile", "📱"),
            (self.desktop, "Desktop", "🖥️"),
        ):
            with self.subTest(label=label):
                self.assertEqual(workflow["name"], f"{emoji} {label} Kernel Build")
                self.assertTrue(workflow["run-name"].startswith(f"{emoji} {label} · "))
                self.assertEqual(workflow["jobs"]["build"]["name"], f"{emoji} {label} pipeline")
                self.assertEqual(
                    workflow["jobs"]["build"]["uses"], "./.github/workflows/kernel-pipeline.yml"
                )

    def test_both_entrypoints_call_one_pipeline_with_identical_delivery_modes(self):
        for workflow in (self.mobile, self.desktop):
            declared = workflow["on"]["workflow_dispatch"]["inputs"]
            self.assertLessEqual(len(declared), 25)
            self.assertNotIn("telegram_release", declared)
            self.assertEqual(declared["publish_to"]["default"], "artifacts only")
            self.assertEqual(declared["publish_to"]["options"], list(MODES))
            job = workflow["jobs"]["build"]
            self.assertEqual(job["uses"], "./.github/workflows/kernel-pipeline.yml")
            self.assertEqual(job["with"]["request_json"], "${{ toJSON(inputs) }}")
            self.assertEqual(set(job["with"]), {"request_json"})
        self.assertIn("workflow_call", self.workflow["on"])
        self.assertNotIn("workflow_dispatch", self.workflow["on"])

    def test_desktop_inheritance_and_overrides_validate_without_duplicate_pipeline(self):
        declared = self.desktop["on"]["workflow_dispatch"]["inputs"]
        dispatch = {
            key: (entry["default"] == "true" if entry["type"] == "boolean" else entry["default"])
            for key, entry in declared.items()
        }
        mobile_defaults = resolve_dispatch(
            {
                key: (
                    entry["default"] == "true" if entry["type"] == "boolean" else entry["default"]
                )
                for key, entry in self.mobile["on"]["workflow_dispatch"]["inputs"].items()
            }
        )
        self.assertEqual(resolve_dispatch(dispatch), mobile_defaults)
        changed = resolve_dispatch(
            dict(
                dispatch,
                device_codenames="gram,curtana",
                defconfig="vendor/custom_defconfig",
                image_type="Image.lz4 + dtbo.img + dtb.img",
                single_branch="custom/branch",
                commit_count="9",
                multi_branches_json='[{"branch":"custom/branch"}]',
            )
        )
        (entry,) = prepare.make_matrix(changed)["include"]
        self.assertEqual(entry["defconfig"], "vendor/custom_defconfig")
        self.assertEqual(entry["branch"], "custom/branch")
        self.assertEqual(changed["device_codenames"], "gram,curtana")
        self.assertEqual(json.loads(changed["multi_branches_json"]), [{"branch": "custom/branch"}])

    def test_channel_release_is_isolated_from_compilation_and_requires_all_builds(self):
        channel = self.workflow["jobs"]["telegram_release"]
        self.assertEqual(channel["needs"], ["prepare", "build"])
        self.assertIn("needs.build.result == 'success'", channel["if"])
        self.assertIn("fromJSON(needs.prepare.outputs.delivery).channel", channel["if"])
        self.assertNotIn("TELEGRAM_BOT_TOKEN", json.dumps(self.build))
        self.assertNotIn("TELEGRAM_CHANNEL_ID", json.dumps(self.build))
        self.assertNotIn("telegram.py", json.dumps(self.build))
        self.assertIn("telegram_release.py", json.dumps(channel))
        self.assertEqual(channel["concurrency"]["cancel-in-progress"], "false")
        self.assertEqual(
            channel["steps"][-1]["env"]["TELEGRAM_CHANNEL_CONFIRMED"],
            "true",
        )

    def test_personal_telegram_job_is_independent_from_channel_checkbox(self):
        private = self.workflow["jobs"]["telegram_delivery"]
        self.assertIn("fromJSON(needs.prepare.outputs.delivery).personal", private["if"])
        self.assertNotIn("inputs.telegram_release", private["if"])
        self.assertNotIn("TELEGRAM_CHANNEL_ID", json.dumps(private))
        self.assertNotIn("TELEGRAM_CHAT_ID", json.dumps(self.workflow["jobs"]["telegram_release"]))
        self.assertNotIn("TELEGRAM_CHAT_ID", json.dumps(self.build))
        self.assertIn("telegram_delivery.py", json.dumps(private))
        self.assertEqual(self.bundle["if"], "fromJSON(needs.prepare.outputs.delivery).bundles")

    def test_downstream_jobs_use_resolved_profile_settings(self):
        prepare_job = self.workflow["jobs"]["prepare"]
        self.assertEqual(prepare_job["outputs"]["settings"], "${{ steps.matrix.outputs.settings }}")
        self.assertEqual(
            self.build["env"]["DEVICE_CODENAMES"],
            "${{ fromJSON(needs.prepare.outputs.settings).device_codenames }}",
        )
        for job_name in ("build", "publish_release"):
            self.assertNotIn("inputs.", json.dumps(self.workflow["jobs"][job_name]))

    def test_readme_documents_every_dispatch_input(self):
        readme = (ROOT / "README.md").read_text()
        for workflow in (self.mobile, self.desktop):
            for name in workflow["on"]["workflow_dispatch"]["inputs"]:
                self.assertIn(f"| `{name}` |", readme)

    def test_readme_action_table_matches_verified_pins(self):
        readme = (ROOT / "README.md").read_text()
        contracts = json.loads((ROOT / "tests/fixtures/actions-contracts.json").read_text())[
            "actions"
        ]
        for contract in contracts.values():
            self.assertIn(f"`{contract['version']}`", readme)
            self.assertIn(f"`{contract['sha']}`", readme)
        self.assertIn("`ubuntu-latest`", readme)

    def test_only_supported_images_trees_and_atoll_custom_trees_are_allowed(self):
        self.assertEqual(
            self.patterns,
            [
                "artifacts/Image.gz.dtb",
                "artifacts/Image.gz",
                "artifacts/Image.lz4",
                "artifacts/dtbo.img",
                "artifacts/dtb.img",
                "artifacts/cust-atoll*.dtb",
            ],
        )
        self.assertEqual(self.payload["with"]["retention-days"], "14")
        self.assertEqual(self.payload["with"]["if-no-files-found"], "error")

    def test_zip_nested_zip_and_unrelated_custom_trees_are_not_uploaded(self):
        allowed = {
            "Image.gz.dtb",
            "Image.gz",
            "Image.lz4",
            "dtbo.img",
            "dtb.img",
            "cust-atoll.dtb",
            "cust-atoll-test.dtb",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "artifacts"
            (artifacts / "17.0").mkdir(parents=True)
            for name in allowed | {
                "kernel.zip",
                "17.0/kernel-rc1.zip",
                "cust-other.dtb",
                "notes.txt",
            }:
                (artifacts / name).write_bytes(b"test")
            selected = {
                path.relative_to(artifacts).as_posix()
                for pattern in self.patterns
                for path in root.glob(pattern)
            }
            self.assertEqual(selected, allowed)

    def test_validation_job_uses_dependency_free_runtime_before_development_venv(self):
        workflow = yaml.load(
            (ROOT / ".github/workflows/validate.yml").read_text(), Loader=yaml.BaseLoader
        )
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertIn("workflow_dispatch", workflow["on"])
        self.assertIn("push", workflow["on"])
        self.assertIn("pull_request", workflow["on"])
        job = workflow["jobs"]["validate"]
        self.assertEqual(job["runs-on"], "ubuntu-latest")
        steps = job.get("steps", [])
        self.assertRegex(steps[0]["uses"], r"^actions/checkout@[a-f0-9]{40}$")
        self.assertEqual(steps[0]["with"]["persist-credentials"], "false")
        self.assertNotIn("pyfiglet", str(workflow))
        self.assertIn("/usr/bin/python3 -m unittest", steps[2]["run"])
        self.assertIn("-m venv", steps[3]["run"])
        self.assertNotIn("secrets.", str(workflow))

    def test_publisher_transfer_excludes_duplicate_raw_images_and_keeps_nested_zips(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            files = (
                "artifacts/staging.zip",
                "artifacts/17.0/modern-sched.zip",
                "artifacts/Image.gz",
                "artifacts/dtb.img",
                "artifacts/cust-atoll.dtb",
                "logs/release-manifest.json",
                "logs/clang-version.txt",
                "logs/lld-version.txt",
                "logs/commit-message.txt",
                "logs/recent-commits.json",
                "logs/private-unrelated.txt",
            )
            for filename in files:
                path = root / filename
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture")
            selected = {
                path.relative_to(root).as_posix()
                for pattern in self.bundle["with"]["path"].splitlines()
                for path in root.glob(pattern)
            }
            self.assertEqual(selected, set(files[:2]) | set(files[5:10]))

    def test_dispatch_forms_keep_exact_requested_order_and_common_defaults(self):
        expected = [
            "artifacts only",
            "personal only",
            "personal+channel",
            "personal+github",
            "channel+github",
            "personal+channel+github",
        ]
        mobile = self.mobile["on"]["workflow_dispatch"]["inputs"]
        desktop = self.desktop["on"]["workflow_dispatch"]["inputs"]
        self.assertEqual(len(desktop), 21)
        for name, value in mobile.items():
            self.assertEqual(value, desktop[name])
        self.assertEqual(mobile["publish_to"]["options"], expected)
        self.assertEqual(desktop["publish_to"]["options"], expected)

    def test_dispatch_defaults_and_all_build_modes_validate(self):
        declared = self.mobile["on"]["workflow_dispatch"]["inputs"]
        self.assertLessEqual(len(declared), 5)
        self.assertNotIn("release_tag", declared)
        defaults = resolve_dispatch(
            {
                key: (
                    entry["default"] == "true" if entry["type"] == "boolean" else entry["default"]
                )
                for key, entry in declared.items()
            }
        )
        self.assertEqual(defaults["kernel_rc"], "rc1")
        for toolchain in prepare.TOOLCHAINS:
            for mode in ("single", "multi"):
                for image in prepare.IMAGE_TYPES:
                    with self.subTest(toolchain=toolchain, mode=mode, image=image):
                        matrix = prepare.make_matrix(
                            dict(defaults, toolchain=toolchain, build_mode=mode, image_type=image)
                        )
                        self.assertEqual(len(matrix["include"]), 1 if mode == "single" else 2)
                        self.assertTrue(all(entry["branch"] for entry in matrix["include"]))
        for name in inputs():
            self.assertIn(name, defaults)

    def test_build_actor_tracks_dispatch_and_rerun_initiator(self):
        self.assertEqual(
            self.build["env"]["BUILD_ACTOR"], "${{ github.triggering_actor || github.actor }}"
        )

    def test_credentials_and_publisher_are_isolated_from_compilation(self):
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        self.assertNotIn("GH_TOKEN", self.build["env"])
        self.assertNotIn("GH_RELEASE_TOKEN", self.build["env"])
        self.assertEqual(
            self.build["env"]["KERNEL_RC"],
            "${{ fromJSON(needs.prepare.outputs.settings).kernel_rc }}",
        )
        self.assertNotIn("BRANCH_SLUG", self.build["env"])
        for step in self.build["steps"]:
            self.assertNotIn("GH_RELEASE_TOKEN", str(step))
        self.assertIn(
            "fromJSON(needs.prepare.outputs.delivery).bundles",
            self.bundle["if"],
        )
        self.assertEqual(
            self.bundle["with"]["path"].splitlines(),
            [
                "artifacts/**/*.zip",
                "logs/release-manifest.json",
                "logs/clang-version.txt",
                "logs/lld-version.txt",
                "logs/commit-message.txt",
                "logs/recent-commits.json",
            ],
        )
        self.assertEqual(self.diagnostics["with"]["path"], "logs/")
        publisher = self.workflow["jobs"]["publish_release"]
        self.assertIn("release_lock", publisher["concurrency"]["group"])
        download = next(
            step
            for step in publisher["steps"]
            if step.get("uses", "").startswith("actions/download-artifact@")
        )
        self.assertEqual(download["with"]["merge-multiple"], "false")

    def test_all_hosts_and_action_inputs_match_the_verified_release_contracts(self):
        contracts = json.loads((ROOT / "tests/fixtures/actions-contracts.json").read_text())[
            "actions"
        ]
        for path in (ROOT / ".github/workflows").glob("*.yml"):
            workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
            for job in workflow["jobs"].values():
                if "uses" in job:
                    self.assertEqual(job["uses"], "./.github/workflows/kernel-pipeline.yml")
                    continue
                self.assertEqual(job["runs-on"], "ubuntu-latest")
                for step in job.get("steps", []):
                    if "uses" not in step:
                        continue
                    action, sha = step["uses"].split("@")
                    contract = contracts[action]
                    self.assertEqual(sha, contract["sha"])
                    self.assertEqual(contract["runtime"], "node24")
                    provided = set(step.get("with", {}))
                    self.assertTrue(provided <= set(contract["inputs"]), step)
                    self.assertTrue(set(contract["required_inputs"]) <= provided, step)

    def test_latest_artifact_actions_keep_archive_layout_and_integrity_checks(self):
        for upload in (self.payload, self.bundle, self.diagnostics):
            self.assertEqual(upload["with"]["archive"], "true")
        publisher = self.workflow["jobs"]["publish_release"]
        download = next(
            step
            for step in publisher["steps"]
            if step.get("uses", "").startswith("actions/download-artifact@")
        )
        self.assertEqual(download["with"]["skip-decompress"], "false")
        self.assertEqual(download["with"]["digest-mismatch"], "error")
        self.assertEqual(download["with"]["merge-multiple"], "false")

    def test_all_cache_restores_are_partitioned_by_actual_host_before_use(self):
        host_index = next(
            index for index, step in enumerate(self.build["steps"]) if step.get("id") == "host"
        )
        for index, step in enumerate(self.build["steps"]):
            if step.get("uses", "").startswith("actions/cache/restore@"):
                self.assertGreater(index, host_index)
                for key in ("key", "restore-keys"):
                    if key not in step["with"]:
                        continue
                    for line in step["with"][key].splitlines():
                        self.assertIn("steps.host.outputs.cache_namespace", line)
                        self.assertNotIn("24.04", line)
                        self.assertIn("-v3-" if "kernel-build-toolchain-" in line else "-v2-", line)

    def test_actions_are_pinned_and_build_concurrency_is_bounded(self):
        for job in self.workflow["jobs"].values():
            for step in job.get("steps", []):
                if "uses" in step:
                    self.assertRegex(step["uses"], r"^[\w/-]+@[a-f0-9]{40}$")
        self.assertEqual(self.build["strategy"]["max-parallel"], "2")
        self.assertEqual(self.build["strategy"]["fail-fast"], "false")


class RunnerNamespaceTests(unittest.TestCase):
    def test_ubuntu_image_migrations_and_architectures_have_separate_namespaces(self):
        old = prepare.runner_cache_namespace({"ID": "ubuntu", "VERSION_ID": "24.04"}, "x86_64")
        new = prepare.runner_cache_namespace({"ID": "ubuntu", "VERSION_ID": "26.04"}, "x86_64")
        arm = prepare.runner_cache_namespace({"ID": "ubuntu", "VERSION_ID": "26.04"}, "aarch64")
        self.assertEqual(new, "ubuntu-26.04-x86_64")
        self.assertEqual(len({old, new, arm}), 3)

    def test_incomplete_or_unsafe_runner_metadata_is_rejected(self):
        for release, machine in (
            ({}, "x86_64"),
            ({"ID": "ubuntu", "VERSION_ID": "26.04\nother=bad"}, "x86_64"),
            ({"ID": None, "VERSION_ID": "26.04"}, "x86_64"),
            ({"ID": "ubuntu", "VERSION_ID": "26.04"}, "bad/arch"),
        ):
            with self.subTest(release=release, machine=machine), self.assertRaises(ValueError):
                prepare.runner_cache_namespace(release, machine)

    def test_runner_command_emits_the_actual_host_namespace(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "outputs"
            with (
                patch.dict(os.environ, GITHUB_OUTPUT=str(output)),
                patch.object(sys, "argv", ["prepare.py", "runner"]),
                patch.object(
                    prepare.platform,
                    "freedesktop_os_release",
                    return_value={"ID": "ubuntu", "VERSION_ID": "26.04"},
                ),
                patch.object(prepare.platform, "machine", return_value="x86_64"),
            ):
                prepare.main()
            self.assertEqual(output.read_text(), "cache_namespace=ubuntu-26.04-x86_64\n")


if __name__ == "__main__":
    unittest.main()
