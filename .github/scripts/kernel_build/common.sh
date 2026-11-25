#!/usr/bin/env bash
# Shared shell settings. Keep xtrace disabled when handling credentials.
set -Eeuo pipefail
trap 'printf "::error file=%s,line=%s::Command failed (exit %s). See the step log above.\n" "${BASH_SOURCE[0]}" "$LINENO" "$?" >&2' ERR
export GIT_TERMINAL_PROMPT=0
log() { printf '\n==> %s\n' "$*"; }
die() { printf '::error::%s\n' "$*" >&2; exit 1; }
require_file() { [[ -s "$1" ]] || die "Missing or empty required output: $1"; }
