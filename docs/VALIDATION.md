# Validation report

**Review date:** 25 November 2026 (UTC)

This report describes local evidence, not production certification. Tests use real
local files, Git, make, shell scripts, ZIP creation/extraction, and checksums.
Compiler executables and kernel payloads are synthetic fixtures. External API
writes are mocked. No actual kernel compilation, Telegram post, GitHub release,
or device flash was performed by these checks.

## Results

| Check | Result |
| --- | --- |
| Offline unit/integration suite | **258 test methods passed** |
| Configuration/handoff cross-product | **432 scenarios passed**, inside one test method |
| Invalid-bundle cross-product | **75 scenarios passed**, inside one test method |
| Shell/Git/make/package cross-product | **12 combinations passed**, plus six custom-identity cases and existing smoke tests |
| Actual upstream template compatibility | 12 configurations passed; UI/backend/tail/controls preserved |
| Minimal runtime dependencies | All 258 tests passed with PyYAML only |
| Ruff lint and formatting | Passed for 31 Python files |
| Bash syntax and ShellCheck | Passed, including sourced-file resolution |
| Clean source archive extraction | Full suite, Ruff, shell checks, and complete-source whitespace passed |
| Archive integrity | ZIP CRC and every packaged source file verified byte-for-byte |
| Whitespace | Tracked and new source files checked |

Scenario counts are not added to the unittest method count. In particular, this
is **258 test methods**, including methods executing **507 publication/handoff
scenarios**. The counts are not additive, and none of the scenarios compiles a real kernel.

## Configuration and publication matrix

`tests/test_mode_matrix.py` uses an independently declared destination truth table.
It does not derive expected destinations from the production routing registry.
Each successful scenario resolves the profile, validates preflight with the required
secrets, emits and checks the matrix/routing outputs, prepares fixture bundles,
and invokes both Telegram entrypoints and the GitHub publisher entrypoint.
GitHub API construction and destination writes are mocked; local verification,
caption preparation, staging, release notes, and SHA256SUMS generation execute.

| Dimension | Values |
| --- | --- |
| Delivery | All six exact modes, in the required order |
| Build/download shape | Single + flat, single + nested, multi + nested |
| Images | Combined Image.gz.dtb, split gzip, split LZ4 |
| Compiler selection | Android Clang, Neutron Clang |
| Telegram history | Latest full message, recent commits |
| Source visibility | Public, private |
| Total | 6 × 3 × 3 × 2 × 2 × 2 = **432 scenarios** |

Six additional joined-pipeline cases resolve actual profile files for Orion/Surya,
Nova Kernel/Marble, and Zenith kernel/Alioth with both compiler selections. They
verify the compact, title-free layout in personal/channel captions, independent
checksum blocks, configured ZIP names/metadata, exact AnyKernel3 names/device
lists, and preserved installer UI. Separate checks enforce
Mobile/Desktop workflow/run/job names and the exact bold status text
`✅ Kernel Compiled Successfully`. Caption fields do not repeat kernel/device
branding; those identities remain in filenames, installer properties, and metadata.

Multi-build fixtures contain both ordinary and slash-separated source branches.
Bundles are written in reverse matrix order to ensure publication follows the
validated matrix rather than discovery order. Tests check destination IDs, caption
limits, compiler labels, asset counts, and hashes of staged assets. Artifacts-only
creates no publisher fixtures and sends nothing even when publisher entrypoints
are invoked. Only selected publication credentials are supplied in successful
scenarios; private sources additionally require the source-read token.

The five publishing modes are also crossed with the three download shapes and
five bundle defects: missing manifest, corrupt ZIP, wrong build identity, duplicate
identity, and unsafe ZIP path. All **75 scenarios** must fail before a selected
publisher writes externally. GitHub must not stage assets after a bundle-validation
failure. A later failed matrix member must prevent earlier members from sending.
Remote partial writes are a separate limitation; local prevalidation cannot make
multiple network operations atomic.

## Delivery contract and forms

| Mode | Personal | Channel | GitHub |
| --- | --- | --- | --- |
| artifacts only | No | No | No |
| personal only | Yes | No | No |
| personal+channel | Yes | Yes | No |
| personal+github | Yes | No | Yes |
| channel+github | No | Yes | Yes |
| personal+channel+github | Yes | Yes | Yes |

