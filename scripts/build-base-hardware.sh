#!/usr/bin/env bash

set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repository_root=$(cd -- "$script_dir/.." && pwd)

if ! command -v vivado >/dev/null 2>&1; then
    printf 'ERROR: Vivado is not active. Run:\n' >&2
    printf '  source scripts/activate-tools.sh\n' >&2
    exit 1
fi

"$repository_root/scripts/fetch-board-files.sh"

vivado -nolog -nojournal -mode batch \
    -source "$repository_root/hardware/vivado/tcl/create_base_platform.tcl"

