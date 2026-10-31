#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$script_dir/.." && pwd)
rtl_dir="$repo_root/hardware/rtl/axis_acomms_router"
build_dir="$repo_root/build/sim/axis_acomms_router"
vivado_bin=${VIVADO_BIN:-/tools/Xilinx/2025.1/Vivado/bin}

mkdir -p "$build_dir"
cd "$build_dir"
"$vivado_bin/xvlog" --sv \
    "$rtl_dir/axis_acomms_router.sv" \
    "$rtl_dir/tb_axis_acomms_router.sv"
"$vivado_bin/xelab" tb_axis_acomms_router -s axis_acomms_router_sim
"$vivado_bin/xsim" axis_acomms_router_sim -runall | tee axis_acomms_router_sim.log
grep -Fq "axis_acomms_router self-check: PASS" axis_acomms_router_sim.log
