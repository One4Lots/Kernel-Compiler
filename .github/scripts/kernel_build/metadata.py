#!/usr/bin/env python3
"""Shared build identity, compatibility validation, and compiler metadata."""

from __future__ import annotations

import re

TOOLCHAIN_NAMES = {"android-clang": "Android Clang", "neutron-clang": "Neutron Clang"}


def display_name(value: object, field: str) -> str:
    # Names are also embedded in installer properties and output filenames.
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 48
        or value != value.strip()
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._-]*", value)
    ):
        raise ValueError(
            f"{field} must be 1–48 characters: letters, numbers, spaces, dots, underscores, or hyphens; start with a letter/number and use no surrounding spaces"
        )
    return value


def device_codename(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,47}", value):
        raise ValueError(
            "device_name and device_codenames must be Android device codenames (1–48 letters, numbers, dots, underscores, or hyphens; no spaces)"
        )
    return value


def supported_devices(value: object) -> tuple[str, ...]:
    """Return the ordered compatibility list, independent of the package label."""
    if not isinstance(value, str) or not value or len(value) > 1600:
        raise ValueError("device_codenames must list 1–32 compatible device codenames")
    devices = tuple(device_codename(item.strip()) for item in value.split(","))
    if len(devices) > 32 or len(set(devices)) != len(devices):
        raise ValueError("device_codenames must contain 1–32 unique device codenames")
    return devices


def numeric_kernel_version(value: str) -> str:
    """Read the numeric base without changing the compiled kernel release string."""
    if (
        not isinstance(value, str)
        or len(value) > 4096
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise ValueError("Generated kernel.release must be a nonempty single line")
    match = re.match(r"^([0-9]+\.[0-9]+\.[0-9]+)(?=$|[-+])", value)
    if match is None:
        raise ValueError(f"Cannot extract numeric kernel version from {value!r}")
    return match.group(1)


def supported_android_versions(value: object) -> str:
    number = r"[0-9]{1,3}(?:\.[0-9]{1,3}){0,2}"
    selection = rf"(?:{number}-{number}|{number}(?:,{number})*)"
    if not isinstance(value, str) or len(value) > 160 or not re.fullmatch(selection, value):
        raise ValueError(
            "supported_versions must be an ascending Android version range or comma-separated versions, e.g. 12.0-17.0 or 12.0,13.0,17.0"
        )

    def version(number: str) -> tuple[int, ...]:
        parts = tuple(int(part) for part in number.split("."))
        return parts + (0,) * (3 - len(parts))

    for entry in value.split(","):
        if "-" in entry:
            start, end = entry.split("-")
            if version(start) > version(end):
                raise ValueError("supported_versions ranges must be ascending")
    return value


def github_maintainer(actor: object) -> str:
    if not isinstance(actor, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9-]{0,38}(?:\[bot\])?", actor
    ):
        raise ValueError(
            "BUILD_ACTOR must be the GitHub username of the user triggering the workflow"
        )
    return f"{actor}@github"


def kernel_title(name: str) -> str:
    return name if re.search(r"\bkernel$", name, re.IGNORECASE) else f"{name} Kernel"


def filename_component(value: str) -> str:
    """Names are validated first; filenames use hyphens instead of whitespace."""
    return re.sub(r" +", "-", value).rstrip(".-")
