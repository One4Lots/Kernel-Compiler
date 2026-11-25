# Telegram message design

Personal and channel destinations use the same clean message format. Their
credentials and authorization remain separate. No change to the six delivery
modes is needed to use the redesigned post.

## Representative post

This is an illustrative example, not a live publication or a hardcoded version.
The attached document still uses the branch/name/device/RC/date ZIP filename.
The document filename and installer still use the configured kernel/device names;
the caption no longer repeats them as a title.

**✅ Kernel Compiled Successfully**

🔩 **ToolChain:** Android Clang 22.0.1<br>
🧶 **Linker:** LLD 22.0.1<br>
⏳ **Build Time:** 2m 56s<br>
〽️ **Zip Size:** 14.8 MiB<br>
📅 **Date:** Thursday, 26 November 2026<br>
📦 **Image:** Image.gz, dtbo.img and dtb.img

**🔑 MD5 hash**

```text
0123456789abcdef0123456789abcdef
```

The checksum above is illustrative; outgoing captions use the exact checksum of
the verified attached ZIP.

**📝 Latest commit**

```text
Fix scheduling

Preserve the full commit body and trailers when they fit.
```

## Field rules

| Field | Source and behavior |
| --- | --- |
| Status | Bold ✅ Kernel Compiled Successfully; sent only after the build and local publisher validation succeed. |
| Image | Combined mode stays Image.gz.dtb; split modes use commas and “and”, not plus signs. Build-mode strings themselves are unchanged. |
| ToolChain | Android Clang or Neutron Clang from the selected compiler, plus a version parsed from its real clang --version log. |
| Linker | LLD plus a version independently parsed from its actual ld.lld --version log. |
| Build Time | Actual recorded compilation duration, displayed as seconds, minutes/seconds, or hours/minutes/seconds. |
| Date | Weekday, day, full month, year from the package timestamp in Asia/Dhaka. The clock time and timezone line are not displayed. |
| Zip Size | Binary MiB: actual archive bytes divided by 1,048,576, rounded to one decimal place. Common Telegram clients show the same number but may label it MB. |
| MD5 hash | Recalculated from the verified outgoing ZIP; exactly 32 hexadecimal characters, in its own copyable code block below the heading. A download checksum, not proof of authenticity. |
| Latest commit | Full subject/body/trailers, with the existing bounded trimming notice when Telegram's caption limit requires it. |

The kernel/device title, branch, local time, RC, defconfig, single/multi, and
public/private are intentionally omitted from the caption. They remain in the
dispatch/profile, build logs, package metadata, and release manifest where applicable. The RC remains part of filenames and the exact
GitHub release tag; this redesign does not remove versioning or source traceability.

## Version parsing

The runtime never hardcodes a Clang or LLD version. It accepts recognizable numeric
release/development version tokens, removes redundant “clang version” wording and
provenance/target/install-directory details, and combines the Clang version with
the selected toolchain label. Generic `clang version …` output is supported for
both compiler selections. A Neutron build is not labelled Android merely because
its compiler prints an unbranded upstream Clang banner.

Unparseable compiler or linker logs fail before the first document is sent. No
fallback invents a version. The linker can have a different version from Clang.

## Timestamp contract

Publisher manifests use **schema 3**, including `built_at`, an ISO-8601 timestamp
with an explicit UTC offset. `date` and AM/PM `time` must describe the same instant
in Asia/Dhaka. A missing offset, invalid calendar date, or contradictory meridiem
fails validation. Tests cover midnight, noon, leap day, and UTC-to-Dhaka date rollover.

Old schema-2 bundles lack an unambiguous meridiem. They are rejected by new
publishers rather than guessed. Deploy package generation, bundle generation,
verification, and presentation together, then start a fresh run. Do not update a
single publisher script while leaving old build/manifest code in place.

## Formatting and limits

The status and labels use Telegram HTML bold. Toolchain/linker and other inline
values stay plain and follow their labels on the same source line. The order is
ToolChain, Linker, Build Time, Zip Size, Date, Image, a blank line, MD5 hash, then
another blank line and the existing latest/recent-commit section.

