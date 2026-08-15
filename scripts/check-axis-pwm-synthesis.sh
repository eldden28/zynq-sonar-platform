#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$script_dir/.." && pwd)
vivado_command=${VIVADO_COMMAND:-/tools/Xilinx/2025.1/Vivado/bin/vivado}

if command -v vivado >/dev/null 2>&1; then
    vivado_command=$(command -v vivado)
elif [[ ! -x $vivado_command ]]; then
    printf 'ERROR: Vivado was not found. Set VIVADO_COMMAND or activate the tools.\n' >&2
    exit 1
fi

"$vivado_command" -mode batch -nolog -nojournal \
    -source "$repo_root/hardware/vivado/tcl/check_axis_pwm_s16.tcl"
