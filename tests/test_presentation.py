"""User-facing redesign: extracted versions, unambiguous Dhaka time, clean HTML, no art."""

from __future__ import annotations

import html
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github/scripts/kernel_build"))

from presentation import (
    build_datetime,
    duration_label,
    image_label,
    linker_label,
    local_time_labels,
    toolchain_label,
    zip_size_label,
)
from telegram import make_caption, utf16_length


def metadata(**changes):
    result = dict(
        kernel_name="Alecto",
        device_name="miatoll",
        kernel_rc="rc1",
        duration_seconds=176,
        date="26112026",
        time="12:29 PM",
        built_at="2026-11-26T12:29:00+06:00",
        zip_bytes=14_800_000,
        md5="a" * 32,
    )
    result.update(changes)
    return result


def environment(**changes):
    result = dict(
        TOOLCHAIN="android-clang",
        BRANCH="staging",
        IMAGE_TYPE="Image.gz + dtbo.img + dtb.img",
        DEFCONFIG="vendor/xiaomi/miatoll_defconfig",
        BUILD_MODE="single",
        REPO_VISIBILITY="private",
    )
    result.update(changes)
    return result


def caption(meta=None, env=None, message="Fix scheduling\n\nPreserve the full body."):
    return make_caption(
        meta or metadata(),
        env or environment(),
        "clang version 22.0.1 (build provenance)\nTarget: aarch64",
        "LLD 22.0.1 (compatible with GNU linkers)",
        message,
    )


class VersionPresentationTests(unittest.TestCase):
    def test_generic_android_clang_uses_selected_brand_and_actual_version(self):
        self.assertEqual(
            toolchain_label("clang version 22.0.1 (git hash)\nTarget: x86_64", "android-clang"),
            "Android Clang 22.0.1",
        )

    def test_android_branded_banner_drops_redundant_version_word_and_provenance(self):
        self.assertEqual(
            toolchain_label(
                "Android clang version 19.1.7 (https://example.org/source)\nInstalledDir: /compiler/bin",
                "android-clang",
            ),
            "Android Clang 19.1.7",
        )

    def test_neutron_brand_comes_from_selection_even_for_generic_clang_banner(self):
        self.assertEqual(
            toolchain_label("clang version 23.0.0git (abcdef)\nTarget: aarch64", "neutron-clang"),
            "Neutron Clang 23.0.0git",
        )

    def test_neutron_branded_release_candidate_version_is_retained(self):
        self.assertEqual(
            toolchain_label("Neutron clang version 21.1.0-rc2 (build)", "neutron-clang"),
            "Neutron Clang 21.1.0-rc2",
        )

    def test_linker_has_independent_extracted_version(self):
        self.assertEqual(
            linker_label("LLD 20.1.8 (compatible with GNU linkers)\nExtra metadata"), "LLD 20.1.8"
        )

    def test_linker_development_suffix_is_not_replaced_by_compiler_version(self):
        self.assertEqual(linker_label("LLD 24.0.0git (provenance)"), "LLD 24.0.0git")

    def test_unparseable_logs_fail_instead_of_inventing_a_version(self):
        for text in (
            "",
            "no compiler here",
            "clang version unknown",
            "clang version 22evil",
            "LLD unknown",
            "InstalledDir: /private/path",
        ):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    toolchain_label(text, "android-clang")
                with self.assertRaises(ValueError):
                    linker_label(text)

    def test_unknown_toolchain_selection_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown selected"):
            toolchain_label("clang version 22.0.1", "invented-compiler")


