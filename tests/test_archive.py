"""Compiler extraction confinement and cache-miss pipeline checks; no network or real compiler."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".github/scripts/kernel_build"
sys.path.insert(0, str(SCRIPTS))

from archive import extract_compiler
from prepare import fingerprint


def entry(name, kind=tarfile.REGTYPE, data=b"compiler", link="", mode=0o644):
    item = tarfile.TarInfo(name)
    item.type, item.linkname, item.mode = kind, link, mode
    if kind == tarfile.REGTYPE:
        item.size = len(data)
    return item, data


def archive_bytes(*entries):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for item, data in entries:
            archive.addfile(item, io.BytesIO(data) if item.isfile() else None)
    return stream.getvalue()


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stage = self.root / "stage"
        self.stage.mkdir()

    def extract(self, *entries):
        extract_compiler(io.BytesIO(archive_bytes(*entries)), self.stage)

    def test_files_directories_and_relative_compiler_symlinks(self):
        self.extract(
            entry("./", tarfile.DIRTYPE),
            entry("bin", tarfile.DIRTYPE),
            entry("bin/clang", data=b"clang", mode=0o755),
            entry("bin/clang++", tarfile.SYMTYPE, link="clang"),
            entry("lib", tarfile.DIRTYPE),
            entry("lib/clang", tarfile.SYMTYPE, link="../bin/clang"),
        )
        self.assertEqual((self.stage / "bin/clang++").read_bytes(), b"clang")
        self.assertEqual((self.stage / "lib/clang").read_bytes(), b"clang")
        self.assertTrue(os.access(self.stage / "bin/clang", os.X_OK))

    def test_forward_and_chained_hardlinks(self):
        self.extract(
            entry("second", tarfile.LNKTYPE, link="first"),
            entry("first", tarfile.LNKTYPE, link="clang"),
            entry("clang", data=b"same executable", mode=0o755),
        )
        self.assertEqual((self.stage / "second").read_bytes(), b"same executable")
        self.assertEqual(
            (self.stage / "second").stat().st_ino, (self.stage / "clang").stat().st_ino
        )

    def test_absolute_and_parent_member_paths_are_rejected(self):
        for name in (str(self.root / "escape"), "../escape", "bin/../../escape", "bin/../clang"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "unsafe member"):
                self.extract(entry(name))
        self.assertFalse((self.root / "escape").exists())

    def test_absolute_and_escaping_link_targets_are_rejected(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
            for link in (str(self.root / "escape"), "../../escape"):
                with self.subTest(kind=kind, link=link), tempfile.TemporaryDirectory() as temporary:
                    with self.assertRaises(ValueError):
                        extract_compiler(
                            io.BytesIO(archive_bytes(entry("bin/alias", kind, link=link))),
                            Path(temporary),
                        )
        self.assertFalse((self.root / "escape").exists())

    def test_link_chain_cannot_escape_staging(self):
        with self.assertRaisesRegex(ValueError, "escapes staging"):
            self.extract(
                entry("bin/alias", tarfile.SYMTYPE, link="../redirect/file"),
                entry("redirect", tarfile.SYMTYPE, link="../outside"),
            )
        self.assertFalse((self.root / "outside").exists())

    def test_symlink_cycles_and_self_links_are_rejected(self):
        for entries in (
            (entry("first", tarfile.SYMTYPE, link="first"),),
            (
                entry("first", tarfile.SYMTYPE, link="second"),
                entry("second", tarfile.SYMTYPE, link="first"),
            ),
        ):
            with self.subTest(entries=entries), tempfile.TemporaryDirectory() as temporary:
                with self.assertRaises(ValueError):
                    extract_compiler(io.BytesIO(archive_bytes(*entries)), Path(temporary))

    def test_devices_and_fifos_are_rejected(self):
        for kind in (tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE):
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "special file"):
                self.extract(entry("special", kind))
        self.assertFalse((self.stage / "special").exists())

    def test_duplicate_files_and_file_directory_conflicts_are_rejected(self):
        for pair in (
            (entry("file"), entry("file")),
            (entry("file"), entry("file", tarfile.DIRTYPE)),
            (entry("file", tarfile.LNKTYPE, link="later"), entry("file", tarfile.DIRTYPE)),
        ):
            with self.subTest(pair=pair), tempfile.TemporaryDirectory() as temporary:
                with self.assertRaises((ValueError, OSError)):
                    extract_compiler(io.BytesIO(archive_bytes(*pair)), Path(temporary))

    def test_missing_cyclic_and_symbolic_hardlink_targets_are_rejected(self):
        cases = (
            (entry("link", tarfile.LNKTYPE, link="missing"),),
            (entry("one", tarfile.LNKTYPE, link="two"), entry("two", tarfile.LNKTYPE, link="one")),
            (
                entry("file"),
                entry("sym", tarfile.SYMTYPE, link="file"),
                entry("hard", tarfile.LNKTYPE, link="sym"),
            ),
        )
        for entries in cases:
            with self.subTest(entries=entries), tempfile.TemporaryDirectory() as temporary:
                with self.assertRaises(ValueError):
                    extract_compiler(io.BytesIO(archive_bytes(*entries)), Path(temporary))

    def test_setid_and_writable_permissions_are_not_restored(self):
        self.extract(entry("clang", mode=0o6777), entry("data", mode=0o666))
        self.assertEqual((self.stage / "clang").stat().st_mode & 0o7777, 0o755)
        self.assertEqual((self.stage / "data").stat().st_mode & 0o7777, 0o644)

    def test_corrupt_and_truncated_archives_fail(self):
        for data in (
            b"",
            b"not a tar archive",
            archive_bytes(entry("large", data=b"x" * 10000))[:1024],
        ):
            with self.subTest(length=len(data)), tempfile.TemporaryDirectory() as temporary:
                with self.assertRaises(ValueError):
                    extract_compiler(io.BytesIO(data), Path(temporary))

    def test_nonempty_or_symlinked_staging_is_rejected(self):
        (self.stage / "keep").write_text("untouched")
        link = self.root / "link"
        link.symlink_to(self.stage, target_is_directory=True)
        for target in (self.stage, link, self.root / "missing"):
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, "empty regular"):
                extract_compiler(io.BytesIO(archive_bytes(entry("clang"))), target)
        self.assertEqual((self.stage / "keep").read_text(), "untouched")

    def test_stream_is_drained_after_tar_terminator(self):
        stream = io.BytesIO(archive_bytes(entry("clang")) + b"trailing decompressed bytes")
        extract_compiler(stream, self.stage)
        self.assertEqual(stream.read(), b"")

    def run_pipeline(self, data, decompressor_status=0, download_status=0):
        fixture = self.root / "compiler.tar"
        fixture.write_bytes(data)
        binary = self.root / "bin"
        binary.mkdir(exist_ok=True)
        curl = binary / "curl"
        curl.write_text(
            "#!/usr/bin/env python3\nimport os,sys,shutil\nif '--head' in sys.argv:\n print('HTTP/2 200\\netag: pinned\\ncontent-length: 1234\\n')\nelse:\n if int(os.environ['DOWNLOAD_STATUS']): sys.exit(int(os.environ['DOWNLOAD_STATUS']))\n shutil.copyfile(os.environ['FIXTURE_TAR'],sys.argv[sys.argv.index('--output')+1])\n"
        )
        zstd = binary / "zstd"
        zstd.write_text(
            "#!/usr/bin/env python3\nimport os,sys\nfrom pathlib import Path\nsys.stdout.buffer.write(Path(sys.argv[-1]).read_bytes())\nsys.stdout.buffer.flush()\nsys.exit(int(os.environ['DECOMPRESSOR_STATUS']))\n"
        )
        curl.chmod(0o755)
        zstd.chmod(0o755)
        url = "https://github.com/example/compiler/releases/download/test/compiler.tar.zst"
        env = dict(
            os.environ,
            PATH=str(binary) + os.pathsep + os.environ["PATH"],
            FIXTURE_TAR=str(fixture),
            DECOMPRESSOR_STATUS=str(decompressor_status),
            DOWNLOAD_STATUS=str(download_status),
            RUNNER_TEMP=str(self.root),
            TOOLCHAIN_DIR=str(self.root / "cache"),
            TOOLCHAIN_BIN_REL="bin",
            TOOLCHAIN_URL=url,
            TOOLCHAIN_HASH=fingerprint(url, {"etag": "pinned", "content-length": "1234"}),
            GITHUB_PATH=str(self.root / "github-path"),
        )
        return subprocess.run(
            ["bash", str(SCRIPTS / "toolchain.sh")],
            env=env,
            cwd=self.root,
            text=True,
            capture_output=True,
        )

    def compiler_archive(self, clang_directory=False):
        tools = (
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
        )
        return archive_bytes(
            *(
                entry(
                    "bin/" + name,
                    tarfile.DIRTYPE if clang_directory and name == "clang" else tarfile.REGTYPE,
                    data=b"#!/bin/sh\necho fixture-compiler\n",
                    mode=0o755,
                )
                for name in tools
            )
        )

    def test_cache_miss_pipeline_installs_complete_compiler(self):
        result = self.run_pipeline(self.compiler_archive())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / "cache/bin/clang").is_file())
        self.assertTrue((self.root / "github-path").is_file())
        self.assertFalse(list(self.root.glob("kernel-build-toolchain.*")))

    def test_decompression_failure_never_installs_partial_cache(self):
        result = self.run_pipeline(self.compiler_archive(), decompressor_status=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "cache").exists())
        self.assertFalse((self.root / "github-path").exists())
        self.assertFalse(list(self.root.glob("kernel-build-toolchain.*")))

    def test_incomplete_release_is_not_installed_and_clean_retry_succeeds(self):
        for data in (
            archive_bytes(entry("bin/clang", mode=0o755)),
            self.compiler_archive(clang_directory=True),
        ):
            with self.subTest(size=len(data)):
                result = self.run_pipeline(data)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("toolchain is incomplete", result.stderr)
                self.assertFalse((self.root / "cache").exists())
                self.assertFalse((self.root / "github-path").exists())
                self.assertFalse(list(self.root.glob("kernel-build-toolchain.*")))
        retry = self.run_pipeline(self.compiler_archive())
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertTrue((self.root / "cache/bin/clang").is_file())

    def test_failed_download_leaves_no_cache_or_path_export(self):
        result = self.run_pipeline(self.compiler_archive(), download_status=22)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "cache").exists())
        self.assertFalse((self.root / "github-path").exists())
        self.assertFalse(list(self.root.glob("kernel-build-toolchain.*")))

    def test_unsafe_download_never_installs_cache_or_escapes_staging(self):
        result = self.run_pipeline(archive_bytes(entry("../escape")))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "cache").exists())
        self.assertFalse((self.root / "escape").exists())
        self.assertFalse((self.root / "github-path").exists())
        self.assertFalse(list(self.root.glob("kernel-build-toolchain.*")))


if __name__ == "__main__":
    unittest.main()
