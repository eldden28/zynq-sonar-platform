#!/usr/bin/env bash
set -euo pipefail

if [[ ! -r /etc/os-release ]]; then
    printf 'ERROR: cannot identify the host operating system.\n' >&2
    exit 1
fi

# shellcheck disable=SC1091
source /etc/os-release
if [[ ${ID:-} != ubuntu || ${VERSION_ID:-} != 22.04 ]]; then
    printf 'ERROR: this package set is validated only for Ubuntu 22.04; found %s %s.\n' \
        "${ID:-unknown}" "${VERSION_ID:-unknown}" >&2
    exit 1
fi

packages=(
    autoconf
    automake
    bc
    bison
    build-essential
    chrpath
    cpio
    curl
    diffstat
    fdisk
    flex
    gawk
    gcc-multilib
    git
    graphviz
    gzip
    iproute2
    iputils-ping
    libasound2
    libegl1-mesa
    libgdk-pixbuf2.0-dev
    libglib2.0-dev
    libncurses5
    libncurses5-dev
    libncursesw5-dev
    libnss3-dev
    libsdl1.2-dev
    libsecret-1-dev
    libselinux1-dev
    libssl-dev
    libtinfo5
    libtool
    libxss-dev
    libgtk-3-dev
    locales
    lsb-release
    make
    net-tools
    ninja-build
    pax
    pkg-config
    python3
    python3-git
    python3-jinja2
    python3-pexpect
    python3-pip
    python3-pytest
    python3-subunit
    rsync
    screen
    socat
    tar
    texinfo
    unzip
    wget
    xterm
    xvfb
    xz-utils
    zip
    zlib1g-dev
    zstd
)

sudo apt-get update
sudo apt-get install -y "${packages[@]}"

printf '\nUbuntu prerequisites installed.\n'
printf '/bin/sh -> %s\n' "$(readlink -f /bin/sh)"
if [[ $(readlink -f /bin/sh) != /usr/bin/bash ]]; then
    printf 'ACTION REQUIRED: PetaLinux 2025.1 requires /bin/sh to be Bash.\n'
    printf 'Run: sudo dpkg-reconfigure dash\n'
    printf 'Answer No when asked whether Dash should be the default system shell.\n'
fi

if ! locale -a 2>/dev/null | grep -qi '^en_US\.utf8$'; then
    printf 'ACTION REQUIRED: create the en_US.UTF-8 locale with:\n'
    printf '  sudo locale-gen en_US.UTF-8\n'
fi

if command -v free >/dev/null 2>&1; then
    memory_kib=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)
    swap_kib=$(awk '/^SwapTotal:/ {print $2}' /proc/meminfo)
    if ((memory_kib < 12 * 1024 * 1024 && swap_kib < 16 * 1024 * 1024)); then
        printf 'NOTICE: this low-memory host has less than 16 GiB swap.\n'
        printf 'The validated 8 GiB machine used a persistent 24 GiB swap file.\n'
    fi
fi

printf 'Next: install AMD Vivado and PetaLinux 2025.1, then run:\n'
printf '  source scripts/activate-tools.sh\n'
printf '  scripts/check-host.sh\n'
