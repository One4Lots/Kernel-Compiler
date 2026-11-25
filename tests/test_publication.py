"""Offline tests for release selection, duplicate handling, and channel changelogs."""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from support import BundleFixture, inputs, settings

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github/scripts/kernel_build"))
import changelog
import publication
import publication_settings
import telegram
from publication_settings import PublicationSettings

SHA = "a" * 40
OTHER_SHA = "b" * 40


def ref(sha=SHA, kind="commit"):
    return {"object": {"type": kind, "sha": sha}}


def release(draft=False, **changes):
    value = dict(
        id=42,
        draft=draft,
        immutable=False,
        html_url="https://github.com/owner/releases/releases/tag/v1.0",
    )
    value.update(changes)
    return value


class FakeGithub:
    def __init__(self, *responses, upload_error=False):
        self.responses = list(responses)
        self.calls = []
        self.upload_error = upload_error

    def api(self, endpoint, method="GET", data=None, optional=False):
        self.calls.append((method, endpoint, data, optional))
        if not self.responses:
            raise AssertionError(f"Unexpected API call: {method} {endpoint}")
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def upload(self, repository, tag, assets, clobber):
        self.calls.append(("UPLOAD", repository, tag, clobber))
        if self.upload_error:
            raise publication.GithubError("Upload failed")


