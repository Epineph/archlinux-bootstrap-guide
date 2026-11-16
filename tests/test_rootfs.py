"""Small fixtures exercise archive boundaries and rootfs command contracts."""

import io
import json
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from archbootstrap import rootfs


class RootfsTests(unittest.TestCase):
  def setUp(self):
    self.temporary = tempfile.TemporaryDirectory()
    self.base = Path(self.temporary.name)
    self.root = self.base / "root.x86_64"
    self.options = {"root_dir": str(self.root), "execute": False}

  def tearDown(self):
    self.temporary.cleanup()

  def archive(self, extra=()):
    archive = self.base / "bootstrap.tar.gz"
    with tarfile.open(archive, "w:gz") as stream:
      for name in (
        "root.x86_64/usr/bin/bash",
        "root.x86_64/usr/bin/arch-chroot",
        "root.x86_64/etc/pacman.conf",
      ):
        info = tarfile.TarInfo(name)
        info.size = 1
        stream.addfile(info, io.BytesIO(b"x"))
      for name, kind, link in extra:
        info = tarfile.TarInfo(name)
        info.type = kind
        info.linkname = link
        stream.addfile(info)
    return archive

  def managed(self, *, configured=False, wsl=False):
    for path, content in (
      ("usr/bin/bash", "fixture"),
      ("usr/bin/arch-chroot", "fixture"),
      ("usr/lib/os-release", "ID=arch\n"),
      ("etc/pacman.d/mirrorlist", "fixture\n"),
      ("etc/resolv.conf", ""),
    ):
      target = self.root / path
      target.parent.mkdir(parents=True, exist_ok=True)
      target.write_text(content)
    (self.root / rootfs.MARKER).write_text(
      json.dumps(
        {
          "format": 1,
          "configured": configured,
          "wsl": wsl,
        }
      )
    )

  def test_allows_official_top_level_metadata(self):
    archive = self.archive(
      [
        ("pkglist.x86_64.txt", tarfile.REGTYPE, ""),
        ("version", tarfile.REGTYPE, ""),
      ]
    )
    self.assertEqual(rootfs.inspect_archive(archive), 3)
    with self.assertRaises(rootfs.RootfsError):
      rootfs.inspect_archive(
        self.archive(
          [
            ("version", tarfile.SYMTYPE, "/etc/passwd"),
          ]
        )
      )

  def test_accepts_official_style_absolute_symlink(self):
    archive = self.archive(
      [
        (
          "root.x86_64/var/lib/dbus/machine-id",
          tarfile.SYMTYPE,
          "/etc/machine-id",
        ),
      ]
    )
    self.assertEqual(rootfs.inspect_archive(archive), 3)

  def test_rejects_path_traversal(self):
    for name in ("../outside", "/tmp/outside", "root.x86_64/../../outside"):
      with self.subTest(name=name):
        archive = self.archive([(name, tarfile.REGTYPE, "")])
        with self.assertRaises(rootfs.RootfsError):
          rootfs.inspect_archive(archive)

  def test_rejects_member_beneath_symlink_regardless_of_order(self):
    archive = self.archive(
      [
        ("root.x86_64/escape/file", tarfile.REGTYPE, ""),
        ("root.x86_64/escape", tarfile.SYMTYPE, "/tmp"),
      ]
    )
    with self.assertRaisesRegex(rootfs.RootfsError, "non-directory"):
      rootfs.inspect_archive(archive)

  def test_rejects_hardlink_to_symlink(self):
    archive = self.archive(
      [
        ("root.x86_64/a", tarfile.SYMTYPE, "/etc/passwd"),
        ("root.x86_64/b", tarfile.LNKTYPE, "root.x86_64/a"),
      ]
    )
    with self.assertRaisesRegex(rootfs.RootfsError, "regular file"):
      rootfs.inspect_archive(archive)

  def test_rejects_devices_and_cyclic_links(self):
    with self.assertRaisesRegex(rootfs.RootfsError, "entry type"):
      rootfs.inspect_archive(
        self.archive(
          [
            ("root.x86_64/device", tarfile.CHRTYPE, ""),
          ]
        )
      )
    with self.assertRaisesRegex(rootfs.RootfsError, "Cyclic"):
      rootfs.inspect_archive(
        self.archive(
          [
            ("root.x86_64/a", tarfile.LNKTYPE, "root.x86_64/b"),
            ("root.x86_64/b", tarfile.LNKTYPE, "root.x86_64/a"),
          ]
        )
      )

  def test_rejects_relative_symlink_escaping_wrapper(self):
    with self.assertRaises(rootfs.RootfsError):
      rootfs.inspect_archive(
        self.archive(
          [
            ("root.x86_64/etc/bad", tarfile.SYMTYPE, "../../outside"),
          ]
        )
      )

  def test_archive_expansion_limit_is_enforced(self):
    with self.assertRaisesRegex(rootfs.RootfsError, "max_extract_gib"):
      rootfs.inspect_archive(self.archive(), max_bytes=2)

  def test_extract_preview_never_creates_or_runs(self):
    opts = dict(self.options, tarball=str(self.archive()))
    with patch.object(rootfs.subprocess, "run") as run:
      rootfs.extract_rootfs(opts)
    run.assert_not_called()
    self.assertFalse(self.root.exists())

  def test_extract_preserves_nonempty_destination(self):
    self.root.mkdir()
    (self.root / "valuable").write_text("preserve")
    with self.assertRaisesRegex(rootfs.RootfsError, "empty"):
      rootfs.extract_rootfs(dict(self.options, tarball="unused.tar.zst"))
    self.assertEqual((self.root / "valuable").read_text(), "preserve")

  def test_safe_root_rejects_symlink_and_host_path(self):
    alias = self.base / "alias"
    alias.symlink_to(self.base, target_is_directory=True)
    for path in ("/", "/dev/test-root", "/usr/test-root", alias / "root"):
      with self.subTest(path=path), self.assertRaises(rootfs.RootfsError):
        rootfs._safe_root(path)

  def test_chroot_command_uses_positionals_and_private_mounts(self):
    opts = dict(self.options, chroot_command=["printf", "%s", "a; b"])
    with patch.object(rootfs.subprocess, "run") as run:
      rootfs.enter_chroot(opts)
    run.assert_not_called()
    command = rootfs._chroot_command(self.root, opts["chroot_command"])
    self.assertEqual(
      command[:8],
      [
        "unshare",
        "--mount",
        "--propagation",
        "private",
        "--pid",
        "--fork",
        "--kill-child",
        "--",
      ],
    )
    self.assertEqual(command[-3:], ["printf", "%s", "a; b"])
    self.assertNotIn("a; b", rootfs._NAMESPACE_SCRIPT)

  def test_configuration_rejects_injection_and_retains_full_upgrade(self):
    for key, value in (
      ("hostname", "host;touch /bad"),
      ("username", "root"),
      ("timezone", "../../etc"),
      ("packages", ["--root=/"]),
      ("services", ["--now"]),
      ("mirror", "http://bad/$repo/os/$arch"),
    ):
      with self.subTest(key=key), self.assertRaises(rootfs.RootfsError):
        rootfs._configuration({key: value})
    args, mirror = rootfs._configuration({"wsl": True, "packages": ["git"]})
    self.assertIn("systemd-sysvcompat", args)
    self.assertIn("git", args)
    self.assertIn("$repo", mirror)
    self.assertIn("pacman -Syu", rootfs._CONFIGURE_SCRIPT)
    self.assertNotIn("NOPASSWD", rootfs._CONFIGURE_SCRIPT)

  def test_configure_only_writes_managed_root(self):
    self.managed()
    opts = dict(self.options, execute=True, username="example", wsl=True)
    with (
      patch.object(rootfs, "_require_host"),
      patch.object(rootfs, "_require_tools"),
      patch.object(rootfs, "_require_quiescent"),
      patch.object(rootfs.subprocess, "run") as run,
    ):
      rootfs.configure_rootfs(opts)
    self.assertEqual(run.call_count, 1)
    self.assertIn(rootfs._CONFIGURE_SCRIPT, run.call_args.args[0])
    self.assertIsNone(run.call_args.kwargs["input"])
    marker = json.loads((self.root / rootfs.MARKER).read_text())
    self.assertTrue(marker["configured"])
    self.assertTrue(marker["wsl"])
    self.assertTrue(
      (self.root / "etc/pacman.d/mirrorlist")
      .read_text()
      .startswith("Server = https://")
    )

  def test_failed_configuration_invalidates_previous_completion(self):
    self.managed(configured=True, wsl=True)
    with (
      patch.object(rootfs, "_require_host"),
      patch.object(rootfs, "_require_tools"),
      patch.object(rootfs, "_require_quiescent"),
      patch.object(rootfs.subprocess, "run", side_effect=RuntimeError("failure")),
      self.assertRaises(RuntimeError),
    ):
      rootfs.configure_rootfs(dict(self.options, execute=True))
    marker = json.loads((self.root / rootfs.MARKER).read_text())
    self.assertFalse(marker["configured"])

  def test_configure_rejects_outside_mirror_symlink(self):
    self.managed()
    target = self.base / "outside"
    target.write_text("preserved")
    mirror = self.root / "etc/pacman.d/mirrorlist"
    mirror.unlink()
    mirror.symlink_to(target)
    with (
      patch.object(rootfs, "_require_host"),
      patch.object(rootfs, "_require_tools"),
      patch.object(rootfs, "_require_quiescent"),
      self.assertRaisesRegex(rootfs.RootfsError, "symlink"),
    ):
      rootfs.configure_rootfs(dict(self.options, execute=True))
    self.assertEqual(target.read_text(), "preserved")

  def test_export_rejects_unconfigured_or_inside_output(self):
    self.managed()
    with self.assertRaisesRegex(rootfs.RootfsError, "configure"):
      rootfs.export_wsl(dict(self.options, wsl_output=str(self.base / "out.tar")))
    with self.assertRaisesRegex(rootfs.RootfsError, "outside"):
      rootfs.export_wsl(dict(self.options, wsl_output=str(self.root / "out.tar")))

  def test_export_actual_small_tar_has_no_wrapper_or_runtime(self):
    self.managed(configured=True, wsl=True)
    for name in ("proc/fixture", "etc/machine-id", "var/cache/pacman/pkg/cache"):
      path = self.root / name
      path.parent.mkdir(parents=True, exist_ok=True)
      path.write_text("exclude me")
    output = self.base / "out.tar"
    with patch.object(rootfs, "_require_host"):
      rootfs.export_wsl(dict(self.options, execute=True, wsl_output=str(output)))
    with tarfile.open(output) as archive:
      names = archive.getnames()
    self.assertIn("./usr/bin/bash", names)
    self.assertNotIn("./proc/fixture", names)
    self.assertNotIn("./etc/machine-id", names)
    self.assertNotIn("./" + rootfs.MARKER, names)
    self.assertTrue(all(not name.startswith("root.x86_64") for name in names))
    with self.assertRaisesRegex(rootfs.RootfsError, "already exists"):
      rootfs.export_wsl(dict(self.options, wsl_output=str(output)))

  def test_keyring_daemon_cleanup_on_success_and_failure(self):
    # Execute the production keyring section with harmless shell stand-ins.
    # Marker files model daemon state without launching persistent processes.
    setup = """set -euo pipefail
confirm=()
packages=(base sudo)
function mount() {
  printf 'mount:%s\\n' "$*" >> "$FIXTURE_LOG"
}
function pacman-key() {
  printf '%s\\n' "$*" >> "$FIXTURE_LOG"
  : > "$FIXTURE_AGENT"
}
function pacman() {
  if [[ $1 == -Syu ]]; then
    return "$FIXTURE_STATUS"
  fi
}
function gpgconf() {
  printf 'cleanup:%s\\n' "$*" >> "$FIXTURE_LOG"
  rm -- "$FIXTURE_AGENT"
}
"""
    for status in (0, 37):
      with self.subTest(status=status):
        agent = self.base / f"agent-{status}"
        log = self.base / f"log-{status}"
        env = os.environ.copy()
        env.update(
          FIXTURE_LOG=str(log),
          FIXTURE_AGENT=str(agent),
          FIXTURE_STATUS=str(status),
        )
        result = subprocess.run(
          ["bash", "-c", setup + rootfs._KEYRING_SCRIPT],
          env=env,
          capture_output=True,
          text=True,
        )
        self.assertEqual(result.returncode, status, result.stderr)
        self.assertFalse(agent.exists())
        self.assertEqual(
          log.read_text().splitlines(),
          [
            "mount:-t tmpfs -o mode=0755,nosuid,nodev tmpfs /run",
            "--init",
            "--populate archlinux",
            "cleanup:--homedir /etc/pacman.d/gnupg --kill all",
          ],
        )

  def test_namespace_and_configuration_shell_syntax(self):
    for script in (rootfs._NAMESPACE_SCRIPT, rootfs._CONFIGURE_SCRIPT):
      subprocess.run(["bash", "-n"], input=script, text=True, check=True)


if __name__ == "__main__":
  unittest.main()
