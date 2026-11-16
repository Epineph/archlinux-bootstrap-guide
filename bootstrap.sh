#!/usr/bin/env bash
# Master entry point; the shared CLI implements configuration and stage ordering.
# -----------------------------------------------------------------------------
set -euo pipefail

function main() {
  local repo_dir
  repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
  exec python3 "$repo_dir/scripts/bootstrap.py" "$@"
}

main "$@"
