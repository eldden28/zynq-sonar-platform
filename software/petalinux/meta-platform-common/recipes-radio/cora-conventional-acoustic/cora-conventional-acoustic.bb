SUMMARY = "Standalone conventional BPSK, QPSK, FSK, and MFSK acoustic modem"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

inherit python3-dir update-rc.d

RDEPENDS:${PN} = " \
    alsa-utils-aplay \
    python3-core \
    python3-io \
    python3-json \
    python3-netserver \
    python3-numpy \
    python3-threading \
"

FILESEXTRAPATHS:prepend := "${PLATFORM_COMMON_SOURCE_ROOT}/cora-conventional-acoustic:"
SRC_URI = " \
    file://cora_conventional_acoustic.py \
    file://cora_conventional_dashboard.py \
    file://www/index.html \
    file://init/cora-conventional-dashboard \
    file://config/cora-conventional-dashboard \
    file://README.md \
"

S = "${WORKDIR}"

INITSCRIPT_NAME = "cora-conventional-dashboard"
INITSCRIPT_PARAMS = "start 84 2 3 4 5 . stop 16 0 1 6 ."

do_install() {
    install -d ${D}${bindir} ${D}${sbindir} ${D}${PYTHON_SITEPACKAGES_DIR}
    install -m 0755 ${WORKDIR}/cora_conventional_acoustic.py \
        ${D}${bindir}/cora-conventional-acoustic
    install -m 0644 ${WORKDIR}/cora_conventional_acoustic.py \
        ${D}${PYTHON_SITEPACKAGES_DIR}/cora_conventional_acoustic.py
    install -m 0755 ${WORKDIR}/cora_conventional_dashboard.py \
        ${D}${sbindir}/cora-conventional-dashboard

    install -d ${D}${datadir}/cora-conventional-acoustic/www
    install -m 0644 ${WORKDIR}/www/index.html \
        ${D}${datadir}/cora-conventional-acoustic/www/index.html

    install -d ${D}${sysconfdir}/init.d ${D}${sysconfdir}/default
    install -m 0755 ${WORKDIR}/init/cora-conventional-dashboard \
        ${D}${sysconfdir}/init.d/cora-conventional-dashboard
    install -m 0644 ${WORKDIR}/config/cora-conventional-dashboard \
        ${D}${sysconfdir}/default/cora-conventional-dashboard

    install -d ${D}${docdir}/cora-conventional-acoustic
    install -m 0644 ${WORKDIR}/README.md \
        ${D}${docdir}/cora-conventional-acoustic/README.md
}

CONFFILES:${PN} = "${sysconfdir}/default/cora-conventional-dashboard"

FILES:${PN} += " \
    ${bindir}/cora-conventional-acoustic \
    ${sbindir}/cora-conventional-dashboard \
    ${PYTHON_SITEPACKAGES_DIR}/cora_conventional_acoustic.py \
    ${datadir}/cora-conventional-acoustic \
    ${sysconfdir}/init.d/cora-conventional-dashboard \
    ${sysconfdir}/default/cora-conventional-dashboard \
    ${docdir}/cora-conventional-acoustic/README.md \
"
