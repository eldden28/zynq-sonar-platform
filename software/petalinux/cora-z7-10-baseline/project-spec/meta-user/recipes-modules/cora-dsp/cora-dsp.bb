SUMMARY = "Cora Z7 DMA FFT/filter/IFFT accelerator kernel client"
LICENSE = "GPL-2.0-only"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/GPL-2.0-only;md5=801f80980d171dd6425610833a22dbe6"

inherit module

FILESEXTRAPATHS:prepend := "${THISDIR}/../../../../../../cora-dsp:"
SRC_URI = " \
    file://kernel/Makefile \
    file://kernel/cora_dsp.c \
    file://include/cora_dsp_ioctl.h \
    file://udev/99-cora-dsp.rules \
"

S = "${WORKDIR}/kernel"

KERNEL_MODULE_AUTOLOAD += "cora_dsp"

do_install:append() {
    install -d ${D}${sysconfdir}/udev/rules.d
    install -m 0644 ${WORKDIR}/udev/99-cora-dsp.rules \
        ${D}${sysconfdir}/udev/rules.d/99-cora-dsp.rules
}

FILES:${PN} += "${sysconfdir}/udev/rules.d/99-cora-dsp.rules"
