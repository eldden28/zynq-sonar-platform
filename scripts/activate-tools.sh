#!/usr/bin/env bash

# This script must be sourced so the vendor environment remains in the caller.
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    printf 'ERROR: source this script instead of executing it:\n' >&2
    printf '  source scripts/activate-tools.sh\n' >&2
    exit 1
fi

_fpga_tools_root=${FPGA_TOOLS_ROOT:-/tools/Xilinx}
_fpga_tools_version=${FPGA_TOOLS_VERSION:-2025.1}
_fpga_settings="${_fpga_tools_root}/${_fpga_tools_version}/settings64.sh"
_fpga_petalinux_settings="${_fpga_tools_root}/${_fpga_tools_version}/PetaLinux/tool/settings.sh"

if [[ -r $_fpga_settings ]]; then
    # Vendor-provided environment setup when included by the installation.
    # shellcheck disable=SC1090
    source "$_fpga_settings"
else
    # The 2025.1 unified Linux layout may omit the historical top-level
    # settings64.sh. Its launchers are self-contained, so expose their bins.
    _fpga_vivado_bin="${_fpga_tools_root}/${_fpga_tools_version}/Vivado/bin"
    _fpga_vitis_bin="${_fpga_tools_root}/${_fpga_tools_version}/Vitis/bin"
    if [[ ! -x ${_fpga_vivado_bin}/vivado ]]; then
        printf 'ERROR: Vivado executable is not available: %s/vivado\n' "$_fpga_vivado_bin" >&2
        printf 'Override FPGA_TOOLS_ROOT or FPGA_TOOLS_VERSION if needed.\n' >&2
        unset _fpga_tools_root _fpga_tools_version _fpga_settings _fpga_vivado_bin _fpga_vitis_bin
        return 1
    fi
    PATH="${_fpga_vivado_bin}:${_fpga_vitis_bin}:${PATH}"
    export PATH
fi

if ! command -v vivado >/dev/null 2>&1; then
    printf 'ERROR: Vivado was not added to PATH by %s\n' "$_fpga_settings" >&2
    unset _fpga_tools_root _fpga_tools_version _fpga_settings
    return 1
fi

if [[ -r $_fpga_petalinux_settings ]]; then
    # PetaLinux performs its own host prerequisite checks while being sourced.
    # shellcheck disable=SC1090
    source "$_fpga_petalinux_settings"
    if [[ ${PETALINUX_VER:-} != "$_fpga_tools_version" ]]; then
        printf 'ERROR: expected PetaLinux %s, detected %s\n' \
            "$_fpga_tools_version" "${PETALINUX_VER:-unknown}" >&2
        unset _fpga_tools_root _fpga_tools_version _fpga_settings \
            _fpga_petalinux_settings _fpga_vivado_bin _fpga_vitis_bin
        return 1
    fi
else
    printf 'NOTICE: PetaLinux settings are not available: %s\n' \
        "$_fpga_petalinux_settings" >&2
fi

printf 'Activated AMD/Xilinx tools %s from %s\n' "$_fpga_tools_version" "$_fpga_tools_root"
vivado -version 2>&1 | head -n 1
if [[ -n ${PETALINUX_VER:-} ]]; then
    printf 'PetaLinux %s activated from %s\n' "$PETALINUX_VER" "$PETALINUX"
fi

unset _fpga_tools_root _fpga_tools_version _fpga_settings _fpga_petalinux_settings \
    _fpga_vivado_bin _fpga_vitis_bin
