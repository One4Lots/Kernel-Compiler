#!/usr/bin/env python3
"""Dependency-free presentation of verified build metadata; never invent versions or times."""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from metadata import TOOLCHAIN_NAMES

DHAKA = ZoneInfo("Asia/Dhaka")
VERSION = r"[0-9]+(?:\.[0-9]+){0,3}(?:git|[-+][A-Za-z0-9._-]+)?"


def toolchain_label(banner: str, selected: str) -> str:
    if selected not in TOOLCHAIN_NAMES:
        raise ValueError("Unknown selected toolchain for Telegram metadata")
    match = re.search(rf"\bclang\s+version\s+({VERSION})(?=\s|\(|$)", banner, re.IGNORECASE)
    if match is None:
        raise ValueError("Cannot extract the actual Clang version from the build log")
    return f"{TOOLCHAIN_NAMES[selected]} {match.group(1)}"


def linker_label(banner: str) -> str:
    match = re.search(rf"\bLLD\s+({VERSION})(?=\s|\(|$)", banner, re.IGNORECASE)
    if match is None:
        raise ValueError("Cannot extract the actual LLD version from the build log")
    return f"LLD {match.group(1)}"


def build_datetime(meta: dict) -> datetime:
    value = meta.get("built_at")
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("Build metadata requires an ISO timestamp with an explicit timezone")
    try:
        instant = datetime.fromisoformat(value)
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("Missing timezone")
        local = instant.astimezone(DHAKA)
    except (ValueError, OverflowError):
        raise ValueError("Invalid build timestamp; an explicit timezone is required") from None
    if meta.get("date") != local.strftime("%d%m%Y") or meta.get("time") != local.strftime(
        "%I:%M %p"
    ):
        raise ValueError("Build timestamp does not match the package date/time")
    return local


def local_time_labels(meta: dict) -> tuple[str, str]:
    local = build_datetime(meta)
    return local.strftime("%I:%M %p") + " • Dhaka (UTC+06:00)", local.strftime("%A, %d %B %Y")


def duration_label(seconds: int) -> str:
    if type(seconds) is not int or not 0 <= seconds <= 604800:
        raise ValueError("Invalid build duration")
    hours, remaining = divmod(seconds, 3600)
    minutes, seconds = divmod(remaining, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def zip_size_label(size: int) -> str:
    """Use binary units, matching the number shown by common Telegram clients."""
    if type(size) is not int or size <= 0:
        raise ValueError("ZIP size must be a positive integer byte count")
    return f"{size / (1024 * 1024):.1f} MiB"


def image_label(selected: str) -> str:
    # Keep the configured build strings stable; only change their display text.
    labels = {
        "Image.gz.dtb": "Image.gz.dtb",
        "Image.gz + dtbo.img + dtb.img": "Image.gz, dtbo.img and dtb.img",
        "Image.lz4 + dtbo.img + dtb.img": "Image.lz4, dtbo.img and dtb.img",
    }
    if selected not in labels:
        raise ValueError("Unknown image layout for Telegram metadata")
    return labels[selected]
