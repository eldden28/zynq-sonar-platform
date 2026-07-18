#!/usr/bin/env bash

set -u
set -o pipefail

section() {
    printf '\n## %s\n' "$1"
}

command_status() {
    local command_name=$1
    if command -v "$command_name" >/dev/null 2>&1; then
        printf '%-24s FOUND (%s)\n' "$command_name" "$(command -v "$command_name")"
    else
        printf '%-24s MISSING\n' "$command_name"
    fi
}

package_status() {
    local package_name=$1
    local status
    local version
    status=$(dpkg-query -W -f='${db:Status-Abbrev}' "$package_name" 2>/dev/null || true)
    if [[ $status == ii* ]]; then
        version=$(dpkg-query -W -f='${Version}' "$package_name" 2>/dev/null)
        printf '%-24s INSTALLED (%s)\n' "$package_name" "$version"
    else
        printf '%-24s NOT INSTALLED\n' "$package_name"
    fi
}

tool_version() {
    local tool_name=$1
    shift
    if command -v "$tool_name" >/dev/null 2>&1; then
        printf '%s: %s\n' "$tool_name" "$("$tool_name" "$@" 2>&1 | head -n 1)"
    else
        printf '%s: not found in PATH\n' "$tool_name"
    fi
}

section "Operating system"
if command -v lsb_release >/dev/null 2>&1; then
    lsb_release -ds 2>/dev/null || true
    printf 'Release: %s\n' "$(lsb_release -rs 2>/dev/null || printf 'unknown')"
    printf 'Codename: %s\n' "$(lsb_release -cs 2>/dev/null || printf 'unknown')"
elif [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    printf '%s\n' "${PRETTY_NAME:-unknown}"
fi
printf 'Kernel: %s\n' "$(uname -r)"
printf 'Architecture: %s\n' "$(uname -m)"

section "Hardware resources"
printf 'Logical CPUs: %s\n' "$(getconf _NPROCESSORS_ONLN 2>/dev/null || printf 'unknown')"
if command -v free >/dev/null 2>&1; then
    free -h
fi
printf '\nDisk space for repository filesystem:\n'
df -hP .

section "Shell and locale"
printf 'Configured login shell: %s\n' "${SHELL:-unset}"
printf 'Running shell: %s\n' "${BASH_VERSION:+bash ${BASH_VERSION}}"
locale 2>/dev/null || printf 'Unable to query locale.\n'

section "Required commands"
for item in bash sh make gcc g++ git tar gzip unzip xz perl python3 cmake ninja pkg-config; do
    command_status "$item"
done

section "Candidate host packages"
printf 'Informational only; the final package list depends on the selected AMD tool versions.\n'
for item in build-essential gcc g++ make git libncurses5-dev libncursesw5-dev \
    libssl-dev zlib1g-dev flex bison libselinux1-dev xterm autoconf libtool \
    texinfo zstd iproute2 net-tools; do
    package_status "$item"
done

section "AMD/Xilinx tools"
tool_version vivado -version
tool_version vitis -version
tool_version petalinux-util --version
tool_version xsct -version

if ! command -v vivado >/dev/null 2>&1 && [[ -x /tools/Xilinx/2025.1/Vivado/bin/vivado ]]; then
    printf 'Vivado installed but not activated: /tools/Xilinx/2025.1/Vivado/bin/vivado\n'
fi
if ! command -v vitis >/dev/null 2>&1 && [[ -x /tools/Xilinx/2025.1/Vitis/bin/vitis ]]; then
    printf 'Vitis installed but not activated: /tools/Xilinx/2025.1/Vitis/bin/vitis\n'
fi
if ! command -v xsct >/dev/null 2>&1 && [[ -x /tools/Xilinx/2025.1/Vitis/bin/xsct ]]; then
    printf 'XSCT installed but not activated: /tools/Xilinx/2025.1/Vitis/bin/xsct\n'
fi
if ! command -v petalinux-util >/dev/null 2>&1 && \
    [[ -r /tools/Xilinx/2025.1/PetaLinux/tool/settings.sh ]]; then
    printf 'PetaLinux installed but not activated: /tools/Xilinx/2025.1/PetaLinux/tool\n'
fi

printf '\nCommon installation roots:\n'
for root in /tools/Xilinx /opt/Xilinx /tools/Xilinx/PetaLinux /opt/pkg/petalinux "$HOME/Xilinx"; do
    if [[ -e "$root" ]]; then
        printf 'FOUND   %s\n' "$root"
        find "$root" -mindepth 1 -maxdepth 2 -type d 2>/dev/null | sort | head -n 40
    else
        printf 'ABSENT  %s\n' "$root"
    fi
done

section "Relevant environment variables"
for variable in XILINX_VIVADO XILINX_VITIS PETALINUX XILINX_XRT; do
    if [[ -n ${!variable-} ]]; then
        printf '%s=%s\n' "$variable" "${!variable}"
    else
        printf '%s is unset\n' "$variable"
    fi
done
for variable in XILINXD_LICENSE_FILE LM_LICENSE_FILE; do
    if [[ -n ${!variable-} ]]; then
        printf '%s is set (value redacted)\n' "$variable"
    else
        printf '%s is unset\n' "$variable"
    fi
done

section "GNU Radio"
tool_version gnuradio-config-info --version
tool_version gnuradio-companion --version
if command -v python3 >/dev/null 2>&1; then
    python3 -c 'import gnuradio; print("Python gnuradio module: import succeeded")' 2>/dev/null || \
        printf 'Python gnuradio module: not importable\n'
fi

section "Containers"
tool_version docker --version
tool_version podman --version
if command -v docker >/dev/null 2>&1; then
    if docker info >/dev/null 2>&1; then
        printf 'Docker daemon: accessible to current user\n'
    else
        printf 'Docker daemon: not accessible to current user or not running\n'
    fi
fi

section "USB and JTAG visibility"
if command -v lsusb >/dev/null 2>&1; then
    lsusb 2>&1 || printf 'lsusb failed.\n'
else
    printf 'lsusb is not installed.\n'
fi
printf '\nLikely JTAG devices:\n'
if [[ -d /dev/serial/by-id ]]; then
    find /dev/serial/by-id -maxdepth 1 -type l -printf '%f -> %l\n' 2>/dev/null || true
else
    printf '/dev/serial/by-id is absent.\n'
fi
if command -v hw_server >/dev/null 2>&1; then
    printf 'hw_server: FOUND (%s)\n' "$(command -v hw_server)"
else
    printf 'hw_server: not found in PATH\n'
fi

section "Summary"
printf 'This report made no host changes. Review version support before installing packages.\n'
