"""Build live ISOs and append data partitions to regular image files.

An Archiso profile is executable shell configuration. Supplying --execute trusts
that profile and its package hooks; this module is not an isolation boundary.
It never accepts a block device as an input or output and never flashes media.
"""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path
import platform
import re
import shlex
import shutil
import stat
import subprocess


class ImageError(ValueError):
  """A rejected image operation, with a user-facing explanation."""


def _path(value: str | Path) -> Path:
  path = Path(os.path.abspath(Path(value).expanduser()))
  for component in (path, *path.parents):
    if component.is_symlink():
      raise ImageError(f"Symlink path is not accepted: {component}")
  if path == Path("/") or path.parts[1:2] in (
    ("dev",),
    ("proc",),
    ("sys",),
    ("run",),
  ):
    raise ImageError(f"Use an ordinary workspace path, not {path}")
  return path


def _fresh(value: str | Path) -> Path:
  path = _path(value)
  if path.exists():
    raise ImageError(f"Refusing to overwrite existing output: {path}")
  return path


def _regular(value: str | Path) -> Path:
  path = _path(value)
  if not path.is_file():
    raise ImageError(f"Expected an existing regular file: {path}")
  return path


def _separate(paths: list[Path]) -> None:
  for index, left in enumerate(paths):
    for right in paths[index + 1 :]:
      if left == right or left in right.parents or right in left.parents:
        raise ImageError(f"Paths must not overlap: {left} and {right}")


def _tools(*names: str) -> None:
  missing = [name for name in names if shutil.which(name) is None]
  if missing:
    raise ImageError("Missing required programs: " + ", ".join(missing))


def _run(argv: list[str], execute: bool, capture: bool = False) -> str:
  print("+ " + shlex.join([str(item) for item in argv]), flush=True)
  if not execute:
    return ""
  result = subprocess.run(
    argv,
    check=True,
    text=True,
    stdout=subprocess.PIPE if capture else None,
    stderr=subprocess.STDOUT if capture else None,
    env={**os.environ, "LC_ALL": "C"},
  )
  return result.stdout if capture else ""


def _existing_parent(path: Path) -> Path:
  while not path.exists():
    path = path.parent
  if not path.is_dir():
    raise ImageError(f"Output parent is not a directory: {path}")
  return path


def _space(requirements: list[tuple[Path, int]]) -> None:
  """Budget every output on its filesystem, including the nonsparse image."""
  devices: dict[int, tuple[Path, int]] = {}
  for path, size in requirements:
    parent = _existing_parent(path.parent)
    device = parent.stat().st_dev
    previous = devices.get(device, (parent, 0))[1]
    devices[device] = (parent, previous + size)
  for parent, required in devices.values():
    free = shutil.disk_usage(parent).free
    if free < required:
      raise ImageError(
        f"Insufficient free space at {parent}: need at least "
        f"{required / 2**30:.2f} GiB, have {free / 2**30:.2f} GiB"
      )


def _copy_overlay(source: Path, destination: Path) -> None:
  """Merge files while replacing, rather than following, destination symlinks."""
  if destination.is_symlink():
    destination.unlink()
  if destination.exists() and not destination.is_dir():
    raise ImageError(f"Overlay directory conflicts with file: {destination}")
  destination.mkdir(exist_ok=True)
  for item in source.iterdir():
    target = destination / item.name
    mode = item.lstat().st_mode
    if stat.S_ISDIR(mode):
      _copy_overlay(item, target)
    elif stat.S_ISLNK(mode) or stat.S_ISREG(mode):
      if target.is_dir() and not target.is_symlink():
        raise ImageError(f"Overlay file conflicts with directory: {target}")
      if target.exists() or target.is_symlink():
        target.unlink()
      if stat.S_ISLNK(mode):
        target.symlink_to(item.readlink())
      else:
        shutil.copy2(item, target, follow_symlinks=False)
    else:
      raise ImageError(f"Overlay contains a special file: {item}")


