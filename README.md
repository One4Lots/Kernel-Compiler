# ⚡ ARM64 Kernel Builder

A profile-driven GitHub Actions pipeline for Android ARM64 kernel builds,
AnyKernel3 packaging, private Telegram delivery, channel publication, and GitHub
Releases. Mobile and desktop entry forms share one implementation and one delivery
routing contract.

**Safe default:** `artifacts only`. A build never posts to Telegram or creates a
GitHub Release merely because credentials are present.

> Use trusted sources and a device-compatible installer. This workflow does not
> port kernels, establish bootability, or certify flashing safety. Review the
> defconfig, compatibility list, partition handling, and actual build before use.

## 🚀 First run

1. Copy the complete source into your workflow repository. Include **all four
   workflow files**, `.github/scripts/kernel_build/`, and
   `configs/`. Copying only a YAML file is insufficient.
2. Make the entry workflows available on your repository's default branch so
   GitHub displays **Run workflow**.
3. Review [`configs/miatoll.json`](configs/miatoll.json), especially the source URL,
   visibility, source branch, device list, and defconfig.
4. Add only the secrets required by your chosen source and delivery mode.
5. Open **Actions → 📱 Mobile Kernel Build** or **🖥️ Desktop Kernel Build → Run workflow**.
6. Start with `artifacts only`. Review compilation and packaging before choosing
   a mode that distributes the resulting ZIP.

After upgrading, start a **new dispatch from the updated revision**. Re-running
an old failed run uses its original source, not the replacement files.

## 📱 Mobile and desktop

| View | Entry file | Form |
| --- | --- | --- |
| 📱 Mobile Kernel Build | `.github/workflows/kernel-build.yml` | Five common controls; advanced values come from the profile. |
| 🖥️ Desktop Kernel Build | `.github/workflows/kernel-desktop.yml` | The same controls plus sixteen optional overrides. |
| Shared pipeline | `.github/workflows/kernel-pipeline.yml` | Reusable build/publish implementation, not a third manual form. |
| Workflow validation | `.github/workflows/validate.yml` | Source and system-Python checks, not kernel compilation. |

**Why one pipeline?** Mobile and desktop need different-sized forms, not different
compilers or build behavior. Keeping one reusable implementation prevents fixes,
cache keys, permissions, and delivery checks drifting between two copies. The
mobile form stays compact without sacrificing desktop overrides.

Both entry points use the reusable workflow from the **same revision**. Select
either form on any device. There is no browser detection or custom website.
GitHub controls the form's CSS and scrolling; YAML cannot fix a browser layout
bug. The five-field mobile form avoids the previous long settings menu.

### Common controls

| Input | Default | Meaning |
| --- | --- | --- |
| `config_file` | `configs/miatoll.json` | Checked-in profile in the selected workflow revision. |
| `kernel_rc` | `rc1` | ZIP version component and exact GitHub release tag. |
| `build_mode` | `single` | Build one source branch or the profile's branch matrix. |
| `toolchain` | `android-clang` | Android Clang or Neutron Clang, shared by every matrix entry. |
| `publish_to` | `artifacts only` | Exactly one of the six delivery modes below. |

### Six explicit delivery modes

**`personal only` always means your personal chat. `channel` means the public release
destination configured separately.** Main raw Actions artifacts are retained in
every mode.

| Mode | Personal ZIP | Channel ZIP | GitHub Release |
| --- | :---: | :---: | :---: |
| `artifacts only` | — | — | — |
| `personal only` | Yes | — | — |
| `personal+channel` | Yes | Yes | — |
| `personal+github` | Yes | — | Yes |
| `channel+github` | — | Yes | Yes |
| `personal+channel+github` | Yes | Yes | Yes |

For a private test build you want to download, select **`personal only`**. For cache
warming with no messages, select **`artifacts only`**. There is no separate channel
checkbox, no implicit channel opt-in, and no ambiguous `both` mode.