class TimePresentationTests(unittest.TestCase):
    def test_noon_and_full_weekday_date(self):
        local, date = local_time_labels(metadata())
        self.assertEqual(local, "12:29 PM • Dhaka (UTC+06:00)")
        self.assertEqual(date, "Thursday, 26 November 2026")

    def test_midnight_is_twelve_am_not_zero_or_pm(self):
        local, _ = local_time_labels(
            metadata(built_at="2026-11-26T00:00:00+06:00", time="12:00 AM")
        )
        self.assertTrue(local.startswith("12:00 AM"))

    def test_utc_timestamp_converts_across_the_local_date_boundary(self):
        local, date = local_time_labels(
            metadata(built_at="2026-11-25T18:30:00+00:00", time="12:30 AM")
        )
        self.assertTrue(local.startswith("12:30 AM"))
        self.assertEqual(date, "Thursday, 26 November 2026")

    def test_leap_day_is_preserved(self):
        local, date = local_time_labels(
            metadata(built_at="2028-02-29T23:59:00+06:00", date="29022028", time="11:59 PM")
        )
        self.assertIn("11:59 PM", local)
        self.assertEqual(date, "Tuesday, 29 February 2028")

    def test_timestamp_requires_timezone_and_valid_date(self):
        for value in (
            None,
            "",
            "2026-11-26T12:29:00",
            "2026-02-30T12:29:00+06:00",
            "not-a-time",
            "x" * 33,
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_datetime(metadata(built_at=value))

    def test_date_and_meridiem_mismatch_is_not_guessed(self):
        for changes in (dict(date="25112026"), dict(time="12:29 AM"), dict(time="12:29")):
            with (
                self.subTest(changes=changes),
                self.assertRaisesRegex(ValueError, "does not match"),
            ):
                build_datetime(metadata(**changes))

    def test_duration_boundaries(self):
        for seconds, expected in (
            (0, "0s"),
            (59, "59s"),
            (60, "1m 0s"),
            (176, "2m 56s"),
            (3600, "1h 0m 0s"),
            (3723, "1h 2m 3s"),
            (604800, "168h 0m 0s"),
        ):
            with self.subTest(seconds=seconds):
                self.assertEqual(duration_label(seconds), expected)

    def test_invalid_duration_types_or_ranges_are_rejected(self):
        for value in (-1, True, 1.0, "60", None, 604801):
            with self.subTest(value=value), self.assertRaises(ValueError):
                duration_label(value)


class CaptionRedesignTests(unittest.TestCase):
    def test_exact_bold_success_heading_without_brand_title(self):
        result = caption()
        self.assertTrue(result.startswith("<b>✅ Kernel Compiled Successfully</b>\n\n"))
        self.assertNotIn("Alecto", result)
        self.assertNotIn("Miatoll", result)
        self.assertNotIn("⚡", result)

    def test_configured_identity_stays_out_of_caption(self):
        result = caption(metadata(kernel_name="Nova Kernel", device_name="marble"))
        self.assertNotIn("Nova Kernel", result)
        self.assertNotIn("marble", result)
        self.assertNotIn(" • ", result)

    def test_compact_heading_is_the_same_across_custom_identities(self):
        baseline = caption()
        for name, device in (
            ("Orion", "surya"),
            ("Nova Kernel", "marble"),
            ("Zenith kernel", "alioth"),
            ("Test Brand", "POCOX3"),
        ):
            with self.subTest(name=name, device=device):
                self.assertEqual(caption(metadata(kernel_name=name, device_name=device)), baseline)

    def test_unrequested_run_controls_are_absent(self):
        result = caption()
        for text in (
            "RC:",
            "rc1",
            "Config:",
            "vendor/xiaomi",
            "single",
            "private",
            "Kernel build</i>",
            "Branch:",
            "staging",
            "Local Time:",
            "Dhaka",
            "UTC+06:00",
        ):
            with self.subTest(text=text):
                self.assertNotIn(text, result)

    def test_image_modes_use_human_lists_without_plus_signs(self):
        for mode, expected in (
            ("Image.gz.dtb", "Image.gz.dtb"),
            ("Image.gz + dtbo.img + dtb.img", "Image.gz, dtbo.img and dtb.img"),
            ("Image.lz4 + dtbo.img + dtb.img", "Image.lz4, dtbo.img and dtb.img"),
        ):
            with self.subTest(mode=mode):
                self.assertEqual(image_label(mode), expected)
                result = caption(env=environment(IMAGE_TYPE=mode))
                self.assertIn(f"<b>Image:</b> {expected}", result)
                self.assertNotIn(" + ", result)

    def test_unknown_image_mode_cannot_be_displayed_as_a_success(self):
        with self.assertRaises(ValueError):
            image_label("unknown")

    def test_toolchain_and_linker_use_labels_and_no_build_provenance(self):
        result = caption()
        self.assertIn("<b>ToolChain:</b> Android Clang 22.0.1", result)
        self.assertIn("<b>Linker:</b> LLD 22.0.1", result)
        self.assertNotIn("clang version", result)
        self.assertNotIn("provenance", result)
        self.assertNotIn("Target:", result)

    def test_build_local_date_and_size_have_explicit_labels_and_binary_mib(self):
        result = caption()
        for text in (
            "<b>Build Time:</b> 2m 56s",
            "<b>Date:</b> Thursday, 26 November 2026",
            "<b>Zip Size:</b> 14.1 MiB",
        ):
            self.assertIn(text, result)
        self.assertNotIn(" MB", result)

    def test_reported_telegram_size_uses_the_same_binary_scale_as_document_card(self):
        # 15.52 decimal MB and 14.8 binary MiB describe the same file bytes.
        result = caption(metadata(zip_bytes=15_520_000))
        self.assertIn("<b>Zip Size:</b> 14.8 MiB", result)
        self.assertNotIn("15.52", result)

    def test_zip_size_boundaries_and_rejection_of_coerced_or_empty_counts(self):
        for size, expected in (
            (1, "0.0 MiB"),
            (1024 * 1024, "1.0 MiB"),
            (50_000_000, "47.7 MiB"),
            (2_000_000_000, "1907.3 MiB"),
        ):
            with self.subTest(size=size):
                self.assertEqual(zip_size_label(size), expected)
        for value in (0, -1, True, 1024.0, "1024", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                zip_size_label(value)

    def test_md5_and_latest_commit_have_separate_copyable_code_blocks(self):
        message = "Fix <scheduler> & stability\n\nBody paragraphs.\n\nSigned-off-by: Dev <dev@example.org>"
        result = caption(message=message)
        blocks = re.findall(r"<pre><code>(.*?)</code></pre>", result, re.DOTALL)
        self.assertEqual([html.unescape(block) for block in blocks], [metadata()["md5"], message])
        self.assertEqual(result.count("<code>"), 2)
        self.assertNotIn("<code>", result.split("<b>🔑 MD5 hash</b>")[0])

    def test_exact_requested_labels_order_spacing_and_separate_checksum_block(self):
        expected = (
            "<b>✅ Kernel Compiled Successfully</b>\n\n"
            "🔩 <b>ToolChain:</b> Android Clang 22.0.1\n"
            "🧶 <b>Linker:</b> LLD 22.0.1\n"
            "⏳ <b>Build Time:</b> 2m 56s\n"
            "〽️ <b>Zip Size:</b> 14.1 MiB\n"
            "📅 <b>Date:</b> Thursday, 26 November 2026\n"
            "📦 <b>Image:</b> Image.gz, dtbo.img and dtb.img\n\n"
            "<b>🔑 MD5 hash</b>\n<pre><code>" + "a" * 32 + "</code></pre>\n\n"
            "<b>📝 Latest commit</b>\n<pre><code>Fix scheduling\n\nPreserve the full body.</code></pre>"
        )
        self.assertEqual(caption(), expected)

    def test_invalid_checksums_are_rejected_instead_of_truncated_or_rendered(self):
        for value in (
            None,
            123,
            True,
            "",
            "a" * 64,
            "a" * 31,
            "a" * 33,
            "g" * 32,
            "a" * 31 + "\n",
            "<code>" + "a" * 32,
        ):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "MD5 hash"):
                caption(metadata(md5=value))

    def test_missing_md5_never_displays_sha256_under_md5_heading(self):
        meta = metadata(sha256="b" * 64)
        del meta["md5"]
        with self.assertRaisesRegex(ValueError, "MD5 hash"):
            caption(meta)
        meta["md5"] = "c" * 32
        result = caption(meta)
        self.assertIn("<b>🔑 MD5 hash</b>\n<pre><code>" + "c" * 32 + "</code></pre>", result)
        self.assertNotIn("b" * 64, result)
        self.assertNotIn("MD5SUM", result)
        self.assertNotIn("SHA256", result)
        self.assertNotIn("Images:", result)

    def test_valid_mixed_case_checksum_is_copied_exactly_without_prefix_or_newlines(self):
        digest = "0123456789aBcDeF0123456789AbCdEf"
        result = caption(metadata(md5=digest))
        blocks = re.findall(r"<pre><code>(.*?)</code></pre>", result, re.DOTALL)
        self.assertEqual(blocks[0], digest)
        self.assertEqual(len(blocks[0]), 32)

    def test_caption_no_longer_reads_removed_brand_and_branch_display_fields(self):
        meta, env = metadata(), environment()
        for key in ("kernel_name", "device_name", "kernel_rc"):
            del meta[key]
        del env["BRANCH"]
        self.assertEqual(caption(meta, env), caption())

    def test_checksum_stays_complete_across_compilers_images_and_overflowing_history(self):
        from html.parser import HTMLParser

        class BalancedCaption(HTMLParser):
            def __init__(self):
                super().__init__()
                self.stack = []

            def handle_starttag(self, tag, attrs):
                if tag not in ("b", "pre", "code") or attrs:
                    raise AssertionError("Unexpected caption tag")
                self.stack.append(tag)

            def handle_endtag(self, tag):
                if not self.stack or self.stack.pop() != tag:
                    raise AssertionError("Unbalanced caption HTML")

        for compiler in ("android-clang", "neutron-clang"):
            for mode in (
                "Image.gz.dtb",
                "Image.gz + dtbo.img + dtb.img",
                "Image.lz4 + dtbo.img + dtb.img",
            ):
                for title in ("📝 Latest commit", "📝 Recent commits (20)"):
                    with self.subTest(compiler=compiler, mode=mode, title=title):
                        result = make_caption(
                            metadata(),
                            environment(TOOLCHAIN=compiler, IMAGE_TYPE=mode),
                            "clang version 22.0.1",
                            "LLD 21.0.0",
                            "Fix <&😀>\n" * 2000,
                            history_title=title,
                        )
                        self.assertLessEqual(utf16_length(result), 1024)
                        blocks = re.findall(r"<pre><code>(.*?)</code></pre>", result, re.DOTALL)
                        self.assertEqual(len(blocks), 2)
                        self.assertEqual(blocks[0], metadata()["md5"])
                        self.assertIn(
                            "trimmed; full message in build logs", html.unescape(blocks[1])
                        )
                        parser = BalancedCaption()
                        parser.feed(result)
                        parser.close()
                        self.assertEqual(parser.stack, [])

    def test_commit_html_cannot_replace_or_merge_with_checksum_block(self):
        message = "</code></pre>\n<b>🔑 MD5 hash</b>\n<pre><code>" + "f" * 32
        result = caption(message=message)
        blocks = re.findall(r"<pre><code>(.*?)</code></pre>", result, re.DOTALL)
        self.assertEqual([html.unescape(block) for block in blocks], [metadata()["md5"], message])
        self.assertEqual(result.count("<b>🔑 MD5 hash</b>"), 1)

    def test_pathological_unicode_html_and_long_fields_fit_telegram_limit(self):
        result = caption(
            metadata(kernel_name="X" * 48, device_name="d" * 48),
            environment(BRANCH="feature/" + "x" * 110),
            '<&😀"' * 1000,
        )
        self.assertLessEqual(utf16_length(result), 1024)
        self.assertIn("trimmed; full message in build logs", result)
        self.assertEqual(result.count("<code>"), 2)
        self.assertNotIn("<&😀", result)


if __name__ == "__main__":
    unittest.main()
