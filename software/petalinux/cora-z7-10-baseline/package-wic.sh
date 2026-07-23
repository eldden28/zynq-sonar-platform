#!/bin/sh
set -eu

# Keep the boot partition small and, critically, include the separately loaded
# device tree.  PetaLinux 2025.1's package-wic defaults omit system.dtb and
# allocate 2 GiB + 4 GiB partitions.
exec petalinux-package wic \
    --size 512M,2G \
    --extra-bootfiles system.dtb \
    "$@"
