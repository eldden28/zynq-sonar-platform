SUMMARY = "Acoustic waveform adapter for the Cora AXI-stream PL loopback"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

inherit python3-dir

RDEPENDS:${PN} = "cora-dsp python3-core python3-numpy"

FILESEXTRAPATHS:prepend := "${PLATFORM_COMMON_SOURCE_ROOT}/cora-acomms-pl:"
SRC_URI = "file://cora_acomms_pl.py file://README.md"

S = "${WORKDIR}"

do_install() {
    install -d ${D}${PYTHON_SITEPACKAGES_DIR} ${D}${docdir}/cora-acomms-pl
    install -m 0644 ${WORKDIR}/cora_acomms_pl.py \
        ${D}${PYTHON_SITEPACKAGES_DIR}/cora_acomms_pl.py
    install -m 0644 ${WORKDIR}/README.md \
        ${D}${docdir}/cora-acomms-pl/README.md
}

FILES:${PN} = " \
    ${PYTHON_SITEPACKAGES_DIR}/cora_acomms_pl.py \
    ${docdir}/cora-acomms-pl/README.md \
"
