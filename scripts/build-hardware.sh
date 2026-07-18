#!/usr/bin/env bash

set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repository_root=$(cd -- "$script_dir/.." && pwd)

if ! command -v vivado >/dev/null 2>&1; then
    printf 'ERROR: Vivado is not active. Run: source scripts/activate-tools.sh\n' >&2
    exit 1
fi

"$script_dir/fetch-board-files.sh"

mkdir -p "$repository_root/reports"
log="$repository_root/reports/vivado-cora-z7-10-base.log"

cd "$repository_root"
vivado -mode batch -nolog -nojournal \
    -source hardware/vivado/tcl/create_cora_z7_10_base.tcl \
    2>&1 | tee "$log"

test -s "$repository_root/hardware/export/cora-z7-10-base.xsa"
printf 'Hardware export: %s\n' \
    "$repository_root/hardware/export/cora-z7-10-base.xsa"
printf 'Build log: %s\n' "$log"
