#!/usr/bin/env bash
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
: "${TOOLCHAIN_DIR:?}" "${TOOLCHAIN_BIN_REL:?}" "${TOOLCHAIN_URL:?}" "${TOOLCHAIN_HASH:?}"
bin_dir="$TOOLCHAIN_DIR/$TOOLCHAIN_BIN_REL"
check_compiler() {
  local directory=$1 tool
  for tool in clang clang++ ld.lld llvm-ar llvm-nm llvm-strip llvm-objcopy llvm-objdump llvm-readelf llvm-size; do
    [[ -f "$directory/$tool" && -x "$directory/$tool" ]] || die "Cached/extracted toolchain is incomplete: $directory/$tool. Bump the toolchain cache version to evict a corrupt cache."
  done
}
if [[ ! -d "$TOOLCHAIN_DIR" ]]; then
  log 'Toolchain cache miss: downloading and extracting release'
  archive=$(mktemp --suffix=.tar.zst)
  staging=$(mktemp -d "${RUNNER_TEMP:?}/kernel-build-toolchain.XXXXXX")
  trap 'rm -f "$archive"; rm -rf "$staging"' EXIT
  # Detect release metadata changes since preflight.
  python3 "$(dirname "${BASH_SOURCE[0]}")/prepare.py" verify-toolchain
  curl --fail --location --silent --show-error --retry 4 --retry-all-errors \
    --connect-timeout 20 --max-time 900 --proto '=https' --proto-redir '=https' \
    --output "$archive" "$TOOLCHAIN_URL"
  python3 "$(dirname "${BASH_SOURCE[0]}")/prepare.py" verify-toolchain
  # Filter actual tar metadata, including links and special files, while extracting.
  # pipefail prevents installing partial output when decompression fails.
  zstd --decompress --stdout -- "$archive" | \
    python3 "$(dirname "${BASH_SOURCE[0]}")/archive.py" "$staging"
  # Do not poison the destination with an incomplete release on a failed attempt.
  check_compiler "$staging/$TOOLCHAIN_BIN_REL"
  mkdir -p "$(dirname "$TOOLCHAIN_DIR")"
  mv "$staging" "$TOOLCHAIN_DIR"
else
  log 'Toolchain cache hit: using existing extraction without downloading'
  check_compiler "$bin_dir"
fi
# Subsequent steps resolve the selected compiler before distro host tools.
printf '/usr/bin\n%s\n' "$bin_dir" >> "$GITHUB_PATH"
export PATH="$bin_dir:/usr/bin:$PATH"
log 'Selected compiler and linker'
clang --version
ld.lld --version
