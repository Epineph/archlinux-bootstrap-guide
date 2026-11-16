"""Root filesystem stages; privileged execution is explicit, never implicit.

Private mount and PID namespaces contain mounts and child-process lifetimes.
A privileged chroot is not a security sandbox: only run trusted Arch archives
and trusted commands.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import posixpath
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


class RootfsError(ValueError):
  """An unsafe target, invalid archive, or unmet stage prerequisite."""


MARKER = ".arch-bootstrap-managed.json"
PREFIX = "root.x86_64"


def _print_command(argv):
  print("+ " + shlex.join([str(part) for part in argv]), flush=True)


def _command_env():
  env = os.environ.copy()
  for name in ("TAR_OPTIONS", "BASH_ENV", "ENV", "ZSTD_CLEVEL", "ZSTD_NBTHREADS"):
    env.pop(name, None)
  return env


def _run(argv, *, execute, input_text=None, outline=None, verbose=False):
  if outline is not None and not verbose:
    print("Command outline (use --verbose for the full script arguments):")
    _print_command(outline)
  else:
    _print_command(argv)
  if execute:
    subprocess.run(
      argv, input=input_text, text=True, check=True, env=_command_env()
    )


def _require_tools(*names):
  missing = [name for name in names if not shutil.which(name)]
  if missing:
    raise RootfsError("Missing commands: " + ", ".join(missing))


def _require_host():
  if platform.system() != "Linux" or platform.machine() not in (
    "x86_64",
    "amd64",
  ):
    raise RootfsError("Execution requires an x86_64 Linux host (WSL 2 works).")
  if os.geteuid() != 0:
    raise RootfsError(
      "This stage requires root; review, then use sudo --execute."
    )


def _absolute(path):
  return Path(os.path.abspath(os.path.expanduser(str(path))))


def _no_symlink_components(path):
  for part in (path, *path.parents):
    if part.is_symlink():
      raise RootfsError(f"Refusing symlink path component: {part}")


def _safe_root(value):
  root = _absolute(value)
  _no_symlink_components(root)
  if any(character in str(root) for character in ("'", "\n", "\r", "\x00")):
    raise RootfsError("Root path must not contain quotes or control characters.")
  protected = ("/dev", "/proc", "/sys", "/etc", "/usr", "/bin", "/sbin")
  if any(root == Path(p) or root.is_relative_to(p) for p in protected):
    raise RootfsError(f"Refusing protected root target: {root}")
  if (
    len(root.parts) < 3 or root == Path.home() or Path.cwd().is_relative_to(root)
  ):
    raise RootfsError("Choose a dedicated root directory, not a host directory.")
  if re.match(r"^/mnt/[a-zA-Z](?:/|$)", str(root)):
    raise RootfsError("Keep the root on Linux storage, not a WSL Windows drive.")
  if root.exists() and not root.is_dir():
    raise RootfsError(f"Root target is not a directory: {root}")
  return root


def _mount_paths():
  """Read mountinfo directly, including mountpoint escape sequences."""
  mountinfo = Path("/proc/self/mountinfo")
  if not mountinfo.exists():
    raise RootfsError("Cannot inspect Linux mounts through /proc/self/mountinfo.")
  result = []
  for line in mountinfo.read_text().splitlines():
    fields = line.split()
    if len(fields) < 6:
      continue
    decoded = re.sub(
      r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), fields[4]
    )
    result.append(Path(decoded))
  return result


def _require_quiescent(root):
  for mount in _mount_paths():
    if mount == root or mount.is_relative_to(root):
      raise RootfsError(f"Unmount the filesystem under the root first: {mount}")
  # This catches accessible chroot processes. The operator must also stop
  # external writers; no unlocked filesystem snapshot can guarantee silence.
  for entry in Path("/proc").iterdir():
    if not entry.name.isdigit():
      continue
    try:
      process_root = Path(os.readlink(entry / "root"))
    except OSError:
      continue
    if process_root == root or process_root.is_relative_to(root):
      raise RootfsError(f"Stop process {entry.name} using this root first.")


def _read_marker(root, *, configured=False):
  marker = root / MARKER
  if marker.is_symlink() or not marker.is_file():
    raise RootfsError(f"Not a helper-managed root: {root}; run extract first.")
  try:
    data = json.loads(marker.read_text())
  except (OSError, ValueError) as exc:
    raise RootfsError(f"Invalid root marker: {marker}") from exc
  if not isinstance(data, dict) or data.get("format") != 1:
    raise RootfsError(f"Unknown root marker format: {marker}")
  if configured and not data.get("configured"):
    raise RootfsError("Run configure before exporting a WSL root filesystem.")
  return data


def _write_marker(root, data):
  marker = root / MARKER
  if marker.is_symlink():
    raise RootfsError("Root marker must not be a symlink.")
  temporary = root / (MARKER + ".tmp")
  with temporary.open("x") as stream:
    json.dump(data, stream, indent=2)
    stream.write("\n")
  os.replace(temporary, marker)


def _archive_name(name):
  path = PurePosixPath(name)
  if path.is_absolute() or ".." in path.parts:
    raise RootfsError(f"Unsafe archive path: {name!r}")
  parts = path.parts
  if not parts or parts[0] != PREFIX:
    raise RootfsError(f"Expected only the {PREFIX}/ archive wrapper: {name!r}")
  return path


def inspect_archive(archive, *, max_bytes=8 * 1024**3):
  """Audit the complete archive before GNU tar writes any extracted file.

  Absolute symlink targets are valid within a future chroot. No archived
  member may be written through a symlink, and hardlinks may only refer to
  regular archived files. Both checks consider all entries, regardless of
  their order. This permits the official /etc/machine-id symlink safely.
  """
  archive = Path(archive)
  process = None
  members = {}
  size = 0
  try:
    if archive.name.endswith(".zst"):
      _require_tools("zstd")
      process = subprocess.Popen(
        ["zstd", "--decompress", "--stdout", "--", str(archive)],
        stdout=subprocess.PIPE,
        env=_command_env(),
      )
      stream = tarfile.open(fileobj=process.stdout, mode="r|")
    else:
      stream = tarfile.open(archive, mode="r|*")
    with stream:
      for member in stream:
        if member.name in ("pkglist.x86_64.txt", "version"):
          if not member.isreg() or member.size > 1024**2:
            raise RootfsError("Invalid bootstrap metadata outside root wrapper.")
          continue
        name = _archive_name(member.name)
        if name in members:
          raise RootfsError(f"Duplicate archive path: {member.name!r}")
        if not (
          member.isdir() or member.isreg() or member.issym() or member.islnk()
        ):
          raise RootfsError(f"Unsupported archive entry type: {member.name!r}")
        if member.islnk():
          _archive_name(member.linkname)
        if member.issym():
          if not member.linkname or "\x00" in member.linkname:
            raise RootfsError(f"Invalid symlink target: {member.name!r}")
          if not member.linkname.startswith("/"):
            target = posixpath.normpath(
              posixpath.join(str(name.parent), member.linkname)
            )
            _archive_name(target)
        members[name] = (member.type, member.linkname)
        size += member.size if member.isreg() else 0
        if size > max_bytes:
          raise RootfsError(
            "Archive exceeds max_extract_gib; review before increasing it."
          )
        if len(members) > 1_000_000:
          raise RootfsError("Archive exceeds the one-million-entry audit limit.")
    if process is not None:
      process.stdout.close()
      if process.wait() != 0:
        raise RootfsError("zstd failed while auditing the archive.")
    for name, (kind, target) in members.items():
      for parent in name.parents:
        if parent in members and members[parent][0] != tarfile.DIRTYPE:
          raise RootfsError(f"Archive member beneath a non-directory: {name}")
      if kind == tarfile.LNKTYPE:
        linked = _archive_name(target)
        seen = {name}
        while linked in members and members[linked][0] == tarfile.LNKTYPE:
          if linked in seen:
            raise RootfsError(f"Cyclic hardlink: {name}")
          seen.add(linked)
          linked = _archive_name(members[linked][1])
        if linked not in members or members[linked][0] not in (
          tarfile.REGTYPE,
          tarfile.AREGTYPE,
        ):
          raise RootfsError(f"Hardlink does not reference a regular file: {name}")
    for essential in ("usr/bin/bash", "usr/bin/arch-chroot", "etc/pacman.conf"):
      if PurePosixPath(PREFIX, essential) not in members:
        raise RootfsError(f"Bootstrap archive lacks {essential}.")
    return size
  except (tarfile.TarError, EOFError) as exc:
    raise RootfsError(f"Cannot read bootstrap archive: {exc}") from exc
  finally:
    if process is not None and process.poll() is None:
      process.stdout.close()
      process.terminate()
      process.wait()


def _safe_child(root, relative):
  child = root / relative
  if not child.is_relative_to(root):
    raise RootfsError(f"Path escapes root: {relative}")
  _no_symlink_components(child)
  return child


def extract_rootfs(opts):
  """Extract a previously verified archive into a fresh dedicated directory."""
  root = _safe_root(opts["root_dir"])
  archive = _absolute(opts["tarball"])
  execute = opts.get("execute", False)
  if root.exists() and any(root.iterdir()):
    raise RootfsError(f"Extraction needs an empty directory: {root}")
  command = [
    "tar",
    "--extract",
    "--file",
    str(archive),
    "--directory",
    str(root),
    "--strip-components=1",
    "--numeric-owner",
    "--same-owner",
    "--same-permissions",
    "--xattrs",
    "--acls",
    "--delay-directory-restore",
  ]
  if not execute:
    print(f"Would audit verified archive and create root: {root}")
    _print_command(command)
    return str(root)
  _require_host()
  _require_tools("tar")
  _require_quiescent(root)
  if archive.is_symlink() or not archive.is_file():
    raise RootfsError(f"Expected a regular archive file: {archive}")
  max_gib = opts.get("max_extract_gib", 8)
  min_free_gib = opts.get("root_min_free_gib", 2)
  if not isinstance(max_gib, int) or max_gib < 1:
    raise RootfsError("max_extract_gib must be a positive integer.")
  if not isinstance(min_free_gib, int) or min_free_gib < 1:
    raise RootfsError("root_min_free_gib must be a positive integer.")
  required = inspect_archive(archive, max_bytes=max_gib * 1024**3)
  parent = root.parent
  while not parent.exists():
    parent = parent.parent
  needed = max(required * 1.2 + 64 * 1024**2, min_free_gib * 1024**3)
  if shutil.disk_usage(parent).free < needed:
    raise RootfsError(
      "Not enough free space for the extracted archive plus overhead."
    )
  root.mkdir(parents=True, exist_ok=True)
  _run(command, execute=True)
  resolver = _safe_child(root, "etc/resolv.conf")
  if not resolver.exists():
    resolver.touch(mode=0o644)
  # Ownership is intentionally preserved by GNU tar, not assigned by names.
  digest = hashlib.sha256()
  with archive.open("rb") as stream:
    for block in iter(lambda: stream.read(1024 * 1024), b""):
      digest.update(block)
  _write_marker(
    root, {"format": 1, "source": str(archive), "sha256": digest.hexdigest()}
  )
  return str(root)


# Positional arguments carry user values; the command body is never assembled
# from configuration. The outer private namespace dies when its children exit.
_NAMESPACE_SCRIPT = """set -euo pipefail
root=$1
shift
mount --bind "$root" "$root"
exec "$root/usr/bin/arch-chroot" "$root" "$@"
"""


def _chroot_command(root, command):
  return [
    "unshare",
    "--mount",
    "--propagation",
    "private",
    "--pid",
    "--fork",
    "--kill-child",
    "--",
    "bash",
    "-c",
    _NAMESPACE_SCRIPT,
    "arch-bootstrap-chroot",
    str(root),
    *command,
  ]


def _validate_managed(root, *, configured=False):
  marker = _read_marker(root, configured=configured)
  for relative in ("usr/bin/bash", "usr/bin/arch-chroot", "usr/lib/os-release"):
    candidate = _safe_child(root, relative)
    if not candidate.is_file():
      raise RootfsError(f"Managed root is missing {relative}.")
  release = (root / "usr/lib/os-release").read_text()
  if not re.search(r'^ID=(?:"arch"|arch)$', release, flags=re.MULTILINE):
    raise RootfsError("Managed root is not Arch Linux.")
  return marker


# arch-chroot initially binds the host /run. Hide it before GnuPG starts so
# homedir-derived socket names cannot select an existing host agent. DNS has
# already been bound to the regular target resolv.conf at this point.
# Stop target daemons before arch-chroot tears down its runtime mounts; the
# outer PID namespace also reaps other descendants and removes this overlay.
_KEYRING_SCRIPT = """mount -t tmpfs -o mode=0755,nosuid,nodev tmpfs /run
function cleanup_keyring_agents() {
  gpgconf --homedir /etc/pacman.d/gnupg --kill all || true
}
trap cleanup_keyring_agents EXIT
pacman-key --init
pacman-key --populate archlinux
pacman -Sy --needed "${confirm[@]}" archlinux-keyring
pacman -Syu --needed "${confirm[@]}" "${packages[@]}"
"""


_CONFIGURE_SCRIPT = (
  """set -euo pipefail
