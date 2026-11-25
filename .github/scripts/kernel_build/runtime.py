"""Small, dependency-free helpers for workflow entry points."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path


def file_digest(path: Path, algorithm: str) -> str:
    """Hash large build outputs without loading the entire archive into memory."""
    digest = hashlib.new(algorithm, usedforsecurity=algorithm != "md5")
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def output(name: str, value: str) -> None:
    """Write a single-line Actions output without allowing extra output records."""
    if not name or any(char in name for char in "=\r\n") or any(char in value for char in "\r\n"):
        raise ValueError("Workflow output names and values must be single-line records")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
        stream.write(f"{name}={value}\n")


def run(main: Callable[[], None]) -> None:
    """Report expected failures consistently without emitting multiline commands."""
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError, subprocess.CalledProcessError) as error:
        message = str(error).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error::{message}", file=sys.stderr)
        sys.exit(1)
