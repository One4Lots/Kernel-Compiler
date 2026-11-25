#!/usr/bin/env bash
# Repeatable, offline validation. Install tests/requirements.txt in a venv first.
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
export PYTHONDONTWRITEBYTECODE=1

printf '\n==> Python lint and formatting\n'
python3 -m ruff check --no-cache .github/scripts/kernel_build tests
python3 -m ruff format --no-cache --check .github/scripts/kernel_build tests

printf '\n==> Bash syntax and ShellCheck\n'
for file in .github/scripts/kernel_build/*.sh scripts/*.sh; do
  bash -n "$file"
done
shellcheck -x -P SCRIPTDIR .github/scripts/kernel_build/*.sh scripts/*.sh

printf '\n==> Offline unit and integration tests\n'
python3 -m unittest discover -s tests -v

printf '\n==> Whitespace validation\n'
git diff --check
# git diff alone skips new/untracked files, including a clean source extraction.
# Check complete source files too, including a clean source extraction.
python3 - <<'PY'
import pathlib
import subprocess

paths = subprocess.check_output(
    ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"]
).decode().split("\0")
for name in filter(None, paths):
    if not pathlib.Path(name).is_file():
        continue
    result = subprocess.run(
        ["git", "diff", "--no-index", "--check", "/dev/null", name],
        capture_output=True, text=True,
    )
    if result.returncode not in (0, 1) or result.stdout or result.stderr:
        raise SystemExit(result.stdout + result.stderr or f"Whitespace check failed: {name}")
PY
printf '\nAll offline checks passed. No real kernel compilation, live publication, or device flash was performed.\n'