export SYSTEMD_IN_CHROOT=1
hostname=$1
timezone=$2
locale=$3
username=$4
user_shell=$5
wsl=$6
noninteractive=$7
shift 7
package_count=$1
shift
packages=("${@:1:package_count}")
shift "$package_count"
services=("$@")
confirm=()
if [[ $noninteractive == true ]]; then
  confirm=(--noconfirm)
fi
"""
  + _KEYRING_SCRIPT
  + """test -f "/usr/share/zoneinfo/$timezone"
ln -sfn "/usr/share/zoneinfo/$timezone" /etc/localtime
printf '%s\\n' "$hostname" > /etc/hostname
if ! grep -Fxq "$locale UTF-8" /etc/locale.gen; then
  printf '%s UTF-8\\n' "$locale" >> /etc/locale.gen
fi
locale-gen
printf 'LANG=%s\\n' "$locale" > /etc/locale.conf
if [[ -n $username ]]; then
  test -x "$user_shell"
  if id "$username" >/dev/null 2>&1; then
    test "$(id -u "$username")" -ge 1000
    usermod -aG wheel -s "$user_shell" "$username"
  else
    useradd -m -G wheel -s "$user_shell" "$username"
  fi
  install -d -m 0750 /etc/sudoers.d
  printf '%%wheel ALL=(ALL:ALL) ALL\\n' > /etc/sudoers.d/10-bootstrap-wheel
  chmod 0440 /etc/sudoers.d/10-bootstrap-wheel
  visudo -cf /etc/sudoers
