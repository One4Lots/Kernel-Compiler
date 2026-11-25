#!/usr/bin/env python3
"""Validated dispatch settings shared by preflight, history, and publishers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from delivery_modes import delivery_plan
from naming import kernel_rc


def commit_count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("commit_count must be a whole number from 1 to 50")
    try:
        count = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("commit_count must be a whole number from 1 to 50") from None
    # Dispatch numbers can serialize as integers or integral floats.
    if not count.is_finite() or count != count.to_integral_value() or not 1 <= count <= 50:
        raise ValueError("commit_count must be a whole number from 1 to 50")
    return int(count)


def repository_name(value: str) -> str:
    if value.startswith("https://"):
        url = urlsplit(value)
        if url.netloc != "github.com" or url.query or url.fragment:
            raise ValueError(
                "release_repository must be owner/repo or a credential-free HTTPS GitHub URL"
            )
        value = url.path.removeprefix("/").removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,99}/[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}", value):
        raise ValueError(
            "release_repository must be owner/repo or a credential-free HTTPS GitHub URL"
        )
    return value


@dataclass(frozen=True)
class PublicationSettings:
    publish_to: str
    changelog_mode: str
    count: int
    repository: str
    tag: str
    target: str
    duplicate_policy: str

    @property
    def telegram(self) -> bool:
        return delivery_plan(self.publish_to).personal

    @property
    def github(self) -> bool:
        return delivery_plan(self.publish_to).github

    @property
    def channel(self) -> bool:
        return delivery_plan(self.publish_to).channel

    @property
    def history_depth(self) -> int:
        # GitHub requires recent history regardless of Telegram's display mode.
        return self.count if self.github or self.changelog_mode == "recent-commits" else 1

    @classmethod
    def from_inputs(cls, inputs: dict, current_repository: str = "") -> PublicationSettings:
        destination = inputs.get("publish_to", "artifacts only")
        mode = inputs.get("changelog_mode", "latest-message")
        policy = inputs.get("duplicate_release", "fail")
        if "telegram_release" in inputs:
            raise ValueError(
                "The old telegram_release checkbox is no longer supported; select a delivery mode"
            )
        plan = delivery_plan(destination)
        if mode not in ("latest-message", "recent-commits"):
            raise ValueError("changelog_mode must be latest-message or recent-commits")
        if policy not in ("fail", "skip", "update"):
            raise ValueError("duplicate_release must be fail, skip, or update")
        count = commit_count(inputs.get("commit_count", 6))
        tag = kernel_rc(inputs.get("kernel_rc", "rc1"))
        repo, target = "", ""
        if plan.github:
            repo = repository_name(inputs.get("release_repository") or current_repository)
            target = inputs.get("release_target", "")
            if (
                not isinstance(target, str)
                or len(target) > 200
                or any(ord(c) < 33 or ord(c) == 127 for c in target)
            ):
                raise ValueError(
                    "release_target must be a branch or commit in the destination repository, without whitespace"
                )
        return cls(destination, mode, count, repo, tag, target, policy)
