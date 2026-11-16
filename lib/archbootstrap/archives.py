"""Date-aware selection, bounded HTTPS downloads, and archive verification."""

import hashlib
import errno
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .common import (
  BootstrapError,
  log,
  new_file,
  publish,
  regular_file,
  require_tools,
  safe_path,
)


ARCHIVE_RE = re.compile(
  r"archlinux-bootstrap-(?:(\d{4}\.\d{2}\.\d{2})-)?"
  r"x86_64\.tar\.(?:zst|gz)\Z"
)
SIGNER = "3E80CA1A8B89F69CBA57D98A76A5EF9054449A5C"
METADATA_LIMIT = 2 * 1024 * 1024


def https_url(url):
  parts = urlsplit(url)
  if (
    parts.scheme != "https"
    or not parts.hostname
    or parts.username
    or parts.password
    or parts.fragment
    or any(ord(char) < 33 for char in url)
  ):
    raise BootstrapError("Expected an HTTPS URL without credentials: " + url)
  return url


class HTTPSRedirects(HTTPRedirectHandler):
  def redirect_request(self, req, fp, code, msg, headers, newurl):
    https_url(newurl)
    return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_url(url):
  https_url(url)
  request = Request(url, headers={"User-Agent": "archlinux-bootstrap-guide/1"})
  return build_opener(HTTPSRedirects()).open(request, timeout=30)


def metadata(url):
  log("Read release metadata: " + url)
  with open_url(url) as response:
    data = response.read(METADATA_LIMIT + 1)
  if len(data) > METADATA_LIMIT:
    raise BootstrapError("Release metadata exceeds 2 MiB")
  return data.decode("utf-8")


def parse_manifest(content):
  """Use only dated bootstrap records; never accidentally select the ISO."""
  records = []
  for line in content.splitlines():
    match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(\S+)", line.strip())
    if match:
      digest, name = match.groups()
      archive = ARCHIVE_RE.fullmatch(name)
      if archive and archive.group(1):
        records.append((archive.group(1), name, digest.lower()))
  if not records:
    raise BootstrapError("No dated x86_64 bootstrap tarball in SHA-256 manifest")
  records.sort(reverse=True)
  return records[0][1:]


def latest_release():
  """Discover the release independently of a potentially lagging mirror."""
  data = json.loads(metadata("https://archlinux.org/releng/releases/json/"))
  if not isinstance(data, dict) or data.get("version") != 1:
    raise BootstrapError("Unrecognized official release metadata format")
  releases = data.get("releases")
  if not isinstance(releases, list):
    raise BootstrapError("Official release metadata lacks a releases list")
  versions = [
    release["version"]
    for release in releases
    if isinstance(release, dict)
    and release.get("available") is True
    and isinstance(release.get("version"), str)
    and re.fullmatch(r"\d{4}\.\d{2}\.\d{2}", release["version"])
  ]
  if not versions:
    raise BootstrapError("No available dated release in official metadata")
  return max(versions)


def cached_archives(directory):
  directory = safe_path(directory)
  if not directory.exists():
    return []
  if not directory.is_dir():
    raise BootstrapError(f"Tarball directory is not a directory: {directory}")
  candidates = []
  for path in directory.iterdir():
    match = ARCHIVE_RE.fullmatch(path.name)
    if match and path.is_file() and not path.is_symlink():
      candidates.append((match.group(1) or "0000.00.00", path.name, path))
  return [item[2] for item in sorted(candidates, reverse=True)]


def select_cached(candidates, selection="auto"):
  if not candidates:
    return None
  if selection in ("auto", "ask") and len(candidates) > 1:
    if not sys.stdin.isatty():
      if selection == "ask":
        raise BootstrapError("--select ask needs a TTY; use newest or a number")
    else:
      log("Available bootstrap archives (newest filename date first):")
      for index, path in enumerate(candidates, 1):
        log(f"  {index}. {path.name}")
      print("Select archive [1]: ", file=sys.stderr, end="", flush=True)
      answer = sys.stdin.readline()
      if not answer:
        raise BootstrapError("Selection ended; use --select newest to proceed")
      selection = answer.strip() or "1"
  if selection in ("auto", "newest", "ask"):
    return candidates[0]
  if not selection.isdecimal() or not 1 <= int(selection) <= len(candidates):
    raise BootstrapError(f"Selection must be 1..{len(candidates)} or newest")
  return candidates[int(selection) - 1]


