# Operations runbook

## 1. Deploy a complete revision

Replace workflows, scripts, and profiles together. Keep the two
entry forms and one shared pipeline from the same revision. Separate mobile and
desktop forms do not require duplicated compilation/publication implementations.

For this redesign, replace the complete script directory. Remove obsolete banner.py
and fonts/ files rather than retaining a mixed old/new source tree. Font-renderer
dependencies are no longer needed. Manifest schema 3 includes built_at with an
explicit timezone; old schemas are rejected rather than guessing AM/PM. Old
artifacts can still be recovered manually, but do not combine them with new runs.

The entry workflows must exist on the repository's default branch for GitHub's
Run workflow control to appear. Select the intended workflow revision when starting
a run. Re-running an earlier run does not substitute newly committed scripts.

Review source visibility, source URL/branch, ARM64 defconfig, image layout, trusted
AnyKernel3 template, supported Android versions, and explicit device codenames.
Neither a successful build nor a plausible filename establishes device safety.

## 2. Configure only required secrets

| Selection | Telegram secrets | GitHub write credential |
| --- | --- | --- |
| artifacts only | None | None |
| personal only | TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID | None |
| personal+channel | Bot token + personal ID + TELEGRAM_CHANNEL_ID | None |
| personal+github | Bot token + personal ID | GH_RELEASE_TOKEN or suitable GH_PAT |
| channel+github | Bot token + channel ID | GH_RELEASE_TOKEN or suitable GH_PAT |
| personal+channel+github | Bot token + both IDs | GH_RELEASE_TOKEN or suitable GH_PAT |

Independently, private source cloning requires GH_PAT with source Contents read.
Prefer a separate release token with destination Contents write. Never put tokens
in source URLs, profiles, dispatch fields, screenshots, or chat.

Personal ID must be your positive numeric chat ID. Open the bot privately and send
/start first. Channel ID must be its negative numeric ID or public @username; the
bot must have channel posting rights. The old TELEGRAM_RELEASE_CHAT_ID is not read.
IDs do not fall back to each other. Local format checks do not prove live access.

## 3. Stage rollout

1. Run Workflow validation on GitHub from the deployed revision. Confirm both the
   native Ubuntu system-Python tests and development checks succeed.
2. Run one actual kernel build in **artifacts only**. Review compiler/linker logs,
   build identity, numeric installer version, payload contents, device list, and
   original AnyKernel3 presentation. No flashable ZIP is retained in this mode.
3. Run **personal only** to verify a private delivery of the intended source. A
   retained publisher bundle is created in this mode if manual recovery is needed.
4. Test public delivery using a dedicated test channel/release repository before
   a real release. Use unique RC tags or deliberately choose a duplicate policy.
5. Verify every selected destination independently. Record the workflow revision,
   source commit, compiler selection, dispatch inputs, and package checksums.
6. Obtain device-specific recovery/partition/compatibility review before any flash.

To validate live routing, check all six modes in their documented order using test
destinations. Artifacts only must contact neither publisher; personal only must not
post publicly; channel+github must not send a private message. Verify multi-build
runs deliver the complete set and do not publish after a missing/corrupt member.
Do not deliberately break production releases to perform these acceptance checks.

Local tests cover these routing and handoff behaviors with fixtures; the steps
above establish whether actual runners, tokens, services, and sources also work.

## 4. Understand stored outputs

- **Main Actions artifact:** raw selected images and individual cust-atoll DTBs;
  no flashable ZIP. Retained 14 days.
- **Diagnostics:** logs and package/installer metadata. Retained 7 days.
- **release-bundle-…:** internal publisher transport, created only when a publishing
  destination is selected. Contains ZIPs under artifacts/ and required metadata
  under logs/. Retained 7 days, separately from raw artifacts.
- **GitHub release:** published ZIPs and SHA256SUMS.txt; independent lifetime.

