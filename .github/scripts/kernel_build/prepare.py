#!/usr/bin/env python3
"""Validate dispatch inputs and resolve immutable, header-aware cache metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
from datetime import datetime, timezone
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from configuration import resolve_dispatch, unique_object
from delivery_modes import delivery_plan
from metadata import (
    device_codename,
    display_name,
    filename_component,
    supported_android_versions,
    supported_devices,
)
from naming import branch_path, kernel_rc, package_relative_path
from publication_settings import PublicationSettings
from runtime import output, run

IMAGE_TYPES = (
    "Image.gz.dtb",
    "Image.gz + dtbo.img + dtb.img",
    "Image.lz4 + dtbo.img + dtb.img",
)
TOOLCHAINS = {
    "android-clang": (
        "https://github.com/One4Lots/Android-Clang/releases/download/"
        "clang-r584948b-74fa19d51840/clang-r584948b-linux-x86_64-74fa19d51840.tar.zst",
        "clang-r584948b/bin",
    ),
    # Pin compiler upgrades to an explicit release.
    "neutron-clang": (
        "https://github.com/Neutron-Toolchains/clang-build-catalogue/releases/"
        "download/06092026/neutron-clang-06092026.tar.zst",
        "bin",
    ),
}


def text(value: object, name: str, limit: int = 200) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ValueError(f"{name} must be a nonempty string of at most {limit} characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{name} contains control characters")
    return value


def validate_source(url: object, field: str = "kernel_repository") -> str:
    url = text(url, field)
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "github.com"
        or parsed.query
        or parsed.fragment
        or not re.fullmatch(r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?", parsed.path)
    ):
        raise ValueError(
            f"{field} must be a credential-free https://github.com/owner/repository URL"
        )
    return url


def validate_defconfig(value: object) -> str:
    value = text(value, "defconfig", 160)
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or any(part in ("", ".", "..") for part in value.split("/"))
        or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./-]*", value)
    ):
        raise ValueError("defconfig must be a safe relative path under arch/arm64/configs")
    return value


def make_matrix(inputs: dict) -> dict:
    if inputs.get("build_mode") not in ("single", "multi"):
        raise ValueError("build_mode must be single or multi")
    if inputs.get("repo_visibility") not in ("private", "public"):
        raise ValueError("repo_visibility must be private or public")
    if inputs.get("toolchain") not in TOOLCHAINS:
        raise ValueError("toolchain must be android-clang or neutron-clang")
    display_name(inputs.get("kernel_name"), "kernel_name")
    rc = kernel_rc(inputs.get("kernel_rc", "rc1"))
    primary_device = device_codename(inputs.get("device_name"))
    supported_devices(inputs.get("device_codenames"))
    supported_android_versions(inputs.get("supported_versions"))
    validate_source(inputs.get("anykernel_repository"), "anykernel_repository")
    validate_source(inputs.get("kernel_repository"))
    default_config = validate_defconfig(inputs.get("defconfig"))
    default_image = inputs.get("image_type")
    if default_image not in IMAGE_TYPES:
        raise ValueError("Unsupported default image_type")
    if inputs["build_mode"] == "single":
        entries = [{"branch": inputs.get("single_branch")}]
    else:
        try:
            entries = json.loads(
                inputs.get("multi_branches_json", ""), object_pairs_hook=unique_object
            )
        except (ValueError, TypeError) as exc:
            raise ValueError("multi_branches_json must be a valid JSON array") from exc
    if not isinstance(entries, list) or not 1 <= len(entries) <= 32:
        raise ValueError("The branch matrix must be a nonempty JSON array with at most 32 entries")
    result = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) - {"branch", "defconfig", "image_type"}:
            raise ValueError("Each matrix entry accepts only branch, defconfig, and image_type")
        branch = text(entry.get("branch"), "branch", 120)
        branch_path(branch)
        # Reject invalid package paths before allocating build runners.
        package_relative_path(branch, inputs["kernel_name"], primary_device, rc, "01012000")
        config = validate_defconfig(entry.get("defconfig", default_config))
        image = entry.get("image_type", default_image)
        if image not in IMAGE_TYPES:
            raise ValueError("Unsupported per-branch image_type")
        signature = (branch, config, image)
        if signature in seen:
            raise ValueError("Duplicate branch/defconfig/image_type matrix entry")
        seen.add(signature)
        digest = hashlib.sha256(json.dumps(signature).encode()).hexdigest()[:12]
        result.append(
            {"branch": branch, "defconfig": config, "image_type": image, "id": f"build-{digest}"}
        )
    return {"include": result}


def runner_cache_namespace(release: dict[str, str], machine: str) -> str:
    """Partition caches by the actual image, not a moving runner label."""
    components = (release.get("ID", ""), release.get("VERSION_ID", ""), machine)
    if any(
        not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", item)
        for item in components
    ):
        raise ValueError("Cannot determine a safe runner OS/version/architecture cache namespace")
    return "-".join(components)


def release_headers(url: str) -> dict[str, str]:
    completed = subprocess.run(
        [
            "curl",
            "--head",
            "--location",
            "--fail",
            "--silent",
            "--show-error",
            "--retry",
            "4",
            "--retry-all-errors",
            "--connect-timeout",
            "20",
            "--max-time",
            "120",
            "--proto",
            "=https",
            "--proto-redir",
            "=https",
            url,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode:
        raise ValueError(
            "Cannot inspect remote toolchain release headers; refusing an unreliable cache key"
        )
    # Fingerprint only the final response, excluding redirect and proxy headers.
    responses = re.split(r"(?im)^HTTP/\S+\s+\d+[^\n]*\n", completed.stdout)
    headers = {}
    for line in responses[-1].splitlines():
        name, separator, value = line.partition(":")
        if separator and name.lower() in ("etag", "last-modified", "content-length"):
            headers[name.lower()] = value.strip()
    if not headers:
        raise ValueError(
            "Release returned no ETag, Last-Modified, or Content-Length; cannot fingerprint it safely"
        )
    return headers


def fingerprint(url: str, headers: dict[str, str]) -> str:
    return hashlib.sha256(
        json.dumps({"url": url, "headers": headers}, sort_keys=True).encode()
    ).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["matrix", "runner", "toolchain", "verify-toolchain"])
    command = parser.parse_args().command
    if command == "matrix":
        inputs = json.loads(os.environ["INPUTS_JSON"])
        if "config_file" in inputs:
            inputs = resolve_dispatch(inputs)
        matrix = make_matrix(inputs)
        publication = PublicationSettings.from_inputs(
            inputs, os.environ.get("GITHUB_REPOSITORY", "")
        )
        if inputs["repo_visibility"] == "private" and not os.environ.get("GH_PAT", "").strip():
            raise ValueError(
                "Private mode requires secrets.GH_PAT with read access to the kernel repository"
            )
        if publication.github and not (
            os.environ.get("GH_RELEASE_TOKEN", "").strip() or os.environ.get("GH_PAT", "").strip()
        ):
            raise ValueError(
                "GitHub release publishing requires GH_RELEASE_TOKEN (or GH_PAT) with Contents: write on the destination repository"
            )
        if publication.telegram or publication.channel:
            from telegram import validate_bot_token

            if not os.environ.get("TELEGRAM_BOT_TOKEN", "").strip():
                raise ValueError(
                    "Missing secrets.TELEGRAM_BOT_TOKEN for the selected Telegram destination"
                )
            validate_bot_token(os.environ["TELEGRAM_BOT_TOKEN"].strip())
        if publication.telegram:
            from telegram import validate_personal_chat

            validate_personal_chat(os.environ.get("TELEGRAM_CHAT_ID", "").strip())
        if publication.channel:
            if not os.environ.get("TELEGRAM_CHANNEL_ID", "").strip():
                raise ValueError("Missing secrets.TELEGRAM_CHANNEL_ID for the release channel")
            from telegram import validate_channel

            validate_channel(os.environ["TELEGRAM_CHANNEL_ID"].strip())
        output(
            "delivery",
            json.dumps(delivery_plan(publication.publish_to).as_dict(), separators=(",", ":")),
        )
        output("settings", json.dumps(inputs, separators=(",", ":")))
        output("matrix", json.dumps(matrix, separators=(",", ":")))
        output("kernel_slug", filename_component(inputs["kernel_name"]))
        output("device_slug", filename_component(inputs["device_name"]))
        output("history_depth", str(publication.history_depth))
        print(f"Validated {len(matrix['include'])} build(s); all use {inputs['toolchain']}.")
        plan = delivery_plan(publication.publish_to)
        print(
            f"Delivery mode: {publication.publish_to} | personal={plan.personal} | channel={plan.channel} | GitHub={plan.github}"
        )
        if summary_path := os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(summary_path, "a", encoding="utf-8") as summary:
                summary.write(
                    f"### Validated build plan\n\n```text\nKernel: {inputs['kernel_name']}\nMode: {publication.publish_to}\nBuilds: {len(matrix['include'])}\nCompiler: {inputs['toolchain']}\nPersonal Telegram: {plan.personal}\nChannel: {plan.channel}\nGitHub Release: {plan.github}\n```\n"
                )

    elif command == "runner":
        namespace = runner_cache_namespace(platform.freedesktop_os_release(), platform.machine())
        output("cache_namespace", namespace)
        print(f"Runner cache namespace: {namespace}")
    elif command == "toolchain":
        url, binary = TOOLCHAINS[os.environ["TOOLCHAIN"]]
        headers = release_headers(url)
        output("url", url)
        output("bin", binary)
        output("hash", fingerprint(url, headers))
        output("apt_epoch", datetime.now(timezone.utc).strftime("%G-W%V"))
        print(f"Release fingerprint metadata: {json.dumps(headers, sort_keys=True)}")
    elif command == "verify-toolchain":
        url = os.environ["TOOLCHAIN_URL"]
        if fingerprint(url, release_headers(url)) != os.environ["TOOLCHAIN_HASH"]:
            raise ValueError(
                "Toolchain release changed during this run. Rerun the workflow to refresh its cache key"
            )


if __name__ == "__main__":
    run(main)
