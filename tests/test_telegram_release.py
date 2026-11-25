"""Fail-closed authorization and all-builds-first channel-release verification."""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github/scripts/kernel_build"))

import prepare
import telegram
import telegram_delivery
import telegram_release
from configuration import resolve_dispatch
from delivery_modes import MODES, delivery_plan
from naming import package_relative_path
from publication_settings import PublicationSettings
from support import inputs


class TelegramReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.downloads = self.root / "release-downloads"
        self.values = inputs(
            kernel_name="Nova",
            device_name="phone",
            kernel_rc="rc2",
            build_mode="multi",
            multi_branches_json=json.dumps(
                [{"branch": "17.0/modern-sched"}, {"branch": "staging"}]
            ),
            publish_to="personal+channel",
            changelog_mode="latest-message",
            commit_count=3,
        )
        self.expected = prepare.make_matrix(self.values)["include"]
        self.bundles = [self.create_bundle(entry) for entry in self.expected]

    def create_bundle(self, entry):
        bundle = self.downloads / entry["id"]
        logs = bundle / "logs"
        logs.mkdir(parents=True)
        relative = package_relative_path(entry["branch"], "Nova", "phone", "rc2", "25112026")
        archive = bundle / "artifacts" / relative
        archive.parent.mkdir(parents=True)
        with ZipFile(archive, "w") as zipped:
            zipped.writestr("Image.gz", b"SYNTHETIC NON-FLASHABLE FIXTURE")
        data = archive.read_bytes()
        meta = dict(
            schema=3,
            matrix_id=entry["id"],
            branch=entry["branch"],
            defconfig=entry["defconfig"],
            image_type=entry["image_type"],
            source_repository=self.values["kernel_repository"],
            kernel_name="Nova",
            device_name="phone",
            kernel_rc="rc2",
            zip_relative_path=relative.as_posix(),
            zip_name=archive.name,
            zip_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            md5="untrusted-recomputed",
            date="25112026",
            time="04:29 PM",
            built_at="2026-11-25T16:29:00+06:00",
            duration_seconds=125,
            commits=[{"sha": "a" * 40, "subject": "Fix scheduling"}],
        )
        (logs / "release-manifest.json").write_text(json.dumps(meta))
        (logs / "clang-version.txt").write_text("Android clang version 22.0.0\n")
        (logs / "lld-version.txt").write_text("LLD 22.0.0\n")
        (logs / "commit-message.txt").write_text("Fix scheduling\n\nPreserve the complete body.\n")
        (logs / "recent-commits.json").write_text(json.dumps(meta["commits"]))
        return bundle, archive

    @contextmanager
    def environment(self, **changes):
        environment = {
            "TELEGRAM_CHANNEL_CONFIRMED": "true",
            "TELEGRAM_BOT_TOKEN": "123:fixture",
            "TELEGRAM_CHANNEL_ID": "-100123",
            "INPUTS_JSON": json.dumps(self.values),
            "MATRIX_JSON": json.dumps({"include": self.expected}),
        }
        environment.update(changes)
        previous = Path.cwd()
        try:
            os.chdir(self.root)
            with patch.dict(os.environ, environment, clear=True):
                yield
        finally:
            os.chdir(previous)

    def test_disabled_entrypoints_never_read_builds_or_send_even_with_secrets(self):
        for value in ("", "false", "False", "TRUE", "1"):
            with (
                self.subTest(value=value),
                self.environment(TELEGRAM_CHANNEL_CONFIRMED=value, INPUTS_JSON="invalid"),
                patch.object(telegram_release, "send_document") as send,
                patch.object(telegram, "telegram_request") as request,
            ):
                telegram_release.main()
                telegram.main()
                send.assert_not_called()
                request.assert_not_called()

    def test_explicit_release_sends_complete_verified_set(self):
        with (
            self.environment(),
            patch.object(telegram_release, "send_document") as send,
            patch.object(telegram_release, "send_changelog") as followup,
        ):
            telegram_release.main()
        self.assertEqual(send.call_count, 2)
        followup.assert_not_called()
        for call, (_, archive) in zip(send.call_args_list, self.bundles, strict=True):
            self.assertEqual((self.root / call.args[0]).resolve(), archive.resolve())
            self.assertIn("Preserve the complete body.", call.args[1])
            self.assertIn(hashlib.md5(archive.read_bytes()).hexdigest(), call.args[1])
            self.assertNotIn("untrusted-recomputed", call.args[1])
            self.assertEqual(call.args[3], "-100123")

    def test_single_flat_download_sends_personal_zip_for_plain_and_slash_branches(self):
        for entry in self.expected:
            for mode in ("latest-message", "recent-commits"):
                with (
                    self.subTest(branch=entry["branch"], changelog=mode),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    # v8 flattens a sole pattern match despite merge-multiple: false.
                    source = self.downloads / entry["id"]
                    root = Path(temporary)
                    shutil.copytree(source, root / "release-downloads")
                    values = dict(
                        self.values,
                        publish_to="personal only",
                        changelog_mode=mode,
                        build_mode="single",
                        single_branch=entry["branch"],
                    )
                    with (
                        patch.object(self, "root", root),
                        self.environment(
                            TELEGRAM_DELIVERY_CONFIRMED="true",
                            TELEGRAM_CHAT_ID="123456789",
                            INPUTS_JSON=json.dumps(values),
                            MATRIX_JSON=json.dumps({"include": [entry]}),
                        ),
                        patch.object(telegram_release, "send_document") as send,
                        patch.object(telegram_release, "send_changelog"),
                    ):
                        telegram_delivery.main()
                    send.assert_called_once()
                    self.assertEqual(send.call_args.args[3], "123456789")
                    self.assertIn("Fix scheduling", send.call_args.args[1])

    def test_last_build_integrity_failure_prevents_all_posts(self):
        self.bundles[-1][1].write_bytes(b"corrupted")
        with (
            self.environment(),
            patch.object(telegram_release, "send_document") as send,
            self.assertRaisesRegex(ValueError, "integrity"),
        ):
            telegram_release.main()
        send.assert_not_called()

    def test_missing_build_prevents_partial_release(self):
        (self.bundles[-1][0] / "logs/release-manifest.json").unlink()
        with (
            self.environment(),
            patch.object(telegram_release, "send_document") as send,
            self.assertRaisesRegex(ValueError, "Not all"),
        ):
            telegram_release.main()
        send.assert_not_called()

    def test_invalid_last_timestamp_blocks_both_destinations_before_any_send(self):
        manifest = self.bundles[-1][0] / "logs/release-manifest.json"
        original = json.loads(manifest.read_text())
        for changes in (
            dict(built_at="2026-11-25T16:29:00"),
            dict(built_at=None),
            dict(time="04:29 AM"),
            dict(schema=2),
        ):
            manifest.write_text(json.dumps(dict(original, **changes)))
            for publisher in (telegram_delivery.main, telegram_release.main):
                with (
                    self.subTest(changes=changes, publisher=publisher.__module__),
                    self.environment(
                        TELEGRAM_DELIVERY_CONFIRMED="true", TELEGRAM_CHAT_ID="123456789"
                    ),
                    patch.object(telegram_release, "send_document") as send,
                    self.assertRaises(ValueError),
                ):
                    publisher()
                send.assert_not_called()

    def test_unparseable_last_compiler_log_prevents_any_send(self):
        (self.bundles[-1][0] / "logs/clang-version.txt").write_text("not-a-compiler-version")
        with (
            self.environment(),
            patch.object(telegram_release, "send_document") as send,
            self.assertRaisesRegex(ValueError, "actual Clang version"),
        ):
            telegram_release.main()
        send.assert_not_called()

    def test_last_build_missing_caption_metadata_prevents_all_posts(self):
        (self.bundles[-1][0] / "logs/clang-version.txt").unlink()
        with (
            self.environment(),
            patch.object(telegram_release, "send_document") as send,
            self.assertRaisesRegex(ValueError, "metadata"),
        ):
            telegram_release.main()
        send.assert_not_called()

    def test_oversized_package_prevents_all_posts(self):
        with (
            self.environment(),
            patch.object(telegram_release, "DOCUMENT_LIMIT", 1),
            patch.object(telegram_release, "send_document") as send,
            self.assertRaisesRegex(ValueError, "50 MB"),
        ):
            telegram_release.main()
        send.assert_not_called()

    def test_symlinked_or_oversized_caption_log_is_rejected(self):
        log = self.bundles[-1][0] / "logs/commit-message.txt"
        log.unlink()
        outside = self.root / "outside"
        outside.write_text("outside log")
        log.symlink_to(outside)
        with self.assertRaises(ValueError):
            telegram_release.prepare_release(self.downloads, self.expected, self.values)
        log.unlink()
        log.write_text("x" * 1_048_577)
        with self.assertRaisesRegex(ValueError, "oversized"):
            telegram_release.prepare_release(self.downloads, self.expected, self.values)

    def test_invalid_last_caption_prevents_all_posts(self):
        (self.bundles[-1][0] / "logs/commit-message.txt").write_text(" \n")
        with (
            self.environment(),
            patch.object(telegram_release, "send_document") as send,
            self.assertRaisesRegex(ValueError, "empty"),
        ):
            telegram_release.main()
        send.assert_not_called()

    def test_channel_sender_rejects_modes_without_channel_permission(self):
        for mode in ("artifacts only", "personal only", "personal+github"):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, "not selected"):
                telegram_release.prepare_release(
                    self.downloads,
                    self.expected,
                    dict(self.values, publish_to=mode, release_repository="owner/releases"),
                )

    def test_missing_channel_secret_does_not_fall_back_to_old_chat_secret(self):
        with (
            self.environment(TELEGRAM_CHANNEL_ID="", TELEGRAM_CHAT_ID="old-test-chat"),
            patch.object(telegram_release, "send_document") as send,
            self.assertRaisesRegex(ValueError, "TELEGRAM_CHANNEL_ID"),
        ):
            telegram_release.main()
        send.assert_not_called()

    def test_mismatched_matrix_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "matrix"):
            telegram_release.prepare_release(self.downloads, self.expected[:1], self.values)

    def test_network_failure_is_not_blindly_retried(self):
        with (
            self.environment(),
            patch.object(
                telegram_release,
                "send_document",
                side_effect=ValueError("ambiguous network failure"),
            ) as send,
            self.assertRaises(ValueError),
        ):
            telegram_release.main()
        send.assert_called_once()

    def test_all_six_modes_deliver_only_to_their_selected_telegram_destinations(self):
        for mode in MODES:
            plan = delivery_plan(mode)
            with (
                self.subTest(mode=mode),
                self.environment(
                    INPUTS_JSON=json.dumps(
                        dict(self.values, publish_to=mode, release_repository="owner/releases")
                    ),
                    TELEGRAM_CHAT_ID="123456789",
                    TELEGRAM_CHANNEL_CONFIRMED="true" if plan.channel else "false",
                    TELEGRAM_DELIVERY_CONFIRMED="true" if plan.personal else "false",
                ),
                patch.object(telegram_release, "send_document") as send,
            ):
                telegram_delivery.main()
                telegram_release.main()
            expected = (["123456789"] * len(self.expected) if plan.personal else []) + (
                ["-100123"] * len(self.expected) if plan.channel else []
            )
            self.assertEqual([call.args[3] for call in send.call_args_list], expected)

    def test_channel_rejects_personal_id_and_does_not_use_deprecated_secret(self):
        for channel_id in ("123456789", ""):
            with (
                self.subTest(channel_id=channel_id),
                self.environment(
                    TELEGRAM_CHANNEL_ID=channel_id, TELEGRAM_RELEASE_CHAT_ID="-100999"
                ),
                patch.object(telegram_release, "send_document") as send,
                self.assertRaisesRegex(ValueError, "TELEGRAM_CHANNEL_ID"),
            ):
                telegram_release.main()
            send.assert_not_called()

    def test_malformed_bot_token_fails_before_any_send(self):
        with (
            self.environment(TELEGRAM_BOT_TOKEN="invalid-token"),
            patch.object(telegram_release, "send_document") as send,
            self.assertRaisesRegex(ValueError, "TELEGRAM_BOT_TOKEN"),
        ):
            telegram_release.main()
        send.assert_not_called()

    def test_both_telegram_jobs_route_to_distinct_destinations(self):
        with (
            self.environment(TELEGRAM_DELIVERY_CONFIRMED="true", TELEGRAM_CHAT_ID="123456789"),
            patch.object(telegram_release, "send_document") as send,
        ):
            telegram_delivery.main()
            telegram_release.main()
        destinations = [call.args[3] for call in send.call_args_list]
        self.assertEqual(
            destinations, ["123456789"] * len(self.expected) + ["-100123"] * len(self.expected)
        )

    def test_personal_delivery_uses_only_personal_chat_without_channel_opt_in(self):
        values = dict(self.values, publish_to="personal only")
        with (
            self.environment(
                INPUTS_JSON=json.dumps(values),
                TELEGRAM_CHANNEL_CONFIRMED="false",
                TELEGRAM_DELIVERY_CONFIRMED="true",
                TELEGRAM_CHAT_ID="123456789",
                TELEGRAM_CHANNEL_ID="",
            ),
            patch.object(telegram_release, "send_document") as send,
        ):
            telegram_delivery.main()
            telegram_release.main()
        self.assertEqual(send.call_count, len(self.expected))
        self.assertTrue(all(call.args[3] == "123456789" for call in send.call_args_list))

    def test_channel_only_does_not_require_personal_destination(self):
        with (
            self.environment(
                INPUTS_JSON=json.dumps(
                    dict(
                        self.values,
                        publish_to="channel+github",
                        release_repository="owner/releases",
                    )
                )
            ),
            patch.object(telegram_release, "send_document") as send,
        ):
            telegram_release.main()
            telegram_delivery.main()
        self.assertEqual(send.call_count, len(self.expected))
        self.assertTrue(all(call.args[3] == "-100123" for call in send.call_args_list))

    def test_personal_delivery_never_falls_back_to_channel_id(self):
        with (
            self.environment(TELEGRAM_DELIVERY_CONFIRMED="true", TELEGRAM_CHAT_ID=""),
            patch.object(telegram_release, "send_document") as send,
            self.assertRaisesRegex(ValueError, "TELEGRAM_CHAT_ID"),
        ):
            telegram_delivery.main()
        send.assert_not_called()

    def test_personal_delivery_rejects_a_channel_or_group_id(self):
        for chat_id in ("-100123", "@channel", "0", ""):
            with (
                self.subTest(chat_id=chat_id),
                self.environment(TELEGRAM_DELIVERY_CONFIRMED="true", TELEGRAM_CHAT_ID=chat_id),
                patch.object(telegram_release, "send_document") as send,
                self.assertRaises(ValueError),
            ):
                telegram_delivery.main()
            send.assert_not_called()

    def test_personal_job_rejects_a_nonpersonal_delivery_plan(self):
        with (
            self.environment(
                TELEGRAM_DELIVERY_CONFIRMED="true",
                TELEGRAM_CHAT_ID="123456789",
                INPUTS_JSON=json.dumps(
                    dict(
                        self.values,
                        publish_to="channel+github",
                        release_repository="owner/releases",
                    )
                ),
            ),
            patch.object(telegram_release, "send_document") as send,
            self.assertRaisesRegex(ValueError, "not selected"),
        ):
            telegram_delivery.main()
        send.assert_not_called()

    def test_personal_integrity_failure_prevents_all_private_posts(self):
        self.bundles[-1][1].write_bytes(b"corrupt")
        with (
            self.environment(TELEGRAM_DELIVERY_CONFIRMED="true", TELEGRAM_CHAT_ID="123456789"),
            patch.object(telegram_release, "send_document") as send,
            self.assertRaisesRegex(ValueError, "integrity"),
        ):
            telegram_delivery.main()
        send.assert_not_called()


