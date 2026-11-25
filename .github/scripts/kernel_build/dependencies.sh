#!/usr/bin/env bash
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
: "${APT_CACHE_DIR:?}"
mkdir -p "$APT_CACHE_DIR/archives/partial" "$APT_CACHE_DIR/lists/partial"
apt_options=(-o "Dir::Cache::archives=$APT_CACHE_DIR/archives"
             -o "Dir::State::lists=$APT_CACHE_DIR/lists"
             -o APT::Sandbox::User=root -o Acquire::Retries=4
             -o APT::Update::Error-Mode=any -o DPkg::Lock::Timeout=120
             -o APT::Keep-Downloaded-Packages=true)
apt_run() { sudo env DEBIAN_FRONTEND=noninteractive apt-get "${apt_options[@]}" "$@"; }
# Refresh cached APT indexes at least weekly.
now=$(date +%s)
updated=0
if [[ -f "$APT_CACHE_DIR/updated-at" ]]; then
  read -r updated < "$APT_CACHE_DIR/updated-at" || updated=0
fi
# Normalize bounded decimal timestamps; cached leading zeros are not shell octal.
if [[ "$updated" =~ ^[0-9]{1,12}$ ]]; then
  updated=$((10#$updated))
else
  updated=0
fi
if (( now - updated >= 604800 || updated > now )); then
  log 'Refreshing APT indexes'
  apt_run update
  printf '%s\n' "$now" > "$APT_CACHE_DIR/updated-at"
else
  log 'Reusing fresh cached APT indexes'
fi
mapfile -t packages < "$(dirname "${BASH_SOURCE[0]}")/packages.txt"
log 'Installing dependencies using cached downloads where available'
# Refresh once if cached indexes reference unavailable mirror packages.
if ! apt_run install --yes --no-install-recommends "${packages[@]}"; then
  log 'APT install failed; refreshing indexes before one retry'
  apt_run update
  date +%s > "$APT_CACHE_DIR/updated-at"
  apt_run install --yes --no-install-recommends "${packages[@]}"
fi
# Cache uploads run as the unprivileged runner user.
sudo chown -R "$(id -u):$(id -g)" "$APT_CACHE_DIR"
ccache --max-size="${CCACHE_MAXSIZE:-2G}"
log 'Dependencies ready'
