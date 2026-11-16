"""Image safety checks and a small real-file xorriso metadata integration test."""

import contextlib
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lib"))
from archbootstrap import images  # noqa: E402


REPORT = """El Torito boot img : 1 BIOS y none 0x0000 0x00 4 66
El Torito boot img : 2 UEFI y none 0x0000 0x00 128 34
System area summary: MBR isohybrid cyl-align-on GPT
MBR partition      : 1 0x80 0x00 0 2048
MBR partition      : 2 0x00 0xef 136 128
GPT partition name : 1 49005300
GPT start and size : 2 136 128
"""


class ImageTests(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory(dir=REPO)
    self.addCleanup(self.temp.cleanup)
    self.base = Path(self.temp.name)

  def iso_opts(self):
    return {
      "iso_profile": str(self.base / "source-profile"),
      "iso_profile_dir": str(self.base / "profile"),
      "iso_work_dir": str(self.base / "work"),
      "iso_out_dir": str(self.base / "out"),
    }

  def append_opts(self):
    source = self.base / "input.iso"
    source.write_bytes(b"fixture")
    return {
      "input_iso": str(source),
      "output_image": str(self.base / "output.img"),
      "data_image": str(self.base / "data.ext4"),
      "data_size": "32M",
    }

  def test_iso_preview_does_not_make_directories(self):
    with contextlib.redirect_stdout(io.StringIO()):
      images.build_iso(self.iso_opts())
    self.assertEqual(list(self.base.iterdir()), [])

  def test_iso_preview_allows_pending_download_payload(self):
    opts = {**self.iso_opts(), "iso_include_tarball": True, "tarball": ""}
    output = io.StringIO()
    with patch.object(images.subprocess, "run") as run:
      with contextlib.redirect_stdout(output):
        images.build_iso(opts)
    run.assert_not_called()
    self.assertIn("pending selected archive", output.getvalue())
    self.assertEqual(list(self.base.iterdir()), [])

  def test_iso_execute_requires_selected_payload_before_writing(self):
    opts = {
      **self.iso_opts(),
      "execute": True,
      "iso_include_tarball": True,
      "tarball": "",
    }
    with self.assertRaisesRegex(images.ImageError, "selected --tarball"):
      images.build_iso(opts)
    self.assertEqual(list(self.base.iterdir()), [])

  def test_iso_offline_execution_rejected_before_writing(self):
    opts = {**self.iso_opts(), "execute": True, "offline": True}
    with self.assertRaisesRegex(images.ImageError, "Offline"):
      images.build_iso(opts)
    self.assertEqual(list(self.base.iterdir()), [])

  def test_append_preview_does_not_write(self):
    opts = self.append_opts()
    with patch.object(images.subprocess, "run") as run:
      with contextlib.redirect_stdout(io.StringIO()):
        images.append_data(opts)
    run.assert_not_called()
    self.assertEqual([p.name for p in self.base.iterdir()], ["input.iso"])

  def test_existing_output_rejected(self):
    opts = self.append_opts()
    output = Path(opts["output_image"])
    output.write_text("keep")
    with self.assertRaisesRegex(images.ImageError, "overwrite"):
      images.append_data(opts)
    self.assertEqual(output.read_text(), "keep")

  def test_symlink_output_rejected(self):
    opts = self.append_opts()
    Path(opts["output_image"]).symlink_to(self.base / "missing")
    with self.assertRaisesRegex(images.ImageError, "Symlink"):
      images.append_data(opts)

  def test_symlink_parent_rejected(self):
    alias = self.base / "alias"
    alias.symlink_to(self.base, target_is_directory=True)
    with self.assertRaisesRegex(images.ImageError, "Symlink"):
      images._fresh(alias / "output")

  def test_device_output_rejected(self):
    with self.assertRaises(images.ImageError):
      images._fresh("/dev/nonexistent-image-output")

  def test_overlapping_iso_directories_rejected(self):
    opts = self.iso_opts()
    opts["iso_out_dir"] = str(self.base / "work" / "out")
    with self.assertRaisesRegex(images.ImageError, "overlap"):
      images.build_iso(opts)

  def test_unsafe_iso_name_rejected(self):
    opts = self.iso_opts()
    opts["iso_name"] = "$(touch evil)"
    with self.assertRaisesRegex(images.ImageError, "iso_name"):
      images.build_iso(opts)

  def test_extra_packages_are_a_single_argv_value(self):
    args = images.iso_command(
      {"iso_packages": ["neovim", "python"]},
      Path("profile"),
      Path("work"),
      Path("out"),
    )
    self.assertEqual(args[args.index("-p") + 1], "neovim python")
    self.assertNotIn("-r", args)

  def test_iso_stages_a_copy_and_keeps_source_profile_unchanged(self):
    opts = self.iso_opts()
    source = Path(opts["iso_profile"])
    source.mkdir()
    definition = source / "profiledef.sh"
    definition.write_text('iso_name="original"\n')
    (source / "airootfs").mkdir()
    payload = self.base / "archlinux-bootstrap-fixture.tar.zst"
    payload.write_bytes(b"fixture payload")
    payload.with_name(payload.name + ".sig").write_bytes(b"fixture signature")
    opts.update(
      {
        "execute": True,
        "iso_include_tarball": True,
        "tarball": str(payload),
        "iso_min_free_gib": 0.001,
      }
    )

    def fake_build(argv, execute, capture=False):
      self.assertEqual(argv[0], "mkarchiso")
      output = Path(opts["iso_out_dir"])
      output.mkdir()
      (output / "fixture.iso").write_bytes(b"not a real ISO")
      return ""

    with (
      patch.object(images.platform, "system", return_value="Linux"),
      patch.object(images.platform, "machine", return_value="x86_64"),
      patch.object(
        images.platform,
        "freedesktop_os_release",
        return_value={
          "ID": "arch",
        },
      ),
      patch.object(images.os, "geteuid", return_value=0),
      patch.object(images, "_tools"),
      patch.object(images, "_run", side_effect=fake_build),
      contextlib.redirect_stdout(io.StringIO()),
    ):
      images.build_iso(opts)
    self.assertEqual(definition.read_text(), 'iso_name="original"\n')
    staged = Path(opts["iso_profile_dir"])
    self.assertIn(
      "iso_name=archlinux-custom", (staged / "profiledef.sh").read_text()
    )
    self.assertEqual(
      (staged / "airootfs/root/bootstrap" / payload.name).read_bytes(),
      b"fixture payload",
    )
    self.assertTrue(
      (staged / "airootfs/root/bootstrap" / (payload.name + ".sig")).is_file()
    )

  def test_option_like_package_rejected(self):
    with self.assertRaises(images.ImageError):
      images.iso_command(
        {"iso_packages": ["--help"]}, Path("p"), Path("w"), Path("o")
      )

  def test_size_bounds_and_no_relative_truncate(self):
    self.assertEqual(images.data_bytes("32M"), 32 * 2**20)
    for value in ["+8G", "8GB", "0G", "1M", "999999G", "1.5G"]:
      with self.subTest(size=value), self.assertRaises(images.ImageError):
        images.data_bytes(value)

  def test_slot_detection_includes_replay_slots(self):
    report = REPORT + "-append_partition 4 0xef --interval:imported_iso\n"
    self.assertEqual(images.occupied_slots(report), {1, 2, 4})
    images.validate_layout(report, 3)
    with self.assertRaisesRegex(images.ImageError, "already used"):
      images.validate_layout(report, 4)

  def test_plain_data_iso_rejected(self):
    with self.assertRaisesRegex(images.ImageError, "El Torito"):
      images.validate_layout("System area summary: MBR", 3)

  def test_overlay_replaces_destination_symlink(self):
    source = self.base / "overlay"
    dest = self.base / "dest"
    outside = self.base / "outside"
    source.mkdir()
    dest.mkdir()
    outside.mkdir()
    (outside / "keep").write_text("original")
    (dest / "etc").symlink_to(outside, target_is_directory=True)
    (source / "etc").mkdir()
    (source / "etc" / "keep").write_text("new")
    images._copy_overlay(source, dest)
    self.assertEqual((outside / "keep").read_text(), "original")
    self.assertEqual((dest / "etc" / "keep").read_text(), "new")
    self.assertFalse((dest / "etc").is_symlink())

  def test_append_command_replays_before_appending(self):
    args = images.append_command(Path("in"), Path("out"), Path("data"), 3)
    self.assertLess(args.index("replay"), args.index("-append_partition"))
    self.assertEqual(
      args[args.index("-append_partition") + 1 :][:2], ["3", "0x83"]
    )

  @unittest.skipUnless(
    shutil.which("xorriso")
    and shutil.which("mkfs.ext4")
    and Path("/usr/lib/syslinux/bios/isolinux.bin").is_file()
    and Path("/usr/lib/syslinux/bios/isohdpfx.bin").is_file(),
    "requires xorriso, e2fsprogs and local Syslinux fixture boot files",
  )
  def test_small_real_hybrid_remaster_preserves_boot_entries(self):
    """A 32 MiB file fixture tests metadata; it is not a bootable OS test."""
    root = self.base / "tree"
    root.mkdir()
    (root / "efi.img").write_bytes(b"\0" * 65536)
    shutil.copyfile("/usr/lib/syslinux/bios/isolinux.bin", root / "isolinux.bin")
    source = self.base / "source.iso"
    subprocess.run(
      [
        "xorriso",
        "-as",
        "mkisofs",
        "-R",
        "-V",
        "TEST",
        "-o",
        str(source),
        "-b",
        "isolinux.bin",
        "-no-emul-boot",
        "-boot-load-size",
        "4",
        "-boot-info-table",
        "-isohybrid-mbr",
        "/usr/lib/syslinux/bios/isohdpfx.bin",
        "-eltorito-alt-boot",
        "-e",
        "efi.img",
        "-no-emul-boot",
        "-isohybrid-gpt-basdat",
        str(root),
      ],
      check=True,
      stdout=subprocess.PIPE,
      stderr=subprocess.PIPE,
    )
    opts = {
      "execute": True,
      "input_iso": str(source),
      "output_image": str(self.base / "with-data.img"),
      "data_image": str(self.base / "data.ext4"),
      "data_size": "32M",
    }
    with contextlib.redirect_stdout(io.StringIO()):
      output = images.append_data(opts)
    report = Path(output + ".layout.txt").read_text()
    self.assertIn("UEFI", report)
    self.assertIn("BIOS", report)
    self.assertIn(3, images.occupied_slots(report))
    self.assertGreater(Path(output).stat().st_size, 32 * 2**20)


if __name__ == "__main__":
  unittest.main()
