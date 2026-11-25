#!/usr/bin/env python3
"""One RC/tag value and branch-preserving, portable package paths."""

from __future__ import annotations

import re
import subprocess
from datetime import datetime
from pathlib import PurePosixPath

from metadata import device_codename, display_name, filename_component


def kernel_rc(value: object) -> str:
    # The RC must be valid as both a filename component and a Git tag.
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,47}", value)
        or subprocess.run(
            ["git", "check-ref-format", f"refs/tags/{value}"], capture_output=True
        ).returncode
    ):
        raise ValueError(
            "kernel_rc must be 1–48 filename-safe Git tag characters (letters, numbers, dots, underscores, plus, or hyphen), e.g. rc1 or v1.0-rc1; no slashes"
        )
    return value


def branch_path(value: object) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 120
        or any(not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.+-]*", part) for part in value.split("/"))
        or subprocess.run(
            ["git", "check-ref-format", "--branch", value], capture_output=True
        ).returncode
    ):
        raise ValueError(
            "Branch must be a safe relative Git branch path with portable letters, numbers, dots, underscores, plus, or hyphens in each slash-separated component"
        )
    return value


def package_relative_path(branch: str, name: str, device: str, rc: str, date: str) -> PurePosixPath:
    branch_path(branch)
    display_name(name, "kernel_name")
    device_codename(device)
    kernel_rc(rc)
    if not isinstance(date, str) or not re.fullmatch(r"[0-9]{8}", date):
        raise ValueError("Package date must be ddmmyyyy")
    try:
        datetime.strptime(date, "%d%m%Y")
    except ValueError:
        raise ValueError("Package date must be a real ddmmyyyy date") from None
    # Branch slashes form directories; publication destinations use path.name.
    path = PurePosixPath(
        f"{branch}-{filename_component(name)}-{filename_component(device)}-{rc}-{date}.zip"
    )
    if len(path.name.encode("utf-8")) > 255:
        raise ValueError(
            "ZIP basename exceeds the filesystem's 255-byte limit; shorten the branch/name/RC"
        )
    return path
