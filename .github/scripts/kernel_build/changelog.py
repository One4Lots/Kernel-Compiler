#!/usr/bin/env python3
"""Capture structured source history without parsing or executing commit text."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from publication_settings import commit_count


def collect_history(repository: Path, count: int) -> list[dict[str, str]]:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "log",
            f"-{count}",
            "--format=%H%x00%s%x00%B%x00",
        ],
        capture_output=True,
        check=True,
        encoding="utf-8",
        errors="replace",
    )
    fields = result.stdout.split("\0")
    records = []
    for index in range(0, len(fields) - 1, 3):
        sha, subject, message = fields[index : index + 3]
        sha = sha.strip()
        if not re.fullmatch(r"[0-9a-f]{40,64}", sha):
            raise ValueError("Malformed source commit history")
        records.append({"sha": sha, "subject": subject.strip(), "message": message.strip()})
    if not records:
        raise ValueError("Kernel source contains no commits")
    return records


def recent_commit_text(records: list[dict[str, str]]) -> str:
    return "\n".join(
        f"{index}. {clean_subject(record['subject'])}" for index, record in enumerate(records, 1)
    )


def clean_subject(subject: str) -> str:
    return (
        " ".join("".join(c for c in subject if c.isprintable() or c.isspace()).split())
        or "(no commit subject)"
    )


def main() -> None:
    count = commit_count(os.environ.get("HISTORY_DEPTH", "1"))
    records = collect_history(Path("kernel"), count)
    logs = Path("logs")
    logs.mkdir(exist_ok=True)
    (logs / "recent-commits.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (logs / "commit-message.txt").write_text(records[0]["message"] + "\n", encoding="utf-8")
    if len(records) < count:
        print(
            f"::warning::Requested {count} commits but this source branch has only {len(records)} available; using all available commits."
        )
    print(f"==> Captured {len(records)} source commit(s)")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        # Do not print source-controlled commit text as workflow commands.
        print(
            f"::error::Cannot collect source history ({type(error).__name__}); see checkout logs.",
            file=sys.stderr,
        )
        sys.exit(1)
