#!/usr/bin/env bash
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
: "${KERNEL_REPOSITORY:?}" "${BRANCH:?}" "${REPO_VISIBILITY:?}"
HISTORY_DEPTH="${HISTORY_DEPTH:-1}"
[[ "$HISTORY_DEPTH" =~ ^([1-9]|[1-4][0-9]|50)$ ]] || die 'HISTORY_DEPTH must be an integer from 1 to 50'
export HISTORY_DEPTH
[[ ! -e kernel ]] || die 'Refusing to overwrite an existing kernel checkout'
clean_url="${KERNEL_REPOSITORY%.git}.git"
clone_url="$clean_url"
if [[ "$REPO_VISIBILITY" == private ]]; then
  [[ -n "${GH_PAT:-}" ]] || die 'Private source requires the GH_PAT repository secret with read access to the kernel repository.'
  clone_url="https://x-access-token:${GH_PAT}@${clean_url#https://}"
elif [[ "$REPO_VISIBILITY" != public ]]; then
  die 'repo_visibility must be private or public'
fi
scrub_remote() {
  if [[ -d kernel/.git ]]; then
    git -C kernel remote set-url origin "$clean_url"
  fi
}
trap scrub_remote EXIT
log "Cloning selected kernel branch ($REPO_VISIBILITY mode; non-interactive)"
git -c credential.helper= clone --depth=1 --single-branch --branch "$BRANCH" -- "$clone_url" kernel
# Remove credentials from origin before subsequent build steps.
scrub_remote
if (( HISTORY_DEPTH > 1 )); then
  log "Deepening source history for $HISTORY_DEPTH recent commits"
  # Keep authenticated fetch URLs out of origin and FETCH_HEAD.
  git -C kernel -c credential.helper= fetch --no-tags --no-write-fetch-head --deepen="$((HISTORY_DEPTH - 1))" -- "$clone_url" "$BRANCH"
fi
unset clone_url GH_PAT
python3 "$(dirname "${BASH_SOURCE[0]}")/changelog.py"
require_file logs/commit-message.txt
require_file logs/recent-commits.json