def iso_command(opts: dict, profile: Path, work: Path, out: Path) -> list[str]:
  """Construct the mkarchiso command without executing a profile."""
  command = [
    "mkarchiso",
    "-v",
    "-m",
    "iso",
    "-w",
    str(work),
    "-o",
    str(out),
    "-L",
    str(opts.get("iso_label", "ARCH_BOOTSTRAP")),
  ]
  packages = opts.get("iso_packages", [])
  if not isinstance(packages, list) or any(
    not isinstance(package, str)
    or not re.fullmatch(r"[A-Za-z0-9@_+][A-Za-z0-9@_.+:-]*", package)
    for package in packages
  ):
    raise ImageError("iso_packages must be a list of package names")
  if packages:
    command.extend(["-p", " ".join(packages)])
  command.append(str(profile))
  return command


def build_iso(opts: dict) -> str:
  """Build a fresh Archiso profile, not a conversion of an extracted rootfs."""
  execute = bool(opts.get("execute", False))
  if execute and opts.get("offline", False):
    raise ImageError("Offline mode forbids the networked Archiso build stage")
  base = Path(opts.get("work_dir", "work"))
  source = _path(opts.get("iso_profile") or "/usr/share/archiso/configs/releng")
  profile = _fresh(opts.get("iso_profile_dir") or base / "iso-profile")
  work = _fresh(opts.get("iso_work_dir") or base / "iso-build")
  out = _fresh(opts.get("iso_out_dir") or "out/iso")
  _separate([source, profile, work, out])
  name = opts.get("iso_name", "archlinux-custom")
  label = opts.get("iso_label", "ARCH_BOOTSTRAP")
  if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", name):
    raise ImageError("iso_name must contain only lowercase letters, digits, ._-.")
  if not isinstance(label, str) or not re.fullmatch(r"[A-Z0-9_]{1,32}", label):
    raise ImageError("iso_label must contain 1-32 uppercase letters, digits or _")
  minimum = opts.get("iso_min_free_gib", 8)
  if isinstance(minimum, bool) or not isinstance(minimum, (int, float)):
    raise ImageError("iso_min_free_gib must be a positive number")
  if not 0 < minimum <= 1024:
    raise ImageError("iso_min_free_gib must be greater than 0 and at most 1024")
  overlay = _path(opts["iso_overlay"]) if opts.get("iso_overlay") else None
  if overlay:
    if not overlay.is_dir():
      raise ImageError(f"Overlay is not a directory: {overlay}")
    _separate([overlay, profile, work, out])
  payload = None
  if opts.get("iso_include_tarball", False):
    if opts.get("tarball"):
      payload = _regular(opts["tarball"])
    elif execute:
      raise ImageError("--iso-include-tarball requires a selected --tarball")
  command = iso_command(opts, profile, work, out)
  print(f"Copy Archiso profile: {source} -> {profile}")
  print(f"Set ISO filename prefix to {name}; volume label to {label}")
  if overlay:
    print(f"Merge overlay: {overlay} -> {profile / 'airootfs'}")
  if payload:
    print(f"Include bootstrap payload in /root/bootstrap/: {payload}")
  elif opts.get("iso_include_tarball", False):
    print(
      "Include pending selected archive in /root/bootstrap/ "
      "after download and verification."
    )
  if not execute:
    _run(command, False)
    print("Execution requires Arch Linux x86_64, root and installed archiso.")
    print("The profile creates a live ISO; it does not clone root_dir.")
    return str(out)
  if platform.system() != "Linux" or platform.machine() != "x86_64":
    raise ImageError("This ISO helper supports an Arch Linux x86_64 build host")
  if platform.freedesktop_os_release().get("ID") != "arch":
    raise ImageError("Build on an Arch Linux host or VM with archiso installed")
  if os.geteuid() != 0:
    raise ImageError("This ISO helper requires root; rerun with sudo --execute")
  _tools("mkarchiso")
  if not source.is_dir():
    raise ImageError(f"Archiso source profile does not exist: {source}")
  _regular(source / "profiledef.sh")
  if (source / "airootfs").is_symlink():
    raise ImageError("The profile's airootfs directory must not be a symlink")
  for location in (work, out):
    _space([(location, int(minimum * 2**30))])
  profile.parent.mkdir(parents=True, exist_ok=True)
  shutil.copytree(source, profile, symlinks=True)
  with (profile / "profiledef.sh").open("a", encoding="utf-8") as handle:
    handle.write(
      "\n# Overrides added by archlinux-bootstrap-guide.\n"
      f"iso_name={shlex.quote(name)}\niso_label={shlex.quote(label)}\n"
    )
  if overlay:
    _copy_overlay(overlay, profile / "airootfs")
  if payload:
    target = profile / "airootfs" / "root" / "bootstrap"
    _path(target)
    target.mkdir(parents=True, exist_ok=True)
    for item in (payload, payload.with_name(payload.name + ".sig")):
      if item.exists():
        _regular(item)
        destination = _fresh(target / item.name)
        shutil.copy2(item, destination)
  work.parent.mkdir(parents=True, exist_ok=True)
  out.parent.mkdir(parents=True, exist_ok=True)
  _run(command, True)
  if not out.is_dir() or not any(out.glob("*.iso")):
    raise ImageError("mkarchiso returned success but no .iso appeared in output")
  print(f"ISO artifacts: {out}. Test BIOS/UEFI boot separately.")
  return str(out)


