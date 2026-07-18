#!/usr/bin/env bash

set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repository_root=$(cd -- "$script_dir/.." && pwd)
project_dir="$repository_root/petalinux/cora-z7-10"
xsa="$repository_root/hardware/export/cora-z7-10-base.xsa"

if [[ ${PETALINUX_VER:-} != 2025.1 ]]; then
    printf 'ERROR: PetaLinux 2025.1 is not active. Run: source scripts/activate-tools.sh\n' >&2
    exit 1
fi
if [[ ! -s $xsa ]]; then
    printf 'ERROR: hardware export is missing: %s\n' "$xsa" >&2
    exit 1
fi
if [[ ! -f $project_dir/project-spec/configs/config ]]; then
    printf 'ERROR: PetaLinux project is not configured: %s\n' "$project_dir" >&2
    exit 1
fi

mkdir -p "$repository_root/reports"
build_log="$repository_root/reports/petalinux-cora-z7-10-build.log"
package_log="$repository_root/reports/petalinux-cora-z7-10-package.log"

cd "$project_dir"
petalinux-build 2>&1 | tee "$build_log"

rm -f images/linux/BOOT.BIN images/linux/petalinux-sdimage.wic
petalinux-package boot \
    --fsbl images/linux/zynq_fsbl.elf \
    --fpga images/linux/system.bit \
    --u-boot \
    --output images/linux/BOOT.BIN \
    2>&1 | tee "$package_log"

petalinux-package wic --size 512M,2G 2>&1 | tee -a "$package_log"

for artifact in \
    images/linux/BOOT.BIN \
    images/linux/uImage \
    images/linux/boot.scr \
    images/linux/rootfs.tar.gz \
    images/linux/petalinux-sdimage.wic; do
    if [[ ! -s $artifact ]]; then
        printf 'ERROR: expected artifact is missing or empty: %s\n' "$artifact" >&2
        exit 1
    fi
done

sha256sum \
    images/linux/BOOT.BIN \
    images/linux/uImage \
    images/linux/boot.scr \
    images/linux/rootfs.tar.gz \
    images/linux/petalinux-sdimage.wic \
    > images/linux/SHA256SUMS

printf 'SD image: %s\n' "$project_dir/images/linux/petalinux-sdimage.wic"
printf 'Checksums: %s\n' "$project_dir/images/linux/SHA256SUMS"
