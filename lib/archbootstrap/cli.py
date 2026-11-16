"""Shared CLI/configuration for the master script and individual stages."""

import argparse
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from urllib.error import URLError

from . import archives
from .common import BootstrapError, log


REPO = Path(__file__).resolve().parents[2]
STAGES = (
  "fetch",
  "extract",
  "configure",
  "chroot",
  "iso",
  "wsl-export",
  "append-data",
)
COMMANDS = ("run", *STAGES, "show-config", "doctor")
DEFAULTS = {
  "tarball_dir": "tarballs",
  "tarball": "",
  "download": "",
  "select": "auto",
  "offline": False,
  "mirror_base": "https://geo.mirror.pkgbuild.com/iso",
  "signature": "",
  "keyring": "/usr/share/pacman/keyrings/archlinux.gpg",
  "signer_fingerprint": archives.SIGNER,
  "verification": "signature",
  "sha256": "",
  "max_download_mib": 1024,
  "root_dir": "work/root.x86_64",
  "root_min_free_gib": 2,
  "max_extract_gib": 8,
  "mirror": "https://geo.mirror.pkgbuild.com/$repo/os/$arch",
  "packages": ["base", "sudo"],
  "hostname": "arch-bootstrap",
  "timezone": "UTC",
  "locale": "en_US.UTF-8",
  "username": "",
  "user_shell": "/bin/bash",
  "services": [],
  "noninteractive": False,
  "verbose": False,
  "wsl": False,
  "wsl_output": "out/archlinux-wsl.tar",
  "iso_profile": "/usr/share/archiso/configs/releng",
  "iso_profile_dir": "work/iso-profile",
  "iso_work_dir": "work/iso-build",
  "iso_out_dir": "out/iso",
  "iso_overlay": "",
  "iso_packages": [],
  "iso_label": "ARCH_BOOTSTRAP",
  "iso_name": "archlinux-custom",
  "iso_min_free_gib": 8,
  "iso_include_tarball": False,
  "input_iso": "",
  "output_image": "out/archlinux-with-data.img",
  "data_image": "out/archlinux-data.ext4",
  "data_size": "1G",
  "data_label": "ARCH_DATA",
  "partition_number": 3,
  "steps": ["fetch", "extract", "configure"],
  "with_iso": False,
  "with_wsl": False,
}
PATH_KEYS = {
  "tarball_dir",
  "tarball",
  "signature",
  "keyring",
  "root_dir",
  "wsl_output",
  "iso_profile",
  "iso_profile_dir",
  "iso_work_dir",
  "iso_out_dir",
  "iso_overlay",
  "input_iso",
  "output_image",
  "data_image",
}
ENUMS = {"verification": {"signature", "sha256"}}
SOURCE_KEYS = {
  "tarball",
  "download",
  "tarball_dir",
  "select",
  "offline",
  "mirror_base",
  "signature",
  "keyring",
  "signer_fingerprint",
  "verification",
  "sha256",
  "max_download_mib",
}


