SUMMARY = "Cora Z7 AXI DMA to signed-16 PWM kernel client"
LICENSE = "GPL-2.0-only"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/GPL-2.0-only;md5=801f80980d171dd6425610833a22dbe6"

inherit module

FILESEXTRAPATHS:prepend := "${PLATFORM_COMMON_SOURCE_ROOT}/cora-pwm:"
SRC_URI = " \
    file://kernel/Makefile \
    file://kernel/cora_pwm.c \
    file://include/cora_pwm_ioctl.h \
    file://udev/99-cora-pwm.rules \
"

S = "${WORKDIR}/kernel"

KERNEL_MODULE_AUTOLOAD += "cora_pwm"

do_install:append() {
    install -d ${D}${sysconfdir}/udev/rules.d
    install -m 0644 ${WORKDIR}/udev/99-cora-pwm.rules \
        ${D}${sysconfdir}/udev/rules.d/99-cora-pwm.rules
}

FILES:${PN} += "${sysconfdir}/udev/rules.d/99-cora-pwm.rules"
