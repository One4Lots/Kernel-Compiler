#!/usr/bin/env python3
"""Collect exact build outputs and package them using AnyKernel3's payload names."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from installer import InstallerConfig, configure_installer
from metadata import device_codename, display_name, numeric_kernel_version
from naming import kernel_rc, package_relative_path
from prepare import IMAGE_TYPES, validate_source
from runtime import file_digest, run


def require_file(path: Path) -> Path:
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"Missing or empty required output: {path}")
    return path


def concatenate(paths: list[Path], target: Path) -> Path:
    if not paths:
        raise ValueError(
            "No compiled DTBs found; refusing to package an image without device trees"
        )
    with target.open("wb") as output:
        for path in paths:
            with require_file(path).open("rb") as source:
                shutil.copyfileobj(source, output)
    return require_file(target)


def collect_outputs(boot: Path, artifacts: Path, image_type: str) -> dict[str, str]:
    """Map collected artifact filenames to AnyKernel3 payload filenames."""
    if image_type not in IMAGE_TYPES:
        raise ValueError("Unsupported image type")
    artifacts.mkdir(parents=True, exist_ok=True)
    payload = {}

    def copy(source: Path, name: str, anykernel_name: str | None = None) -> None:
        require_file(source)
        if name in payload:
            raise ValueError(f"Duplicate output filename: {name}")
        shutil.copy2(source, artifacts / name)
        payload[name] = anykernel_name or name

    def dtb_image() -> Path:
        # Prefer a native aggregate; otherwise concatenate all compiled DTBs.
        for candidate in (boot / "dtb.img", boot.parents[2] / "dtb.img"):
            if candidate.is_file() and candidate.stat().st_size:
                return candidate
        trees = sorted((boot / "dts").rglob("*.dtb"))
        return concatenate(trees, boot / "dtb.img")

    if image_type == "Image.gz.dtb":
        candidates = [boot / "Image.gz.dtb", boot / "Image.gz-dtb"]
        combined = next(
            (path for path in candidates if path.is_file() and path.stat().st_size), None
        )
        if combined is None:
            combined = concatenate(
                [require_file(boot / "Image.gz"), dtb_image()], boot / "Image.gz.dtb"
            )
        # AnyKernel3 expects Image.gz-dtb; raw artifacts retain Image.gz.dtb.
        copy(combined, "Image.gz.dtb", "Image.gz-dtb")
    else:
        image = "Image.lz4" if image_type.startswith("Image.lz4") else "Image.gz"
        copy(boot / image, image)
        copy(boot / "dtbo.img", "dtbo.img", "dtbo")
        copy(dtb_image(), "dtb.img", "dtb")
    for tree in sorted(boot.rglob("cust-atoll*.dtb")):
        require_file(tree)
        target = artifacts / tree.name
        if target.exists():
            raise ValueError(f"Duplicate output filename: {tree.name}")
        shutil.copy2(tree, target)
    return payload


def main() -> None:
    root = Path.cwd()
    kernel_name = display_name(os.environ["KERNEL_NAME"], "kernel_name")
    device_name = device_codename(os.environ["DEVICE_NAME"])
    rc = kernel_rc(os.environ["KERNEL_RC"])
    template_url = validate_source(os.environ["ANYKERNEL_REPOSITORY"], "anykernel_repository")
    full_release = (
        require_file(root / "kernel/out/include/config/kernel.release")
        .read_text(encoding="utf-8")
        .strip()
    )
    release = numeric_kernel_version(full_release)
    print(f"==> Installer kernel version: {release}", flush=True)
    installer_config = InstallerConfig(
        kernel_name=kernel_name,
        device_name=device_name,
        release=release,
        toolchain=os.environ["TOOLCHAIN"],
        actor=os.environ["BUILD_ACTOR"],
        supported_versions=os.environ["SUPPORTED_VERSIONS"],
        device_codenames=os.environ["DEVICE_CODENAMES"],
    )
    artifacts = root / "artifacts"
    if artifacts.exists():
        raise ValueError("Refusing to mix new outputs with an existing artifacts directory")
    payload = collect_outputs(
        root / "kernel/out/arch/arm64/boot", artifacts, os.environ["IMAGE_TYPE"]
    )
    stage = root / "anykernel"
    if stage.exists():
        raise ValueError("Refusing to overwrite an existing AnyKernel3 checkout")
    print("==> Cloning AnyKernel3 packaging template", flush=True)
    subprocess.run(
        [
            "git",
            "-c",
            "credential.helper=",
            "clone",
            "--depth=1",
            "--",
            template_url,
            str(stage),
        ],
        check=True,
    )
    configure_installer(stage / "anykernel.sh", installer_config)
    (root / "logs/installer-properties.json").write_text(
        json.dumps(installer_config.properties(), indent=2) + "\n",
        encoding="utf-8",
    )
    # Remove template payloads before adding this build's outputs.
    stale = {
        "Image",
        "Image.gz",
        "Image.gz.dtb",
        "Image.gz-dtb",
        "Image.lz4",
        "Image.lz4-dtb",
        "Image-dtb",
        "zImage",
        "zImage-dtb",
        "dtb",
        "dtbo",
        "dtb.img",
        "dtbo.img",
    }
    for path in stage.iterdir():
        if path.name in stale or path.suffix == ".dtb":
            if not path.is_file() and not path.is_symlink():
                raise ValueError(f"Unexpected template payload directory: {path.name}")
            path.unlink()
    for filename, installer_name in payload.items():
        target = stage / installer_name
        if target.exists() or target.is_symlink():
            raise ValueError(f"Template conflicts with generated payload: {installer_name}")
        shutil.copy2(artifacts / filename, target)
    now = datetime.now(ZoneInfo("Asia/Dhaka"))
    relative_path = package_relative_path(
        os.environ["BRANCH"], kernel_name, device_name, rc, now.strftime("%d%m%Y")
    )
    name = relative_path.as_posix()
    archive = artifacts / name
    archive.parent.mkdir(parents=True, exist_ok=True)
    print(f"==> Creating {name}", flush=True)
    subprocess.run(
        [
            "zip",
            "-9",
            "-r",
            str(archive),
            ".",
            "-x",
            "README.md",
            "*/README.md",
            "LICENSE",
            "*/LICENSE",
            "LICENCE",
            "*/LICENCE",
            ".git*",
            "*/.git*",
            "*.dtb",
        ],
        cwd=stage,
        check=True,
    )
    subprocess.run(["unzip", "-tq", str(archive)], check=True)
    metadata = {
        "kernel_name": kernel_name,
        "device_name": device_name,
        "kernel_rc": rc,
        "installer": installer_config.properties(),
        "kernel_release": full_release,
        "zip_path": str(archive),
        "zip_name": archive.name,
        "zip_relative_path": name,
        "zip_bytes": archive.stat().st_size,
        "sha256": file_digest(archive, "sha256"),
        "md5": file_digest(archive, "md5"),
        "date": now.strftime("%d%m%Y"),
        "time": now.strftime("%I:%M %p"),
        "built_at": now.isoformat(timespec="seconds"),
        "duration_seconds": int((root / "logs/build-duration.txt").read_text().strip()),
        "payload": payload,
    }
    (root / "logs/package.json").write_text(json.dumps(metadata, indent=2) + "\n")
    # SHA256 is used for integrity checks; Telegram displays a separate MD5 hash.
    print(f"==> ZIP size: {metadata['zip_bytes']:,} bytes | SHA256: {metadata['sha256']}")
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
        # Code fences prevent filenames from being interpreted as Markdown.
        summary.write(
            f"### Kernel package\n\n```text\n{kernel_name} · {device_name}\n{name}\n"
            f"Size: {metadata['zip_bytes']:,} bytes\nSHA256: {metadata['sha256']}\n"
            f"Dhaka: {metadata['date']} {metadata['time']}\n```\n"
        )


if __name__ == "__main__":
    run(main)