def sha256(path):
  digest = hashlib.sha256()
  with open(path, "rb") as stream:
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
      digest.update(chunk)
  return digest.hexdigest()


def check_digest(path, expected):
  if not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
    raise BootstrapError("SHA-256 must contain exactly 64 hexadecimal digits")
  actual = sha256(path)
  if actual != expected.lower():
    raise BootstrapError(f"SHA-256 mismatch for {path}; got {actual}")
  log(f"SHA-256 verified: {actual}")


def download_file(url, target, limit):
  """Write a bounded, exclusively created staging file; never append/resume."""
  new_file(target)
  log("Download: " + url)
  with open_url(url) as response:
    size = response.headers.get("Content-Length")
    if size and int(size) > limit:
      raise BootstrapError(f"Download exceeds the {limit} byte limit")
    with open(target, "xb") as stream:
      total = 0
      last = time.monotonic()
      while chunk := response.read(1024 * 1024):
        total += len(chunk)
        if total > limit:
          raise BootstrapError(f"Download exceeded the {limit} byte limit")
        stream.write(chunk)
        if time.monotonic() - last >= 2:
          suffix = f" / {int(size) / 1048576:.1f}" if size else ""
          log(f"  {total / 1048576:.1f}{suffix} MiB received")
          last = time.monotonic()
      stream.flush()
      os.fsync(stream.fileno())
  if not total:
    raise BootstrapError("The server returned an empty file")
  log(f"  Received {total} bytes")


def signature_url(name):
  match = ARCHIVE_RE.fullmatch(name)
  if not match or not match.group(1):
    raise BootstrapError(
      "An undated or renamed local tarball needs its matching --signature PATH"
    )
  return f"https://archlinux.org/iso/{match.group(1)}/{name}.sig"


def verify_signature(path, signature, opts):
  require_tools("gpg")
  keyring = regular_file(opts["keyring"])
  fingerprint = opts["signer_fingerprint"].upper().replace(" ", "")
  if not re.fullmatch(r"[0-9A-F]{40}|[0-9A-F]{64}", fingerprint):
    raise BootstrapError("Use a complete verified release signing fingerprint")
  # Import handles both armored exports and binary keyrings. Arch's shipped
  # archlinux.gpg may be armored despite its extension. A separate temporary
  # home also allows an archive on read-only media and leaves user trust alone.
  with tempfile.TemporaryDirectory(prefix="arch-bootstrap-gpg-") as home:
    base = [
      "gpg",
      "--no-options",
      "--homedir",
      home,
      "--batch",
      "--no-autostart",
      "--no-auto-key-retrieve",
      "--auto-key-locate",
      "clear",
    ]
    imported = subprocess.run(
      [*base, "--import-options", "import-minimal", "--import", str(keyring)],
      text=True,
      capture_output=True,
      check=False,
    )
    if imported.returncode:
      log(imported.stderr.strip())
      raise BootstrapError("Could not import the supplied trusted public keyring")
    argv = [
      *base,
      "--trust-model",
      "always",
      "--status-fd",
      "1",
      "--verify",
      str(signature),
      str(path),
    ]
    result = subprocess.run(argv, text=True, capture_output=True, check=False)
  statuses = [line.split() for line in result.stdout.splitlines()]
  bad = {
    "BADSIG",
    "ERRSIG",
    "REVKEYSIG",
    "EXPKEYSIG",
    "EXPSIG",
    "KEYEXPIRED",
    "SIGEXPIRED",
    "KEYREVOKED",
  }
  valid = any(
    len(parts) >= 3
    and parts[:2] == ["[GNUPG:]", "VALIDSIG"]
    and fingerprint in (parts[2], parts[-1])
    for parts in statuses
  )
  rejected = any(len(parts) > 1 and parts[1] in bad for parts in statuses)
  if result.returncode or rejected or not valid:
    log(result.stderr.strip())
    raise BootstrapError(
      "Signature verification failed or signer differs from " + fingerprint
    )
  log("Signature verified against release signer " + fingerprint)