The mode registry in `delivery_modes.py` drives dispatch validation, required
secrets, transfer bundles, and publisher job conditions. Each publisher verifies
its permission again before sending. Previous `telegram`/`telegram+…` labels,
`artifacts-only`, `github`, `both`, and the old `telegram_release` checkbox are
rejected rather than guessed or silently remapped. Use the exact strings above
in API/CLI dispatches, including spaces in `artifacts only` and `personal only`.
The option order is identical in both forms and checked by regression tests.

Common form defaults take precedence over matching profile fields. Publication
permission comes only from the dispatch mode: an omitted selection means
artifacts only, even if an older profile contains a publishing value.

### Desktop overrides and profiles

Desktop's optional text controls inherit when blank. Choice controls inherit when
set to **profile**. Overrides affect the current run only. To clear an inherited
nonempty release repository/target, edit the profile itself. A desktop matrix is
JSON text; the profile stores a normal JSON array.

Copy the complete profile to `configs/<name>.json` for another configuration.
For another device, see [the device/profile setup guide](docs/DEVICES.md) for
source, defconfig, installer, payload, and credential requirements.
Files must be regular UTF-8 JSON directly inside `configs/`, at most 32 KiB.
Traversal, symlinks, duplicate/unknown keys, missing fields, and invalid types are
rejected. Retain every schema field and never store credentials in a profile.

CLI alternative for private Telegram delivery:

```sh
gh workflow run kernel-build.yml --repo OWNER/WORKFLOW-REPO --ref WORKFLOW-BRANCH \
  -f config_file=configs/miatoll.json \
  -f kernel_rc=rc2 \
  -f build_mode=single \
  -f toolchain=android-clang \
  -f publish_to='personal only'
```

The workflow branch here is distinct from the kernel source branch in the profile.

## 🔐 Repository secrets

Add secrets to the **repository running the workflow** under **Settings → Secrets
and variables → Actions → Secrets**. No repository variables are required.

| Secret | Purpose | Required when |
| --- | --- | --- |
| `GH_PAT` | Private source clone; Contents read on the kernel repository. Also an optional release-token fallback if granted destination write access. | Source visibility is `private`. |
| `GH_RELEASE_TOKEN` | Contents read/write on the GitHub release destination repository. | Mode includes GitHub, unless a suitable `GH_PAT` is used. |
| `TELEGRAM_BOT_TOKEN` | Shared bot token from the official **@BotFather**. | Mode includes personal Telegram or channel. |
| `TELEGRAM_CHAT_ID` | Your **positive numeric personal chat ID**, for example `123456789`. | Mode includes personal Telegram. |
| `TELEGRAM_CHANNEL_ID` | Release channel's negative numeric ID, such as `-1001234567890`, or public `@ChannelUsername`. | Mode includes channel. |

### Telegram setup

1. Create a bot through **@BotFather** and save its token.
2. For personal delivery, open the bot from your account and send **`/start`**.
   Use your own numeric chat ID—not the bot ID or a username. Do not block the bot.
3. For channel delivery, add the bot as an administrator with permission to post.
   Configure that channel separately through `TELEGRAM_CHANNEL_ID`.

The personal route rejects negative IDs and channel usernames. The channel route
rejects positive personal IDs. These local checks cannot establish channel type,
admin status, or live Bot API access; configure the actual destination correctly.
**Neither destination falls back to the other.** The old
`TELEGRAM_RELEASE_CHAT_ID` name is not read; replace it with `TELEGRAM_CHANNEL_ID`.

Only selected destinations need credentials. A public-source artifacts only build
requires no custom secrets. The default private-source profile still needs
`GH_PAT`, including in artifacts only mode. Missing selected secrets and malformed
Telegram token/ID formats fail before compilation.

### GitHub setup

Create a fine-grained token through your account's **Settings → Developer settings
→ Personal access tokens**. Select the correct resource owner/repositories and
minimum Contents permissions. Prefer separate read-only source and write-enabled
release tokens. Organizations may require approval or SSO authorization.

