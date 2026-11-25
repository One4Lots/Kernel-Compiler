#!/usr/bin/env python3
"""Verify Telegram packages and keep personal delivery separate from channel release."""

from __future__ import annotations

import json
import os
from pathlib import Path

from prepare import make_matrix
from publication import load_bundles
from publication_settings import PublicationSettings
from runtime import file_digest, run
from telegram import (
    DOCUMENT_LIMIT,
    prepare_caption,
    send_changelog,
    send_document,
    validate_bot_token,
    validate_channel,
    validate_personal_chat,
)


def validate_log(path: Path, root: Path, limit: int = 1_048_576) -> None:
    """Bound caption inputs and reject symlinks anywhere in the download path."""
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Missing or unsafe Telegram release metadata")
    relative = path.relative_to(root)
    if any(
        root.joinpath(*relative.parts[:index]).is_symlink()
        for index in range(len(relative.parts) + 1)
    ):
        raise ValueError("Symlinked Telegram release metadata is not allowed")
    if not 0 < path.stat().st_size <= limit:
        raise ValueError("Empty or oversized Telegram release metadata")


def prepare_release(
    root: Path, expected: list[dict], inputs: dict, *, channel: bool = True
) -> list[dict]:
    """Validate all ZIPs and captions before sending the first document."""
    settings = PublicationSettings.from_inputs(inputs, os.environ.get("GITHUB_REPOSITORY", ""))
    if channel and not settings.channel:
        raise ValueError("Channel publication was not selected by the delivery mode")
    if not channel and not settings.telegram:
        raise ValueError("Personal Telegram delivery was not selected")
    if expected != make_matrix(inputs)["include"]:
        raise ValueError("Telegram release matrix differs from validated settings")
    manifests = load_bundles(
        root,
        expected,
        settings,
        inputs["kernel_name"],
        inputs["device_name"],
        inputs["kernel_repository"],
    )
    deliveries = []
    for manifest in manifests:
        archive = manifest["archive"]
        if archive.stat().st_size > DOCUMENT_LIMIT:
            raise ValueError("A release ZIP exceeds Telegram's 50 MB limit; no documents sent")
        # The canonical artifact path and identity were verified by load_bundles.
        logs = archive.parents[len(Path(manifest["zip_relative_path"]).parts)] / "logs"
        names = ["clang-version.txt", "lld-version.txt"]
        names += (
            ["recent-commits.json"]
            if inputs["changelog_mode"] == "recent-commits"
            else ["commit-message.txt"]
        )
        for name in names:
            validate_log(logs / name, root)
        # SHA256 integrity was verified by load_bundles; recompute display MD5.
        meta = dict(manifest, md5=file_digest(archive, "md5"))
        environment = {
            "TOOLCHAIN": inputs["toolchain"],
            "BRANCH": manifest["branch"],
            "DEFCONFIG": manifest["defconfig"],
            "IMAGE_TYPE": manifest["image_type"],
            "BUILD_MODE": inputs["build_mode"],
            "REPO_VISIBILITY": inputs["repo_visibility"],
            "CHANGELOG_MODE": settings.changelog_mode,
            "COMMIT_COUNT": str(settings.count),
        }
        caption, followup, heading = prepare_caption(logs, meta, environment)
        deliveries.append(
            {"archive": archive, "caption": caption, "followup": followup, "heading": heading}
        )
    return deliveries


def deliver(*, channel: bool) -> None:
    flag = "TELEGRAM_CHANNEL_CONFIRMED" if channel else "TELEGRAM_DELIVERY_CONFIRMED"
    label = "Telegram channel release" if channel else "Personal Telegram delivery"
    secret = "TELEGRAM_CHANNEL_ID" if channel else "TELEGRAM_CHAT_ID"
    if os.environ.get(flag) != "true":
        print(f"==> {label} disabled; no message sent")
        return
    inputs = json.loads(os.environ["INPUTS_JSON"])
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get(secret, "").strip()
    if not token or not chat_id:
        raise ValueError(f"{label} requires TELEGRAM_BOT_TOKEN and {secret}")
    validate_bot_token(token)
    if channel:
        validate_channel(chat_id)
    else:
        validate_personal_chat(chat_id)
    deliveries = prepare_release(
        Path("release-downloads"),
        json.loads(os.environ["MATRIX_JSON"])["include"],
        inputs,
        channel=channel,
    )
    print(f"==> All {len(deliveries)} package(s) verified; beginning {label.lower()}")
    for delivery in deliveries:
        send_document(delivery["archive"], delivery["caption"], token, chat_id)
        if delivery["followup"]:
            send_changelog(delivery["followup"], delivery["heading"], token, chat_id)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as stream:
            stream.write(f"### {label}\n\nDelivered {len(deliveries)} verified package(s).\n")


def main() -> None:
    deliver(channel=True)


if __name__ == "__main__":
    run(main)
