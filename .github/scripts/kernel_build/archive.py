#!/usr/bin/env python3
"""Extract compiler tar streams with bounded memory and confined links (Python 3.10+)."""

from __future__ import annotations

import os
import shutil
import sys
import tarfile
from pathlib import Path
from typing import BinaryIO

from runtime import run


def extract_compiler(stream: BinaryIO, destination: Path) -> None:
    """Write only files/directories/confined links into an empty staging directory.

    Never use tar's extraction methods or trust archive ownership/permissions.
    The caller deletes staging on failure and installs it only after successful
    decompression and extraction. Compiler releases are still trusted executable
    inputs; confinement is not a sandbox for the compiler once it runs.
    """
    if destination.is_symlink() or not destination.is_dir() or any(destination.iterdir()):
        raise ValueError("Compiler extraction requires an empty regular staging directory")
    root = destination.resolve()
    hardlinks = []
    reserved = set()

    def confined(path: Path) -> Path:
        try:
            resolved = path.resolve()
        except (OSError, RuntimeError):
            raise ValueError("Compiler archive contains an invalid or cyclic link") from None
        if not resolved.is_relative_to(root):
            raise ValueError("Compiler archive path or link escapes staging")
        return path

    def member_path(name: str) -> Path:
        path = Path(name)
        if path.is_absolute() or ".." in path.parts or "\x00" in name:
            raise ValueError("Compiler archive contains an unsafe member path")
        return confined(root / path)

    try:
        with tarfile.open(fileobj=stream, mode="r|") as archive:
            for member in archive:
                target = member_path(member.name)
                if member.isdir():
                    if target in reserved or target.is_symlink():
                        raise ValueError("Compiler archive directory conflicts with another member")
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if target == root or target in reserved or target.exists() or target.is_symlink():
                    raise ValueError("Compiler archive contains a duplicate or conflicting member")
                target.parent.mkdir(parents=True, exist_ok=True)
                reserved.add(target)
                if member.isfile():
                    source = archive.extractfile(member)
                    if source is None:
                        raise ValueError("Compiler archive regular file has no data")
                    with source, target.open("xb") as output:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
                    target.chmod(0o755 if member.mode & 0o111 else 0o644)
                elif member.issym() or member.islnk():
                    link = Path(member.linkname)
                    if link.is_absolute() or not member.linkname or "\x00" in member.linkname:
                        raise ValueError("Compiler archive contains an unsafe link target")
                    source = confined((target.parent if member.issym() else root) / link)
                    if source == target:
                        raise ValueError("Compiler archive contains a self-referencing link")
                    if member.issym():
                        target.symlink_to(member.linkname)
                        # Detect cycles introduced by this new link as well.
                        confined(target)
                    else:
                        hardlinks.append((target, source))
                else:
                    raise ValueError("Compiler archive contains an unsupported special file")
        # Resolve forward hardlinks only after all regular files have been read.
        while hardlinks:
            pending = []
            for target, source in hardlinks:
                confined(target)
                confined(source)
                if source.is_symlink():
                    raise ValueError("Compiler archive hardlink points to a symbolic link")
                if not source.exists():
                    pending.append((target, source))
                elif source.is_file():
                    os.link(source, target)
                else:
                    raise ValueError("Compiler archive hardlink target is not a regular file")
            if len(pending) == len(hardlinks):
                raise ValueError("Compiler archive has missing or cyclic hardlink targets")
            hardlinks = pending
        # Drain the decompressor so a valid tar terminator does not cause SIGPIPE
        # and trailing compressed-stream corruption is reported by pipefail.
        while stream.read(1024 * 1024):
            pass
    except (tarfile.TarError, EOFError) as error:
        raise ValueError(f"Corrupt compiler archive ({type(error).__name__})") from None


def main() -> None:
    if len(sys.argv) != 2:
        raise ValueError("Usage: archive.py EMPTY_STAGING_DIRECTORY < decompressed.tar")
    extract_compiler(sys.stdin.buffer, Path(sys.argv[1]))


if __name__ == "__main__":
    run(main)