def data_bytes(size: str) -> int:
  """Accept a bounded binary size, with no truncate relative-size syntax."""
  match = re.fullmatch(r"([1-9][0-9]*)(M|G)", str(size))
  if not match:
    raise ImageError("data_size must be a positive binary size such as 64M or 8G")
  value = int(match[1]) * (2**20 if match[2] == "M" else 2**30)
  if not 32 * 2**20 <= value <= 1024 * 2**30:
    raise ImageError("data_size must be between 32M and 1024G")
  return value


def occupied_slots(report: str) -> set[int]:
  """Conservatively include table entries AND xorriso replay append slots."""
  slots = set()
  for line in report.splitlines():
    match = re.match(
      r"\s*(?:MBR partition|GPT partition name|GPT start and size|GPT type GUID)"
      r"\s*:\s*(\d+)\b",
      line,
    )
    if match:
      slots.add(int(match[1]))
    match = re.search(r"(?:^|\s)-append_partition\s+(\d+)\b", line)
    if match:
      slots.add(int(match[1]))
  return slots


def _data_extent(report: str, image: Path, label: str, size: int) -> None:
  """Confirm a table entry actually points to the appended ext4 superblock."""
  extents = set()
  for line in report.splitlines():
    match = re.match(
      r"\s*MBR partition\s*:\s*\d+\s+0x[0-9a-f]+\s+0x[0-9a-f]+"
      r"\s+(\d+)\s+(\d+)",
      line,
      re.IGNORECASE,
    )
    if not match:
      match = re.match(r"\s*GPT start and size\s*:\s*\d+\s+(\d+)\s+(\d+)", line)
    if match:
      extents.add((int(match[1]) * 512, int(match[2]) * 512))
  image_size = image.stat().st_size
  with image.open("rb") as handle:
    for offset, length in extents:
      if offset <= 0 or length < size or offset + size > image_size:
        continue
      handle.seek(offset + 1024)
      superblock = handle.read(1024)
      if superblock[56:58] == b"\x53\xef" and superblock[120:136].rstrip(
        b"\0"
      ) == label.encode("ascii"):
        return
  raise ImageError("No output partition points to the expected ext4 filesystem")


def _inspect(path: Path, execute: bool) -> str:
  return _run(
    [
      "xorriso",
      "-abort_on",
      "FAILURE",
      "-indev",
      str(path),
      "-report_el_torito",
      "plain",
      "-report_system_area",
      "plain",
      "-report_el_torito",
      "cmd",
    ],
    execute,
    capture=True,
  )