def parser():
  cli = argparse.ArgumentParser(
    description=(
      "Arch bootstrap helpers. Preview is the default; --execute permits "
      "changes. No helper writes physical devices. Paths are repo-relative."
    ),
    epilog=(
      "Examples:\n"
      "  ./bootstrap.sh doctor\n"
      "  ./scripts/fetch-tarball.sh --download latest --execute\n"
      "  ./bootstrap.sh run --config config/local.json\n"
      "  sudo ./bootstrap.sh run --config config/local.json --execute\n"
      "  sudo ./scripts/enter-chroot.sh --execute -- /bin/bash\n"
      "  ./scripts/build-iso.sh --iso-packages git neovim\n"
      "See docs/guide.md for prerequisites, trust setup and manual commands."
    ),
    formatter_class=argparse.RawDescriptionHelpFormatter,
    argument_default=argparse.SUPPRESS,
    allow_abbrev=False,
  )
  cli.add_argument("command", nargs="?", choices=COMMANDS, default="run")
  cli.add_argument(
    "--config", help="JSON file (path relative to current directory)"
  )
  cli.add_argument(
    "--verbose",
    action=argparse.BooleanOptionalAction,
    help="show full embedded chroot commands",
  )
  mode = cli.add_mutually_exclusive_group()
  mode.add_argument(
    "--execute", action="store_true", help="perform the selected stages"
  )
  mode.add_argument(
    "--dry-run", dest="execute", action="store_false", help="preview only"
  )
  workflow = cli.add_argument_group("workflow")
  workflow.add_argument(
    "--steps", nargs="+", choices=[s for s in STAGES if s != "chroot"]
  )
  workflow.add_argument("--with-iso", action=argparse.BooleanOptionalAction)
  workflow.add_argument("--with-wsl", action=argparse.BooleanOptionalAction)
  fetch = cli.add_argument_group("archive selection and verification")
  source = fetch.add_mutually_exclusive_group()
  source.add_argument("--tarball", help="use exactly this archive")
  source.add_argument(
    "--download", help="latest or an explicit HTTPS bootstrap URL"
  )
  fetch.add_argument("--tarball-dir", help="cache directory (default: tarballs)")
  fetch.add_argument("--select", help="auto, newest, ask, or 1-based menu number")
  fetch.add_argument(
    "--offline",
    action=argparse.BooleanOptionalAction,
    help="forbid downloads and networked configure/ISO stages",
  )
  fetch.add_argument("--mirror-base", help="HTTPS mirror ISO directory")
  fetch.add_argument("--signature", help="matching detached signature file")
  fetch.add_argument("--keyring", help="trusted exported GPG keyring")
  fetch.add_argument(
    "--signer-fingerprint", help="full trusted release signer fingerprint"
  )
  fetch.add_argument("--verification", choices=sorted(ENUMS["verification"]))
  fetch.add_argument(
    "--sha256", help="independently trusted digest for sha256 mode"
  )
  fetch.add_argument(
    "--max-download-mib", type=int, help="download cap (default: 1024)"
  )
  root = cli.add_argument_group("root filesystem and chroot")
  root.add_argument(
    "--root-dir", help="dedicated root directory (default: work/root.x86_64)"
  )
  root.add_argument(
    "--root-min-free-gib", type=int, help="minimum free space (default: 2)"
  )
  root.add_argument(
    "--max-extract-gib", type=int, help="expanded archive cap (default: 8)"
  )
  root.add_argument(
    "--mirror", help="pacman server URL; quote literal $repo and $arch"
  )
  root.add_argument(
    "--packages",
    nargs="+",
    help="additional packages; base and sudo are always installed",
  )
  root.add_argument("--hostname")
  root.add_argument("--timezone")
  root.add_argument("--locale")
  root.add_argument(
    "--username", help="optional locked user; set password in chroot"
  )
  root.add_argument("--user-shell")
  root.add_argument(
    "--services", nargs="*", help="units to enable in target, not start"
  )
  root.add_argument("--noninteractive", action=argparse.BooleanOptionalAction)
  root.add_argument(
    "--wsl", action=argparse.BooleanOptionalAction, help="configure for WSL2"
  )
  root.add_argument("--wsl-output", help="uncompressed export .tar filename")
  iso = cli.add_argument_group(
    "Archiso live image (independent of configured root)"
  )
  for name, help_text in {
    "iso_profile": "trusted Archiso profile directory to copy",
    "iso_profile_dir": "fresh profile copy destination",
    "iso_work_dir": "fresh mkarchiso work directory",
    "iso_out_dir": "fresh ISO output directory",
    "iso_overlay": "optional tree copied into profile airootfs",
    "iso_label": "ISO filesystem label (default: ARCH_BOOTSTRAP)",
    "iso_name": "output base name (default: archlinux-custom)",
  }.items():
    iso.add_argument("--" + name.replace("_", "-"), help=help_text)
  iso.add_argument(
    "--iso-packages", nargs="*", help="additional live ISO packages"
  )
  iso.add_argument(
    "--iso-min-free-gib", type=int, help="free-space floor (default: 8)"
  )
  iso.add_argument("--iso-include-tarball", action=argparse.BooleanOptionalAction)
  data = cli.add_argument_group(
    "append a data filesystem to an existing bootable ISO"
  )
  data.add_argument(
    "--input-iso", help="existing bootable ISO (required for append-data)"
  )
  data.add_argument("--output-image", help="new combined .img file")
  data.add_argument("--data-image", help="new sparse ext4 staging file")
  data.add_argument("--data-size", help="data filesystem size, e.g. 1G or 8G")
  data.add_argument("--data-label", help="filesystem label (default: ARCH_DATA)")
  data.add_argument(
    "--partition-number", type=int, help="unused appended slot (default: 3)"
  )
  return cli