Tests enforce the exact labels/order in both forms, matching common defaults,
five mobile inputs, 21 desktop inputs, and same-revision reuse of one pipeline.
A publishing profile cannot authorize dispatch: omitted selection means
artifacts only. Old labels, old checkbox inputs, capitalization/whitespace aliases,
unknown keys, wrong types, unsafe profiles, and duplicate JSON keys are rejected.
Each selected secret is removed in turn to prove preflight fails appropriately.
Malformed unused publication credentials do not activate or block artifacts-only.

Personal uses TELEGRAM_CHAT_ID, a positive numeric ID. Channel uses
TELEGRAM_CHANNEL_ID, a negative numeric ID or @username. TELEGRAM_BOT_TOKEN is shared;
destination secrets never fall back to each other. The deprecated
TELEGRAM_RELEASE_CHAT_ID is ignored. Format validation cannot prove the live chat
is a channel, that the user started the bot, or that the bot has posting rights.

## Download Artifact v8 regression

The reported hosted failure occurred after a successful artifact download and
before a Telegram API request. The pinned action's source contains an
`artifacts.length === 1` destination condition: one matching artifact extracts
straight into the requested directory, even with merge-multiple false. Multiple
matches get artifact-name subdirectories.

Reviewed source:
https://github.com/actions/download-artifact/blob/9000827ccba6bdab643e8b6fd33ac0654aef8333/src/download-artifact.ts#L185-L197

The old loader only searched the nested shape; the new regression reproduced that
error before the fix. The verifier now searches exactly these two locations:

```text
release-downloads/logs/release-manifest.json
release-downloads/*/logs/release-manifest.json
```

It does not accept arbitrary deeply nested manifests. Duplicate identities across
flat/nested shapes, missing expected members, unexpected identities, and corrupt
ZIPs still fail. Missing-bundle errors identify expected/verified counts, missing
IDs, and searched locations. Both the GitHub entrypoint test and joined shell
integration fixture now use the actual flat shape for one downloaded artifact.

Earlier tests modeled the wrong single-artifact shape. Their previous success was
not evidence of correct hosted artifact handoff; the expanded checks address that
specific gap but do not substitute for another hosted run.

## Additional audit hardening

- Empty, malformed, oversized, or duplicate expected matrices fail explicitly.
- GitHub, like Telegram, regenerates the matrix from validated build settings and
  rejects altered, reordered, or partial handoffs before staging/publication.
- Manifest files must be regular, bounded files with no symlinked parent inside
  the download path. A symlinked download root is rejected.
- Schema and byte-count fields require actual integers, not coercible floats or
  booleans. Schema 3 timestamps must be timezone-aware and consistent with the package date/time. SHA-256 must be canonical lowercase hexadecimal and match file bytes.
- Duplicate manifest JSON keys are rejected; matrix JSON rejects duplicate keys.
- Compiler cache completeness is checked before path export or compiler execution.
- The checker now covers new/untracked source files, which git diff alone omitted.
  No font-specific whitespace exception remains.

Publisher transport includes only `artifacts/**/*.zip` and explicit manifest/
caption-source files. Tests retain root and slash-branch ZIPs and exclude raw
images, standalone DTBs, and unrelated logs. Full integration passes without raw
images in the transport bundle. No network-speed benchmark is claimed; the
optimization removes duplicate payload files by selection.

## Build, packaging, and installer coverage

The joined integration test crosses all three image layouts, both compiler
selections, and public/private source modes: **12 combinations**. Real local Git,
fixture compiler executables, GNU make, build.sh, package generation, manifests,
GitHub staging, and both Telegram publishers are connected. Remote clones are
rewritten to local fixture repositories. Make creates synthetic non-flashable
images and does not execute a real compiler.

Assertions cover explicit CROSS_COMPILE_ARM32 and CROSS_COMPILE_COMPAT on every
target, no forced CC_COMPAT/LD_COMPAT, triggering actor with GitHub host, history,
requested image/DTB/DTBO fallback targets, selected installer compiler identity,
ZIP integrity, exact ordered device list, and numeric installer kernel.version.
For example, 4.14.357-Alecto/8528922d becomes 4.14.357 only in installer metadata.

