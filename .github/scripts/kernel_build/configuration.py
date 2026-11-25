#!/usr/bin/env python3
"""Resolve a small dispatch form into one validated, credential-free build profile."""

from __future__ import annotations

import json
import re
from pathlib import Path

from delivery_modes import delivery_plan

PROFILE_KEYS = frozenset(
    {
        "kernel_name",
        "kernel_rc",
        "device_name",
        "device_codenames",
        "supported_versions",
        "anykernel_repository",
        "repo_visibility",
        "kernel_repository",
        "build_mode",
        "single_branch",
        "multi_branches_json",
        "defconfig",
        "image_type",
        "toolchain",
        "publish_to",
        "changelog_mode",
        "commit_count",
        "release_repository",
        "release_target",
        "duplicate_release",
    }
)
COMMON_KEYS = frozenset({"kernel_rc", "build_mode", "toolchain", "publish_to"})
ADVANCED_KEYS = PROFILE_KEYS - COMMON_KEYS
DISPATCH_KEYS = PROFILE_KEYS | {"config_file"}


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def resolve_dispatch(dispatch: dict, root: Path | None = None) -> dict:
    """Only load bounded regular JSON files from the checked-out configs directory."""
    if not isinstance(dispatch, dict):
        raise ValueError("Dispatch inputs must be a JSON object")
    if set(dispatch) - DISPATCH_KEYS:
        raise ValueError("Unknown dispatch input; edit a build profile for advanced settings")
    name = dispatch.get("config_file", "configs/miatoll.json")
    if not isinstance(name, str) or not re.fullmatch(
        r"configs/[A-Za-z0-9][A-Za-z0-9_-]{0,63}\.json", name
    ):
        raise ValueError("config_file must be configs/<profile>.json; no traversal or nested paths")
    root = (root or Path(__file__).resolve().parents[3]).resolve()
    path = root / name
    if (root / "configs").is_symlink() or path.is_symlink() or not path.is_file():
        raise ValueError("Build profile must be a regular, non-symlinked JSON file in configs/")
    if path.stat().st_size > 32768:
        raise ValueError("Build profile exceeds 32 KiB")
    try:
        settings = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Build profile must be valid UTF-8 JSON") from error
    if not isinstance(settings, dict) or set(settings) != PROFILE_KEYS:
        raise ValueError(
            "Build profile fields must match configs/miatoll.json; unknown/missing keys are not accepted"
        )
    for key, value in settings.items():
        if key not in ("commit_count", "multi_branches_json") and not isinstance(value, str):
            raise ValueError(f"Profile {key} must be a string")
    entries = settings["multi_branches_json"]
    if not isinstance(entries, list):
        raise ValueError("Profile multi_branches_json must be a JSON array")
    settings["multi_branches_json"] = json.dumps(entries, separators=(",", ":"))
    for key in ADVANCED_KEYS:
        if key not in dispatch:
            continue
        value = dispatch[key]
        if not isinstance(value, str):
            raise ValueError(f"Dispatch {key} must be a string")
        if value == "" or (
            key in {"repo_visibility", "image_type", "changelog_mode", "duplicate_release"}
            and value == "profile"
        ):
            continue
        if key == "multi_branches_json":
            try:
                entries = json.loads(value, object_pairs_hook=unique_object)
            except json.JSONDecodeError as error:
                raise ValueError("Desktop branch matrix must be a JSON array") from error
            if not isinstance(entries, list):
                raise ValueError("Desktop branch matrix must be a JSON array")
            value = json.dumps(entries, separators=(",", ":"))
        settings[key] = value
    for key in COMMON_KEYS:
        if key in dispatch:
            if not isinstance(dispatch[key], str) or not dispatch[key]:
                raise ValueError(f"Dispatch {key} must be a nonempty string")
            settings[key] = dispatch[key]
    # Dispatch selection is the only publication authority; profiles cannot opt in.
    destination = dispatch.get("publish_to", "artifacts only")
    delivery_plan(destination)
    settings["publish_to"] = destination
    return settings
