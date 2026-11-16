#!/usr/bin/env bash
# Run the wsl-export stage with the same configuration as bootstrap.sh.
# -----------------------------------------------------------------------------
set -euo pipefail

function main() {
  local script_dir
  script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
  exec python3 "$script_dir/bootstrap.py" wsl-export "$@"
}

main "$@"