def validate_layout(report: str, slot: int) -> None:
  if not re.search(r"El Torito boot img\s*:", report):
    raise ImageError("Input has no reported El Torito boot image")
  if not re.search(r"System area summary\s*:.*\b(?:MBR|GPT)\b", report):
    raise ImageError("Input needs a recognized MBR/GPT hybrid ISO layout")
  if re.search(r"System area summary\s*:.*\b(?:APM|SUN|MIPS|PALO)\b", report):
    raise ImageError("This helper supports only MBR/GPT hybrid layouts")
  if slot in occupied_slots(report):
    raise ImageError(
      f"Partition slot {slot} is already used. Inspect the report and choose "
      "an unused --partition-number (2, 3 or 4)."
    )


def append_command(
  source: Path, output: Path, data: Path, slot: int
) -> list[str]:
  return [
    "xorriso",
    "-abort_on",
    "FAILURE",
    "-indev",
    str(source),
    "-outdev",
    str(output),
    "-boot_image",
    "any",
    "replay",
    "-append_partition",
    str(slot),
    "0x83",
    str(data),
    "-commit",
    "-end",
  ]


def append_data(opts: dict) -> str:
  """Append an ext4 data filesystem; this does not implement live persistence."""
  execute = bool(opts.get("execute", False))
  if not opts.get("input_iso"):
    raise ImageError("--input-iso is required")
  source = _regular(opts["input_iso"])
  output = _fresh(opts.get("output_image") or "out/archlinux-with-data.img")
  data = _fresh(opts.get("data_image") or "out/archlinux-data.ext4")
  before = _fresh(str(output) + ".source-layout.txt")
  after = _fresh(str(output) + ".layout.txt")
  _separate([source, output, data, before, after])
  size = data_bytes(opts.get("data_size", "1G"))
  label = opts.get("data_label", "ARCH_DATA")
  if not isinstance(label, str) or not re.fullmatch(
    r"[A-Za-z0-9_-]{1,16}", label
  ):
    raise ImageError("data_label must contain 1-16 ASCII letters, digits, _ or -")
  slot = opts.get("partition_number", 3)
  if isinstance(slot, bool) or slot not in (2, 3, 4):
    raise ImageError("partition_number must be 2, 3 or 4")
  command = append_command(source, output, data, slot)
  if not execute:
    _inspect(source, False)
    print(f"Execution checks that partition slot {slot} is unused.")
    _run(["truncate", "--size", str(size), str(data)], False)
    _run(["mkfs.ext4", "-m", "0", "-L", label, str(data)], False)
    _run(command, False)
    _inspect(output, False)
    print("No files were written; boot metadata and free space remain unchecked.")
    return str(output)
  _tools("xorriso", "mkfs.ext4")
  report = _inspect(source, True)
  print(report, end="" if report.endswith("\n") else "\n")
  validate_layout(report, slot)
  # Count both files as fully allocated, even though the ext4 source is sparse.
  _space([(data, size), (output, size + source.stat().st_size + 16 * 2**20)])
  for path in (output, data):
    path.parent.mkdir(parents=True, exist_ok=True)
  with before.open("x", encoding="utf-8") as handle:
    handle.write(report)
  # O_EXCL/open('xb') makes the no-clobber rule effective at creation time.
  with data.open("xb") as handle:
    handle.truncate(size)
  _run(["mkfs.ext4", "-m", "0", "-L", label, str(data)], True)
  # Reserve a regular output before handing it to xorriso, never a device.
  with output.open("xb"):
    pass
  _run(command, True)
  result = _inspect(output, True)
  with after.open("x", encoding="utf-8") as handle:
    handle.write(result)
  print(result, end="" if result.endswith("\n") else "\n")
  boot_pattern = r"El Torito boot img\s*:\s*\d+\s+(\S+)\s+(\S+)"
  before_boot = Counter(re.findall(boot_pattern, report))
  after_boot = Counter(re.findall(boot_pattern, result))
  if before_boot - after_boot:
    raise ImageError(
      "Output lost boot entries; retained artifacts need inspection"
    )
  if len(occupied_slots(result)) <= len(occupied_slots(report)):
    raise ImageError("Could not confirm an additional partition in output report")
  _data_extent(result, output, label, size)
  print(f"Created {output} ({output.stat().st_size} bytes).")
  print(f"Locate the data filesystem by label {label}, not partition number.")
  print("Firmware boot and writable-data use still need a real boot test.")
  return str(output)