def validate_values(values, origin):
  if not isinstance(values, dict):
    raise BootstrapError(f"{origin} must be a JSON object")
  for key, value in values.items():
    if key not in DEFAULTS:
      raise BootstrapError(f"Unknown setting {origin}.{key}")
    expected = type(DEFAULTS[key])
    if type(value) is not expected:
      raise BootstrapError(f"{origin}.{key} must be {expected.__name__}")
    if expected is list and any(not isinstance(item, str) for item in value):
      raise BootstrapError(f"{origin}.{key} must contain strings only")
    if expected is int and value < 1:
      raise BootstrapError(f"{origin}.{key} must be positive")
    if key in ENUMS and value not in ENUMS[key]:
      raise BootstrapError(f"Invalid value for {origin}.{key}: {value}")
    if isinstance(value, str) and (
      "\0" in value or "\n" in value or "\r" in value
    ):
      raise BootstrapError(
        f"Control characters are not allowed in {origin}.{key}"
      )
  if "steps" in values:
    steps = values["steps"]
    if not steps or len(set(steps)) != len(steps):
      raise BootstrapError("steps must be nonempty and have no duplicates")
    if any(step not in STAGES or step == "chroot" for step in steps):
      raise BootstrapError("Invalid run stage; use enter-chroot.sh separately")


def load_config(filename):
  if not filename:
    return {}

  def unique_pairs(items):
    result = {}
    for key, value in items:
      if key in result:
        raise BootstrapError(f"Duplicate JSON key: {key}")
      result[key] = value
    return result

  with open(filename, encoding="utf-8") as stream:
    config = json.load(stream, object_pairs_hook=unique_pairs)
  if not isinstance(config, dict):
    raise BootstrapError("Configuration must be a JSON object")
  for section, values in config.items():
    if section not in ("common", "run", *STAGES):
      raise BootstrapError(f"Unknown configuration section: {section}")
    validate_values(values, section)
  return config


def settings(stage, config, arguments):
  opts = dict(DEFAULTS)
  opts.update(config.get("common", {}))
  opts.update(config.get(stage, {}))
  explicit = {key: value for key, value in arguments.items() if key in DEFAULTS}
  validate_values(explicit, "CLI")
  opts.update(explicit)
  # CLI source selectors replace the other selector inherited from JSON.
  if "tarball" in explicit:
    opts["download"] = ""
  if "download" in explicit:
    opts["tarball"] = ""
  if opts["tarball"] and opts["download"]:
    raise BootstrapError("Set tarball or download, not both, in configuration")
  for key in PATH_KEYS:
    if opts[key]:
      path = Path(opts[key]).expanduser()
      opts[key] = str(path if path.is_absolute() else REPO / path)
  opts["execute"] = arguments.get("execute", False)
  opts["chroot_command"] = arguments.get("chroot_command", ["/bin/bash"])
  return opts


