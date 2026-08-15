#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repository_root=$(cd -- "$script_dir/.." && pwd)
vivado_command=${VIVADO_COMMAND:-/tools/Xilinx/2025.1/Vivado/bin/vivado}

if command -v vivado >/dev/null 2>&1; then
    vivado_command=$(command -v vivado)
elif [[ ! -x $vivado_command ]]; then
    printf 'ERROR: Vivado was not found. Set VIVADO_COMMAND or activate the tools.\n' >&2
    exit 1
fi

"$script_dir/fetch-avnet-board-files.sh"

mode=${1:-build}
case $mode in
    build)
        tcl_arguments=()
        ;;
    validate)
        tcl_arguments=(-tclargs validate)
        ;;
    *)
        printf 'Usage: %s [build|validate]\n' "$0" >&2
        exit 64
        ;;
esac

"$vivado_command" -mode batch -nolog -nojournal \
    -source "$repository_root/hardware/vivado/tcl/create_ultrazed_3eg_pcie.tcl" \
    "${tcl_arguments[@]}"