def verify_local(path, opts, expected_digest=""):
  path = regular_file(path)
  if expected_digest:
    check_digest(path, expected_digest)
  if opts["verification"] == "sha256":
    if not opts["sha256"]:
      raise BootstrapError("--verification sha256 requires --sha256 DIGEST")
    check_digest(path, opts["sha256"])
    log("Trust depends on the independently obtained SHA-256 value.")
    return
  require_tools("gpg")
  regular_file(opts["keyring"])
  signature = (
    Path(opts["signature"]) if opts["signature"] else Path(str(path) + ".sig")
  )
  if signature.exists() or signature.is_symlink():
    verify_signature(path, regular_file(signature), opts)
    return
  if opts["signature"] or opts["offline"]:
    raise BootstrapError(f"Matching signature is missing: {signature}")
  url = signature_url(path.name)
  with tempfile.TemporaryDirectory(prefix="arch-bootstrap-signature-") as temp:
    staged = Path(temp) / "archive.sig"
    download_file(url, staged, METADATA_LIMIT)
    verify_signature(path, staged, opts)
    # Verification works on read-only media. Caching is optional in that case.
    try:
      new_file(signature)
      with signature.open("xb") as stream:
        stream.write(staged.read_bytes())
    except OSError as error:
      if error.errno not in (errno.EACCES, errno.EROFS):
        raise
      log(
        "Verified; signature could not be cached beside this read-only archive."
      )


def fetch_tarball(opts):
  """Return the selected verified path, or None for an unresolved preview."""
  target = opts["tarball"]
  request = opts["download"]
  if target and request:
    raise BootstrapError("--tarball and --download are mutually exclusive")
  if target:
    chosen = regular_file(target)
  elif not request:
    chosen = select_cached(cached_archives(opts["tarball_dir"]), opts["select"])
    if chosen is None:
      request = "latest"
  else:
    chosen = None
  if not opts["execute"]:
    if chosen:
      log(f"Selected local archive: {chosen}")
      log(f"Would verify using {opts['verification']} before use")
    else:
      if request != "latest":
        https_url(request)
      log(f"Would resolve/download {request} into {opts['tarball_dir']}")
      log("Preview performs no network requests. Add --execute to proceed.")
    return chosen
  if chosen:
    verify_local(chosen, opts)
    log(f"Ready: {chosen}")
    return chosen
  if opts["offline"]:
    raise BootstrapError(
      "Offline: select a cached tarball instead of downloading"
    )
  if opts["verification"] == "signature":
    require_tools("gpg")
    regular_file(opts["keyring"])
  expected = ""
  if request == "latest":
    base = https_url(opts["mirror_base"].rstrip("/"))
    release = latest_release()
    name, expected = parse_manifest(metadata(f"{base}/{release}/sha256sums.txt"))
    date = ARCHIVE_RE.fullmatch(name).group(1)
    if date != release:
      raise BootstrapError(
        "Mirror manifest differs from the latest official release"
      )
    url = f"{base}/{date}/{name}"
  else:
    url = https_url(request)
    name = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    if not ARCHIVE_RE.fullmatch(name):
      raise BootstrapError(
        "Download URL must end with an official bootstrap filename"
      )
  directory = safe_path(opts["tarball_dir"])
  destination = directory / name
  if destination.exists() or destination.is_symlink():
    log("The requested archive is already cached; verifying it again.")
    verify_local(destination, opts, expected)
    return destination
  directory.mkdir(parents=True, exist_ok=True)
  new_file(destination)
  with tempfile.TemporaryDirectory(prefix=".download-", dir=directory) as temp:
    staged = Path(temp) / name
    download_file(url, staged, opts["max_download_mib"] * 1024 * 1024)
    verify_local(staged, opts, expected)
    # A failed verification never publishes the archive as a valid cache entry.
    publish(staged, destination)
    sig = Path(str(staged) + ".sig")
    if sig.exists():
      publish(sig, Path(str(destination) + ".sig"))
  log(f"Ready: {destination}")
  return destination