Split aggregate dtb/dtbo payloads stay in the package; standalone cust-atoll DTBs
are raw artifacts only. Original template banner/core/UI content stays intact; no generated artwork is inserted.
Tests cover malformed installer templates, path/symlink failures, metadata limits,
real dates, source branch paths, naming collisions, and preserving full metadata.

A previous direct review of One4Lots/AnyKernel3 used commit:

```text
f04f39059bbd002bde91d8f4fbd42f7a2fae0aa1
```

Synthetic packaging in all image modes preserved original presentation/core and
installer operations. This is historical source-review evidence, not a new clone
or a flash test in the current audit. Future upstream template changes need review.

## Redesigned post and banner removal

`tests/test_presentation.py` verifies the exact bold success line, requested emojis,
field order, blank-line separators, removal of the title/branch/local-time fields,
all image layouts, binary MiB sizes, and independent checksum/commit code blocks.
Six added methods verify the complete expected caption, invalid checksums,
lossless mixed-case checksums, independence from removed display fields, balanced
HTML under overflow, and commit-text injection resistance. Overflow checks cross
both compilers, three image modes, and latest/recent-history headings. The 432-case
handoff test also checks labels, omissions, and the first code block against the
actual attachment's MD5 before checking commit content in the second block.

Tests parse Android, generic, and Neutron Clang logs, including differing
release/development versions, and independently parse LLD logs. Unknown or
unparseable versions fail instead of being replaced by a guessed value.

Timestamp checks cover noon, midnight, leap day, timezone conversion across the
local date boundary, missing offsets, impossible dates, contradictory dates, and
incorrect AM/PM. Publisher manifests now use schema 3 with built_at; package and
publisher code must be deployed together. A later matrix member with a bad timestamp
or compiler log blocks every document in the job before the first send.

Speed/PyFiglet, its renderer script/assets, preflight, injected banner loop, and
banner-preview logs are removed. Tests verify no generated output is added,
original AnyKernel3 UI/banner/core files remain intact, packaging is idempotent,
and complete legacy generated blocks are removed without disturbing original
content. Malformed/duplicate legacy markers fail without changing the source file.

The full suite runs in a fresh development environment with **no PyFiglet**. Runtime
modules also import under `python -S`, with third-party site packages disabled.
An additional minimal test environment needs only PyYAML for workflow-structure
tests, not a font renderer. Runtime scripts use the Python standard library.
Dependency-phase tests stub APT/sudo/ccache while exercising cache freshness,
retry, and failure behavior; they do not perform a native Ubuntu installation.
Local host: Debian 12/Python 3.11. The hosted validation workflow runs native
system-Python tests before development checks; its new hosted result still needs
observation.

See [the message specification](TELEGRAM.md) for a representative post and exact
field semantics. Client wrapping/appearance remains controlled by Telegram.

## Failures and external-service boundaries

Tests cover GitHub fail/skip/update policies, bare/annotated tags, tag-target
protection, immutable releases, missing permissions, malformed API replies,
timeouts, failed uploads, and drafts retained until uploads complete. Telegram
tests cover rate limits, malformed replies, access failures, caption Unicode/HTML
limits, complete history, unsafe/oversized metadata, oversized documents, and
ambiguous writes without blind retries. No exactly-once or rollback guarantee is
claimed. Check each destination independently after any partial network failure.

Action commits and declared input/runtime contracts are checked against the
recorded upstream snapshot. Host caches are partitioned by actual OS/version/
architecture; cached compiler binaries and fingerprints are tested. Target compiler
archives are not downloaded or executed in these tests. Sources and compilers are
trusted executable inputs, not sandboxed untrusted workloads; header fingerprints
are cache freshness metadata, not cryptographic supply-chain authentication.

YAML/workflow contracts are checked locally. **No actionlint success is claimed**:
the available package attempted a GitHub release-asset download that failed in this
environment. It was not added as an unreliable mandatory dependency.

## Reproduce and roll out

```sh
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r tests/requirements.txt
bash scripts/check.sh
```