The destination repository must already exist and contain at least one commit.
Do not manually create `GITHUB_TOKEN`, `GH_TOKEN`, Kbuild identity, or actor secrets.
Never paste credentials into dispatch fields, source URLs, profiles, or logs.

## ⚙️ Profile reference

The supplied JSON file is the complete schema. Common dispatch controls override
`kernel_rc`, `build_mode`, `toolchain`, and `publish_to`; other fields can be
changed in the profile or optionally overridden through Desktop Kernel Build.

| Field | Default | Meaning |
| --- | --- | --- |
| `kernel_name` | `Alecto` | Package branding, installer title, release title; not repeated in Telegram captions. |
| `device_name` | `miatoll` | Package label only, not an implicit installer codename. |
| `device_codenames` | `curtana,gram,excalibur,joyeuse` | Ordered installer compatibility list. |
| `supported_versions` | `12.0-17.0` | Android compatibility expression. |
| `anykernel_repository` | `https://github.com/One4Lots/AnyKernel3` | Public trusted device-compatible template. |
| `kernel_repository` | `https://github.com/One4Lots/Alecto_kernel_qcom` | Credential-free HTTPS GitHub source URL. |
| `repo_visibility` | `private` | `private` uses the source token; `public` clones without it. |
| `single_branch` | `staging` | Kernel source branch for single mode. |
| `multi_branches_json` | Two branch objects | JSON array; branch required, defconfig/image optional. |
| `defconfig` | `vendor/xiaomi/miatoll_defconfig` | Relative to `arch/arm64/configs`. |
| `image_type` | `Image.gz + dtbo.img + dtb.img` | Payload layout. |
| `changelog_mode` | `latest-message` | Latest full message or `recent-commits` for Telegram. |
| `commit_count` | `6` | Whole number 1–50; recent Telegram mode and all GitHub release notes. |
| `release_repository` | empty | `owner/repo` or HTTPS GitHub URL; blank uses this workflow repository. |
| `release_target` | empty | Destination branch/commit for the tag; blank uses its default branch. |
| `duplicate_release` | `fail` | Existing GitHub tag/release handling: `fail`, `skip`, or `update`. |

The default multi-branch selection is:

```json
"multi_branches_json": [
  {"branch": "17.0/modern-sched"},
  {"branch": "17.0/upstream-susfs"}
]
```

Entries inherit the selected defconfig/image mode unless explicitly overridden.
A matrix accepts 1–32 unique branch/config/image combinations, runs two builds at
a time, and does not cancel sibling builds after one fails. There is no toolchain
matrix dimension.

Supported payload layouts:

```text
Image.gz + dtbo.img + dtb.img
Image.lz4 + dtbo.img + dtb.img
Image.gz.dtb
```

Target compilers remain deliberately pinned: Android Clang r584948b and Neutron
Clang `06092026`. Host tools use distro Clang. Every make invocation explicitly
receives both `CROSS_COMPILE_ARM32=arm-linux-gnueabi-` and
`CROSS_COMPILE_COMPAT=arm-linux-gnueabi-`. The workflow does **not** assign
`CC_COMPAT` or `LD_COMPAT`; those remain under the kernel tree's control.
Different source trees can still require different compiler/VDSO conventions.

### Validation limits

Kernel names: 1–48 ASCII letters/numbers, spaces, dots, underscores, or hyphens;
start with a letter/number and omit surrounding whitespace. Filename spaces become
hyphens. Device labels/codenames: 1–48 ASCII letters/numbers, dots, underscores,
or hyphens, starting with a letter/number. The compatibility list accepts 1–32
unique entries with no empty codenames.

