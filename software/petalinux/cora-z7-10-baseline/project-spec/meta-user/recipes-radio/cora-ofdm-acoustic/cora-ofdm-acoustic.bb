SUMMARY = "Speaker-to-microphone acoustic OFDM modem for the Cora Z7-10"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

inherit python3-dir

RDEPENDS:${PN} = " \
    alsa-utils-aplay \
    python3-core \
    python3-json \
    python3-numpy \
"

FILESEXTRAPATHS:prepend := "${THISDIR}/../../../../../../cora-ofdm-acoustic:"
SRC_URI = " \
    file://cora_ofdm_acoustic.py \
    file://cora_ofdm_fec.c \
    file://README.md \
"

S = "${WORKDIR}"

do_compile() {
    ${CC} ${CFLAGS} ${LDFLAGS} -fPIC -shared \
        -Wl,-soname,libcora_ofdm_fec.so.1 \
        -o libcora_ofdm_fec.so.1 ${WORKDIR}/cora_ofdm_fec.c
}

do_install() {
    install -d ${D}${bindir} ${D}${PYTHON_SITEPACKAGES_DIR} \
        ${D}${libdir} ${D}${docdir}/cora-ofdm-acoustic
    install -m 0755 ${WORKDIR}/cora_ofdm_acoustic.py \
        ${D}${bindir}/cora-ofdm-acoustic
    install -m 0644 ${WORKDIR}/cora_ofdm_acoustic.py \
        ${D}${PYTHON_SITEPACKAGES_DIR}/cora_ofdm_acoustic.py
    install -m 0755 ${B}/libcora_ofdm_fec.so.1 \
        ${D}${libdir}/libcora_ofdm_fec.so.1
    install -m 0644 ${WORKDIR}/README.md \
        ${D}${docdir}/cora-ofdm-acoustic/README.md
}

FILES:${PN} = " \
    ${bindir}/cora-ofdm-acoustic \
    ${libdir}/libcora_ofdm_fec.so.1 \
    ${PYTHON_SITEPACKAGES_DIR}/cora_ofdm_acoustic.py \
    ${docdir}/cora-ofdm-acoustic/README.md \
"
