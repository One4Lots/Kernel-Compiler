# Adding another device

You normally add a **build profile**, not a new set of device credentials. Keep
`configs/miatoll.json` for miatoll and create a separate file for each other target.
Do not change only the device label: that does not change the kernel source,
defconfig, payload format, or installer flash layout.

## 1. Copy the complete profile

In your workflow repository, copy `configs/miatoll.json` to, for example,
`configs/surya.json`. This name is illustrative, not a verified surya configuration.
Keep **every existing JSON key**: profiles use a strict schema and missing or
unknown fields fail validation. Files must be directly inside `configs/`, with
letters, numbers, underscores, or hyphens in the filename, not in a nested folder.
Never put tokens, passwords, or authenticated clone URLs in a profile.

Change these fields to match the new device's actual source and installer:

| Profile field | What to supply |
| --- | --- |
| `kernel_name` | Your kernel/package branding. |
| `device_name` | Filename/package label, such as `surya`; this does not set installer compatibility. |
| `device_codenames` | Explicit ordered, comma-separated list of the devices this kernel really supports. Do not retain miatoll's aliases or add related models without checking compatibility. |
| `kernel_repository` | Credential-free `https://github.com/OWNER/KERNEL-REPO` URL for the new source. |
| `repo_visibility` | `public` or `private` for that source. |
| `single_branch` | An existing branch in that kernel repository. |
| `multi_branches_json` | Branch entries for the new device; replace the miatoll entries even if initially building one branch. |
| `defconfig` | Path relative to `arch/arm64/configs/`; for example `vendor/surya_defconfig` only if that file actually exists in your selected branch. |
| `image_type` | One of the three supported payload modes below, according to the device's installer requirements. |
| `anykernel_repository` | A public GitHub AnyKernel3 repository whose partition, slot, DTB/DTBO, AVB, and flash behavior have been configured for this device. |
| `supported_versions` | Android versions actually supported by this kernel/installer; do not copy miatoll's range blindly. |
| `toolchain` | Supported compiler selection compatible with the source. |
| `kernel_rc` | Package version/exact release tag, also selectable in the dispatch form. |

The supported `image_type` values are exactly:

- `Image.gz.dtb`
- `Image.gz + dtbo.img + dtb.img`
- `Image.lz4 + dtbo.img + dtb.img`

The profile changes **metadata and compatibility checks**, not installer flash
operations. Selecting an unrelated AnyKernel3 template or changing codenames does
not make it safe to flash on the new device.

## 2. Select it when running the workflow

Commit/upload the profile to the same workflow revision that you will dispatch.
In **Actions → Mobile Kernel Build** or **Desktop Kernel Build → Run workflow**:

1. Select the workflow branch containing the new profile and scripts.
2. Set `config_file` to `configs/surya.json` (or your actual filename).
3. Initially use `build_mode: single` and `publish_to: artifacts only`.
4. Choose the desired RC and compiler, then start a fresh run.

Both forms accept the profile path as text; there is no device dropdown to update.
The mobile form inherits advanced fields from the profile. The desktop form can
override advanced fields for one run; leave them blank or set to `profile` to
inherit. Common form controls—RC, build mode, compiler, and publication mode—take
precedence over profile defaults. Publication is always an explicit dispatch
choice; a profile cannot silently turn it on.

Keep one profile per device and run a separate dispatch for each device.
`build_mode: multi` means multiple **source branches of the selected profile**, not
a matrix of different devices. Each branch entry can select its supported
branch/defconfig/image combination, but source repository and installer/profile
identity are shared by that run.

## 3. Credentials: usually reuse the same repository secrets

Add secrets under your **workflow repository → Settings → Secrets and variables
→ Actions**. Do not put their values in JSON, chat, dispatch fields, or source URLs.

| Secret | Needed for a new device? |
| --- | --- |
| `GH_PAT` | Only if its kernel source is private. The existing source-read credential can be reused if it has Contents read access to that repository. Otherwise adjust its authorized repositories or replace it securely. Access to repositories under another owner/organization may need a different approved credential. |
| `TELEGRAM_BOT_TOKEN` | Only for Telegram publication; normally reuse the same bot. |
| `TELEGRAM_CHAT_ID` | Only when the selected mode includes personal delivery; not device-specific. |
| `TELEGRAM_CHANNEL_ID` | Only when the mode includes channel delivery; not device-specific. |
| `GH_RELEASE_TOKEN` | Only for GitHub publication; must have Contents write access to the configured release repository. A suitably authorized `GH_PAT` is an optional existing fallback, but separate read/write credentials are preferable. |

Public kernel source + artifacts-only delivery needs **no custom secrets**. The
AnyKernel3 repository must currently be public. One run accepts one source-read
credential; profile files do not select named per-device secrets. Do not invent
`SURYA_TOKEN` or similar names without also deliberately implementing workflow
secret routing. A separate workflow repository is another option for targets
requiring separately scoped credentials.

If using the same GitHub release repository for different devices, use distinct RC
release tags (for example `miatoll-rc1` and `surya-rc1`) to avoid duplicate-release
policy conflicts. `kernel_rc` is the exact tag, not a device-specific namespace
added automatically.

## 4. Check compatibility before publishing or flashing

This workflow builds ARM64 sources with its existing Clang/GNU cross-tool setup
and the three payload layouts above. It is not a universal Android build system.
Sources requiring a different architecture, Bazel/Kleaf, `build.sh`, vendor
modules, GKI-specific packaging, `vendor_boot`, or another flashing scheme may
need code changes—not just another JSON profile.

The current packager uses an existing `dtb.img` when available; otherwise it
concatenates discovered DTBs. Review whether that behavior is appropriate for
your device, especially if the source emits DTBs for several boards. Extra raw
standalone DTB exports currently match `cust-atoll*.dtb` for miatoll; adding a
profile does not automatically generalize that export rule.

First inspect the build logs, raw artifacts, selected payloads, generated
`anykernel.sh`, SHA256, and installer behavior for the exact device. A successful
compile, matching hash, or passing compatibility check is not proof that a ZIP
will boot or flash safely. Use your device's recovery/rollback procedures and
only publish a flashable package after device-specific validation.