RC values: 1–48 ASCII letters/numbers, dots, underscores, plus, or hyphens,
starting with a letter/number and forming a valid Git tag. Source branches must
be valid Git branches up to 120 characters, with safe slash-separated components.
Defconfigs must be safe relative paths; traversal and symlink escapes are rejected.
Android versions accept a single version, ascending range, or list, such as
`12.0`, `12.0-17.0`, or `12.0,13.0,17.0`.

## 📦 Outputs and package naming

| Output | Contents and retention |
| --- | --- |
| Main Actions artifact | Raw selected image, applicable DTB/DTBO images, and individual `cust-atoll*.dtb`; 14 days; no flashable ZIP. |
| AnyKernel3 ZIP | Selected installer payloads and original installer presentation; no individual `*.dtb` members. Sent only to selected destinations. |
| Diagnostics | Build/compiler logs, history, package metadata, installer properties; 7 days. |
| Internal publisher bundle | Only ZIPs, verified manifest, and required caption-source metadata when any destination is selected; 7 days; separate from the main artifact. |

Split modes install `dtb.img` as **`dtb`** and `dtbo.img` as **`dtbo`**. These
aggregate payloads stay in the flashable package. Individual `cust-atoll*.dtb`
files are raw artifacts only. Unrelated `cust-*.dtb` files are not exposed as
individual artifacts, although compiled device trees can contribute to an
aggregate/combined image. Combined mode installs `Image.gz-dtb` while its raw
artifact is named `Image.gz.dtb`.

Publisher transfers do not duplicate raw images or standalone DTBs. They retain
slash-branch subdirectories, so the verified package path is unchanged. A single
publisher bundle per build is reused by all selected destination jobs instead of
uploading separate transport bundles for each destination.

GitHub's archive wrapper for a multi-file artifact is not an AnyKernel3 flashable
ZIP. Artifacts-only intentionally does not retain the flashable ZIP. Select
`personal only` to receive a private copy. GitHub Release assets have their own lifetime.

Package pattern:

```text
<branch>-<safe-kernel-name>-<safe-device-name>-<kernel_rc>-<ddmmyyyy>.zip
```

Example: `17.0/modern-sched-Alecto-miatoll-rc1-25112026.zip`. The slash remains a
folder separator locally and inside the transfer bundle. Telegram/GitHub receive
only the basename; the full branch stays in metadata and notes. Colliding flattened
GitHub asset names receive a stable build-digest suffix, reflected in SHA256SUMS.
Dates use Asia/Dhaka. Filenames omit time. Metadata includes an ISO-8601 timestamp
with an explicit offset and a 12-hour clock with AM/PM. Telegram shows only the
weekday and readable calendar date in Asia/Dhaka; the clock line is hidden.
The timestamp remains validated rather than guessed from ambiguous old metadata.

## 🎨 Installer identity and preservation

The default compatibility properties are exactly:

```text
device.name1=curtana
device.name2=gram
device.name3=excalibur
device.name4=joyeuse
```

`miatoll` is not automatically inserted. Only the explicit ordered list is used;
stale template codenames are removed and device checks stay enabled.

- `kernel.name`: selected name with a ` Kernel` suffix unless already present.
- `kernel.string`: no generated description; an existing legacy slot is cleared,
  and an absent slot is not recreated.
- `kernel.version`: numeric base of the compiled release; for example
  `4.14.357-Alecto/8528922d` becomes `4.14.357` in installer metadata only.
- `kernel.toolchain`: selected toolchain label.
- `kernel.maintainer`: triggering user followed by `@github`.
- `supported.versions`: selected Android compatibility expression.

These are actual AnyKernel3 properties, not extra recovery-printing variables.
`kernel.name` follows the configured kernel name. `device.name1`, `device.name2`,
and so on follow the explicit ordered `device_codenames` list—not the display label.
Managed properties follow the current AnyKernel3 order: name, optional blank legacy
slot, version, toolchain, maintainer, ordered device aliases, Android compatibility.
The UI detects Device Name and Android Version at install time; the workflow does
not inject alternate UI variables. No template flash controls or original UI files
are changed.

