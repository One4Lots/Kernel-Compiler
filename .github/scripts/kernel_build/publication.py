#!/usr/bin/env python3
"""GitHub release preflight, verified matrix bundles, and serialized publishing.

All GitHub operations use gh. No destination tag is ever moved or deleted.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from urllib.parse import quote

from changelog import clean_subject
from configuration import unique_object
from naming import package_relative_path
from presentation import build_datetime, local_time_labels
from publication_settings import PublicationSettings
from runtime import file_digest, output, run

MAX_ASSET_BYTES = 2_000_000_000


def file_sha256(path: Path) -> str:
    return file_digest(path, "sha256")


class GithubError(ValueError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class GithubClient:
    def api(
        self, endpoint: str, method: str = "GET", data: dict | None = None, optional: bool = False
    ):
        command = [
            "gh",
            "api",
            "--method",
            method,
            "-H",
            "Accept: application/vnd.github+json",
            endpoint,
        ]
        if data is not None:
            command += ["--input", "-"]
        try:
            result = subprocess.run(
                command,
                input=json.dumps(data) if data is not None else None,
                text=True,
                capture_output=True,
                check=False,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            raise GithubError(
                "GitHub API request timed out. A write may have completed; inspect the destination before retrying."
            ) from None
        if result.returncode:
            match = re.search(r"HTTP (\d{3})", result.stderr)
            status = int(match.group(1)) if match else None
            if optional and status == 404:
                return None
            # Preserve API failures without exposing raw credential-bearing stderr.
            raise GithubError(
                f"GitHub API {method} failed (HTTP {status or 'unknown'}). Check GH_RELEASE_TOKEN access, Contents: write, rate limits, and repository rules.",
                status,
            )
        try:
            response = json.loads(result.stdout)
            if not isinstance(response, dict):
                raise ValueError("Expected a GitHub object response")
            return response
        except ValueError:
            raise GithubError("GitHub returned malformed JSON; refusing to publish") from None

    def upload(self, repository: str, tag: str, assets: list[Path], clobber: bool) -> None:
        command = ["gh", "release", "upload", tag, "--repo", repository]
        if clobber:
            command.append("--clobber")
        command += [str(path.resolve()) for path in assets]
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, check=False, timeout=900
            )
        except subprocess.TimeoutExpired:
            raise GithubError(
                "GitHub asset upload timed out. Some assets may have uploaded; inspect the release before retrying."
            ) from None
        if result.returncode:
            raise GithubError(
                "GitHub asset upload failed. The release may remain a draft or contain partially updated assets; inspect it before retrying with duplicate_release=update."
            )


def existing_state(client: GithubClient, settings: PublicationSettings):
    base = f"repos/{settings.repository}"
    tag = quote(settings.tag, safe="")
    release = client.api(f"{base}/releases/tags/{tag}", optional=True)
    ref = client.api(f"{base}/git/ref/tags/{tag}", optional=True)
    return release, ref


def duplicate_action(settings: PublicationSettings, release, ref) -> str:
    if release is None and ref is None:
        return "create"
    if settings.duplicate_policy == "fail":
        raise ValueError(
            "The destination tag or release already exists. Choose a new kernel_rc, skip, or explicitly opt into update."
        )
    if settings.duplicate_policy == "skip":
        return "skip"
    if release and release.get("immutable"):
        raise ValueError(
            "The existing GitHub release is immutable and cannot be updated; use a new kernel_rc"
        )
    return "update"


def tag_commit(client: GithubClient, repository: str, ref: dict) -> str:
    obj = ref["object"]
    for _ in range(8):
        if obj["type"] == "commit" and re.fullmatch(r"[0-9a-f]{40}", obj["sha"]):
            return obj["sha"]
        if obj["type"] != "tag" or not re.fullmatch(r"[0-9a-f]{40}", obj["sha"]):
            break
        obj = client.api(f"repos/{repository}/git/tags/{obj['sha']}")["object"]
    raise ValueError(
        "The existing tag does not resolve to a commit (or has too many annotated-tag layers)"
    )


def check_tag_target(client: GithubClient, settings: PublicationSettings, ref, target: str) -> None:
    if ref is not None and tag_commit(client, settings.repository, ref) != target:
        raise ValueError(
            "The existing tag points to a different commit than release_target. Tags are never moved; choose a new tag or explicitly select its existing target."
        )


def preflight(client: GithubClient, settings: PublicationSettings) -> dict[str, str]:
    repository = client.api(f"repos/{settings.repository}")
    if repository.get("permissions", {}).get("push") is False:
        raise ValueError("The release credential has no write access to the destination repository")
    release, ref = existing_state(client, settings)
    if duplicate_action(settings, release, ref) == "skip":
        return {"release_skip": "true", "release_target_sha": ""}
    target = settings.target or repository["default_branch"]
    commit = client.api(f"repos/{settings.repository}/commits/{quote(target, safe='')}")
    sha = commit["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Destination release target did not resolve to a full commit SHA")
    check_tag_target(client, settings, ref, sha)
    return {"release_skip": "false", "release_target_sha": sha}


def bundle() -> None:
    meta = json.loads(Path("logs/package.json").read_text())
    archive = Path(meta["zip_path"])
    commits = json.loads(Path("logs/recent-commits.json").read_text())
    manifest = {
        "schema": 3,
        "matrix_id": os.environ["MATRIX_ID"],
        "branch": os.environ["BRANCH"],
        "defconfig": os.environ["DEFCONFIG"],
        "image_type": os.environ["IMAGE_TYPE"],
        "source_repository": os.environ["KERNEL_REPOSITORY"],
        "kernel_name": meta["kernel_name"],
        "device_name": meta["device_name"],
        "kernel_rc": meta["kernel_rc"],
        "zip_relative_path": meta["zip_relative_path"],
        "zip_name": archive.name,
        "zip_bytes": archive.stat().st_size,
        "sha256": file_sha256(archive),
        "md5": meta["md5"],
        "date": meta["date"],
        "time": meta["time"],
        "built_at": meta["built_at"],
        "duration_seconds": meta["duration_seconds"],
        "commits": [{"sha": entry["sha"], "subject": entry["subject"]} for entry in commits],
    }
    Path("logs/release-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def load_bundles(
    root: Path,
    expected: list[dict],
    settings: PublicationSettings,
    kernel_name: str,
    device_name: str,
    source_repository: str,
) -> list[dict]:
    if not isinstance(expected, list) or not 1 <= len(expected) <= 32:
        raise ValueError("Expected release matrix must contain 1–32 builds")
    wanted = {}
    for entry in expected:
        if (
            not isinstance(entry, dict)
            or set(entry) != {"id", "branch", "defconfig", "image_type"}
            or any(not isinstance(value, str) or not value for value in entry.values())
            or entry["id"] in wanted
        ):
            raise ValueError("Invalid or duplicate expected release matrix entry")
        wanted[entry["id"]] = entry
    if root.is_symlink():
        raise ValueError("Symlinked release download root is not allowed")
    found = {}
    # download-artifact v8 flattens a single pattern match into root, even with
    # merge-multiple: false; multiple matches retain their artifact directories.
    # Search only these two explicit layouts, not arbitrary recursive manifests.
    paths = [*root.glob("logs/release-manifest.json"), *root.glob("*/logs/release-manifest.json")]
    for path in sorted(paths):
        if (
            not path.is_file()
            or any(
                root.joinpath(*path.relative_to(root).parts[:index]).is_symlink()
                for index in range(len(path.relative_to(root).parts) + 1)
            )
            or not path.resolve().is_relative_to(root.resolve())
            or path.stat().st_size > 4_000_000
        ):
            raise ValueError("Unsafe or oversized release manifest")
        meta = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
        if not isinstance(meta, dict):
            raise ValueError("Release manifest must be a JSON object")
        identity = meta.get("matrix_id")
        if (
            not isinstance(identity, str)
            or identity not in wanted
            or identity in found
            or type(meta.get("schema")) is not int
            or meta["schema"] != 3
        ):
            raise ValueError("Unexpected, duplicate, or unsupported release manifest")
        entry = wanted[identity]
        if any(meta.get(key) != entry[key] for key in ("branch", "defconfig", "image_type")):
            raise ValueError("Release manifest does not match the validated build matrix")
        if (
            meta.get("kernel_name") != kernel_name
            or meta.get("device_name") != device_name
            or meta.get("source_repository") != source_repository
        ):
            raise ValueError("Release manifest build identity/source does not match this dispatch")
        build_datetime(meta)
        relative = package_relative_path(
            meta["branch"], kernel_name, device_name, settings.tag, meta.get("date")
        )
        if (
            meta.get("kernel_rc") != settings.tag
            or meta.get("zip_name") != relative.name
            or meta.get("zip_relative_path") != relative.as_posix()
        ):
            raise ValueError("Unsafe release asset filename/path or mismatched kernel RC")
        artifact_root = path.parent.parent / "artifacts"
        archive = artifact_root / relative.as_posix()
        if (
            not archive.is_file()
            or not archive.resolve().is_relative_to(root.resolve())
            or any(
                artifact_root.joinpath(*relative.parts[:index]).is_symlink()
                for index in range(0, len(relative.parts) + 1)
            )
        ):
            raise ValueError("Missing or unsafe release ZIP")
        size = archive.stat().st_size
        if not 0 < size <= MAX_ASSET_BYTES:
            raise ValueError("Release ZIP is empty or exceeds the safe 2 GB GitHub asset limit")
        if (
            type(meta.get("zip_bytes")) is not int
            or size != meta["zip_bytes"]
            or not isinstance(meta.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", meta["sha256"])
            or file_sha256(archive) != meta["sha256"]
        ):
            raise ValueError("Release ZIP integrity verification failed")
        commits = meta.get("commits")
        if not isinstance(commits, list) or not 1 <= len(commits) <= settings.count:
            raise ValueError("Release manifest contains an invalid commit count")
        for commit in commits:
            if (
                not isinstance(commit, dict)
                or not isinstance(commit.get("subject"), str)
                or not isinstance(commit.get("sha"), str)
                or not re.fullmatch(r"[0-9a-f]{40}", commit["sha"])
            ):
                raise ValueError("Release manifest contains invalid commit metadata")
        meta["archive"] = archive
        found[identity] = meta
    if set(found) != set(wanted):
        raise ValueError(
            "Not all matrix builds produced verified release bundles; refusing to publish a partial release. "
            f"Expected {len(wanted)}, verified {len(found)}; missing: {', '.join(sorted(set(wanted) - set(found)))}. "
            f"Searched {root}/logs/release-manifest.json and {root}/*/logs/release-manifest.json"
        )
    return [found[entry["id"]] for entry in expected]


def markdown(value: str) -> str:
    value = html.escape(" ".join(str(value).split()), quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", value)


def stage_assets(manifests: list[dict], destination: Path) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=False)
    counts = Counter(meta["zip_name"] for meta in manifests)
    assets = []
    checksums = []
    for meta in manifests:
        name = meta["zip_name"]
        if counts[name] > 1:
            name = f"{Path(name).stem[:220]}-{meta['matrix_id'].rsplit('-', 1)[-1]}.zip"
        meta["asset_name"] = name
        target = destination / name
        if target.exists():
            raise ValueError("Release asset names collide after matrix disambiguation")
        shutil.copy2(meta["archive"], target)
        assets.append(target)
        checksums.append(f"{meta['sha256']}  {name}")
    checksum_file = destination / "SHA256SUMS.txt"
    checksum_file.write_text("\n".join(checksums) + "\n")
    return [*assets, checksum_file]


def release_notes(
    manifests: list[dict], settings: PublicationSettings, kernel_name: str, target_sha: str
) -> str:
    lines = [
        f"# {markdown(kernel_name)}",
        "",
        "## Kernel builds",
        "",
        f"Requested recent commits per build: **{settings.count}** (newest first; all available if fewer).",
        "",
        f"Destination: {markdown(settings.repository)} · Tag: {markdown(settings.tag)}",
        "",
        f"[Destination tag target](https://github.com/{settings.repository}/commit/{target_sha}). Source commits below identify the actual kernel builds.",
        "",
    ]
    for meta in manifests:
        source = meta["source_repository"].removesuffix(".git")
        local_time, local_date = local_time_labels(meta)
        lines += [
            f"### {markdown(meta['branch'])}",
            "",
            f"- Device: {markdown(meta['device_name'])}",
            f"- Defconfig: {markdown(meta['defconfig'])}",
            f"- Image: {markdown(meta['image_type'])}",
            f"- ZIP: {markdown(meta['asset_name'])} ({meta['zip_bytes']:,} bytes)",
            f"- Branch-preserving artifact path: {markdown(meta['zip_relative_path'])}",
            f"- Built: {markdown(local_date)} · {markdown(local_time)}",
            f"- SHA-256: `{meta['sha256']}`",
            "",
            f"#### Recent commits ({len(meta['commits'])})",
            "",
        ]
        for record in meta["commits"]:
            lines.append(
                f"- {markdown(clean_subject(record['subject']))} ([commit]({source}/commit/{record['sha']}))"
            )
        lines.append("")
    text = "\n".join(lines)
    if len(text.encode("utf-8")) > 120_000:
        raise ValueError("Release notes are too large; reduce commit_count or matrix size")
    return text


def publish(
    client: GithubClient,
    settings: PublicationSettings,
    target_sha: str,
    kernel_name: str,
    notes: str,
    assets: list[Path],
) -> str | None:
    if not re.fullmatch(r"[0-9a-f]{40}", target_sha):
        raise ValueError("Missing validated release target SHA")
    base = f"repos/{settings.repository}"
    release, ref = existing_state(client, settings)
    action = duplicate_action(settings, release, ref)
    if action == "skip":
        return None
    check_tag_target(client, settings, ref, target_sha)
    if ref is None:
        try:
            client.api(
                f"{base}/git/refs", "POST", {"ref": f"refs/tags/{settings.tag}", "sha": target_sha}
            )
        except GithubError as error:
            if error.status not in (409, 422):
                raise
            # Resolve concurrent tag creation through the configured duplicate policy.
            release, ref = existing_state(client, settings)
            if ref is None:
                raise
            action = duplicate_action(settings, release, ref)
            if action == "skip":
                return None
            check_tag_target(client, settings, ref, target_sha)
    if release is None:
        try:
            release = client.api(
                f"{base}/releases",
                "POST",
                {
                    "tag_name": settings.tag,
                    "name": kernel_name,
                    "body": notes,
                    "draft": True,
                    "prerelease": False,
                    "make_latest": "false",
                },
            )
        except GithubError as error:
            if error.status != 422:
                raise
            release, ref = existing_state(client, settings)
            if release is None:
                raise
            action = duplicate_action(settings, release, ref)
            if action == "skip":
                return None
            check_tag_target(client, settings, ref, target_sha)
    release_id = release.get("id")
    if not isinstance(release_id, int) or isinstance(release_id, bool):
        raise ValueError("GitHub returned an invalid release ID")
    client.upload(
        settings.repository, settings.tag, assets, clobber=settings.duplicate_policy == "update"
    )
    # Publish only after all uploads succeed; updates preserve unrelated assets.
    result = client.api(
        f"{base}/releases/{release_id}",
        "PATCH",
        {
            "name": kernel_name,
            "body": notes,
            "draft": False,
            "make_latest": "false",
        },
    )
    return result["html_url"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["bundle", "preflight", "publish"])
    command = parser.parse_args().command
    if command == "bundle":
        bundle()
        return
    settings = PublicationSettings.from_inputs(
        json.loads(os.environ["INPUTS_JSON"]), os.environ.get("GITHUB_REPOSITORY", "")
    )
    if not settings.github:
        raise ValueError("GitHub publication was not selected")
    if not os.environ.get("GH_TOKEN", "").strip():
        raise ValueError(
            "GitHub publishing requires secrets.GH_RELEASE_TOKEN (or GH_PAT) with Contents: write on the destination repository"
        )
    client = GithubClient()
    if command == "preflight":
        values = preflight(client, settings)
        values["release_repository"] = settings.repository
        values["release_lock"] = hashlib.sha256(
            f"{settings.repository.lower()}:{settings.tag}".encode()
        ).hexdigest()
        for name, value in values.items():
            output(name, value)
        print(
            f"==> GitHub release preflight passed (duplicate policy: {settings.duplicate_policy}; skip: {values['release_skip']})"
        )
    elif command == "publish":
        inputs = json.loads(os.environ["INPUTS_JSON"])
        expected = json.loads(os.environ["MATRIX_JSON"])["include"]
        # Revalidate the handoff as Telegram does; never trust a partial job matrix.
        from prepare import make_matrix

        if expected != make_matrix(inputs)["include"]:
            raise ValueError("GitHub release matrix differs from validated settings")
        manifests = load_bundles(
            Path("release-downloads"),
            expected,
            settings,
            inputs["kernel_name"],
            inputs["device_name"],
            inputs["kernel_repository"],
        )
        assets = stage_assets(manifests, Path("release-assets"))
        notes = release_notes(
            manifests, settings, inputs["kernel_name"], os.environ["RELEASE_TARGET_SHA"]
        )
        Path("release-assets/release-notes.md").write_text(notes, encoding="utf-8")
        url = publish(
            client, settings, os.environ["RELEASE_TARGET_SHA"], inputs["kernel_name"], notes, assets
        )
        print(
            f"==> GitHub release {'published: ' + url if url else 'skipped because the tag/release already exists'}"
        )
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write(
                f"### GitHub release\n\n{url or 'Skipped: destination tag/release already exists.'}\n"
            )


if __name__ == "__main__":
    run(main)
