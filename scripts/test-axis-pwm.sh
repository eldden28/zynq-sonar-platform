#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$script_dir/.." && pwd)
rtl_dir="$repo_root/hardware/rtl/axis_pwm_s16"
build_dir="$repo_root/build/sim/axis_pwm_s16"

vivado_bin=${VIVADO_BIN:-/tools/Xilinx/2025.1/Vivado/bin}
if command -v xvlog >/dev/null 2>&1; then
    xvlog_command=$(command -v xvlog)
    xelab_command=$(command -v xelab)
    xsim_command=$(command -v xsim)
elif [[ -x $vivado_bin/xvlog && -x $vivado_bin/xelab && -x $vivado_bin/xsim ]]; then
    xvlog_command=$vivado_bin/xvlog
    xelab_command=$vivado_bin/xelab
    xsim_command=$vivado_bin/xsim
else
    printf 'ERROR: Vivado simulator tools were not found.\n' >&2
    printf 'Set VIVADO_BIN or source scripts/activate-tools.sh.\n' >&2
    exit 1
fi

mkdir -p "$build_dir"
cd "$build_dir"

"$xvlog_command" --sv \
    "$rtl_dir/axis_pwm_s16.sv" \
    "$rtl_dir/axis_pwm_s16_axi.sv" \
    "$rtl_dir/tb_axis_pwm_s16.sv" \
    "$rtl_dir/tb_axis_pwm_s16_axi.sv"
"$xelab_command" tb_axis_pwm_s16 -s axis_pwm_s16_sim
"$xsim_command" axis_pwm_s16_sim -runall | tee axis_pwm_s16_sim.log
grep -Fq "axis_pwm_s16 self-check: PASS" axis_pwm_s16_sim.log
"$xelab_command" tb_axis_pwm_s16_axi -s axis_pwm_s16_axi_sim
"$xsim_command" axis_pwm_s16_axi_sim -runall | tee axis_pwm_s16_axi_sim.log
grep -Fq "axis_pwm_s16_axi self-check: PASS" axis_pwm_s16_axi_sim.log
