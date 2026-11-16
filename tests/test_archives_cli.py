"""Exercise cache precedence, verification failure, and safe configuration."""

import contextlib
import hashlib
import io
import json
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from archbootstrap import archives, cli  # noqa: E402
from archbootstrap.common import BootstrapError  # noqa: E402


class Response(io.BytesIO):
  def __init__(self, payload):
    super().__init__(payload)
    self.headers = {"Content-Length": str(len(payload))}


class ArchivesTests(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory(dir=cli.REPO)
    self.addCleanup(self.temp.cleanup)
    self.root = Path(self.temp.name)
    self.opts = cli.settings("fetch", {}, {})
    self.opts.update(tarball_dir=str(self.root), select="newest")
    self.release_patch = patch.object(
      archives, "latest_release", return_value="2026.11.01"
    )
    self.release_patch.start()
    self.addCleanup(self.release_patch.stop)

  def archive(self, date="2026.11.01", data=b"fixture"):
    path = self.root / f"archlinux-bootstrap-{date}-x86_64.tar.gz"
    path.write_bytes(data)
    return path

  def checksum_opts(self, data=b"fixture"):
    self.opts.update(
      execute=True,
      verification="sha256",
      sha256=hashlib.sha256(data).hexdigest(),
    )

  def test_dates_rank_above_undated_and_ignore_unrelated_files(self):
    newest = self.archive()
    older = self.archive("2025.12.01")
    undated = self.root / "archlinux-bootstrap-x86_64.tar.zst"
    undated.write_bytes(b"new mtime")
    (self.root / "other.tar.zst").write_bytes(b"x")
    self.assertEqual(
      archives.cached_archives(self.root), [newest, older, undated]
    )

  def test_noninteractive_automatic_newest_and_explicit_number(self):
    paths = [self.archive(), self.archive("2025.12.01")]
    with patch("sys.stdin.isatty", return_value=False):
      self.assertEqual(archives.select_cached(paths), paths[0])
      with self.assertRaisesRegex(BootstrapError, "TTY"):
        archives.select_cached(paths, "ask")
    self.assertEqual(archives.select_cached(paths, "2"), paths[1])
    with self.assertRaises(BootstrapError):
      archives.select_cached(paths, "0")

  def test_menu_enter_uses_newest(self):
    paths = [self.archive(), self.archive("2025.12.01")]
    with patch("sys.stdin", io.StringIO("\n")) as stdin:
      with patch.object(stdin, "isatty", return_value=True):
        self.assertEqual(archives.select_cached(paths), paths[0])

  def test_target_overrides_cache_and_is_verified(self):
    self.archive()
    explicit = self.root / "renamed.tar.gz"
    explicit.write_bytes(b"fixture")
    self.checksum_opts()
    self.opts["tarball"] = str(explicit)
    self.assertEqual(archives.fetch_tarball(self.opts), explicit)

  def test_preview_with_empty_cache_never_accesses_network_or_creates_dirs(self):
    self.opts["tarball_dir"] = str(self.root / "absent")
    with patch.object(archives, "open_url", side_effect=AssertionError):
      self.assertIsNone(archives.fetch_tarball(self.opts))
    self.assertFalse((self.root / "absent").exists())

  def test_local_hash_failure_leaves_input_unchanged(self):
    path = self.archive(data=b"corrupted")
    self.checksum_opts()
    with self.assertRaisesRegex(BootstrapError, "mismatch"):
      archives.fetch_tarball(self.opts)
    self.assertEqual(path.read_bytes(), b"corrupted")

  def test_latest_manifest_ignores_iso_undated_and_path_traversal(self):
    digest = "b" * 64
    manifest = "\n".join(
      [
        f"{digest}  archlinux-2027.01.01-x86_64.iso",
        f"{digest}  archlinux-bootstrap-x86_64.tar.zst",
        f"{digest}  ../archlinux-bootstrap-2027.01.01-x86_64.tar.zst",
        f"{digest}  archlinux-bootstrap-2026.11.01-x86_64.tar.zst",
        f"{digest}  archlinux-bootstrap-2026.10.01-x86_64.tar.zst",
      ]
    )
    self.assertEqual(
      archives.parse_manifest(manifest),
      (
        "archlinux-bootstrap-2026.11.01-x86_64.tar.zst",
        digest,
      ),
    )

  def test_latest_reuses_verified_existing_file_without_body_download(self):
    path = self.archive()
    self.checksum_opts()
    self.opts["download"] = "latest"
    manifest = f"{self.opts['sha256']}  {path.name}\n"
    with patch.object(archives, "metadata", return_value=manifest):
      with patch.object(archives, "download_file", side_effect=AssertionError):
        self.assertEqual(archives.fetch_tarball(self.opts), path)

  def test_new_download_is_published_only_after_verification(self):
    self.checksum_opts()
    self.opts["download"] = "latest"
    name = "archlinux-bootstrap-2026.11.01-x86_64.tar.gz"
    manifest = f"{self.opts['sha256']}  {name}\n"
    with patch.object(archives, "metadata", return_value=manifest):
      with patch.object(archives, "open_url", return_value=Response(b"fixture")):
        result = archives.fetch_tarball(self.opts)
    self.assertEqual(result.read_bytes(), b"fixture")
    self.assertEqual(list(self.root.iterdir()), [result])

  def test_bad_download_never_publishes_and_staging_is_removed(self):
    self.checksum_opts()
    self.opts["download"] = "latest"
    name = "archlinux-bootstrap-2026.11.01-x86_64.tar.gz"
    manifest = f"{self.opts['sha256']}  {name}\n"
    with patch.object(archives, "metadata", return_value=manifest):
      with patch.object(archives, "open_url", return_value=Response(b"bad")):
        with self.assertRaisesRegex(BootstrapError, "mismatch"):
          archives.fetch_tarball(self.opts)
    self.assertEqual(list(self.root.iterdir()), [])

  def test_network_requests_and_redirects_require_https(self):
    for url in (
      "file:///etc/passwd",
      "http://archlinux.org/x",
      "https://a:b@host/x",
    ):
      with self.assertRaises(BootstrapError):
        archives.https_url(url)
    redirect = archives.HTTPSRedirects()
    with self.assertRaises(BootstrapError):
      redirect.redirect_request(None, None, 302, "", {}, "http://host/file")

  def test_bounded_download_checks_content_length_before_creating_file(self):
    target = self.root / "new"
    with patch.object(archives, "open_url", return_value=Response(b"1234")):
      with self.assertRaises(BootstrapError):
        archives.download_file("https://example.com/file", target, 3)
    self.assertFalse(target.exists())

  def test_cache_symlinks_and_device_targets_are_refused(self):
    path = self.archive()
    alias = self.root / "archlinux-bootstrap-2027.01.01-x86_64.tar.gz"
    alias.symlink_to(path)
    self.assertEqual(archives.cached_archives(self.root), [path])
    self.opts["tarball"] = str(alias)
    with self.assertRaises(BootstrapError):
      archives.fetch_tarball(self.opts)
    self.opts["tarball"] = "/dev/null"
    with self.assertRaises(BootstrapError):
      archives.fetch_tarball(self.opts)

  def test_offline_does_not_download_signature_or_latest(self):
    self.archive()
    keyring = self.root / "trusted.gpg"
    keyring.write_bytes(b"fixture")
    self.opts.update(execute=True, offline=True, keyring=str(keyring))
    with patch.object(archives, "require_tools"):
      with patch.object(archives, "open_url", side_effect=AssertionError):
        with self.assertRaisesRegex(BootstrapError, "signature is missing"):
          archives.fetch_tarball(self.opts)
        self.opts["download"] = "latest"
        with self.assertRaisesRegex(BootstrapError, "Offline"):
          archives.fetch_tarball(self.opts)

  def test_signature_requires_expected_valid_unrevoked_signer(self):
    path = self.archive()
    signature = self.root / "archive.sig"
    signature.write_bytes(b"sig")
    keyring = self.root / "trusted.gpg"
    keyring.write_bytes(b"key")
    self.opts["keyring"] = str(keyring)
    fingerprint = self.opts["signer_fingerprint"]
    good = f"[GNUPG:] VALIDSIG {fingerprint} 1 2 3 4 5 6 7 8 {fingerprint}\n"
    for status, accepted in (
      (good, True),
      (good + "[GNUPG:] REVKEYSIG x\n", False),
      (good.replace(fingerprint, "F" * 40), False),
      (good + "[GNUPG:] EXPKEYSIG x\n", False),
      ("[GNUPG:] GOODSIG someone\n", False),
    ):
      result = subprocess.CompletedProcess([], 0, status, "")
      with patch.object(archives, "require_tools"):
        with patch.object(archives.subprocess, "run", return_value=result):
          if accepted:
            archives.verify_signature(path, signature, self.opts)
          else:
            with self.assertRaises(BootstrapError):
              archives.verify_signature(path, signature, self.opts)

  def test_official_release_discovery_ignores_unavailable_and_rejects_schema(
    self,
  ):
    self.release_patch.stop()
    releases = {
      "version": 1,
      "releases": [
        {"version": "2026.10.01", "available": True},
        {"version": "2026.12.01", "available": False},
        {"version": "2026.11.01", "available": True},
      ],
    }
    with patch.object(archives, "metadata", return_value=json.dumps(releases)):
      self.assertEqual(archives.latest_release(), "2026.11.01")
    with patch.object(archives, "metadata", return_value='{"version": 2}'):
      with self.assertRaises(BootstrapError):
        archives.latest_release()

  def test_stale_mirror_cannot_downgrade_latest_release(self):
    self.checksum_opts()
    self.opts["download"] = "latest"
    manifest = (
      f"{self.opts['sha256']}  archlinux-bootstrap-2026.10.01-x86_64.tar.gz"
    )
    with patch.object(archives, "metadata", return_value=manifest):
      with self.assertRaisesRegex(BootstrapError, "differs"):
        archives.fetch_tarball(self.opts)
    self.assertEqual(list(self.root.iterdir()), [])

  @unittest.skipUnless(
    shutil.which("gpg") and shutil.which("gpgconf"), "GnuPG missing"
  )
  def test_real_signed_fixture_armored_keyring_and_readonly_archive(self):
    home = self.root / "key-home"
    home.mkdir(mode=0o700)
    base = ["gpg", "--no-options", "--homedir", str(home), "--batch"]
    try:
      subprocess.run(
        [
          *base,
          "--pinentry-mode",
          "loopback",
          "--passphrase",
          "",
          "--quick-generate-key",
          "Bootstrap fixture <fixture@example.invalid>",
          "ed25519",
          "sign",
          "0",
        ],
        check=True,
        capture_output=True,
      )
      listed = subprocess.run(
        [*base, "--with-colons", "--list-keys"],
        check=True,
        text=True,
        capture_output=True,
      )
      fingerprint = next(
        line.split(":")[9]
        for line in listed.stdout.splitlines()
        if line.startswith("fpr:")
      )
      ring = self.root / "armored.gpg"
      subprocess.run(
        [*base, "--armor", "--output", str(ring), "--export"],
        check=True,
        capture_output=True,
      )
      media = self.root / "readonly-media"
      media.mkdir()
      archive = media / "archive.tar.gz"
      archive.write_bytes(b"signed fixture")
      signature = media / "archive.tar.gz.sig"
      subprocess.run(
        [*base, "--output", str(signature), "--detach-sign", str(archive)],
        check=True,
        capture_output=True,
      )
      self.opts.update(keyring=str(ring), signer_fingerprint=fingerprint)
      media.chmod(0o555)
      try:
        archives.verify_signature(archive, signature, self.opts)
      finally:
        media.chmod(0o755)
      archive.write_bytes(b"tampered fixture")
      with self.assertRaises(BootstrapError):
        archives.verify_signature(archive, signature, self.opts)
    finally:
      subprocess.run(
        ["gpgconf", "--homedir", str(home), "--kill", "all"],
        check=False,
        capture_output=True,
      )


class ConfigTests(unittest.TestCase):
  def test_common_stage_cli_precedence_and_paths(self):
    config = {
      "common": {"hostname": "common", "root_dir": "work/a"},
      "configure": {"hostname": "stage", "timezone": "UTC"},
    }
    opts = cli.settings("configure", config, {"hostname": "argument"})
    self.assertEqual(opts["hostname"], "argument")
    self.assertEqual(opts["root_dir"], str(cli.REPO / "work/a"))
    self.assertFalse(opts["execute"])

  def test_cli_source_selection_replaces_other_config_selector(self):
    opts = cli.settings(
      "fetch", {"common": {"download": "latest"}}, {"tarball": "a.tar.gz"}
    )
    self.assertEqual(opts["download"], "")
    opts = cli.settings(
      "fetch", {"common": {"tarball": "a.tar.gz"}}, {"download": "latest"}
    )
    self.assertEqual(opts["tarball"], "")

  def test_unknown_type_execute_and_duplicate_settings_rejected(self):
    for values in (
      {"execute": True},
      {"typo": 1},
      {"offline": "true"},
      {"max_download_mib": True},
      {"steps": ["fetch", "fetch"]},
    ):
      with self.assertRaises(BootstrapError):
        cli.validate_values(values, "common")
    with tempfile.TemporaryDirectory(dir=cli.REPO) as directory:
      path = Path(directory) / "config.json"
      path.write_text('{"common":{"offline":false,"offline":true}}')
      with self.assertRaisesRegex(BootstrapError, "Duplicate"):
        cli.load_config(path)

  def test_master_wsl_enables_configuration_and_adds_outputs(self):
    seen = []

    def record(stage, opts, selected):
      seen.append((stage, opts["wsl"]))

    with patch.object(cli, "run_stage", side_effect=record):
      cli.main(["run", "--with-wsl", "--with-iso"])
    self.assertEqual(
      [s for s, _ in seen], ["fetch", "extract", "configure", "wsl-export", "iso"]
    )
    self.assertTrue(dict(seen)["configure"])

  def test_offline_pipeline_fails_before_any_stage(self):
    with patch.object(cli, "run_stage") as stage:
      with self.assertRaisesRegex(BootstrapError, "network"):
        cli.main(["run", "--offline", "--execute"])
    stage.assert_not_called()

  def test_master_preserves_same_source_but_honors_stage_override(self):
    seen = []
    selected = Path("/fixture/tarball.tar.gz")

    def record(stage, opts, previous):
      seen.append((stage, previous))
      return selected

    config = {"extract": {"tarball": "/different/archive.tar.gz"}}
    with patch.object(cli, "load_config", return_value=config):
      with patch.object(cli, "run_stage", side_effect=record):
        cli.main(["run", "--steps", "fetch", "extract"])
    self.assertIsNone(seen[1][1])
    seen.clear()
    with patch.object(cli, "run_stage", side_effect=record):
      cli.main(["run", "--steps", "fetch", "extract"])
    self.assertEqual(seen[1][1], selected)

  def test_example_config_is_valid_and_show_config_is_json(self):
    config = cli.load_config(cli.REPO / "config/example.json")
    self.assertIn("configure", config)
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
      cli.main(["show-config"])
    self.assertFalse(json.loads(output.getvalue())["fetch"]["execute"])


if __name__ == "__main__":
  unittest.main()
