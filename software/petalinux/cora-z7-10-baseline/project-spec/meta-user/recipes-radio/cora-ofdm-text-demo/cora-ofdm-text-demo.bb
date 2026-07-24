SUMMARY = "Progressive acoustic OFDM text-terminal dashboard for Cora Z7-10"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

inherit python3-dir update-rc.d

RDEPENDS:${PN} = " \
    cora-ofdm-acoustic \
    python3-core \
    python3-io \
    python3-json \
    python3-netserver \
    python3-numpy \
    python3-threading \
"

FILESEXTRAPATHS:prepend := "${THISDIR}/../../../../../../cora-ofdm-text-demo:"
SRC_URI = " \
    file://cora_ofdm_text_transfer.py \
    file://cora_ofdm_text_dashboard.py \
    file://www/index.html \
    file://init/cora-ofdm-text-dashboard \
    file://config/cora-ofdm-text-dashboard \
    file://README.md \
"

S = "${WORKDIR}"

INITSCRIPT_NAME = "cora-ofdm-text-dashboard"
INITSCRIPT_PARAMS = "start 83 2 3 4 5 . stop 17 0 1 6 ."

do_install() {
    install -d ${D}${bindir} ${D}${sbindir} \
        ${D}${PYTHON_SITEPACKAGES_DIR}
    install -m 0755 ${WORKDIR}/cora_ofdm_text_transfer.py \
        ${D}${bindir}/cora-ofdm-text-transfer
    install -m 0644 ${WORKDIR}/cora_ofdm_text_transfer.py \
        ${D}${PYTHON_SITEPACKAGES_DIR}/cora_ofdm_text_transfer.py
    install -m 0755 ${WORKDIR}/cora_ofdm_text_dashboard.py \
        ${D}${sbindir}/cora-ofdm-text-dashboard

    install -d ${D}${datadir}/cora-ofdm-text-demo/www
    install -m 0644 ${WORKDIR}/www/index.html \
        ${D}${datadir}/cora-ofdm-text-demo/www/index.html

    install -d ${D}${sysconfdir}/init.d ${D}${sysconfdir}/default
    install -m 0755 ${WORKDIR}/init/cora-ofdm-text-dashboard \
        ${D}${sysconfdir}/init.d/cora-ofdm-text-dashboard
    install -m 0644 ${WORKDIR}/config/cora-ofdm-text-dashboard \
        ${D}${sysconfdir}/default/cora-ofdm-text-dashboard

    install -d ${D}${docdir}/cora-ofdm-text-demo
    install -m 0644 ${WORKDIR}/README.md \
        ${D}${docdir}/cora-ofdm-text-demo/README.md
}

CONFFILES:${PN} = "${sysconfdir}/default/cora-ofdm-text-dashboard"

FILES:${PN} += " \
    ${bindir}/cora-ofdm-text-transfer \
    ${sbindir}/cora-ofdm-text-dashboard \
    ${PYTHON_SITEPACKAGES_DIR}/cora_ofdm_text_transfer.py \
    ${datadir}/cora-ofdm-text-demo \
    ${sysconfdir}/init.d/cora-ofdm-text-dashboard \
    ${sysconfdir}/default/cora-ofdm-text-dashboard \
    ${docdir}/cora-ofdm-text-demo/README.md \
"
