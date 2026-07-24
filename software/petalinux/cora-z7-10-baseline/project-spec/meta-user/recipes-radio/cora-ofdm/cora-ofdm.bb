SUMMARY = "Headless underwater OFDM CPU baseline for the Cora Z7-10"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

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

FILESEXTRAPATHS:prepend := "${THISDIR}/../../../../../../cora-ofdm:"
SRC_URI = "file://cora_ofdm.py"

S = "${WORKDIR}"

do_install() {
    install -d ${D}${bindir}
    install -m 0755 ${WORKDIR}/cora_ofdm.py ${D}${bindir}/cora-ofdm
}

FILES:${PN} = "${bindir}/cora-ofdm"