The compiled binary and generated `kernel.release` are not rewritten. Full release
text is retained in `logs/package.json`. Kbuild identity is the triggering user
with host exactly `GitHub`; installer maintainer spelling remains `user@github`.

Use a complete trusted AnyKernel3 template with one recognizable quoted
`properties()` block. Blank managed fields are filled; ambiguous/duplicate fields
fail clearly. Partition, slot, AVB, ramdisk, cleanup, and flash operations are not
rewritten.

**No generated banner or ASCII art is added.** Speed, PyFiglet, the bundled font,
renderer preflight, recovery heredoc, and `logs/flash-banner.txt` are removed. The
original template's root `banner`, UI files, and core scripts remain untouched.
A complete legacy workflow-owned section between `# BEGIN KERNEL_BUILD_FLASH_BANNER`
and `# END KERNEL_BUILD_FLASH_BANNER` is removed if present; malformed/duplicate
markers fail rather than deleting an uncertain section of installer code.

Runtime Python scripts use only the standard library. Development-only validation
tools are not installed into the production build runtime.

## ✉️ Telegram delivery

The caption starts with **✅ Kernel Compiled Successfully**, with no kernel/device
title, branch, or local-time line. Inline fields use this order and spelling:
🔩 **ToolChain**, 🧶 **Linker**, ⏳ **Build Time**, 〽️ **Zip Size**, 📅 **Date**,
📦 **Image**. Values follow their labels on the same source line. Telegram still
controls wrapping on each screen; the workflow cannot force a device's line width.

After one blank line, **🔑 MD5 hash** has a separate copyable code block containing
only the exact checksum. The existing latest/recent-commit section follows after
another blank line, in its own code block. Caption budgeting includes both blocks;
the checksum is never shortened. Kernel/device names, branch, time, RC, defconfig,
single/multi, and public/private stay in the appropriate files and metadata, not
in caption fields. Installer identity and ZIP filenames are unchanged.

For example, `Image.gz + dtbo.img + dtb.img` is displayed as
`Image.gz, dtbo.img and dtb.img`. Toolchain names follow the selected compiler;
Clang and LLD version numbers are independently extracted from their actual build
logs. No version is hardcoded or inferred from the other executable's version.
Unparseable logs fail before the first send. See [the message design](docs/TELEGRAM.md)
for a representative post and timestamp/size semantics.

Personal and channel jobs are separate and run only after all build jobs succeed.
They receive only their matching destination secret; neither falls back to the
other. Telegram credentials are not exposed to compilation steps.

Before the first send in each job, the publisher verifies the complete expected
matrix, canonical paths, size/SHA-256, the hosted Bot API's 50 MB document limit,
bounded non-symlinked caption inputs, compiler banners, and all captions. SHA256 is
verified against each ZIP. Local validation failure prevents partial sends within
that job. Remote delivery is **not atomic**: a network/API failure after one upload
can leave partial posts. There is no automatic rollback or exactly-once guarantee.

Latest-message mode retains the full latest commit subject/body/trailers, with
bounded trimming and a diagnostics pointer when necessary. Recent mode lists the
last N subjects newest first, including merges. Commit content and MD5 hash use
separate code blocks; compiler/linker details remain plain. No standalone SHA line
is added.
HTML is escaped and UTF-16 length bounded; overflowing recent lists follow in
bounded messages. Short histories use all available commits with a warning.

Only valid explicit rate limits are retried. Ambiguous writes are not blindly
repeated. Inspect your personal chat/channel before redispatching after a timeout.
Each destination has its own concurrency group. GitHub controls pending-job queue
behavior; inspect canceled/skipped jobs before deciding to redispatch.

## 🚀 GitHub Releases

The release title is exactly `kernel_name`; the tag is exactly `kernel_rc`.
A serialized publisher verifies all expected ZIPs and publishes them together
with `SHA256SUMS.txt` and grouped source commit links.

