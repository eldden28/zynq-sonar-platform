SUMMARY = "Portable time-series forward-sonar laboratory"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

inherit python3-dir update-rc.d

RDEPENDS:${PN} = " \
    python3-compression \
    python3-core \
    python3-io \
    python3-json \
    python3-netserver \
    python3-numpy \
    python3-threading \
"

FILESEXTRAPATHS:prepend := "${PLATFORM_COMMON_SOURCE_ROOT}/cora-sonar:"
SRC_URI = " \
    file://cora_sonar.py \
    file://cora_sonar_service.py \
    file://cora_sonar_smoke.py \
    file://www/index.html \
    file://init/cora-sonar \
    file://config/cora-sonar \
    file://README.md \
"

S = "${WORKDIR}"

INITSCRIPT_NAME = "cora-sonar"
INITSCRIPT_PARAMS = "start 85 2 3 4 5 . stop 15 0 1 6 ."

do_install() {
    install -d ${D}${bindir} ${D}${sbindir} ${D}${PYTHON_SITEPACKAGES_DIR}
    install -m 0644 ${WORKDIR}/cora_sonar.py \
        ${D}${PYTHON_SITEPACKAGES_DIR}/cora_sonar.py
    install -m 0755 ${WORKDIR}/cora_sonar_service.py \
        ${D}${sbindir}/cora-sonar
    install -m 0755 ${WORKDIR}/cora_sonar_smoke.py \
        ${D}${bindir}/cora-sonar-smoke

    install -d ${D}${datadir}/cora-sonar/www
    install -m 0644 ${WORKDIR}/www/index.html \
        ${D}${datadir}/cora-sonar/www/index.html

    install -d ${D}${sysconfdir}/init.d ${D}${sysconfdir}/default
    install -m 0755 ${WORKDIR}/init/cora-sonar \
        ${D}${sysconfdir}/init.d/cora-sonar
    install -m 0644 ${WORKDIR}/config/cora-sonar \
        ${D}${sysconfdir}/default/cora-sonar

    install -d ${D}${docdir}/cora-sonar
    install -m 0644 ${WORKDIR}/README.md \
        ${D}${docdir}/cora-sonar/README.md
}

CONFFILES:${PN} = "${sysconfdir}/default/cora-sonar"

FILES:${PN} += " \
    ${bindir}/cora-sonar-smoke \
    ${sbindir}/cora-sonar \
    ${PYTHON_SITEPACKAGES_DIR}/cora_sonar.py \
    ${datadir}/cora-sonar \
    ${sysconfdir}/init.d/cora-sonar \
    ${sysconfdir}/default/cora-sonar \
    ${docdir}/cora-sonar/README.md \
"