These are retention requests subject to repository/organization limits and manual
deletion. Artifacts and logs inherit repository access, not source-repository
privacy. Public channel/release delivery intentionally discloses selected binaries
and source history. Treat publisher bundles as downloadable release material.

## 5. Successful download, then missing-bundle error

Download Artifact v8 uses two shapes:

```text
# One matching artifact, extracted flat
release-downloads/logs/release-manifest.json
release-downloads/artifacts/<package>.zip

# Multiple matches, kept separate
release-downloads/<artifact-name>/logs/release-manifest.json
release-downloads/<artifact-name>/artifacts/<package>.zip
```

Source branch slashes can add subdirectories beneath artifacts/. The current
verifier accepts both download shapes without merging unrelated build contents.

The earlier loader only accepted the second shape. For that known bug, update
`.github/scripts/kernel_build/publication.py`, or preferably the complete source
revision. Do not remove the matrix/manifest/checksum guard. New errors include
expected/verified counts and missing IDs to make genuine incomplete runs clearer.

Other possible causes include a failed build/package/manifest/upload job, expired
artifacts, a mixed source revision, or an attempt-specific artifact mismatch. Review
the first failed build step and confirm every expected release-bundle was uploaded.
Do not force publication of a surviving subset.

## 6. Recover an existing compiled ZIP without rebuilding

1. Open the completed Actions run summary.
2. Download its **release-bundle-…** artifact before expiry.
3. Extract the outer transport archive.
4. Open **artifacts/**, following any source-branch subdirectory.
5. Use the kernel ZIP inside; preserve logs/release-manifest.json for its SHA-256
   and build identity. Check the hash before manual distribution.

**Do not flash the outer Actions artifact archive.** This procedure retrieves the
existing ZIP manually; it does not retry the Telegram job. The main raw artifact
alone is not an AnyKernel3 package, and artifacts-only does not retain the ZIP.

## 7. Retry and partial-publication rules

- A script fix needs a **fresh dispatch from the updated revision**. Re-run failed
  jobs on an old run still uses that run's old source revision.
- Bundles are scoped to the same run and attempt. A partial rerun must not combine
  old and new build outputs. Prefer a fresh dispatch or Re-run all jobs as appropriate.
- Telegram sends are not deduplicated. If a request timed out, inspect the actual
  chat/channel before retrying; it might already have arrived.
- A new GitHub release stays draft until uploads finish. Failed updates can leave
  partially replaced assets. Inspect before using update, and never assume the
  old release remains unchanged after a failed write.
- fail is the safe duplicate default; skip preserves existing objects; update
  requires the existing tag to match the validated target and never moves tags.
- GitHub and Telegram jobs are independent. Success in one does not prove success
  in another, and there is no cross-service rollback or exactly-once guarantee.

## 8. Before upgrading dependencies

Review full action commit pins and their input/runtime contracts together. Keep
matrix/preflight/download-layout regression checks. Refresh actual hosted runner
validation after Ubuntu, Python, or action upgrades. Keep target compiler
upgrades deliberate and review kernel compatibility separately from host tooling.

Run bash scripts/check.sh before deployment. Consult VALIDATION.md for exact local
coverage and remaining limitations. Do not replace live acceptance evidence with
an assertion that the workflow is perfect or guaranteed to boot every source tree.

## Compiler cache safety upgrade

Deploy `archive.py`, `toolchain.sh`, and the shared workflow together. Compiler
cache keys now use v3, causing one fresh download per runner/compiler fingerprint;
no v2 compiler fallback is used. APT and object-cache behavior is unchanged.

Unsafe or unusual tar metadata fails before cache installation. Inspect the
trusted compiler release if extraction rejects links, special files, or duplicate
members; do not disable confinement to silence an error. Compiler completeness is
checked before moving staging into the cache, so a failed fresh download can be
retried without leaving an incomplete destination. Existing corrupt v3 caches
still fail clearly; delete that specific cache or deliberately bump its namespace.
The extraction hardening is not compiler-content authentication or a sandbox.
