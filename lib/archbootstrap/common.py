"""Shared path, subprocess, and download safeguards."""

import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys


class BootstrapError(ValueError):
  """An actionable error, shown without an implementation traceback."""


def log(message):
  print(message, file=sys.stderr, flush=True)


def command(argv, execute=False, **kwargs):
  log("+ " + shlex.join(str(arg) for arg in argv))
  if execute:
    return subprocess.run([str(arg) for arg in argv], check=True, **kwargs)
  return None


def require_tools(*names):
  missing = [name for name in names if shutil.which(name) is None]
  if missing:
    raise BootstrapError("Missing commands: " + ", ".join(missing))


def safe_path(path):
  """Reject device namespaces and symlink traversal before writing files."""
  path = Path(os.path.abspath(path))
  if path == Path("/") or path.parts[1:2] in [("dev",), ("proc",), ("sys",)]:
    raise BootstrapError(f"Not a regular workspace path: {path}")
  for parent in (path, *path.parents):
    if parent.is_symlink():
      raise BootstrapError(f"Refusing a symlink in output path: {parent}")
  return path


def regular_file(path):
  path = safe_path(path)
  if not path.exists() or not stat.S_ISREG(path.stat().st_mode):
    raise BootstrapError(f"Expected a regular file: {path}")
  return path


def new_file(path):
  path = safe_path(path)
  if path.exists():
    raise BootstrapError(f"Output already exists; choose another name: {path}")
  return path


def publish(staged, target):
  """Publish on the same filesystem, refusing even a concurrent replacement."""
  new_file(target)
  os.link(staged, target)
  Path(staged).unlink()
