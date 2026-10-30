SUMMARY = "Minimal sonar EDF image for boot and driver bring-up"
LICENSE = "MIT"

# This deliverable boots a physical Cora SD card; do not build the generic
# machine's system emulators and runqemu helpers as image dependencies.
IMAGE_CLASSES:remove = "qemuboot-xilinx image-types-xilinx-qemu"
EXTRA_IMAGEDEPENDS:remove = "qemu-devicetrees-native qemu-system-native qemu-helper-native:do_addto_recipe_sysroot"
# Its uEnv generator recursively depends on the machine's QEMU image tools.
# This board boots through our PARTUUID-aware boot.scr and does not use uEnv.
EXTRA_IMAGEDEPENDS:remove:ultrazed-3eg-iocc = "u-boot-xlnx-uenv"
inherit core-image

COMPATIBLE_MACHINE = "^(cora-z7-10-edf|ultrazed-3eg-iocc-edf)$"
IMAGE_FEATURES += "ssh-server-openssh"
IMAGE_INSTALL:append = " os-release kernel-modules cora-pwm cora-dsp iproute2 i2c-tools usbutils"
IMAGE_INSTALL:remove:ultrazed-3eg-iocc = "cora-pwm"
IMAGE_LINGUAS = "en-us"

# Do not inherit the aarch64-only EDF disk image/package set.