def doctor():
  print(f"Host: {platform.system()} {platform.machine()} {platform.release()}")
  if Path("/etc/os-release").exists():
    for line in Path("/etc/os-release").read_text().splitlines():
      if line.startswith("PRETTY_NAME="):
        print(line)
  print(f"Python: {platform.python_version()}; repository: {REPO}")
  print(f"Free repository space: {shutil.disk_usage(REPO).free / 2**30:.1f} GiB")
  print("Available commands (requirements depend on the stage):")
  for name in (
    "bash",
    "gpg",
    "tar",
    "zstd",
    "unshare",
    "mount",
    "chroot",
    "mkarchiso",
    "xorriso",
    "mkfs.ext4",
    "truncate",
  ):
    print(f"  {name}: {shutil.which(name) or 'MISSING'}")
  print("No packages were installed and no system settings were changed.")


def run_stage(stage, opts, selected=None):
  from . import images, rootfs

  log(f"\n[{stage}] {'EXECUTE' if opts['execute'] else 'PREVIEW'}")
  if opts["offline"] and stage in ("configure", "iso"):
    raise BootstrapError(
      f"{stage} needs package network access; remove --offline"
    )
  if stage == "fetch":
    return archives.fetch_tarball(opts)
  if stage == "extract":
    # The master can reuse a verified selection; a standalone extract verifies.
    if selected is None:
      selected = archives.fetch_tarball(opts)
    if selected is None:
      log(f"Would extract the resolved archive into {opts['root_dir']}")
      return None
    opts["tarball"] = str(selected)
    rootfs.extract_rootfs(opts)
  elif stage == "configure":
    rootfs.configure_rootfs(opts)
  elif stage == "chroot":
    rootfs.enter_chroot(opts)
  elif stage == "wsl-export":
    rootfs.export_wsl(opts)
  elif stage == "iso":
    if opts["iso_include_tarball"]:
      if selected is None:
        selected = archives.fetch_tarball(opts)
      if selected is None:
        log("ISO payload depends on the archive resolved during execution.")
      opts["tarball"] = str(selected) if selected else ""
    images.build_iso(opts)
  elif stage == "append-data":
    images.append_data(opts)
  return selected


def main(argv=None):
  argv = list(sys.argv[1:] if argv is None else argv)
  remainder = None
  if "--" in argv:
    split = argv.index("--")
    remainder, argv = argv[split + 1 :], argv[:split]
  args = vars(parser().parse_args(argv))
  if remainder is not None:
    if args["command"] != "chroot" or not remainder:
      raise BootstrapError("Use -- COMMAND only with the chroot stage")
    args["chroot_command"] = remainder
  config = load_config(args.get("config"))
  stage = args["command"]
  if stage == "doctor":
    doctor()
    return 0
  if stage == "show-config":
    print(
      json.dumps(
        {s: settings(s, config, args) for s in ("run", *STAGES)}, indent=2
      )
    )
    return 0
  opts = settings(stage, config, args)
  if stage != "run":
    result = run_stage(stage, opts)
    if stage == "fetch" and result:
      print(result)
    return 0
  steps = list(opts["steps"])
  if opts["with_wsl"] and "wsl-export" not in steps:
    steps.append("wsl-export")
  if opts["with_iso"] and "iso" not in steps:
    steps.append("iso")
  selected = None
  selected_settings = None
  # Build all stage settings before the first change: malformed config fails early.
  all_settings = [(item, settings(item, config, args)) for item in steps]
  for item, current in all_settings:
    if current["offline"] and item in ("configure", "iso"):
      raise BootstrapError(
        f"{item} needs package network access; remove --offline"
      )
    if opts["with_wsl"] and item == "configure":
      if args.get("wsl") is False:
        raise BootstrapError("--with-wsl conflicts with --no-wsl")
      current["wsl"] = True
  for item, current in all_settings:
    source_settings = {key: current[key] for key in SOURCE_KEYS}
    if source_settings != selected_settings:
      selected = None
    selected = run_stage(item, current, selected)
    selected_settings = source_settings
  return 0


def entrypoint():
  try:
    raise SystemExit(main())
  except KeyboardInterrupt:
    log("Interrupted. Review any partial output before retrying.")
    raise SystemExit(130) from None
  except (ValueError, OSError, URLError, subprocess.CalledProcessError) as error:
    log(f"Error: {error}")
    raise SystemExit(1) from None


if __name__ == "__main__":
  entrypoint()
