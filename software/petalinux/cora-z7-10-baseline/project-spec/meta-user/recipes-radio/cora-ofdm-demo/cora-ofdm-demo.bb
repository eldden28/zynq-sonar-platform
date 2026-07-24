SUMMARY = "Progressive OFDM image-transfer dashboard for the Cora Z7-10"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

inherit update-rc.d

RDEPENDS:${PN} = " \
    cora-ofdm \
    python3-compression \
    python3-core \
    python3-io \
    python3-json \
    python3-netserver \
    python3-numpy \
    python3-threading \
"

FILESEXTRAPATHS:prepend := "${THISDIR}/../../../../../../cora-ofdm-demo:"
SRC_URI = " \
    file://cora_ofdm_transfer.py \
    file://cora_ofdm_dashboard.py \
    file://www/index.html \
    file://assets/rickroll.png \
    file://assets/rickroll.rgb \
    file://init/cora-ofdm-dashboard \
    file://config/cora-ofdm-dashboard \
"

S = "${WORKDIR}"

INITSCRIPT_NAME = "cora-ofdm-dashboard"
INITSCRIPT_PARAMS = "start 82 2 3 4 5 . stop 18 0 1 6 ."

do_install() {
    install -d ${D}${bindir} ${D}${sbindir}
    install -m 0755 ${WORKDIR}/cora_ofdm_transfer.py \
        ${D}${bindir}/cora-ofdm-transfer
    install -m 0755 ${WORKDIR}/cora_ofdm_dashboard.py \
        ${D}${sbindir}/cora-ofdm-dashboard

    install -d ${D}${datadir}/cora-ofdm-demo/www
    install -m 0644 ${WORKDIR}/www/index.html \
        ${D}${datadir}/cora-ofdm-demo/www/index.html
    install -m 0644 ${WORKDIR}/assets/rickroll.png \
        ${D}${datadir}/cora-ofdm-demo/rickroll.png
    install -m 0644 ${WORKDIR}/assets/rickroll.rgb \
        ${D}${datadir}/cora-ofdm-demo/rickroll.rgb

    install -d ${D}${sysconfdir}/init.d ${D}${sysconfdir}/default
    install -m 0755 ${WORKDIR}/init/cora-ofdm-dashboard \
        ${D}${sysconfdir}/init.d/cora-ofdm-dashboard
    install -m 0644 ${WORKDIR}/config/cora-ofdm-dashboard \
        ${D}${sysconfdir}/default/cora-ofdm-dashboard
}

CONFFILES:${PN} = "${sysconfdir}/default/cora-ofdm-dashboard"

FILES:${PN} += " \
    ${bindir}/cora-ofdm-transfer \
    ${sbindir}/cora-ofdm-dashboard \
    ${datadir}/cora-ofdm-demo \
    ${sysconfdir}/init.d/cora-ofdm-dashboard \
    ${sysconfdir}/default/cora-ofdm-dashboard \
"
