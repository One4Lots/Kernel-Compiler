#!/usr/bin/env python3
"""Populate AnyKernel3 properties without adding presentation or changing template UI."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from metadata import (
    TOOLCHAIN_NAMES,
    device_codename,
    display_name,
    github_maintainer,
    kernel_title,
    supported_android_versions,
    supported_devices,
)

BANNER_START = "# BEGIN KERNEL_BUILD_FLASH_BANNER"
BANNER_END = "# END KERNEL_BUILD_FLASH_BANNER"


@dataclass(frozen=True)
class InstallerConfig:
    kernel_name: str
    device_name: str
    release: str
    toolchain: str
    actor: str
    supported_versions: str
    device_codenames: str

    def __post_init__(self) -> None:
        display_name(self.kernel_name, "kernel_name")
        device_codename(self.device_name)
        supported_devices(self.device_codenames)
        supported_android_versions(self.supported_versions)
        github_maintainer(self.actor)
        if self.toolchain not in TOOLCHAIN_NAMES:
            raise ValueError("Unknown selected toolchain for installer metadata")
        # Property values must remain literal inside the template's quoted block.
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+~:-]{0,199}", self.release):
            raise ValueError(
                f"Invalid installer kernel version: {self.release!r}; expected literal-safe metadata"
            )

    def properties(self) -> dict[str, str]:
        result = {
            "kernel.name": kernel_title(self.kernel_name),
            "kernel.version": self.release,
            "kernel.toolchain": TOOLCHAIN_NAMES[self.toolchain],
            "kernel.maintainer": github_maintainer(self.actor),
        }
        devices = supported_devices(self.device_codenames)
        result.update({f"device.name{index}": name for index, name in enumerate(devices, 1)})
        result["supported.versions"] = self.supported_versions
        result["do.devicecheck"] = "1"
        return result


def _set_properties(script: str, config: InstallerConfig) -> str:
    # Restrict edits to the template's quoted properties block.
    block = re.compile(
        r"^(?P<head>[ \t]*properties[ \t]*\([ \t]*\)[ \t]*\{[ \t]*)(?P<quote>['\"])\n"
        r"(?P<body>.*?)(?P<tail>^[ \t]*(?P=quote)[ \t]*;?[ \t]*\})",
        re.MULTILINE | re.DOTALL,
    )
    matches = list(block.finditer(script))
    if len(matches) != 1:
        raise ValueError("AnyKernel3 must contain one recognizable quoted properties() block")
    match = matches[0]
    values = config.properties()
    body = match.group("body")
    # Match the current template: name, optional blank legacy slot, version,
    # compiler, maintainer, ordered device list, Android compatibility, controls.
    entries = [re.match(r"^[ \t]*([A-Za-z0-9_.]+)=", line) for line in body.splitlines()]
    keys = [entry.group(1) if entry else None for entry in entries]
    seen = set()
    for key in keys:
        if (
            key in values
            or key == "kernel.string"
            or (key and re.fullmatch(r"device\.name[0-9]+", key))
        ):
            if key in seen:
                raise ValueError(f"AnyKernel3 template has duplicate {key} properties")
            seen.add(key)
    metadata = []
    for key, value in values.items():
        if key == "do.devicecheck":
            continue
        metadata.append(f"{key}={value}")
        if key == "kernel.name" and "kernel.string" in seen:
            # Upstream retains this slot for optional module metadata, not its UI.
            # Clear old workflow descriptions, but never add a removed property.
            metadata.append("kernel.string=")
    emitted = False
    lines = []
    for line, key in zip(body.splitlines(), keys, strict=True):
        if key == "do.devicecheck":
            lines.append("do.devicecheck=1")
        elif (
            key in values
            or key == "kernel.string"
            or (key and re.fullmatch(r"device\.name[0-9]+", key))
        ):
            if not emitted:
                lines.extend(metadata)
                emitted = True
        else:
            lines.append(line)
    if not emitted:
        lines = metadata + lines
    if "do.devicecheck" not in seen:
        lines.append("do.devicecheck=1")
    updated = (
        match.group("head")
        + match.group("quote")
        + "\n"
        + "\n".join(lines)
        + "\n"
        + match.group("tail")
    )
    result = script[: match.start()] + updated + script[match.end() :]
    for key in values:
        if len(re.findall(rf"(?m)^[ \t]*{re.escape(key)}=", result)) != 1:
            raise ValueError(
                f"AnyKernel3 has ambiguous {key} properties outside its properties block"
            )
    if len(re.findall(r"(?m)^[ \t]*kernel\.string=", result)) != (
        1 if "kernel.string" in seen else 0
    ):
        raise ValueError(
            "AnyKernel3 has ambiguous kernel.string properties outside its properties block"
        )
    for device in re.findall(r"(?m)^[ \t]*(device\.name[0-9]+)=", result):
        if device not in values:
            raise ValueError(
                "AnyKernel3 contains a stale device alias outside its properties block"
            )
    return result


def _remove_legacy_banner(script: str) -> str:
    """Remove only a complete workflow-owned block left by older packaging revisions."""
    if BANNER_START not in script and BANNER_END not in script:
        return script
    managed = re.compile(
        rf"^{re.escape(BANNER_START)}\n.*?^{re.escape(BANNER_END)}\n?", re.MULTILINE | re.DOTALL
    )
    cleaned, count = managed.subn("", script)
    if count != 1 or BANNER_START in cleaned or BANNER_END in cleaned:
        raise ValueError("AnyKernel3 has a malformed or duplicate legacy managed banner")
    return cleaned


def configure_installer(path: Path, config: InstallerConfig) -> None:
    """Edit properties and remove legacy generated UI; preserve all template-owned UI."""
    if path.is_symlink() or not path.is_file() or not path.stat().st_size:
        raise ValueError("AnyKernel3 anykernel.sh must be a nonempty regular file, not a symlink")
    script = path.read_text(encoding="utf-8")
    script = _set_properties(script, config)
    script = _remove_legacy_banner(script)
    path.write_text(script, encoding="utf-8")