**MD5 hash and commit content have two separate code blocks.** The checksum block
contains only the exact checksum: no labels, extra whitespace, or truncation.
Telegram clients that support Copy Code can copy it independently of the commit.
Invalid checksum lengths, non-hex values, missing MD5, and HTML-like checksum
input are rejected. SHA256 values are never used as a fallback for the MD5 block.
Untrusted commit text is escaped, and escaped UTF-16 length—including both code
blocks and their markup—is conservatively kept within Telegram's
1,024-character document-caption limit. Large latest messages retain an explicit
trim notice pointing to diagnostics; recent-commits mode retains its bounded
follow-up-message behavior.

This layout is a message, not a custom client theme. Telegram controls font size,
line wrapping, document-card appearance, and Copy Code buttons. Those vary by
client and cannot be fixed by a workflow caption. Removing the title, branch, and
clock line reduces clutter, but no caption can guarantee an unwrapped line on
every screen. The full checksum stays in its own code block even if a client wraps
or horizontally scrolls it; no spaces or line breaks are inserted into the value.

## No generated installer artwork

The workflow no longer renders or inserts a Speed-font/ASCII banner. PyFiglet,
font assets, renderer preflight, injected printing loops, and banner previews are
removed. Original AnyKernel3 banner/UI/core assets are preserved. A complete old
workflow-owned banner block can be removed safely; malformed or duplicate markers
fail instead of guessing how much installer code to delete.

## Configuration-to-installer mapping

The same resolved profile drives packaging and installer metadata. Change
`kernel_name` and `device_name` in the selected profile, or use the desktop overrides.
Mobile builds inherit these fields from the chosen profile. They remain in package
names and metadata but are not displayed as a Telegram caption title.

| AnyKernel3 property | Value |
| --- | --- |
| `kernel.name` | Configured kernel name with Kernel appended only when absent |
| `kernel.string` | No description generated; clear an existing legacy slot, do not add an absent slot |
| `kernel.version` | Actual numeric base of the compiled kernel release |
| `kernel.toolchain` | Selected Android Clang / Neutron Clang label |
| `kernel.maintainer` | Triggering GitHub user followed by @github |
| `supported.versions` | Configured Android compatibility expression |
| `device.name1`, `device.name2`, … | Explicit ordered device_codenames, independently of the package/display device_name |

No new UI text, banner, or flash-operation code is inserted into AnyKernel3. Full
fixture-pipeline tests exercise three different profile identities with both
compiler selections and check both Telegram destinations, independent checksum
blocks, omitted display identities, and unchanged installer properties.
The default profile may contain Alecto/miatoll; the runtime renderer has no such
fixed names.

### Size and current-template compatibility

A ZIP containing 15,520,000 bytes is 15.52 decimal MB but approximately 14.8 MiB.
The caption now displays **14.8 MiB**, using the actual packaged archive byte count,
not an estimate or the size of a different artifact. Telegram client rounding and
unit labels are outside the bot's control. Upload limits still use the Bot API's
decimal byte limit; changing the presentation does not relax upload validation.

Reviewed upstream: [One4Lots/AnyKernel3 at b982147e3e4603eb59cc01699f09cc9d62f5715e](https://github.com/One4Lots/AnyKernel3/tree/b982147e3e4603eb59cc01699f09cc9d62f5715e).
Its `tools/install-ui.sh` displays name, numeric version, runtime device, compiler,
runtime Android version, and maintainer; there is no description row. Its
`anykernel.sh` still contains an empty `kernel.string=` property used by upstream's
optional module metadata path. SyncroBot clears any old description in that slot
without altering upstream backend scripts. If your template removes the property
entirely, SyncroBot leaves it absent. The recorded property/UI contract and source
hashes are in `tests/fixtures/anykernel3-contract.json`; this fixture is not a
vendored installer and cannot be flashed.

### MD5 display and SHA256 integrity

The caption shows the actual 32-character MD5 of the attached ZIP under the exact
heading **🔑 MD5 hash**. Packaging records MD5 in `logs/package.json`; personal and
channel publisher jobs recompute it from the verified outgoing ZIP rather than
trusting a manifest's MD5 field. A SHA256 is never displayed under an MD5 heading.

SHA256 remains in package/manifest metadata and GitHub's `SHA256SUMS.txt` for
integrity verification. The switch back to MD5 affects only the caption. MD5 is a
download checksum, not an authenticity or collision-resistant security guarantee.
No schema change is necessary.

The singular **Image** label and payload list are unchanged. Telegram controls
wrapping; the bot adds no spaces, line breaks, or truncation to the checksum.
