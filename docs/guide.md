# Arch Linux bootstrap guide

This repository prepares an Arch Linux environment from an official bootstrap
tarball, with a terminal-only workflow. It can also export a prepared root
filesystem for WSL 2, build an Archiso live ISO, and append a separate data
filesystem to a compatible ISO. The helper scripts never select, partition,
format, or write a physical storage device.

The manual instructions below are independent of the helpers. Read the commands
for the route you intend to use before running them. Commands in a `bash` block
are for Bash, including when your normal interactive shell is Zsh. Commands in a
`powershell` block are for Windows PowerShell.

## 1. Choose the result you need

| Result | Route | What you receive |
| --- | --- | --- |
| Arch tools on an existing Linux host | Fetch, extract, configure, chroot | An Arch directory tree in `work/` |
| Install Arch on prepared storage | Bootstrap, then manual installation | A system on storage you explicitly mounted |
| Custom Arch distribution for WSL 2 | Configure, export, Windows import | A root filesystem tar archive |
| Bootable rescue/install medium | Build an Archiso profile | An `.iso` file in `out/` |
| Live medium with writable file storage | Append data to a suitable ISO | A hybrid `.img` file in `out/` |

A bootstrap archive is an environment for running Arch tools. It is not a live
ISO, and merely putting its files in an ISO does not supply a boot loader, kernel,
initramfs, or live-root setup. The ISO route builds these through an Archiso
profile. The extracted bootstrap root and the ISO profile are separate products.
[Arch bootstrap overview](https://wiki.archlinux.org/title/Install_Arch_Linux_from_existing_Linux),
[Archiso project](https://github.com/archlinux/archiso).

For a standard Arch installation in WSL, the official download page currently
offers this shorter route from Windows:

```powershell
wsl --install archlinux
```

The export/import route in section 7 is useful when you want to prepare your own
package set and configuration, or move a prepared environment between machines.
[Official Arch downloads](https://archlinux.org/download/).

## 2. Confirm the host and prepare tools

### 2.1 Host, filesystem, space, and network

Run these read-only checks first:

```bash
bash
uname -srm
cat /etc/os-release
pwd -P
df -h .
findmnt -T .
date -u
```

The bootstrap execution route here requires an `x86_64` Linux host capable of
running the archive's programs. WSL 2 on an x86-64 Windows machine is suitable
for the root filesystem route. ARM Windows/ARM Linux is outside this guide's
native-execution scope. WSL 1 and restrictive containers may lack the required
mount or namespace facilities.

The helpers require **Python 3.11 or newer** and Bash. Check `python3 --version`
after installing prerequisites, particularly on older Debian/Ubuntu releases.

In WSL, keep the working root under the distribution's Linux filesystem, for
example `~/archlinux-bootstrap-guide`, rather than under `/mnt/c`. Linux file
ownership, symlinks, permissions, and package operations need native filesystem
semantics. You can copy the finished export to a Windows drive afterward.

Budget for the compressed download, extracted files, package cache, and output
archives. An ISO build needs substantially more space than the bootstrap tarball.
Check available capacity before each large operation; a sparse data image can
consume its full logical size later. No test or helper deliberately fills your
disk to discover its capacity.

A working connection and accurate host clock are needed for HTTPS downloads and
signature validation. A chroot shares the host's kernel and usually its network;
it does not provide isolation from a hostile root user or downloaded executable.
Do not run untrusted customization scripts as root.

### 2.2 Arch host packages

These are installation commands for an Arch host, not commands the helpers run
automatically. First review the package transaction:

```bash
sudo pacman -Syu --needed bash python curl gnupg tar zstd util-linux
```

For live ISO creation and regular-file data images, add:

```bash
sudo pacman -S --needed archiso libisoburn e2fsprogs
```

The Arch package providing `xorriso` is `libisoburn`. Optional tools are `pv` for
transfer progress, and QEMU/OVMF for virtual boot checks. Archiso resolves its
other build dependencies through the package manager. Building images is
officially supported on Arch Linux; use an Arch host or appropriately configured
Arch VM for this route.
[Archiso requirements](https://github.com/archlinux/archiso).

### 2.3 Debian/Ubuntu host, including WSL 2

Confirm `ID`/`ID_LIKE` in `/etc/os-release` before using this package-manager
example:

```bash
sudo apt-get update
sudo apt-get install \
  bash python3 curl ca-certificates gnupg tar zstd util-linux
```

For the optional appended-data image route:

```bash
sudo apt-get install xorriso e2fsprogs
```

The bootstrap provides its own Arch executables; you do not need to replace the
host's package manager with pacman. A Debian host can prepare an Arch root and
export it for WSL. The ISO helper requires an Arch build host rather than trying
to install Archiso into Debian.

## 3. Helper scripts and shared configuration

Run helper commands from the repository root. Preview is the default: it does
not download files, extract archives, mount filesystems, or install packages.
Add `--execute` only after reviewing the selected paths and actions. Root
operations require invoking the relevant helper with `sudo`; fetching an archive
normally does not.

### 3.1 Tarball selection

The selection rules are:

1. `--tarball PATH` selects that archive explicitly.
2. `--download latest` or `--download HTTPS_URL` selects a download explicitly.
3. Otherwise, the helper looks in `tarballs/`. One candidate is selected
   automatically. With several candidates, a terminal session offers a numbered
   choice, with the newest dated official filename as the default.
4. With no cached candidate, execution discovers the current release and
   downloads it. A preview only describes this operation.

`--tarball` and `--download` are mutually exclusive. Date ordering comes from
official-style filenames, not modification times. An archive named
`archlinux-bootstrap-YYYY.MM.DD-x86_64.tar.zst` can therefore be copied from
another machine without becoming the wrong default. Undated official-style
names rank below dated archives. The scan is limited to supported bootstrap
archive names; an unrelated backup tarball is not a candidate.

Use `--select newest` for a deterministic choice, `--select N` for a numbered
candidate, or `--select ask` to request a terminal prompt. Without a terminal,
the default is the newest candidate. Use `--offline` to prohibit network access,
including downloading a missing signature. Configuration and ISO builds need
package downloads, so these stages reject `--offline` before making changes.

```bash
./scripts/fetch-tarball.sh
./scripts/fetch-tarball.sh --select newest --execute
./scripts/fetch-tarball.sh --tarball /path/to/bootstrap.tar.zst --execute
./scripts/fetch-tarball.sh --download latest --execute
```

The cache takes precedence over the network by default, even if a newer release
exists online. `--download latest` explicitly checks the current release and
avoids downloading its archive again when the corresponding cached file can be
verified. Discovery uses Arch's authoritative release metadata, then reads the
selected release's dated mirror manifest; a stale mirror's `latest/` directory
cannot silently choose an older release. If that mirror has not synchronized
the announced release, try another current HTTPS ISO mirror with
`--mirror-base`. A local file's presence is not proof of authenticity.

### 3.2 Verification

Signature verification is the default. The helper uses the maintained Arch
keyring on an Arch host, or a trusted exported keyring supplied with `--keyring`.
It imports that public key material into a temporary private GnuPG home, accepts
armored or binary exports, and requires the configured full release signer
fingerprint. Expired or revoked signing material is rejected. A sibling `.sig`
is used when present; the signature for an official dated archive can be obtained
from `archlinux.org`. Downloading a key from an arbitrary server and accepting it
without checking its identity would not establish trust.

The configured fingerprint is a deliberate trust choice. If Arch changes the
release signer, independently verify the new full fingerprint before setting
`--signer-fingerprint`. A verification error is not a reason by itself to accept
a different signer.

If you have independently obtained the expected SHA-256 value through a trusted
channel, you may explicitly use `--verification sha256 --sha256 DIGEST`. A digest
copied from the same untrusted source as the archive only detects accidental
corruption. There is no option to silently skip verification.

For a renamed archive, pass its detached signature explicitly:

```bash
./scripts/fetch-tarball.sh \
  --tarball /path/to/bootstrap.tar.zst \
  --signature /path/to/bootstrap.tar.zst.sig \
  --keyring /path/to/trusted-release-keyring.gpg \
  --offline --execute
```

### 3.3 Master workflow and individual stages

The default master workflow is `fetch`, `extract`, then `configure`. Start by
checking the host and copying the example configuration to an ignored local
file. Edit that copy with your available terminal editor:

```bash
./bootstrap.sh doctor
cp --no-clobber config/example.json config/local.json
./bootstrap.sh show-config --config config/local.json
./bootstrap.sh run --config config/local.json
```

`doctor` reports the host, Python version, available commands, and free space.
It does not install dependencies. `show-config` prints the resolved settings for
each stage, including absolute paths. To inspect full embedded commands in a
preview, add `--verbose`. Ordinary previews keep those details concise.

After reviewing the configuration and preview, execute the default workflow:

```bash
sudo ./bootstrap.sh run --config config/local.json --execute
```

This initializes and configures the root under `work/root.x86_64`. It does not
install onto a disk, create a boot loader on the host, or reboot. Package
transactions remain interactive unless you explicitly add `--noninteractive`.

Each stage is also available as its own script:

| Script | Master stage | Purpose |
| --- | --- | --- |
| `scripts/fetch-tarball.sh` | `fetch` | Select, obtain, and verify an archive |
| `scripts/extract-rootfs.sh` | `extract` | Verify and extract into a fresh managed root |
| `scripts/configure-rootfs.sh` | `configure` | Update packages and configure that root |
| `scripts/enter-chroot.sh` | `chroot` | Enter a managed root or run a specified command |
| `scripts/build-iso.sh` | `iso` | Build an independent Archiso live image |
| `scripts/export-wsl.sh` | `wsl-export` | Export a prepared root as a WSL import tar |
| `scripts/append-data.sh` | `append-data` | Append ext4 storage to a supported ISO layout |

Use `--help` on any script for the complete shared option reference. Fetching
and appending a regular-file data image normally run without root. Extraction,
configuration, chroot entry, WSL export, and the Archiso build require root.

Select only the stages you want. These examples are alternative invocations;
extraction requires a fresh destination, while configuration uses an existing
managed root:

```bash
./bootstrap.sh run --config config/local.json --steps fetch extract
sudo ./bootstrap.sh run --config config/local.json \
  --steps fetch extract --execute
sudo ./bootstrap.sh run --config config/local.json \
  --steps configure --packages neovim openssh --execute
sudo ./scripts/enter-chroot.sh --config config/local.json \
  --execute -- /bin/bash
```

`--steps` takes separate stage arguments in the requested order. It does not
silently rebuild missing prerequisites. `chroot` is an explicit standalone
operation rather than an interactive step in a batch run. Arguments after `--`
in the chroot helper are passed directly as the command and its arguments.

The configured `packages` list, or a `--packages` override, selects additional
packages. `base` and `sudo` are always included. `--packages neovim openssh`
replaces the configured additional list; it still installs those two required
packages. `--services` selects target units to enable for future startup, not
services to start on the host. An empty `--services` clears that configured list.

To add optional outputs to the default workflow, preview one of these:

```bash
./bootstrap.sh run --config config/local.json --with-wsl
./bootstrap.sh run --config config/local.json --with-iso
./bootstrap.sh run --config config/local.json --with-wsl --with-iso
```

Add `sudo` and `--execute` when ready. With the default stages, `--with-wsl`
configures the root for WSL and appends the export stage. `--with-iso` appends an
independent profile-based ISO build. If you customize `--steps`, retain the
configuration stage or prepare the root separately before requesting a WSL
export. Both optional outputs can be produced later by their individual scripts.

New accounts created by `--username` are locked until you set a password. For a
WSL account that is ready before export, use this staged workflow on a fresh
root instead of immediately adding `--with-wsl`:

```bash
sudo ./bootstrap.sh run --config config/local.json \
  --wsl --username archuser --execute
sudo ./scripts/enter-chroot.sh --config config/local.json \
  --execute -- /usr/bin/passwd archuser
sudo ./scripts/export-wsl.sh --config config/local.json \
  --wsl-output out/arch-custom-wsl.tar --execute
```

The password is entered interactively and is not stored in the configuration.
Follow section 7.3 to import the finished tar from Windows. For a previously
configured managed root, use `--steps configure` in the first command instead
of repeating extraction. If you exported a locked account already, enter the
imported distribution as root with `wsl --distribution NAME --user root` and set
its password there.

Preview an independent ISO build or appended-data image with specific settings:

```bash
./scripts/build-iso.sh --config config/local.json \
  --iso-packages git neovim python --iso-label ARCH_RESCUE
./scripts/build-iso.sh --iso-profile profiles/my-releng \
  --iso-overlay overlays/rescue
./scripts/append-data.sh --input-iso out/iso/CHOSEN.iso \
  --data-size 4G --data-label ARCH_DATA \
  --output-image out/arch-rescue-with-data.img
```

The profile and overlay paths in the second example are user-created inputs.
The data-image helper inspects the chosen ISO layout during execution and
requires an unused partition slot; `--partition-number` defaults to `3`.
Use section 9 to understand the boot metadata and layout checks before changing
that number. No output option accepts a physical-device destination.

The master and stage scripts use the same implementation, so a stage can be run
by itself with the same configuration. JSON configuration has a `common` object
and optional stage objects. Precedence is built-in defaults, then `common`, then
the stage's settings, then explicitly supplied command-line arguments. List
overrides replace the configured list; they do not concatenate unpredictably.

Configuration keys use underscores, such as `tarball_dir`; corresponding command
options use hyphens, such as `--tarball-dir`. Configuration contains data, not
shell code. Dollar signs in a mirror URL remain literal. JSON does not support
comments, so keep explanatory notes outside a local JSON configuration.

For example, stage-specific settings can choose different package sets for the
configured root and the live ISO:

```json
{
  "common": {
    "root_dir": "work/my-root",
    "select": "newest"
  },
  "configure": {
    "packages": ["neovim", "openssh"],
    "timezone": "Europe/Copenhagen",
    "hostname": "arch-work"
  },
  "iso": {
    "iso_packages": ["git", "python"],
    "iso_label": "ARCH_RESCUE"
  }
}
```

The JSON section names are the master stage names in the table. The `run`
section controls orchestration through `steps`, `with_iso`, and `with_wsl`.
`--execute` is deliberately a command-line decision and is not stored in JSON.
Boolean options can be overridden explicitly, for example `--no-offline`,
`--no-wsl`, or `--no-with-iso`.

Relative path values are resolved against the repository root, including values
inside a configuration file. The path passed to `--config` itself is resolved
from your current working directory. Prefer absolute paths when sharing a
configuration between checkouts. Do not store passwords or private keys in a
tracked example configuration.

Root extraction, configuration, and export operate on a dedicated managed root.
A manually extracted directory is not automatically treated as a helper-managed
root. Use the manual route throughout if you prefer to manage that directory
yourself. ISO creation is independent of that root; configure its packages and
overlay separately.

### 3.4 Repository artifacts and recovery

`tarballs/`, working root filesystems, downloaded signatures, and generated image
or archive outputs are excluded from Git. Review the actual ignored paths with:

```bash
git status --short
git status --short --ignored
git check-ignore tarballs/archlinux-bootstrap-2026.11.01-x86_64.tar.zst
```

The date in that last command is only an example filename. Existing tracked
files are not retroactively untracked by `.gitignore`; if you previously tracked
an archive, remove it from the index deliberately before publishing the commit.

Helpers refuse to replace existing output files or nonempty build destinations.
If interrupted, inspect the paths and error message before retrying. Reuse a
verified cache archive, but choose fresh extraction/build/output paths for a new
attempt. Inspect mounts before deleting a failed root directory: a recursive
deletion through a mounted path can delete data outside the intended tree.

## 4. Manual download and authentication

### 4.1 Start with an existing tarball if you have one

Set paths from the repository root. Replace the example date with the archive
you actually chose:

```bash
REPO=$(pwd -P)
mkdir -p "$REPO/tarballs" "$REPO/work" "$REPO/out"
ls -lh "$REPO/tarballs"
TARBALL="$REPO/tarballs/archlinux-bootstrap-2026.11.01-x86_64.tar.zst"
test -f "$TARBALL"
```

Do not proceed if the last command fails. Set `TARBALL` to an absolute external
path instead if that is where your archive is stored. Skip the next subsection
when you want to use this existing file.

### 4.2 Discover and download the latest release manually

This sequence reads the current bootstrap signature link on the official
download page. It derives a dated filename, so the archive and signature remain
paired if `latest/` changes during the download. Python here only parses a local
HTML file; no repository helper is involved.

```bash
curl --fail --location --proto '=https' --retry 3 \
  https://archlinux.org/download/ \
  --output "$REPO/work/download-page.html"

SIG_URL=$(python3 - "$REPO/work/download-page.html" <<'PY'
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

class BootstrapLink(HTMLParser):
  def __init__(self):
    super().__init__()
    self.matches = set()

  def handle_starttag(self, tag, attrs):
    if tag != "a":
      return
    href = dict(attrs).get("href", "")
    pattern = (
      r"https://archlinux\.org/iso/(\d{4}\.\d{2}\.\d{2})/"
      r"archlinux-bootstrap-\1-x86_64\.tar\.zst\.sig"
    )
    if re.fullmatch(pattern, href):
      self.matches.add(href)

page = BootstrapLink()
page.feed(Path(sys.argv[1]).read_text())
if len(page.matches) != 1:
  sys.exit("Expected one current bootstrap signature link; inspect the page.")
print(page.matches.pop())
PY
)
test -n "$SIG_URL"
```

Check each command's exit status before continuing. The parser stops if the
official page format changes, rather than selecting an unrelated URL.

```bash
TARBALL_NAME=${SIG_URL##*/}
TARBALL_NAME=${TARBALL_NAME%.sig}
RELEASE=${TARBALL_NAME#archlinux-bootstrap-}
RELEASE=${RELEASE%-x86_64.tar.zst}
TARBALL="$REPO/tarballs/$TARBALL_NAME"
printf 'Release: %s\nArchive: %s\n' "$RELEASE" "$TARBALL"
test ! -e "$TARBALL" && test ! -e "$TARBALL.part"
```

Stop if either file already exists. A completed existing file belongs in the
verification route; an interrupted `.part` should be inspected or renamed before
another attempt. Download the archive from a listed HTTPS mirror and the
signature from the official site:

```bash
curl --fail --location --proto '=https' --retry 3 \
  "https://geo.mirror.pkgbuild.com/iso/$RELEASE/$TARBALL_NAME" \
  --output "$TARBALL.part"
curl --fail --location --proto '=https' --retry 3 \
  "$SIG_URL" --output "$TARBALL.part.sig"
```

Verify `TARBALL.part` against `TARBALL.part.sig` using the next subsection. Only
after successful verification should you publish the completed filenames:

```bash
test ! -e "$TARBALL" && test ! -e "$TARBALL.sig"
mv --no-clobber -- "$TARBALL.part" "$TARBALL"
mv --no-clobber -- "$TARBALL.part.sig" "$TARBALL.sig"
```

The authoritative download page also links release checksums and the signing
identity. Keep the date and file pairing when retrieving a signature for an
older cache archive.
[Download verification information](https://archlinux.org/download/).

### 4.3 Verify using an already trusted Arch keyring

On Arch, ensure the host keyring is maintained through normal system upgrades.
The installed `/usr/share/pacman/keyrings/archlinux.gpg` may contain an armored
public-key export despite its `.gpg` suffix. Passing that file directly to
`gpg --keyring` or `gpgv --keyring` can therefore fail with a packet-format error.
Import the trusted public keys into a dedicated private home first; this accepts
both armored and binary public-key exports:

```bash
VERIFY_DIR=$(mktemp -d "$REPO/work/verify-gnupg.XXXXXX")
gpg --homedir "$VERIFY_DIR" --batch --no-autostart \
  --import /usr/share/pacman/keyrings/archlinux.gpg
gpg --homedir "$VERIFY_DIR" --batch --no-autostart --status-fd 1 \
  --verify "$TARBALL.sig" "$TARBALL" \
  > "$VERIFY_DIR/verification.status"
```

For the in-progress manual download above, substitute `"$TARBALL.part.sig"` and
`"$TARBALL.part"`. Stop if either GnuPG command fails. Read the displayed signer
and inspect the machine-readable status for the expected full fingerprint and
any expiration or revocation warning. This check uses the release fingerprint
verified when this guide was written:

```bash
python3 - "$VERIFY_DIR/verification.status" <<'PY'
import sys
from pathlib import Path

expected = "3E80CA1A8B89F69CBA57D98A76A5EF9054449A5C"
records = [
  line.split()
  for line in Path(sys.argv[1]).read_text().splitlines()
  if line.startswith("[GNUPG:] ")
]
invalid = {
  "BADSIG", "ERRSIG", "REVKEYSIG", "EXPKEYSIG", "EXPSIG",
  "KEYREVOKED", "KEYEXPIRED", "SIGEXPIRED", "NO_PUBKEY",
}
if any(record[1] in invalid for record in records):
  sys.exit("Signature or signing key is invalid, expired, or revoked.")
valid = [record for record in records if record[1] == "VALIDSIG"]
if len(valid) != 1 or expected not in (valid[0][2], valid[0][-1]):
  sys.exit("Expected release signer was not verified.")
print("Signature and expected release signer verified.")
PY
```

The last field can identify the primary key when a signing subkey was used. A
future signer change requires independently checking the new fingerprint before
updating the expected value. The helpers perform these checks automatically.

`--no-autostart` avoids starting GnuPG background agents for this public-key
verification. If you used other GnuPG operations in the temporary home, stop any
agents associated with it when finished:

```bash
gpgconf --homedir "$VERIFY_DIR" --kill all
```

The directory remains under `work/` for inspection; remove only that exact
temporary directory after you no longer need its key database and status log.

For a dated cached archive missing its signature, retrieve the matching
`https://archlinux.org/iso/DATE/FILENAME.sig`, using the exact date and basename
from your archive. Do not pair an old tarball with today's signature.

### 4.4 Establish a release keyring on another distribution

The current official download page identifies the release signer and provides
the full fingerprint link. At the documentation check date, the signer was
Pierre Schmitz and the fingerprint was
`3E80CA1A8B89F69CBA57D98A76A5EF9054449A5C`. Recheck the official information if
signers change; do not change a fingerprint just to silence a verification
failure.

Use a separate key directory for this task:

```bash
install -d -m 0700 "$REPO/work/release-gnupg"
gpg --homedir "$REPO/work/release-gnupg" \
  --auto-key-locate clear,wkd \
  --locate-external-key pierre@archlinux.org
gpg --homedir "$REPO/work/release-gnupg" \
  --with-fingerprint --list-keys pierre@archlinux.org
```

Compare the full fingerprint with the official page before using the key.
Then export a keyring file for repeated verification:

```bash
gpg --homedir "$REPO/work/release-gnupg" \
  --output "$REPO/work/release-keyring.gpg" \
  --export 3E80CA1A8B89F69CBA57D98A76A5EF9054449A5C
gpg --homedir "$REPO/work/release-gnupg" \
  --batch --no-autostart --status-fd 1 \
  --verify "$TARBALL.sig" "$TARBALL" \
  > "$REPO/work/release-gnupg/verification.status"
gpgconf --homedir "$REPO/work/release-gnupg" --kill all
```

Use the `.part` filenames instead if completing section 4.2. A correct signature
with a key you have not authenticated is not sufficient evidence of origin.
Check the verification status with the Python fingerprint/expiration check in
section 4.3, passing `"$REPO/work/release-gnupg/verification.status"` as its input.
The exported `release-keyring.gpg` can then be supplied to the helpers with
`--keyring`. Stopping this home's agents also stops the WKD lookup's dirmngr, if
it was started. The key lookup follows the current signer information on the
[official downloads page](https://archlinux.org/download/).

## 5. Manual extraction and usable bootstrap environment

### 5.1 Extract to a fresh Linux directory

Inspect the archive listing without executing anything inside it:

```bash
tar --list --file "$TARBALL" | sed -n '1,25p'
```

Official bootstrap archives contain a `root.x86_64/` directory. Choose a new
parent directory and extract as root to preserve numeric owners, modes, and
links. Do not extract an unauthenticated archive with elevated privileges.

```bash
MANUAL_WORK="$REPO/work/manual-bootstrap"
test ! -e "$MANUAL_WORK"
mkdir -- "$MANUAL_WORK"
sudo tar --extract --file "$TARBALL" \
  --numeric-owner --preserve-permissions --directory "$MANUAL_WORK"
ROOT="$MANUAL_WORK/root.x86_64"
test -x "$ROOT/usr/bin/bash"
test -x "$ROOT/usr/bin/arch-chroot"
```

Stop on failure. Extracting unprivileged and later running `chown -R root` is not
equivalent: files owned by other system accounts would lose their intended UID
and GID. The bootstrap-specific layout and ownership requirement are documented
in [Arch's existing-Linux installation guide](https://wiki.archlinux.org/title/Install_Arch_Linux_from_existing_Linux).

### 5.2 Select mirrors and provide DNS

The bootstrap's mirrorlist may contain only commented examples. Save the
original and provide a usable HTTPS mirror. Single quotes preserve pacman's
literal `$repo` and `$arch` placeholders:

```bash
sudo cp --no-clobber -- "$ROOT/etc/pacman.d/mirrorlist" \
  "$ROOT/etc/pacman.d/mirrorlist.bootstrap-original"
printf '%s\n' \
  'Server = https://geo.mirror.pkgbuild.com/$repo/os/$arch' \
  | sudo tee "$ROOT/etc/pacman.d/mirrorlist" >/dev/null
```

Use another current HTTPS Arch mirror if the selected service is unreachable.
Network conditions vary; a mirror choice is not a permanent performance claim.

Provide a regular resolver file in the new root. Remove only that root's existing
resolver file or symlink first, so copying does not follow an absolute symlink
outside the root:

```bash
sudo rm -f -- "$ROOT/etc/resolv.conf"
sudo install -m 0644 /etc/resolv.conf "$ROOT/etc/resolv.conf"
```

The chroot entry command also makes the host resolver available during its
lifetime. A loopback resolver can work because this mount namespace does not
create a separate network namespace. In a host with unusual DNS isolation,
review the reachable resolver addresses explicitly.

### 5.3 Enter using a private mount namespace

This command self-binds the new root to make it a mountpoint, then invokes the
bootstrap's `arch-chroot`. The positional argument avoids shell interpolation of
the path into executable command text:

```bash
sudo unshare --mount --propagation private --pid --fork --kill-child \
  bash -c '
    set -e
    root=$1
    mount --bind "$root" "$root"
    exec "$root/usr/bin/arch-chroot" "$root" /bin/bash
  ' bash "$ROOT"
```

`arch-chroot` prepares API filesystems and resolver access. The private mount
namespace keeps these mount changes from propagating to the host namespace. The
PID namespace gives the session its own process lifetime; child processes are
terminated when its initial process exits, and `--kill-child` also handles the
outer `unshare` process terminating. The helpers use this lifecycle as well.
Before leaving with `exit`, stop target GnuPG agents as described below and do
not leave other background processes running in the root. These mechanisms
manage mounts and processes; a root chroot remains able to affect host devices.
[arch-chroot manual](https://man.archlinux.org/man/arch-chroot.8).

### 5.4 Initialize trust and install the required tools

You are now root **inside the bootstrap**. Confirm that before changing files:

```bash
cat /etc/os-release
pwd
pacman-key --init
pacman-key --populate archlinux
pacman -Syu --needed base arch-install-scripts
```

The initial keyring population uses the key material included in the verified
archive. Keep signature checking enabled. A full `-Syu` refreshes repository
metadata and upgrades installed packages together; avoid turning a routine
setup into a partial upgrade with `pacman -Sy` followed by unrelated installs.
[pacman manual](https://man.archlinux.org/man/pacman.8).

Additional packages depend on the purpose. For an interactive rescue environment
you might install `neovim`, `less`, `openssh`, `rsync`, or filesystem tools. An SSH
client is useful without enabling an SSH server. Filesystem, disk, and network
commands invoked manually in a chroot can still affect the host.

If an old archive cannot authenticate current packages, check the clock and
prefer a current verified bootstrap. Do not disable signature validation or
blindly import a key named by an error message. Keep the error output for
diagnosis.

`pacman-key` may start GnuPG agents that otherwise outlive your shell and retain
access to the chroot or its namespace. Before leaving any manually entered
bootstrap/target shell, stop the agents for that target keyring:

```bash
gpgconf --homedir /etc/pacman.d/gnupg --kill all
```

Run this inside the relevant root, then exit when finished. Also stop any other
background commands you started. The configure helper performs target-agent
cleanup on exit; an interactive shell still needs deliberate cleanup before
export or deletion.

## 6. Continue to an installation on already prepared storage

This section is optional and describes real installation writes. It is not run
by the helpers. It assumes you have already chosen, partitioned, and mounted the
intended target storage under `/mnt` on the host, including any separate `/boot`
or EFI system partition. Review the official installation guide for your actual
firmware, encryption, filesystem, swap, and boot-loader arrangement.
[Arch installation guide](https://wiki.archlinux.org/title/Installation_guide).

Do not use this route to replace your currently running root filesystem. Keep
the bootstrap on storage that will remain accessible. On an existing Arch host,
you can normally run `pacstrap` directly after installing `arch-install-scripts`;
the extra bootstrap is primarily useful on another distribution.

### 6.1 Expose the chosen target to the bootstrap

Exit the previous chroot first. On the host, verify the target:

```bash
lsblk --output NAME,PATH,SIZE,MODEL,FSTYPE,LABEL,MOUNTPOINTS
findmnt --recursive /mnt
mountpoint -q /mnt
```

Stop if `/mnt` is not the intended mounted installation target. In WSL, `/mnt`
often holds Windows drives, so it must not be assumed to be an installation
target. Bind the explicitly reviewed hierarchy into the bootstrap in a fresh
private namespace:

```bash
sudo unshare --mount --propagation private --pid --fork --kill-child \
  bash -c '
    set -e
    root=$1
    target=$2
    mount --bind "$root" "$root"
    mount --rbind "$target" "$root/mnt"
    mount --make-rslave "$root/mnt"
    exec "$root/usr/bin/arch-chroot" "$root" /bin/bash
  ' bash "$ROOT" /mnt
```

### 6.2 Install a package set and inspect fstab

These commands are inside the bootstrap. The `/mnt` below is the target, not
the bootstrap itself. The example is for a conventional hardware installation;
adjust firmware and microcode packages to the actual machine:

```bash
findmnt --recursive /mnt
pacstrap -K /mnt base linux linux-firmware \
  networkmanager sudo neovim
genfstab -U /mnt > /mnt/etc/fstab.candidate
cat /mnt/etc/fstab.candidate
```

`pacstrap -K` initializes a target keyring. Review `pacman.conf` and hooks in the
bootstrap if you customized them, because they influence package installation.
[pacstrap manual](https://man.archlinux.org/man/pacstrap.8).

`genfstab` derives entries from mounted filesystems. Check every UUID, mountpoint,
filesystem type, and mount option, especially a separate `/boot`. Install the
reviewed candidate rather than repeatedly appending duplicate entries:

```bash
cp --no-clobber /mnt/etc/fstab /mnt/etc/fstab.before-bootstrap
install -m 0644 /mnt/etc/fstab.candidate /mnt/etc/fstab
arch-chroot /mnt /bin/bash
```

The new shell is inside the installed target. The candidate includes only what
was mounted when it was generated; it cannot infer your intended missing mounts.
[genfstab manual](https://man.archlinux.org/man/genfstab.8).

### 6.3 Configure the installed system

Choose the locale, timezone, console keymap, and hostname you want. This example
uses English text, Copenhagen time, and a Danish console layout:

```bash
ln -sfn /usr/share/zoneinfo/Europe/Copenhagen /etc/localtime
printf '%s\n' 'en_US.UTF-8 UTF-8' 'da_DK.UTF-8 UTF-8' > /etc/locale.gen
locale-gen
printf '%s\n' 'LANG=en_US.UTF-8' > /etc/locale.conf
printf '%s\n' 'KEYMAP=dk' > /etc/vconsole.conf
printf '%s\n' 'arch-host' > /etc/hostname
passwd
systemctl enable NetworkManager
systemctl enable systemd-timesyncd
```

Choose a network manager appropriate for the installation rather than enabling
multiple competing managers. `systemctl enable` configures future startup; it
does not demonstrate a working network in the chroot. Hardware-clock setup, if
needed, should use the time convention shared with any dual-boot operating
system. Do not run `hwclock` for a WSL image.

For an ordinary user and password-protected administrative access:

```bash
useradd --create-home --groups wheel --shell /bin/bash archuser
passwd archuser
printf '%s\n' '%wheel ALL=(ALL:ALL) ALL' > /etc/sudoers.d/10-wheel
chmod 0440 /etc/sudoers.d/10-wheel
visudo --check
```

Replace `archuser` before running the commands. If you use encryption, LVM, RAID,
or a special root filesystem, configure the corresponding initramfs hooks and
kernel parameters before boot-loader setup. After an initramfs configuration
change:

```bash
mkinitcpio -P
```

### 6.4 A deliberately narrow UEFI boot example

The following example applies only to a plain, unencrypted root filesystem,
an x86-64 UEFI machine, and an EFI system partition already mounted at `/boot`.
The kernel and initramfs must be on that partition. It does not cover BIOS boot,
encrypted root, a separate XBOOTLDR partition, Secure Boot enrollment, or Btrfs
subvolume arguments. Use the dedicated boot-loader documentation for those
arrangements.

Inspect the mounts and files before installing:

```bash
findmnt /boot
ls -l /boot/vmlinuz-linux /boot/initramfs-linux.img
test -d /sys/firmware/efi
ROOT_UUID=$(findmnt --noheadings --output UUID /)
test -n "$ROOT_UUID"
```

`bootctl install` writes the target EFI partition and may update firmware boot
variables. Run it only after confirming that this is the intended target:

```bash
bootctl --esp-path=/boot install
install -d /boot/loader/entries
printf '%s\n' 'default arch.conf' 'timeout 4' > /boot/loader/loader.conf
printf '%s\n' \
  'title Arch Linux' \
  'linux /vmlinuz-linux' \
  'initrd /initramfs-linux.img' \
  "options root=UUID=$ROOT_UUID rw" \
  > /boot/loader/entries/arch.conf
bootctl --esp-path=/boot list
```

Review microcode handling for the selected CPU and current initramfs setup.
Do not assume a boot menu listing proves the system will boot.
[bootctl manual](https://man.archlinux.org/man/bootctl.1),
[systemd-boot configuration](https://man.archlinux.org/man/loader.conf.5).

Stop each target's GnuPG agents as in section 5.4 before exiting its shell, then
exit both nested shells. Review mounts on the host and unmount only the intended
installation target when no longer in use. Reboot deliberately after checking
fstab, boot files, credentials, and network configuration; the helpers never
reboot the host.

## 7. Prepare, export, and import an Arch WSL 2 root

### 7.1 Configure the root inside the bootstrap

Use a dedicated root for WSL. Enter it with section 5.3, then install a useful
base and the systemd compatibility package that provides `/sbin/init`:

```bash
pacman-key --init
pacman-key --populate archlinux
pacman -Syu --needed base systemd systemd-sysvcompat sudo
```

WSL 2 supplies its Linux kernel. This root filesystem does not need a PC boot
loader, an EFI partition, or the Arch `linux` package. It also does not need the
host's disk UUIDs or a generated hardware fstab. Configure a locale and timezone
as in section 6.3 if desired.

Create a user, set its password interactively, and configure sudo:

```bash
useradd --create-home --groups wheel --shell /bin/bash archuser
passwd archuser
printf '%s\n' '%wheel ALL=(ALL:ALL) ALL' > /etc/sudoers.d/10-wheel
chmod 0440 /etc/sudoers.d/10-wheel
visudo --check
```

The account named in `wsl.conf` must already exist. For systemd and normal WSL
resolver generation, create this configuration in the prepared root:

```bash
cat > /etc/wsl.conf <<'EOF'
[boot]
systemd=true

[network]
generateResolvConf=true

[user]
default=archuser
EOF
```

Current WSL supports enabling systemd per distribution; check `wsl --version`
from Windows and update WSL if the required support is absent. The configuration
is `/etc/wsl.conf` inside the distribution, not the Windows-wide `.wslconfig`.
[Microsoft systemd guidance](https://learn.microsoft.com/en-us/windows/wsl/systemd),
[WSL configuration](https://learn.microsoft.com/en-us/windows/wsl/wsl-config).

### 7.2 Export the directory contents

Before exiting the chroot, run
`gpgconf --homedir /etc/pacman.d/gnupg --kill all` there. Exit and stop any other
processes using this root. Inspect the host's mount table and confirm that no API
filesystems or other mounts remain underneath `$ROOT`. A filesystem boundary
option alone is insufficient for same-filesystem bind mounts.

```bash
findmnt --list --output TARGET,SOURCE,FSTYPE
WSL_TAR="$REPO/out/arch-custom-wsl.tar"
test ! -e "$WSL_TAR"
```

Create an uncompressed tar archive with numeric ownership and metadata. Archive
the contents of the root, not the enclosing `root.x86_64` directory. The filters
retain empty mountpoint directories while omitting runtime contents, package
cache, resolver configuration, and per-machine identity material:

```bash
sudo tar --create --file "$WSL_TAR" \
  --directory "$ROOT" --numeric-owner --acls --xattrs \
  --one-file-system \
  --exclude='./dev/*' --exclude='./proc/*' --exclude='./sys/*' \
  --exclude='./run/*' --exclude='./tmp/*' --exclude='./mnt/*' \
  --exclude='./media/*' --exclude='./var/cache/pacman/pkg/*' \
  --exclude='./etc/resolv.conf' --exclude='./etc/machine-id' \
  --exclude='./var/lib/dbus/machine-id' \
  --exclude='./var/lib/systemd/random-seed' \
  --exclude='./etc/ssh/ssh_host_*' \
  .
sudo chmod 0600 "$WSL_TAR"
```

Review the members and create a transport checksum:

```bash
sudo tar --list --file "$WSL_TAR" | sed -n '1,30p'
sudo sha256sum "$WSL_TAR"
```

The first entries should resemble `./etc/`, `./usr/`, and `./var/`, with no
`root.x86_64/` prefix. Treat the export as private: it may contain accounts,
password hashes, configuration, and any personal files you added. Preserve its
checksum when copying it between machines.

### 7.3 Copy to Windows and import

From the existing WSL distribution, choose an actual Windows user path and copy
only the completed archive. This is an ordinary file copy, not a device write:

```bash
sudo cp --no-clobber -- "$WSL_TAR" \
  /mnt/c/Users/YOUR_WINDOWS_USER/Downloads/arch-custom-wsl.tar
```

Then open Windows PowerShell. Choose a new distribution name and a dedicated
installation directory with enough space:

```powershell
wsl --version
wsl --list --verbose
New-Item -ItemType Directory -Path C:\WSL\ArchCustom
wsl --import ArchCustom C:\WSL\ArchCustom `
  "$env:USERPROFILE\Downloads\arch-custom-wsl.tar" --version 2
wsl --list --verbose
wsl --distribution ArchCustom
```

Inside the imported Arch distribution, check the user, DNS, package manager, and
init process:

```bash
id
cat /etc/os-release
getent hosts archlinux.org
ps -p 1 -o comm=
sudo pacman -Syu
```

Imported distributions otherwise default to root; the prepared `wsl.conf` sets
the desired default user. If a correction is needed, start it explicitly as root
from PowerShell:

```powershell
wsl --distribution ArchCustom --user root
```

After changing `wsl.conf`, save work, exit the distribution, and restart it:

```powershell
wsl --terminate ArchCustom
wsl --distribution ArchCustom
```

Termination stops that distribution's running processes. Do not use
`wsl --unregister` as a restart command: unregistering deletes the installed
distribution. The import behavior and restart workflow are described in
[Microsoft's custom distribution guide](https://learn.microsoft.com/en-us/windows/wsl/use-custom-distro).

### 7.4 Export an already installed WSL distribution

When the root is already registered in WSL, use Windows' native export rather
than tarring a running filesystem from inside it. Save work and stop the selected
distribution first:

```powershell
wsl --terminate ArchCustom
wsl --export ArchCustom C:\Backups\arch-custom-backup.tar
```

Choose a new output filename and an existing backup directory. Import that
archive under a new name with `wsl --import ... --version 2` when required.
`wsl --export` and this repository's directory-export helper solve different
starting situations.
[WSL command reference](https://learn.microsoft.com/en-us/windows/wsl/basic-commands).

## 8. Build a live ISO with Archiso

### 8.1 Create a custom profile

Use an Arch build host with the dependencies from section 2.2. Start with a fresh
copy of the installed `releng` profile:

```bash
PROFILE="$REPO/work/manual-releng"
ISO_WORK="$REPO/work/manual-iso-build"
ISO_OUT="$REPO/out/manual-iso"
test ! -e "$PROFILE" && test ! -e "$ISO_WORK" && test ! -e "$ISO_OUT"
cp -a /usr/share/archiso/configs/releng "$PROFILE"
```

The copied profile controls the live system. `packages.x86_64` is the package
list, `airootfs/` is an overlay for files in the live root, and `profiledef.sh`
controls labels, boot modes, permissions, and compression. The profile is
executable configuration, so review a profile obtained from someone else before
building it as root. Keep custom profiles in version control when appropriate.
[Archiso profile reference](https://github.com/archlinux/archiso/blob/master/docs/README.profile.rst).

For example, add your chosen utilities after checking the existing list:

```bash
cat "$PROFILE/packages.x86_64"
printf '%s\n' 'python' 'git' 'neovim' >> "$PROFILE/packages.x86_64"
```

Avoid duplicate package entries and remove packages only after understanding
their role. Copy a configuration file into an overlay path to make it available
in the live environment. Set required executable modes or account ownership in
the profile's `file_permissions` array; copying a file's mode into `airootfs/`
alone is not the complete Archiso permissions mechanism.

You may include the verified bootstrap archive as a payload:

```bash
mkdir -p "$PROFILE/airootfs/root/bootstrap-cache"
cp -- "$TARBALL" "$TARBALL.sig" \
  "$PROFILE/airootfs/root/bootstrap-cache/"
```

This adds the archive's size to the image. It provides an offline bootstrap
starting point, not a full offline Arch package repository. Installing or
upgrading packages still needs a network connection or a separately prepared
package repository/cache with valid signatures.

### 8.2 Build and inspect

The profile's current default boot modes determine its firmware support; the
provided `releng` profile is designed for BIOS and UEFI. Preserve the profile's
boot configuration when making only package or file changes.
[Archiso overview](https://wiki.archlinux.org/title/Archiso).

Build into fresh directories:

```bash
df -h "$REPO"
sudo mkarchiso -v -w "$ISO_WORK" -o "$ISO_OUT" "$PROFILE"
find "$ISO_OUT" -maxdepth 1 -type f -name '*.iso' -print
```

Select the actual resulting ISO path rather than assuming its date:

```bash
ISO="$ISO_OUT/REPLACE_WITH_ACTUAL_FILENAME.iso"
test -f "$ISO"
sha256sum "$ISO"
xorriso -indev "$ISO" \
  -report_el_torito plain -report_system_area plain
```

The build fetches packages and can be resource-intensive. `mkarchiso` does not
provide a general `-j` jobs option; tune the profile's compression settings and
host resources deliberately if required. A completed build and boot metadata
inspection do not prove firmware boot compatibility.
[mkarchiso reference](https://github.com/archlinux/archiso/blob/master/man/mkarchiso.1.rst).

For optional virtual boot checks, install the QEMU/OVMF packages appropriate to
your Arch host and inspect `run_archiso --help`:

```bash
run_archiso -i "$ISO"
run_archiso -b -i "$ISO"
```

The first command exercises the helper's UEFI route and the second BIOS. Run
them separately and shut down the first test before starting the second. For a
TTY-only machine, configure a serial-console capable profile and a suitable QEMU
serial invocation; a graphical default VM window may not be accessible. Actual
target hardware remains a separate boot test.
[Archiso testing instructions](https://github.com/archlinux/archiso#testing).

## 9. Append a separate writable data filesystem

This optional route adapts the xorriso remastering approach to Arch. It creates
regular files in the repository and does not write a USB device. The appended
filesystem is storage for files. It does not automatically make changes to the
live system's root filesystem persistent across reboots.

### 9.1 Check the original image's layout

Use a verified original ISO or your completed build. Preserve it unchanged:

```bash
xorriso -indev "$ISO" \
  -report_el_torito plain -report_system_area plain
fdisk --list "$ISO"
```

Find an unused appended partition slot. The example below uses `3` only if that
slot is free for this specific image; it is not a universal Arch ISO partition
number. Existing MBR, GPT, and El Torito entries describe different aspects of a
hybrid image. Do not discard an EFI boot partition or ask a partition editor to
repair intentional overlap. If the layout is unfamiliar or the helper refuses
it, inspect it before changing a partition number.

### 9.2 Create a bounded regular-file data image

Choose filenames that do not exist, and size the data area independently of any
installed system's swap space:

```bash
DATA_IMG="$REPO/out/arch-usb-data.ext4"
USB_IMG="$REPO/out/arch-with-data.img"
DATA_SIZE='1G'
DATA_PARTITION='3'
test ! -e "$DATA_IMG" && test ! -e "$USB_IMG"
```

Stop if either file exists. `1G` in the GNU tools here means one GiB. Increase it
only after accounting for the original image, data area, alignment, and output
space. A marketed 16 GB device cannot hold an ISO plus 16 GiB of data.

```bash
truncate --size="$DATA_SIZE" "$DATA_IMG"
mkfs.ext4 -m 0 -L ARCH_DATA "$DATA_IMG"
```

`truncate` creates a sparse file. Formatting and writing the combined image can
allocate physical storage; the full image still has to fit on the eventual
destination. This sparse-file technique is not a swapfile recipe.

### 9.3 Replay boot metadata and append

After confirming `DATA_PARTITION` is unused, create a new output image:

```bash
xorriso \
  -indev "$ISO" \
  -outdev "$USB_IMG" \
  -boot_image any replay \
  -append_partition "$DATA_PARTITION" 0x83 "$DATA_IMG" \
  -commit \
  -end
```

`replay` reconstructs boot provisions that xorriso recognizes in the input.
Appending a filesystem is an image modification, so the original ISO's
signature does not authenticate the new output. Keep the original and record a
new output checksum. Recognition and metadata reconstruction still need a real
boot test on the intended firmware.
[xorriso boot and partition options](https://www.gnu.org/software/xorriso/man_1_xorriso.html).

Inspect the result:

```bash
xorriso -indev "$USB_IMG" \
  -report_el_torito plain -report_system_area plain
fdisk --list "$USB_IMG"
stat --format='%n: %s bytes' "$USB_IMG"
sha256sum "$USB_IMG"
```

Compare the original and resulting reports. The required boot entries and
existing EFI filesystem must remain, and the data filesystem must be placed
after the ISO data. Determine its eventual Linux device by the `ARCH_DATA` label,
not by assuming the command's partition slot becomes a particular `/dev/sdXN`.

## 10. Optional manual USB writing and byte verification

No repository helper executes this section. These are examples for a later,
deliberate hardware operation. **Writing an image destroys the selected whole
device's existing partition layout and files.**

### 10.1 Identify and unmount the actual destination

Inspect devices by size, model, transport, filesystem, and mountpoints:

```bash
lsblk --output NAME,PATH,SIZE,MODEL,TRAN,FSTYPE,LABEL,MOUNTPOINTS
```

Set the variable only after identifying the intended physical device.
`/dev/sdX` is a placeholder, not an auto-discovery mechanism:

```bash
USB='/dev/sdX'
IMAGE="$USB_IMG"
lsblk "$USB"
sudo blockdev --getsize64 "$USB"
stat --format='%s' "$IMAGE"
```

Use `IMAGE="$ISO"` for the plain ISO route instead. The device size must be at
least the complete image size. Do not overwrite the running live medium, your
system disk, or the drive holding the source image. Select the whole device, not
a partition such as `/dev/sdX1`. Unmount each mounted partition of the verified
destination individually; an example is:

```bash
sudo umount /dev/sdX1
```

Repeat only for partitions you actually identified. Recheck `lsblk` before the
write.

### 10.2 Choose one transfer command

The standard `dd` route reports transferred bytes and flushes the output before
successful completion:

```bash
sudo dd if="$IMAGE" of="$USB" bs=4M status=progress conv=fsync
sync
```

For an ETA and percentage, `pv` already knows the input size when given the file
directly. In Bash, enable pipeline failure reporting before piping into `dd`:

```bash
set -o pipefail
sudo -v
pv --progress --timer --eta --rate --bytes "$IMAGE" \
  | sudo dd of="$USB" bs=4M iflag=fullblock conv=fsync status=none
sync
```

Run one transfer route, not both. `iflag=fullblock` makes `dd` collect complete
input blocks from the pipe. `conv=fsync` adds final flushing; the progress display
can reach the byte total before that flush finishes. Check the transfer command's
exit status. GNU ddrescue is mainly useful when recovery/retry mapping is needed;
it is not required for a healthy source image and a healthy destination device.

### 10.3 Compare before using the data partition

Compare exactly the image length, before modifying files in an appended data
filesystem:

```bash
sudo cmp --bytes="$(stat --format=%s "$IMAGE")" "$IMAGE" "$USB"
```

No output and a zero exit status mean the compared image bytes match. This
checks the written byte range, not every unused byte on the USB. A checksum or
successful copy alone does not test bootability. After verification, boot-test
the medium and locate any data filesystem by its label.

## 11. Troubleshooting and validation limits

| Symptom | First checks |
| --- | --- |
| No tarball candidates | Check `tarball_dir`, supported names, or use `--tarball` |
| Missing signature offline | Supply the matching `.sig` and trusted keyring |
| Wrong or unknown signer | Check the official release identity and full fingerprint |
| Download interrupted | Inspect the partial file; do not treat it as a verified cache |
| `Exec format error` | Check `uname -m` and the archive architecture |
| Mount or `unshare` permission error | Use root on Linux/WSL 2 with needed capabilities |
| Pacman cannot determine cache mountpoint | Ensure the chroot root was self-bind-mounted |
| Name resolution fails | Inspect host DNS and the chroot's resolver file |
| Package signatures fail | Check the clock, keyring, release age, and exact error |
| WSL opens as root | Check that the default user exists and `wsl.conf` is valid |
| WSL systemd absent | Check WSL version, init packages, config, and restart |
| ISO build refuses an old directory | Use fresh work/output paths and review leftovers |
| ISO boots but changes vanish | Live-root persistence is separate from a data partition |

The repository's automated tests cover argument/configuration handling, safe
selection, rejection of unsafe paths and archives, and fixture-based command
planning. Offline fixtures cannot demonstrate current package resolution, a
successful privileged bootstrap, a complete live ISO build, WSL import, or
physical firmware behavior. See the project context note for the actual checks
performed on this checkout.

Upstream behavior was checked on **2026-11-16**. Arch is rolling-release software;
read current package announcements and upstream documentation when an operation
differs from these examples. Keep your local configuration and package/profile
choices reviewable, and preserve the exact commands and errors when diagnosing
an incomplete run.
