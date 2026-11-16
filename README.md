# Arch Linux bootstrap guide

A terminal-first guide with independent helpers for an Arch bootstrap root,
a WSL 2 export, and Archiso live images. Start with the
[complete guide](docs/guide.md), which explains every route with manual commands
as well as helper examples.

The helpers preview by default. Add `--execute` to run the reviewed steps.
They never partition or write physical devices; USB writing is documented as a
separate manual operation.

## Start here

Requirements: Linux, Bash, Python 3.11 or newer. Extraction and chroot execution
require an x86_64 Linux host and root. Other tools depend on the selected stage;
see the guide for Arch and Debian/Ubuntu prerequisites. ISO builds require an
Arch Linux build host with Archiso installed.

```bash
./bootstrap.sh doctor
cp -n config/example.json config/local.json
# Edit config/local.json with nvim, nano, or your preferred editor.
./bootstrap.sh run --config config/local.json
```

For a complete bootstrap, after reviewing that preview:

```bash
sudo ./bootstrap.sh run --config config/local.json --execute
sudo ./scripts/enter-chroot.sh --config config/local.json --execute
```

Default stages are fetch, extract, and configure. All relative artifact paths
are resolved from the repository root. Use a dedicated root directory; existing
nonempty extraction targets and existing image outputs are refused.

## Choose an archive

Put official bootstrap tarballs in `tarballs/`. The default uses a cached file;
multiple candidates produce a numbered terminal menu with the newest filename
date first. Noninteractive selection defaults to the newest. Cached archives,
signatures, roots, build trees, and output images are ignored by Git.

```bash
# Explicit file, current official release, or deterministic cache selection:
./scripts/fetch-tarball.sh --tarball /path/to/archive.tar.zst --execute
./scripts/fetch-tarball.sh --download latest --execute
./scripts/fetch-tarball.sh --select newest --offline --execute
```

The default checks the detached signature against a trusted Arch keyring and
the configured release signing fingerprint. A missing signature for a dated
archive is fetched from `archlinux.org`. Non-Arch hosts need a trusted keyring;
follow the [key setup instructions](docs/guide.md#44-establish-a-release-keyring-on-another-distribution).
For offline use, provide both archive and signature. No archive is extracted
without verification.

## Individual stages and optional outputs

| Script | Purpose |
| --- | --- |
| `scripts/fetch-tarball.sh` | Select, download if needed, and verify |
| `scripts/extract-rootfs.sh` | Verify, audit, and extract a fresh managed root |
| `scripts/configure-rootfs.sh` | Packages, mirrors, locale, timezone, user, WSL |
| `scripts/enter-chroot.sh` | Interactive shell or a specified command |
| `scripts/export-wsl.sh` | Export a configured WSL root to a plain `.tar` |
| `scripts/build-iso.sh` | Build a live ISO from an Archiso profile |
| `scripts/append-data.sh` | Add an ext4 data partition to a bootable ISO file |

Every stage accepts the same `--config` and command-line settings. The order is
built-in defaults, JSON `common`, JSON stage section, then CLI overrides.
Inspect effective settings with `./bootstrap.sh show-config --config ...`.

```bash
# Run only selected stages, in the supplied order:
./bootstrap.sh run --steps fetch extract --config config/local.json

# Preview bootstrap + WSL export, with a regular user:
./bootstrap.sh run --with-wsl --username example --config config/local.json

# Preview bootstrap + a separate Archiso live image:
./bootstrap.sh run --with-iso --config config/local.json

# Or build the ISO later, independently:
./scripts/build-iso.sh --iso-packages git neovim

# Preview a writable data area appended to an existing ISO:
./scripts/append-data.sh --input-iso /path/to/archlinux.iso --data-size 8G
```

An Archiso build uses its own package list and optional `--iso-overlay`; it does
not clone the configured bootstrap root. `--iso-include-tarball` carries the
verified bootstrap archive as a file inside the live image. A WSL user starts
with a locked password: set it through the chroot before export, or through
`wsl -d ArchCustom -u root -- passwd example` after import. See the guide for
the exact export/import and customization sequences.

## Validation and limits

Run `make check` for Bash syntax, ShellCheck, shfmt, Ruff, and unit/integration
fixtures. Tests are offline and small; the image test uses a 32 MiB data file
and is skipped if its external tools are absent. No test writes a device,
installs packages, or runs a full Archiso build.

Development validation includes the supplied November 2026 archive's real
signature and read-only archive audit, plus small real GPG, tar export, and
xorriso fixtures. Actual package installation, WSL import, full ISO building,
and BIOS/UEFI boot remain to be tested on the intended target.
