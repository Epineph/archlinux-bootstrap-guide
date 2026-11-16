# Project context

## Scope and decisions

- Build a TTY-friendly Arch Linux bootstrap guide and modular helpers.
- Defaults preview; `--execute` is an explicit per-invocation switch.
- Shared JSON configuration has common and per-stage sections. CLI arguments
  override file values. Configuration is data, never sourced or evaluated.
- Local dated tarballs take priority unless a path/download is specified.
- Signature verification precedes extraction; a separately trusted SHA-256
  digest is an explicit alternative. No implicit verification bypass.
- A bootstrap root, a WSL export, and an Archiso live ISO are different outputs.
- Helpers create regular files/directories only, with no physical-device
  writing feature. Manual USB commands are documented separately.
- Preserve the existing executable modes of README.md and LICENSE.

## Initial evidence, 2026-11-16

- Host: Arch Linux, x86_64, Linux 6.18 LTS; shell Zsh.
- Existing tarball: `archlinux-bootstrap-2026.11.01-x86_64.tar.zst`,
  127575204 bytes; no need to download another full archive for development.
- No existing project checks, implementation, or nested AGENTS.md files.
- Official latest manifest and release metadata were read live.

## Validation boundary

Use small offline fixtures and static checks. Do not run a full package
installation, ISO build, device write, WSL import, or VM/firmware boot as part
of development. Add exact completed checks and remaining limits at wrap-up.
