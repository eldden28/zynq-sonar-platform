SUMMARY = "Headless underwater OFDM CPU baseline for the Cora Z7-10"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

inherit python3-dir

RDEPENDS:${PN} = " \
    gnuradio \
    gnuradio-blocks \
    gnuradio-channels \
    gnuradio-digital \
    gnuradio-fft \
    gnuradio-filter \
    python3-core \
    python3-json \
    python3-netclient \
    python3-numpy \
"

FILESEXTRAPATHS:prepend := "${PLATFORM_COMMON_SOURCE_ROOT}/cora-ofdm:"
SRC_URI = "file://cora_ofdm.py"

S = "${WORKDIR}"

do_install() {
    install -d ${D}${bindir} ${D}${PYTHON_SITEPACKAGES_DIR}
    install -m 0755 ${WORKDIR}/cora_ofdm.py ${D}${bindir}/cora-ofdm
    install -m 0644 ${WORKDIR}/cora_ofdm.py \
        ${D}${PYTHON_SITEPACKAGES_DIR}/cora_ofdm.py
}

FILES:${PN} = " \
    ${bindir}/cora-ofdm \
    ${PYTHON_SITEPACKAGES_DIR}/cora_ofdm.py \
"
