SUMMARY = "JANUS interoperability modem and dashboard"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

inherit python3-dir update-rc.d

RDEPENDS:${PN} = " \
    alsa-utils-aplay \
    cora-acomms-pl \
    janus-c \
    python3-core \
    python3-io \
    python3-json \
    python3-netserver \
    python3-numpy \
    python3-threading \
"

FILESEXTRAPATHS:prepend := "${PLATFORM_COMMON_SOURCE_ROOT}/cora-janus-acoustic:"
SRC_URI = " \
    file://cora_janus_acoustic.py \
    file://cora_janus_dashboard.py \
    file://www/index.html \
    file://init/cora-janus-dashboard \
    file://config/cora-janus-dashboard \
    file://README.md \
"

S = "${WORKDIR}"

INITSCRIPT_NAME = "cora-janus-dashboard"
INITSCRIPT_PARAMS = "stop 14 0 1 2 3 4 5 6 ."

do_install() {
    install -d ${D}${bindir} ${D}${sbindir} ${D}${PYTHON_SITEPACKAGES_DIR}
    install -m 0755 ${WORKDIR}/cora_janus_acoustic.py \
        ${D}${bindir}/cora-janus-acoustic
    install -m 0644 ${WORKDIR}/cora_janus_acoustic.py \
        ${D}${PYTHON_SITEPACKAGES_DIR}/cora_janus_acoustic.py
    install -m 0755 ${WORKDIR}/cora_janus_dashboard.py \
        ${D}${sbindir}/cora-janus-dashboard

    install -d ${D}${datadir}/cora-janus-acoustic/www
    install -m 0644 ${WORKDIR}/www/index.html \
        ${D}${datadir}/cora-janus-acoustic/www/index.html

    install -d ${D}${sysconfdir}/init.d ${D}${sysconfdir}/default
    install -m 0755 ${WORKDIR}/init/cora-janus-dashboard \
        ${D}${sysconfdir}/init.d/cora-janus-dashboard
    install -m 0644 ${WORKDIR}/config/cora-janus-dashboard \
        ${D}${sysconfdir}/default/cora-janus-dashboard

    install -d ${D}${docdir}/cora-janus-acoustic
    install -m 0644 ${WORKDIR}/README.md \
        ${D}${docdir}/cora-janus-acoustic/README.md
}

CONFFILES:${PN} = "${sysconfdir}/default/cora-janus-dashboard"

FILES:${PN} += " \
    ${bindir}/cora-janus-acoustic \
    ${sbindir}/cora-janus-dashboard \
    ${PYTHON_SITEPACKAGES_DIR}/cora_janus_acoustic.py \
    ${datadir}/cora-janus-acoustic \
    ${sysconfdir}/init.d/cora-janus-dashboard \
    ${sysconfdir}/default/cora-janus-dashboard \
    ${docdir}/cora-janus-acoustic/README.md \
"