fi
if [[ $wsl == true ]]; then
  printf '[boot]\\nsystemd=true\\n' > /etc/wsl.conf
  if [[ -n $username ]]; then
    printf '\\n[user]\\ndefault=%s\\n' "$username" >> /etc/wsl.conf
  fi
fi
if (( ${#services[@]} )); then
  systemctl enable -- "${services[@]}"
fi
"""
)


def _configuration(opts):
  hostname = opts.get("hostname", "arch-bootstrap")
  timezone = opts.get("timezone", "UTC")
  locale = opts.get("locale", "en_US.UTF-8")
  username = opts.get("username", "") or ""
  shell = opts.get("user_shell", "/bin/bash")
  mirror = opts.get("mirror", "https://geo.mirror.pkgbuild.com/$repo/os/$arch")
  if not re.fullmatch(
    r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]{0,61}[a-zA-Z0-9])?", hostname
  ):
    raise RootfsError(
      "Hostname must contain 1-63 letters, digits, dots or hyphens."
    )
  if not re.fullmatch(r"[a-zA-Z0-9_+/-]+", timezone) or ".." in timezone:
    raise RootfsError(
      "Invalid timezone; use UTC or an IANA name such as Europe/Copenhagen."
    )
  if timezone.startswith("/"):
    raise RootfsError("Timezone must be relative to /usr/share/zoneinfo.")
  if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_@.-]*\.UTF-8", locale):
    raise RootfsError("Locale must be a UTF-8 locale, for example en_US.UTF-8.")
  if username and not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", username):
    raise RootfsError("Username must be a conventional Linux account name.")
  if username == "root":
    raise RootfsError("The optional username must be a regular user, not root.")
  if not re.fullmatch(r"/[a-zA-Z0-9_./+-]+", shell) or ".." in shell:
    raise RootfsError("User shell must be an absolute executable path.")
  if not mirror.startswith("https://") or any(c.isspace() for c in mirror):
    raise RootfsError("Mirror must be one HTTPS Server URL without whitespace.")
  if "$repo" not in mirror or "$arch" not in mirror:
    raise RootfsError(
      "Mirror URL must contain literal $repo and $arch placeholders."
    )
  packages = list(dict.fromkeys(["base", "sudo", *opts.get("packages", [])]))
  if opts.get("wsl"):
    packages.append("systemd-sysvcompat")
  for package in packages:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9@._+:-]*", package):
      raise RootfsError(f"Invalid package name: {package!r}")
  services = opts.get("services", [])
  for service in services:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9@_.:-]*", service):
      raise RootfsError(f"Invalid service name: {service!r}")
  args = [hostname, timezone, locale, username, shell]
  args += ["true" if opts.get("wsl") else "false"]
  args += ["true" if opts.get("noninteractive") else "false", str(len(packages))]
  return args + packages + services, mirror


def configure_rootfs(opts):
  execute = opts.get("execute", False)
  if execute and opts.get("offline"):
    raise RootfsError(
      "Configure requires network package updates; --offline prevents execution."
    )
  root = _safe_root(opts["root_dir"])
  args, mirror = _configuration(opts)
  command = _chroot_command(
    root,
    [
      "/bin/bash",
      "-c",
      _CONFIGURE_SCRIPT,
      "arch-bootstrap-configure",
      *args,
    ],
  )
  package_count = int(args[7])
  packages = args[8 : 8 + package_count]
  services = args[8 + package_count :]
  print(f"Configure root: {root}")
  print(f"Hostname: {args[0]}; timezone: {args[1]}; locale: {args[2]}")
  print(f"User: {args[3] or '(none)'}; shell: {args[4]}; WSL: {args[5]}")
  print(f"Packages: {shlex.join(packages)}")
  print(f"Enable services: {shlex.join(services) if services else '(none)'}")
  print(f"Mirror: {mirror}; noninteractive: {args[6]}")
  print("Steps: private /run; initialize keys; full upgrade; write settings.")
  print("Stop target GnuPG agents before leaving the chroot.")
  print("Self-bind root in private mount/PID namespaces; reap children on exit.")
  outline = [
    "unshare",
    "--mount",
    "--propagation",
    "private",
    "--pid",
    "--fork",
    "--kill-child",
    "--",
    str(root / "usr/bin/arch-chroot"),
    str(root),
    "/bin/bash",
    "<configuration script from rootfs.py>",
    *args,
  ]
  if not execute:
    if root.exists():
      _validate_managed(root)
    _run(
      command, execute=False, outline=outline, verbose=opts.get("verbose", False)
    )
    return str(root)
  _require_host()
  _require_tools("unshare", "bash", "mount", "chroot")
  _require_quiescent(root)
  marker = _validate_managed(root)
  mirrorlist = _safe_child(root, "etc/pacman.d/mirrorlist")
  resolver = _safe_child(root, "etc/resolv.conf")
  marker["configured"] = False
  _write_marker(root, marker)
  mirrorlist.write_text(f"Server = {mirror}\n")
  if not resolver.exists():
    resolver.touch(mode=0o644)
  _run(command, execute=True, outline=outline, verbose=opts.get("verbose", False))
  marker.update(configured=True, wsl=bool(opts.get("wsl")))
  _write_marker(root, marker)
  if opts.get("username"):
    print(
      f"Set the user password with enter-chroot -- passwd {opts['username']}."
    )
  return str(root)


def enter_chroot(opts):
  root = _safe_root(opts["root_dir"])
  execute = opts.get("execute", False)
  command = opts.get("chroot_command") or ["/bin/bash"]
  if not isinstance(command, list) or not all(
    isinstance(v, str) for v in command
  ):
    raise RootfsError("Chroot command must be a list of command arguments.")
  argv = _chroot_command(root, command)
  if execute:
    _require_host()
    _require_tools("unshare", "bash", "mount", "chroot")
    _require_quiescent(root)
    _validate_managed(root)
  elif root.exists():
    _validate_managed(root)
  print("Self-bind root in private mount/PID namespaces; reap children on exit.")
  outline = [
    "unshare",
    "--mount",
    "--propagation",
    "private",
    "--pid",
    "--fork",
    "--kill-child",
    "--",
    str(root / "usr/bin/arch-chroot"),
    str(root),
    *command,
  ]
  _run(argv, execute=execute, outline=outline, verbose=opts.get("verbose", False))


def _safe_output(value, root):
  output = _absolute(value)
  _no_symlink_components(output)
  if output == root or output.is_relative_to(root):
    raise RootfsError("Export output must be outside the exported root.")
  if any(
    output == Path(p) or output.is_relative_to(p)
    for p in ("/dev", "/proc", "/sys")
  ):
    raise RootfsError(
      "Output must be a regular file, never a device or virtual path."
    )
  if output.exists():
    raise RootfsError(f"Output already exists; choose a new filename: {output}")
  return output


def export_wsl(opts):
  root = _safe_root(opts["root_dir"])
  output = _safe_output(opts["wsl_output"], root)
  execute = opts.get("execute", False)
  exclusions = [
    "./proc/*",
    "./sys/*",
    "./dev/*",
    "./run/*",
    "./tmp/*",
    "./mnt/*",
    "./media/*",
    "./var/cache/pacman/pkg/*",
    "./var/log/*",
    "./etc/machine-id",
    "./var/lib/dbus/machine-id",
    "./etc/ssh/ssh_host_*",
    "./" + MARKER,
  ]
  command = [
    "tar",
    "--create",
    "--format=pax",
    "--numeric-owner",
    "--xattrs",
    "--acls",
    "--one-file-system",
    "--directory",
    str(root),
  ]
  command += [f"--exclude={pattern}" for pattern in exclusions]
  if not execute:
    if root.exists():
      _validate_managed(root, configured=True)
    print("Export stopped root contents (no root.x86_64 wrapper).")
    _print_command([*command, "--file", str(output), "."])
    return str(output)
  _require_host()
  _require_tools("tar")
  _require_quiescent(root)
  marker = _validate_managed(root, configured=True)
  if not marker.get("wsl"):
    raise RootfsError("Configure with --wsl before using this WSL export helper.")
  output.parent.mkdir(parents=True, exist_ok=True)
  # Work in the final directory so hardlink publication is atomic and refuses
  # to replace any file created while tar was running.
  fd, temporary_name = tempfile.mkstemp(prefix=".wsl-export-", dir=output.parent)
  os.close(fd)
  temporary = Path(temporary_name)
  try:
    _run([*command, "--file", str(temporary), "."], execute=True)
    with temporary.open("rb") as stream:
      os.fsync(stream.fileno())
    os.link(temporary, output)
  finally:
    temporary.unlink(missing_ok=True)
  print(f"WSL tar created: {output}")
  return str(output)
