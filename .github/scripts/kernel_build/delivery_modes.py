#!/usr/bin/env python3
"""The single routing contract shared by dispatch, validation, and publishers."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class DeliveryPlan:
    personal: bool = False
    channel: bool = False
    github: bool = False

    @property
    def bundles(self) -> bool:
        return self.personal or self.channel or self.github

    def as_dict(self) -> dict[str, bool]:
        return {
            "personal": self.personal,
            "channel": self.channel,
            "github": self.github,
            "bundles": self.bundles,
        }


MODES = MappingProxyType(
    {
        "artifacts only": DeliveryPlan(),
        "personal only": DeliveryPlan(personal=True),
        "personal+channel": DeliveryPlan(personal=True, channel=True),
        "personal+github": DeliveryPlan(personal=True, github=True),
        "channel+github": DeliveryPlan(channel=True, github=True),
        "personal+channel+github": DeliveryPlan(personal=True, channel=True, github=True),
    }
)


def delivery_plan(value: object) -> DeliveryPlan:
    if not isinstance(value, str) or value not in MODES:
        raise ValueError(f"publish_to must be one of: {', '.join(MODES)}")
    return MODES[value]
