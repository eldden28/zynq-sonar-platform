SUMMARY = "Minimal Cora EDF image for boot and driver bring-up"
LICENSE = "MIT"
inherit core-image

COMPATIBLE_MACHINE = "^cora-z7-10-edf$"
IMAGE_FEATURES += "ssh-server-openssh"
IMAGE_INSTALL:append = " kernel-modules cora-pwm cora-dsp iproute2 i2c-tools usbutils"
IMAGE_LINGUAS = "en-us"

# Do not inherit the aarch64-only EDF disk image/package set.