class DeliveryModeTests(unittest.TestCase):
    EXPECTED = {
        "artifacts only": (False, False, False),
        "personal only": (True, False, False),
        "personal+channel": (True, True, False),
        "personal+github": (True, False, True),
        "channel+github": (False, True, True),
        "personal+channel+github": (True, True, True),
    }

    def test_exact_six_mode_routing_contract(self):
        self.assertEqual(list(MODES), list(self.EXPECTED))
        for mode, expected in self.EXPECTED.items():
            with self.subTest(mode=mode):
                values = resolve_dispatch({"publish_to": mode})
                settings = PublicationSettings.from_inputs(values, "owner/workflow")
                self.assertEqual((settings.telegram, settings.channel, settings.github), expected)
                self.assertEqual(delivery_plan(mode).bundles, any(expected))
                self.assertEqual(
                    delivery_plan(mode).as_dict(),
                    dict(
                        personal=expected[0],
                        channel=expected[1],
                        github=expected[2],
                        bundles=any(expected),
                    ),
                )

    def test_profile_cannot_silently_enable_any_destination(self):
        for mode in self.EXPECTED:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "configs").mkdir()
                profile = json.loads((ROOT / "configs/miatoll.json").read_text())
                profile["publish_to"] = mode
                (root / "configs/miatoll.json").write_text(json.dumps(profile))
                self.assertEqual(resolve_dispatch({}, root)["publish_to"], "artifacts only")

    def test_legacy_checkbox_and_ambiguous_modes_fail_closed(self):
        for mode in (
            "github",
            "both",
            "channel",
            "telegram-only",
            "telegram",
            "telegram+channel",
            "telegram+github",
            "telegram+channel+github",
            "artifacts-only",
            "personal",
            "personal-only",
            "Artifacts only",
            "personal only ",
            " personal only",
            "personal+github+channel",
            "",
            None,
            True,
            [],
        ):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                resolve_dispatch({"publish_to": mode})
        for flag in (True, False, "false"):
            with self.assertRaises(ValueError):
                resolve_dispatch({"telegram_release": flag})
            with self.assertRaises(ValueError):
                PublicationSettings.from_inputs(
                    {"publish_to": "personal only", "telegram_release": flag}
                )

    def test_preflight_requires_only_selected_destination_secrets(self):
        for mode, (personal, channel, github) in self.EXPECTED.items():
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary) / "outputs"
                environment = {
                    "INPUTS_JSON": json.dumps(
                        {
                            "config_file": "configs/miatoll.json",
                            "publish_to": mode,
                            "repo_visibility": "public",
                        }
                    ),
                    "GITHUB_REPOSITORY": "owner/workflow",
                    "GITHUB_OUTPUT": str(output),
                }
                # Separate write credential: source token fallback must not hide missing-secret tests.
                if github:
                    environment["GH_RELEASE_TOKEN"] = "fixture-write"
                if personal or channel:
                    environment["TELEGRAM_BOT_TOKEN"] = "123:fixture"
                if personal:
                    environment["TELEGRAM_CHAT_ID"] = "123456789"
                if channel:
                    environment["TELEGRAM_CHANNEL_ID"] = "-100123"
                with (
                    patch.dict(os.environ, environment, clear=True),
                    patch.object(sys, "argv", ["prepare.py", "matrix"]),
                ):
                    prepare.main()
                records = dict(line.split("=", 1) for line in output.read_text().splitlines())
                self.assertEqual(json.loads(records["delivery"]), delivery_plan(mode).as_dict())
                self.assertEqual(json.loads(records["settings"])["publish_to"], mode)
                for secret in (
                    "TELEGRAM_BOT_TOKEN",
                    "TELEGRAM_CHAT_ID",
                    "TELEGRAM_CHANNEL_ID",
                    "GH_RELEASE_TOKEN",
                ):
                    if secret not in environment:
                        continue
                    broken = dict(environment)
                    broken.pop(secret)
                    with (
                        patch.dict(os.environ, broken, clear=True),
                        patch.object(sys, "argv", ["prepare.py", "matrix"]),
                        self.assertRaisesRegex(ValueError, secret),
                    ):
                        prepare.main()

    def test_artifact_mode_ignores_even_malformed_unused_publication_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "outputs"
            summary = Path(temporary) / "summary"
            environment = {
                "INPUTS_JSON": json.dumps(
                    {
                        "repo_visibility": "public",
                        "publish_to": "artifacts only",
                        "config_file": "configs/miatoll.json",
                    }
                ),
                "TELEGRAM_BOT_TOKEN": "not-a-token",
                "TELEGRAM_CHAT_ID": "-100123",
                "TELEGRAM_CHANNEL_ID": "123456789",
                "GH_RELEASE_TOKEN": "unused-fixture",
                "GITHUB_OUTPUT": str(output),
                "GITHUB_STEP_SUMMARY": str(summary),
            }
            with (
                patch.dict(os.environ, environment, clear=True),
                patch.object(sys, "argv", ["prepare.py", "matrix"]),
            ):
                prepare.main()
            records = dict(line.split("=", 1) for line in output.read_text().splitlines())
            self.assertEqual(
                json.loads(records["delivery"]),
                dict(personal=False, channel=False, github=False, bundles=False),
            )
            self.assertIn("Mode: artifacts only", summary.read_text())
            self.assertNotIn("unused-fixture", output.read_text() + summary.read_text())

    def test_mode_registry_and_plans_cannot_be_mutated(self):
        with self.assertRaises(TypeError):
            MODES["personal only"] = delivery_plan("artifacts only")
        with self.assertRaises(AttributeError):
            delivery_plan("personal only").personal = False