- **fail:** stop if either the destination tag or release exists.
- **skip:** leave existing objects untouched; builds and selected Telegram jobs continue.
- **update:** require the tag to resolve to the validated target, peeling annotated
  tags; replace exact-name assets and notes/title while retaining unrelated assets.
  Immutable releases cannot be updated.

Tags are never moved or deleted. New releases stay drafts until uploads finish and
are not forced to become latest. Failed updates may leave partial changes; inspect
before retrying. API requests have a two-minute limit; uploads have a fifteen-minute
limit. Timed-out writes are not blindly repeated.

GitHub and Telegram jobs are independent: success in one does not imply success
in another. Telegram requires successful build jobs, including diagnostics/cache
steps. GitHub can still publish a complete verified bundle set after a diagnostic
failure. Missing bundles always prevent partial GitHub releases.

A release tag points into its destination repository, not necessarily the kernel
source. Actual source commits are recorded separately. Recovery should use a new
dispatch or Re-run all jobs; bundles are scoped to the same run and attempt, so
partial reruns do not combine old and new outputs. Telegram posts are not deduplicated.

## 🖥️ Runner, actions, and caches

All jobs use `ubuntu-latest`, documented upstream as Ubuntu 26.04 at this review;
GitHub can migrate that label gradually. Stable action releases reviewed on
25 November 2026 are pinned by full commit:

| Action | Release | Commit |
| --- | --- | --- |
| Checkout | `v7.0.1` | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
| Cache restore/save | `v6.1.0` | `55cc8345863c7cc4c66a329aec7e433d2d1c52a9` |
| Upload Artifact | `v7.0.2` | `cf430e030ddbb5b0abf93d22962f4752f3646cd9` |
| Download Artifact | `v8.0.2` | `9000827ccba6bdab643e8b6fd33ac0654aef8333` |

All steps have short descriptive names, including **📂 Checkout**, instead of
falling back to a SHA reference. These Node 24 actions target GitHub.com-hosted
runners, not GHES or unmanaged self-hosted runners. Pins require deliberate review
for future upgrades. Target compiler releases are independent of host/action updates.

APT, compiler, and object caches use the actual OS/version/architecture, not merely
the moving runner label. APT caches use weekly keys and refresh indexes after seven
days, with one refresh/install retry. Compiler caches use pinned URLs and final
response headers, with pre/post-download checks and required binary verification;
the header fingerprint is not a compiler authenticity signature. Object caches
reuse across branches only within the same host/compiler namespace, including
partial failed builds. Selected unused SDK directories are reclaimed for disk space.

Archived uploads preserve payload paths and reject transport digest mismatches
before application manifest checks. Download Artifact v8 extracts a single matching
artifact directly into `release-downloads/`; multiple matches get artifact-name
subdirectories. The shared verifier accepts these two explicit layouts, validates
every expected matrix identity, and rejects duplicate identities or corrupt ZIPs.

## Operational boundaries

Kernel and installer repositories contain executable code: use trusted sources.
Release credentials stay outside compilation; the workflow token is read-only and
source credentials are removed from origin. A private source token does not make
public workflow logs/artifacts private. Public delivery deliberately exposes the
selected binaries and source history.

The source-validation workflow and local fixtures are not real kernel compilation.
A fresh hosted build is required for source/compiler compatibility, followed by
live destination checks and device-specific verification. The mobile form is
smaller, not a guarantee that every GitHub browser layout issue is eliminated.
No source review can honestly promise that every branch compiles or boots perfectly.

## Compiler extraction safety

Compiler cache misses stream the decompressed tar through `archive.py` rather than
screening only a textual filename list. Absolute/traversing member paths, escaping
links, cyclic/missing hardlinks, duplicate files, device nodes, and FIFOs are
rejected. Normal in-tree compiler symlinks and forward hardlinks are supported;
unsafe ownership and permission bits are not restored. All required compiler tools
must be executable regular files before the staging directory is installed.
Failed downloads, decompression, extraction, or completeness checks leave no new
cache installation or compiler PATH export. The toolchain cache namespace is now
`kernel-build-toolchain-v3-…` so old extractions are not restored through a fallback.
APT and ccache namespaces remain unchanged.