class PublicationSettingsTests(unittest.TestCase):
    def test_default_preserves_latest_message_without_publication(self):
        value = PublicationSettings.from_inputs({})
        self.assertFalse(value.telegram)
        self.assertFalse(value.github)
        self.assertEqual(value.history_depth, 1)

    def test_recent_mode_and_github_require_selected_history_depth(self):
        self.assertEqual(settings().history_depth, 3)
        self.assertEqual(
            settings(publish_to="personal only", changelog_mode="recent-commits").history_depth, 3
        )
        self.assertFalse(settings(publish_to="artifacts only").telegram)
        self.assertFalse(settings(publish_to="artifacts only").github)
        self.assertTrue(settings(publish_to="personal+channel+github").telegram)

    def test_count_is_bounded_and_integer_only(self):
        for value in (1, 6, 50, "12", 6.0, "6.0"):
            self.assertEqual(publication_settings.commit_count(value), int(float(value)))
        for value in (0, -1, 51, 1.5, True, "1;command", "6.5", float("nan"), float("inf"), None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                publication_settings.commit_count(value)

    def test_repository_url_normalization_and_current_repository_fallback(self):
        self.assertEqual(
            settings(release_repository="https://github.com/owner/releases.git").repository,
            "owner/releases",
        )
        value = PublicationSettings.from_inputs(
            dict(publish_to="personal+github", kernel_rc="v1"), "current/repository"
        )
        self.assertEqual(value.repository, "current/repository")
        for value in (
            "https://token@github.com/a/b",
            "http://github.com/a/b",
            "a/b/c",
            "../repo",
            "https://github.com/a/b?token=x",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                settings(release_repository=value)

    def test_invalid_tags_targets_and_policies_fail_early(self):
        for tag in ("", "../bad", "v1..0", "-v1", "tag\nother", "v1;command"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                settings(kernel_rc=tag)
        for changes in (
            dict(release_target="bad target"),
            dict(duplicate_release="delete"),
            dict(changelog_mode="unknown"),
            dict(publish_to="unknown"),
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                settings(**changes)


class PreflightTests(unittest.TestCase):
    def test_new_release_freezes_destination_target_commit(self):
        client = FakeGithub(
            {"default_branch": "stable", "permissions": {"push": True}}, None, None, {"sha": SHA}
        )
        result = publication.preflight(client, settings(release_target=""))
        self.assertEqual(result, {"release_skip": "false", "release_target_sha": SHA})
        self.assertTrue(client.calls[-1][1].endswith("/commits/stable"))
        self.assertTrue(all(call[0] == "GET" for call in client.calls))

    def test_fail_policy_rejects_bare_tag_or_release_before_build(self):
        for old_release, old_ref in (
            (None, ref()),
            (release(), ref()),
            (release(draft=True), None),
        ):
            client = FakeGithub({"default_branch": "main"}, old_release, old_ref)
            with (
                self.subTest(release=old_release),
                self.assertRaisesRegex(ValueError, "already exists"),
            ):
                publication.preflight(client, settings())

    def test_skip_policy_preserves_bare_tags_and_releases(self):
        for old_release, old_ref in ((None, ref()), (release(), ref())):
            client = FakeGithub({"default_branch": "main"}, old_release, old_ref)
            self.assertEqual(
                publication.preflight(client, settings("skip"))["release_skip"], "true"
            )
            self.assertTrue(all(call[0] == "GET" for call in client.calls))

    def test_update_rejects_tag_movement_and_immutable_releases(self):
        client = FakeGithub({"default_branch": "main"}, release(), ref(OTHER_SHA), {"sha": SHA})
        with self.assertRaisesRegex(ValueError, "never moved"):
            publication.preflight(client, settings("update"))
        client = FakeGithub({"default_branch": "main"}, release(immutable=True), ref())
        with self.assertRaisesRegex(ValueError, "immutable"):
            publication.preflight(client, settings("update"))

    def test_annotated_tag_is_peeled_and_verified(self):
        client = FakeGithub(
            {"default_branch": "main"}, release(), ref(OTHER_SHA, "tag"), {"sha": SHA}, ref()
        )
        result = publication.preflight(client, settings("update"))
        self.assertEqual(result["release_target_sha"], SHA)
        self.assertTrue(client.calls[-1][1].endswith(f"/git/tags/{OTHER_SHA}"))

    def test_missing_write_permissions_stop_preflight(self):
        client = FakeGithub({"default_branch": "main", "permissions": {"push": False}})
        with self.assertRaisesRegex(ValueError, "no write access"):
            publication.preflight(client, settings())


class PublishTests(unittest.TestCase):
    def test_new_release_is_draft_until_assets_are_uploaded(self):
        client = FakeGithub(None, None, ref(), release(draft=True), release())
        url = publication.publish(client, settings(), SHA, "Nova", "notes", [Path("kernel.zip")])
        self.assertEqual(url, release()["html_url"])
        self.assertEqual(
            [call[0] for call in client.calls], ["GET", "GET", "POST", "POST", "UPLOAD", "PATCH"]
        )
        self.assertEqual(client.calls[2][2], {"ref": "refs/tags/v1.0", "sha": SHA})
        self.assertEqual(client.calls[3][2]["name"], "Nova")
        self.assertTrue(client.calls[3][2]["draft"])
        self.assertFalse(client.calls[-1][2]["draft"])
        self.assertFalse(client.calls[-2][3])

    def test_asset_failure_never_publishes_new_draft(self):
        client = FakeGithub(None, None, ref(), release(draft=True), upload_error=True)
        with self.assertRaises(publication.GithubError):
            publication.publish(client, settings(), SHA, "Nova", "notes", [])
        self.assertNotIn("PATCH", [call[0] for call in client.calls])

    def test_update_reuses_release_and_explicitly_clobbers_matching_asset_names(self):
        client = FakeGithub(release(), ref(), release())
        publication.publish(client, settings("update"), SHA, "Nova", "new notes", [])
        self.assertEqual([call[0] for call in client.calls], ["GET", "GET", "UPLOAD", "PATCH"])
        self.assertTrue(client.calls[2][3])
        self.assertEqual(client.calls[-1][2]["body"], "new notes")
        self.assertTrue(client.calls[-1][1].endswith("/releases/42"))

    def test_publish_rechecks_duplicates_after_preflight(self):
        for policy in ("fail", "skip"):
            client = FakeGithub(release(), ref())
            with self.subTest(policy=policy):
                if policy == "fail":
                    with self.assertRaises(ValueError):
                        publication.publish(client, settings(policy), SHA, "Nova", "notes", [])
                else:
                    self.assertIsNone(
                        publication.publish(client, settings(policy), SHA, "Nova", "notes", [])
                    )
                self.assertTrue(all(call[0] == "GET" for call in client.calls))

    def test_tag_creation_race_is_not_silently_overwritten(self):
        client = FakeGithub(None, None, publication.GithubError("Conflict", 422), None, ref())
        with self.assertRaisesRegex(ValueError, "already exists"):
            publication.publish(client, settings(), SHA, "Nova", "notes", [])
        self.assertNotIn("UPLOAD", [call[0] for call in client.calls])

    def test_explicit_update_can_resume_a_tag_creation_race(self):
        client = FakeGithub(
            None,
            None,
            publication.GithubError("Conflict", 422),
            None,
            ref(),
            release(draft=True),
            release(),
        )
        publication.publish(client, settings("update"), SHA, "Nova", "notes", [])
        self.assertTrue(any(call[0] == "UPLOAD" for call in client.calls))

    def test_github_auth_errors_are_not_confused_with_missing_release(self):
        result = subprocess.CompletedProcess([], 1, "", "private-token HTTP 403")
        with patch.object(publication.subprocess, "run", return_value=result):
            with self.assertRaises(publication.GithubError) as error:
                publication.GithubClient().api("repos/a/b/releases/tags/v1", optional=True)
        self.assertEqual(error.exception.status, 403)
        self.assertNotIn("private-token", str(error.exception))
        result = subprocess.CompletedProcess([], 1, "", "gh: Not Found (HTTP 404)")
        with patch.object(publication.subprocess, "run", return_value=result):
            self.assertIsNone(
                publication.GithubClient().api("repos/a/b/releases/tags/v1", optional=True)
            )

    def test_api_timeout_is_bounded_sanitized_and_not_retried(self):
        error = subprocess.TimeoutExpired(["gh", "secret-value"], 120, output="secret-value")
        with patch.object(publication.subprocess, "run", side_effect=error) as request:
            with self.assertRaisesRegex(publication.GithubError, "timed out") as result:
                publication.GithubClient().api("repos/a/b/releases", "POST", {"draft": True})
        self.assertNotIn("secret-value", str(result.exception))
        request.assert_called_once()
        self.assertEqual(request.call_args.kwargs["timeout"], 120)

    def test_upload_timeout_reports_possible_partial_success_without_retry(self):
        error = subprocess.TimeoutExpired(["gh", "secret-value"], 900, stderr="secret-value")
        with patch.object(publication.subprocess, "run", side_effect=error) as request:
            with self.assertRaisesRegex(publication.GithubError, "Some assets") as result:
                publication.GithubClient().upload("a/b", "rc1", [Path("kernel.zip")], False)
        self.assertNotIn("secret-value", str(result.exception))
        request.assert_called_once()
        self.assertEqual(request.call_args.kwargs["timeout"], 900)


class BundleTests(BundleFixture, unittest.TestCase):
    def test_build_bundle_serializes_the_package_and_selected_history(self):
        manifest, archive = self.create_bundle("first")
        source = json.loads(manifest.read_text())
        logs = self.root / "logs"
        logs.mkdir()
        package = dict(source, zip_path=str(archive))
        (logs / "package.json").write_text(json.dumps(package))
        (logs / "recent-commits.json").write_text(
            json.dumps([dict(record, message="full message") for record in source["commits"]])
        )
        env = dict(
            MATRIX_ID=source["matrix_id"],
            BRANCH=source["branch"],
            DEFCONFIG=source["defconfig"],
            IMAGE_TYPE=source["image_type"],
            KERNEL_REPOSITORY=source["source_repository"],
        )
        previous = Path.cwd()
        try:
            os.chdir(self.root)
            with patch.dict(os.environ, env):
                publication.bundle()
        finally:
            os.chdir(previous)
        output = json.loads((logs / "release-manifest.json").read_text())
        self.assertEqual(output, source)
        self.assertNotIn("message", output["commits"][0])

    def test_all_matrix_bundles_are_aggregated_and_duplicate_zip_names_disambiguated(self):
        self.create_bundle("first", "branch/one")
        # Use distinct matrix digests, as the real matrix generator does.
        manifest, _ = self.create_bundle("second", "other/one")
        value = json.loads(manifest.read_text())
        value["matrix_id"] = "second-abcdefabcdef"
        self.expected[-1]["id"] = value["matrix_id"]
        manifest.write_text(json.dumps(value))
        manifests = self.load()
        assets = publication.stage_assets(manifests, self.root / "assets")
        self.assertEqual(len(assets), 3)
        self.assertEqual(len({path.name for path in assets}), 3)
        self.assertEqual(assets[-1].name, "SHA256SUMS.txt")
        notes = publication.release_notes(manifests, settings(), "Nova", SHA)
        self.assertEqual(notes.count("#### Recent commits (3)"), 2)
        self.assertEqual(notes.count("[commit]"), 6)
        self.assertIn("branch/one", notes)
        self.assertIn("other/one", notes)

    def test_single_artifact_flat_layout_matches_download_action_v8(self):
        manifest, archive = self.create_bundle("first", "17.0/modern-sched")
        bundle = manifest.parent.parent
        for child in list(bundle.iterdir()):
            child.rename(self.downloads / child.name)
        bundle.rmdir()
        result = self.load()
        self.assertEqual(len(result), 1)
        self.assertEqual(
            result[0]["archive"],
            self.downloads / "artifacts" / archive.relative_to(bundle / "artifacts"),
        )

    def test_flat_layout_does_not_hide_missing_matrix_build(self):
        manifest, _ = self.create_bundle("first")
        bundle = manifest.parent.parent
        for child in list(bundle.iterdir()):
            child.rename(self.downloads / child.name)
        bundle.rmdir()
        self.expected.append(dict(self.expected[0], id="missing", branch="missing"))
        with self.assertRaisesRegex(ValueError, "partial release"):
            self.load()

    def test_flat_and_nested_duplicate_identity_is_rejected(self):
        manifest, _ = self.create_bundle("first")
        bundle = manifest.parent.parent
        shutil.copytree(bundle / "logs", self.downloads / "logs")
        shutil.copytree(bundle / "artifacts", self.downloads / "artifacts")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.load()

    def test_flat_layout_still_verifies_zip_checksum(self):
        manifest, archive = self.create_bundle("first")
        archive.write_bytes(b"corrupt")
        bundle = manifest.parent.parent
        for child in list(bundle.iterdir()):
            child.rename(self.downloads / child.name)
        bundle.rmdir()
        with self.assertRaisesRegex(ValueError, "integrity"):
            self.load()

    def test_missing_matrix_build_blocks_publishing(self):
        self.create_bundle("first")
        self.expected.append(
            dict(
                id="missing",
                branch="missing",
                defconfig="vendor/defconfig",
                image_type="Image.gz.dtb",
            )
        )
        with self.assertRaisesRegex(ValueError, "partial release"):
            self.load()

    def test_corrupt_zip_and_wrong_build_identity_fail_closed(self):
        manifest, archive = self.create_bundle("first")
        original = archive.read_bytes()
        archive.write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "integrity"):
            self.load()
        archive.write_bytes(original)
        value = json.loads(manifest.read_text())
        value["branch"] = "not-the-selected-branch"
        manifest.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "matrix"):
            self.load()

    def test_path_traversal_and_symlink_assets_are_rejected(self):
        manifest, archive = self.create_bundle("first")
        value = json.loads(manifest.read_text())
        value["zip_name"] = "../../private.zip"
        manifest.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "filename"):
            self.load()
        value["zip_name"] = archive.name
        manifest.write_text(json.dumps(value))
        archive.unlink()
        elsewhere = self.root / "elsewhere.zip"
        elsewhere.write_bytes(b"not-a-build")
        archive.symlink_to(elsewhere)
        with self.assertRaisesRegex(ValueError, "unsafe release ZIP"):
            self.load()

    def test_malformed_manifest_shapes_are_rejected_with_actionable_errors(self):
        manifest, _ = self.create_bundle("first")
        original = json.loads(manifest.read_text())
        for value in (
            None,
            [],
            "not-an-object",
            dict(original, matrix_id=[]),
            dict(original, schema=3.0),
            dict(original, commits=[{"sha": None, "subject": "Bad SHA"}]),
        ):
            manifest.write_text(json.dumps(value))
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "manifest"):
                self.load()

    def test_empty_malformed_and_duplicate_expected_matrices_are_rejected(self):
        self.create_bundle("first")
        valid = self.expected[0]
        for expected in (
            [],
            None,
            {},
            [None],
            [{"id": "only"}],
            [dict(valid, id=[])],
            [valid, valid],
            [valid] * 33,
        ):
            with self.subTest(expected=expected), self.assertRaisesRegex(ValueError, "matrix"):
                publication.load_bundles(
                    self.downloads,
                    expected,
                    settings(),
                    "Nova",
                    "phone",
                    "https://github.com/owner/kernel",
                )

    def test_duplicate_json_keys_in_manifest_are_rejected(self):
        manifest, _ = self.create_bundle("first")
        text = manifest.read_text()
        manifest.write_text(text.replace('"schema": 3', '"schema": 1, "schema": 3'))
        with self.assertRaisesRegex(ValueError, "Duplicate JSON key"):
            self.load()

    def test_manifest_size_and_checksum_fields_reject_coercible_types(self):
        manifest, archive = self.create_bundle("first")
        original = json.loads(manifest.read_text())
        for field, value in (
            ("zip_bytes", float(archive.stat().st_size)),
            ("zip_bytes", str(archive.stat().st_size)),
            ("zip_bytes", True),
            ("sha256", []),
            ("sha256", "A" * 64),
            ("sha256", None),
        ):
            manifest.write_text(json.dumps(dict(original, **{field: value})))
            with (
                self.subTest(field=field, value=value),
                self.assertRaisesRegex(ValueError, "integrity"),
            ):
                self.load()

    def test_symlinked_download_root_is_rejected(self):
        self.create_bundle("first")
        alias = self.root / "alias"
        alias.symlink_to(self.downloads, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Symlinked"):
            publication.load_bundles(
                alias, self.expected, settings(), "Nova", "phone", "https://github.com/owner/kernel"
            )

    def test_symlinked_manifest_parent_inside_download_root_is_rejected(self):
        manifest, _ = self.create_bundle("first")
        actual = self.downloads / "saved-logs"
        manifest.parent.rename(actual)
        manifest.parent.symlink_to(actual, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            self.load()

    def test_manifest_directory_instead_of_regular_file_is_rejected(self):
        manifest, _ = self.create_bundle("first")
        manifest.unlink()
        manifest.mkdir()
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            self.load()

    def test_arbitrary_deep_manifest_is_not_recursively_accepted(self):
        manifest, _ = self.create_bundle("first")
        bundle = manifest.parent.parent
        deeper = self.downloads / "outer/deeper"
        deeper.mkdir(parents=True)
        bundle.rename(deeper / bundle.name)
        with self.assertRaisesRegex(ValueError, "partial release"):
            self.load()

    def test_commit_markdown_is_escaped_without_losing_requested_entries(self):
        manifest, _ = self.create_bundle("first")
        value = json.loads(manifest.read_text())
        value["commits"][0]["subject"] = "<script> ![tracking](https://example.org) **bold**"
        manifest.write_text(json.dumps(value))
        manifests = self.load()
        publication.stage_assets(manifests, self.root / "assets")
        notes = publication.release_notes(manifests, settings(), "Nova", SHA)
        self.assertIn("&lt;script&gt;", notes)
        self.assertIn(r"\!\[tracking\]", notes)
        self.assertEqual(notes.count("[commit]"), 3)


class TelegramChangelogTests(unittest.TestCase):
    def test_recent_caption_keeps_checksum_separate_from_commits(self):
        meta = dict(
            kernel_name="Nova",
            device_name="phone",
            duration_seconds=123,
            date="25112026",
            time="04:29 PM",
            built_at="2026-11-25T16:29:00+06:00",
            zip_bytes=1234,
            md5="a" * 32,
        )
        env = dict(
            TOOLCHAIN="android-clang",
            BRANCH="staging",
            BUILD_MODE="single",
            REPO_VISIBILITY="public",
            DEFCONFIG="vendor/defconfig",
            IMAGE_TYPE="Image.gz.dtb",
        )
        records = [dict(subject=f"Fix issue {i}") for i in range(6)]
        text = changelog.recent_commit_text(records)
        caption = telegram.make_caption(
            meta,
            env,
            "Android clang version 22",
            "LLD 22",
            text,
            "Recent commits (6)",
            "\n… [full changelog follows]",
        )
        self.assertIn("Recent commits (6)", caption)
        self.assertEqual(caption.count("<code>"), 2)
        for i in range(6):
            self.assertIn(f"Fix issue {i}", caption)

    def test_full_changelog_splitting_is_lossless_and_html_safe(self):
        text = "\n".join(f"{i}. Fix <scheduler> & issue 😀 " + "x" * 200 for i in range(50))
        chunks = telegram.changelog_chunks(text)
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(telegram.utf16_length(html.escape(chunk)) <= 3000 for chunk in chunks))
        with (
            patch.object(telegram, "telegram_request") as request,
            patch.object(telegram.time, "sleep"),
        ):
            telegram.send_changelog(
                text, "Nova · branch · Recent commits (50)", "123:test", "-100123"
            )
        emitted = ""
        for call in request.call_args_list:
            message = call.args[1]["text"]
            self.assertLessEqual(telegram.utf16_length(message), 4096)
            block = re.search(r"<pre><code>(.*?)</code></pre>", message, re.DOTALL)
            emitted += html.unescape(block.group(1))
        self.assertEqual(emitted, text)

    def test_explicit_rate_limits_are_retried_without_blind_post_retries(self):
        responses = [dict(ok=False, error_code=429, parameters={"retry_after": 2}), dict(ok=True)]

        def fake_curl(command, **kwargs):
            Path(command[command.index("--output") + 1]).write_text(json.dumps(responses.pop(0)))
            self.assertNotIn("--retry", command)
            return subprocess.CompletedProcess(command, 22 if responses else 0)

        with (
            patch.object(telegram.subprocess, "run", side_effect=fake_curl) as curl,
            patch.object(telegram.time, "sleep") as sleep,
        ):
            telegram.telegram_request(
                "sendMessage", {"text": "<pre><code>commits</code></pre>"}, "123:test", "-100123"
            )
        self.assertEqual(curl.call_count, 2)
        sleep.assert_called_once_with(2)

    def test_invalid_rate_limit_parameters_fail_cleanly_without_retry(self):
        for parameters in (
            None,
            [],
            "bad",
            {},
            {"retry_after": True},
            {"retry_after": -1},
            {"retry_after": 121},
            {"retry_after": "2"},
        ):

            def fake_curl(command, parameters=parameters, **kwargs):
                response = dict(ok=False, error_code=429, parameters=parameters)
                Path(command[command.index("--output") + 1]).write_text(json.dumps(response))
                return subprocess.CompletedProcess(command, 22)

            with (
                self.subTest(parameters=parameters),
                patch.object(telegram.subprocess, "run", side_effect=fake_curl) as request,
                patch.object(telegram.time, "sleep") as sleep,
            ):
                with self.assertRaisesRegex(ValueError, "retry"):
                    telegram.telegram_request(
                        "sendMessage", {"text": "history"}, "123:test", "-100123"
                    )
                request.assert_called_once()
                sleep.assert_not_called()


class WorkflowPublicationIntegrationTests(unittest.TestCase):
    def test_artifacts_only_does_not_require_telegram_credentials(self):
        import prepare

        with tempfile.TemporaryDirectory() as temporary:
            environment = dict(
                INPUTS_JSON=json.dumps(inputs(publish_to="artifacts only")),
                GITHUB_OUTPUT=str(Path(temporary) / "output"),
            )
            with (
                patch.dict(os.environ, environment, clear=True),
                patch.object(sys, "argv", ["prepare.py", "matrix"]),
            ):
                prepare.main()
            self.assertIn("history_depth=1", Path(environment["GITHUB_OUTPUT"]).read_text())

    def test_github_requires_a_release_credential_not_telegram_secrets(self):
        import prepare

        values = inputs(
            publish_to="personal+github",
            release_repository="owner/releases",
            kernel_rc="v1",
            commit_count=12,
        )
        with tempfile.TemporaryDirectory() as temporary:
            environment = dict(
                INPUTS_JSON=json.dumps(values), GITHUB_OUTPUT=str(Path(temporary) / "output")
            )
            with (
                patch.dict(os.environ, environment, clear=True),
                patch.object(sys, "argv", ["prepare.py", "matrix"]),
            ):
                with self.assertRaisesRegex(ValueError, "GH_RELEASE_TOKEN"):
                    prepare.main()
            environment["GH_RELEASE_TOKEN"] = "test-token"
            environment["TELEGRAM_BOT_TOKEN"] = "123:fixture"
            environment["TELEGRAM_CHAT_ID"] = "123456789"
            with (
                patch.dict(os.environ, environment, clear=True),
                patch.object(sys, "argv", ["prepare.py", "matrix"]),
            ):
                prepare.main()
            self.assertIn("history_depth=12", Path(environment["GITHUB_OUTPUT"]).read_text())

    def test_recent_telegram_mode_follows_up_with_every_selected_commit_when_caption_overflows(
        self,
    ):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            logs = root / "logs"
            logs.mkdir()
            metadata = dict(
                kernel_name="Nova",
                device_name="phone",
                duration_seconds=123,
                date="25112026",
                time="04:29 PM",
                built_at="2026-11-25T16:29:00+06:00",
                zip_bytes=1234,
                md5="a" * 32,
                zip_path=str(root / "kernel.zip"),
            )
            (logs / "package.json").write_text(json.dumps(metadata))
            (logs / "clang-version.txt").write_text("Android clang version 22")
            (logs / "lld-version.txt").write_text("LLD 22")
            records = [
                dict(subject=f"Commit {index} " + "<scheduler> & details " * 30)
                for index in range(10)
            ]
            (logs / "recent-commits.json").write_text(json.dumps(records))
            environment = dict(
                TELEGRAM_DELIVERY_CONFIRMED="true",
                TELEGRAM_BOT_TOKEN="123:test",
                TELEGRAM_CHAT_ID="123456789",
                TOOLCHAIN="android-clang",
                CHANGELOG_MODE="recent-commits",
                COMMIT_COUNT="8",
                BRANCH="staging",
                BUILD_MODE="single",
                REPO_VISIBILITY="public",
                DEFCONFIG="vendor/defconfig",
                IMAGE_TYPE="Image.gz.dtb",
            )
            previous = Path.cwd()
            try:
                os.chdir(root)
                with (
                    patch.dict(os.environ, environment),
                    patch.object(telegram, "send_document") as document,
                    patch.object(telegram, "send_changelog") as full,
                ):
                    telegram.main()
                self.assertIn("Recent commits (8)", document.call_args.args[1])
                self.assertIn("full changelog follows", document.call_args.args[1])
                full.assert_called_once()
                for index in range(8):
                    self.assertIn(f"Commit {index} ", full.call_args.args[0])
                self.assertNotIn("Commit 8 ", full.call_args.args[0])
            finally:
                os.chdir(previous)


class HistoryTests(unittest.TestCase):
    def test_authenticated_history_deepening_keeps_remote_and_fetch_head_clean(self):
        real_git = shutil.which("git")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
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
                        f"Body {index}",
                    ],
                    check=True,
                    capture_output=True,
                )
            binary = root / "bin"
            binary.mkdir()
            wrapper = binary / "git"
            wrapper.write_text(
                "#!/usr/bin/env python3\nimport os, subprocess, sys\n"
                "args=sys.argv[1:]\n"
                "args=[os.environ['SOURCE_URL'] if x.startswith('https://') and ('clone' in args or 'fetch' in args) else x for x in args]\n"
                "sys.exit(subprocess.call([os.environ['REAL_GIT'], *args]))\n"
            )
            wrapper.chmod(0o755)
            for visibility in ("public", "private"):
                work = root / visibility
                work.mkdir()
                env = dict(
                    os.environ,
                    PATH=f"{binary}:{os.environ['PATH']}",
                    SOURCE_URL=source.as_uri(),
                    REAL_GIT=real_git,
                    GH_PAT="test-secret",
                    KERNEL_REPOSITORY="https://github.com/test/kernel",
                    BRANCH="staging",
                    REPO_VISIBILITY=visibility,
                    HISTORY_DEPTH="6",
                )
                result = subprocess.run(
                    ["bash", str(ROOT / ".github/scripts/kernel_build/clone.sh")],
                    cwd=work,
                    env=env,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                records = json.loads((work / "logs/recent-commits.json").read_text())
                self.assertEqual(
                    [entry["subject"] for entry in records],
                    [f"Subject {i}" for i in range(7, 1, -1)],
                )
                self.assertIn("Body 7", (work / "logs/commit-message.txt").read_text())
                self.assertNotIn("test-secret", (work / "kernel/.git/config").read_text())
                fetch_head = work / "kernel/.git/FETCH_HEAD"
                self.assertFalse(fetch_head.exists() and "test-secret" in fetch_head.read_text())


if __name__ == "__main__":
    unittest.main()