After dependencies are installed, the checks require no real secrets, external
kernel clones, or destination access. Runtime dependencies remain APT-managed and
separate from development requirements.

See [the operations runbook](OPERATIONS.md) for deployment, manual artifact recovery,
and live acceptance criteria. Still unverified here: fresh hosted kernel compilation,
Ubuntu APT installation, real artifact/cache services, destination permissions,
mobile-browser layout, bootability, and flashing safety. Use the local results as
evidence for the behaviors tested, not a promise of flawless production operation.

## Current AnyKernel3 and Telegram size correction

The current-template contract was recorded from One4Lots/AnyKernel3 revision
`b982147e3e4603eb59cc01699f09cc9d62f5715e`. Offline tests check twelve
name/compiler/optional-description-slot combinations, managed-property order,
preserved control flags and flash-script tail, idempotency, shell syntax, four
ordered device aliases, and rejection of an ambiguous out-of-block description.
Only upstream facts and SHA-256 hashes are recorded; the installer implementation
is not vendored. Additional tests check the reported 15.52 MB / 14.8 MiB example,
size boundaries, and rejection of invalid byte-count types. Existing six-mode,
image-layout, visibility, compiler, custom-name, and publication tests remain.

A separate local check applies the updater to a copy of the actual reviewed
upstream checkout, then checks that every file except `anykernel.sh` retains its
hash and that the executable tail and unmanaged controls remain unchanged. This
is a packaging compatibility check, not a recovery execution or device flash.

## Singular Image label, MD5 hash, and additional devices

The exact-caption test requires `📦 Image` (singular) and a standalone
`🔑 MD5 hash` block containing exactly 32 hexadecimal characters. The 432-case
handoff cross-product checks the displayed digest against the actual ZIP's MD5.
SHA256 verification and GitHub checksum assets remain unchanged. Regression tests
reject invalid MD5 values and prevent SHA256 from being used as an MD5 fallback.
Compiler/image/visibility pipeline tests and custom profiles check the same layout.

Another new regression exercises independent surya/alioth profile fixtures,
repository/installer selection, ordered aliases, defconfigs, branch/image modes,
default artifacts-only routing, and run-local desktop overrides. These are
configuration fixtures, not proof of device compatibility. The deployment steps
and architecture/installer limitations are documented in `docs/DEVICES.md`.

## Fresh end-to-end source audit

Reviewed both dispatch forms and their shared pipeline, profile schema and secret
routing, dependency/cache steps, source/history collection, compiler selection and
Kbuild arguments, payload packaging and installer edits, manifest verification,
release duplicate policies, Telegram request/retry behavior, and the current
caption contract. This is a source audit plus local fixture evidence, not a claim
that every source/device or remote service was exercised.

Two focused changes resulted:

1. Replace filename-list screening plus external tar extraction with a streaming
   extractor that checks member types and confined links, rejects conflicting
   entries, and applies safe file modes without restoring archive ownership.
2. Validate all required compiler tools as executable regular files **before**
   installing a fresh extraction. Previously an incomplete release could leave an
   unusable destination directory before failing. Fresh failures now clean up
   staging and a subsequent clean attempt can succeed. The compiler cache alone
   moves to v3, without a fallback to previously extracted v2 contents.

`tests/test_archive.py` adds **18 methods** covering normal files/directories,
compiler symlinks, forward/chained hardlinks, absolute/traversing paths, escaping
and cyclic links, missing targets, devices/FIFOs, conflicting entries, permissions,
corrupt/truncated streams, invalid staging, and stream draining. Shell-level
cache-miss fixtures exercise success, failed downloads, decompressor failures,
unsafe archives, incomplete tools (including a directory masquerading as a tool),
cleanup, and a clean retry. Curl and zstd are controlled stubs in those shell
fixtures; no real release archive was downloaded/decompressed/executed. The
extractor itself processes actual tar bytes using the Python standard library.

The previous 240-method suite and all 507 handoff scenarios remain. The MD5 hash
caption, installer controls/UI, exact six modes, separate destination IDs, both
workflow forms, compiler/build flags, and image payload contracts are unchanged.
A stale package-code comment was also corrected: logs retain SHA256, while
Telegram displays the separately computed MD5 hash.