This confines extraction; it does not sandbox the compiler or authenticate a
release. Source repositories, AnyKernel3 templates, and compiler releases remain
trusted executable inputs. The header fingerprint is freshness metadata, not a
trusted content signature.

## Validation and rollout

The current local suite passes **258 test methods**, including cross-product
checks across delivery, configuration, and failure modes:

| Coverage | Scope |
| --- | --- |
| Valid configuration/handoff scenarios | 432: all modes, image layouts, compiler selections, history modes, source visibilities, and download shapes |
| Invalid-bundle scenarios | 75: missing, corrupt, mismatched, duplicate, or unsafe bundles across publishing modes/download shapes |
| Joined build/package fixtures | 12 image/compiler/visibility combinations, plus six custom-profile/compiler identity cases; local Git, shell, make, and synthetic payloads |
| Static/source checks | Ruff on 31 Python files, Bash syntax, ShellCheck, tracked and new-source whitespace |
| Font-free runtime | Full suite in environments without PyFiglet; runtime imports with Python site packages disabled |
| Distribution check | Full suite, static checks, and whitespace checks repeated from a clean extraction of the final source archive |

Scenario counts run inside test methods; they are not additional independent
unit tests or actual hosted kernel builds. Destination writes are mocked.
See [the validation report](docs/VALIDATION.md) for the precise evidence and
[the operations runbook](docs/OPERATIONS.md) for rollout, troubleshooting, manual
ZIP recovery, and live acceptance criteria.

Replace the complete source together, including the script directory—not just a
single publisher file. Remove the obsolete `banner.py` and `fonts/` directory if
upgrading in place. Publisher manifests now use schema 3 with an explicit timezone;
do not mix old package/bundle scripts with the new publisher. Update CLI dispatches
to the exact mode labels and start a **new** run from the updated revision. First validate the actual source
in `artifacts only`, then use `personal only` for private delivery before enabling
public destinations. Keep `TELEGRAM_CHANNEL_ID` separate from `TELEGRAM_CHAT_ID`.
Local success is not proof of hosted compilation, delivery permissions, or boot
safety. This is a tested deployment baseline, not a promise of perfect operation.

### Troubleshooting: download succeeds, personal delivery says bundles are missing

The earlier loader assumed every download had an artifact-name subdirectory. The
pinned Download Artifact v8 instead flattens a single match, even when
`merge-multiple: false`. That caused successful single builds to fail before any
Telegram API request. It was a loader bug, not evidence of bad Telegram secrets.

The corrected `.github/scripts/kernel_build/publication.py` searches both
`release-downloads/logs/release-manifest.json` and
`release-downloads/*/logs/release-manifest.json`. It retains ZIP checksum,
canonical-path, source identity, duplicate identity, and complete-matrix checks.
The audit additionally rejects empty/duplicate expected matrices, symlinked
manifest parents, ambiguous JSON keys, and non-integer byte-count/schema fields.
GitHub and Telegram both regenerate the expected matrix from validated settings
before accepting the publisher handoff. All publishers use this verifier, including
`personal only`.

To retrieve an existing build **without recompiling**, open its Actions run summary,
download the `release-bundle-…` artifact, and extract it. The flashable ZIP is inside
`artifacts/` (possibly under a source-branch subdirectory). The downloaded outer
artifact archive is transport, not the flashable package. Do not flash the outer
archive. This recovers the file manually; it does not retry bot delivery.

For subsequent automatic delivery, replace the corrected script (or the complete
source archive) in the workflow repository and start a fresh dispatch. Re-running
only the old failed job uses its original workflow revision, so it does not pick up
a newly committed script. No channel secret is needed for `personal only`.
