SUMMARY = "Simulated DVL, LBL/iUSBL, and fused INS laboratory for Cora"
LICENSE = "MIT & BSD-2-Clause"
LIC_FILES_CHKSUM = " \
    file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302 \
    file://www/acoustic/vendor/leaflet/LICENSE;md5=24dae3136001ef7bbc671453a33458a2 \
"

inherit python3-dir update-rc.d

RDEPENDS:${PN} = " \
    cora-ofdm-acoustic \
    python3-core \
    python3-io \
    python3-json \
    python3-multiprocessing \
    python3-netserver \
    python3-numpy \
    python3-threading \
"

FILESEXTRAPATHS:prepend := "${PLATFORM_COMMON_SOURCE_ROOT}/cora-navigation:"
SRC_URI = " \
    file://cora_navigation.py \
    file://cora_navigation_service.py \
    file://cora_navigation_smoke.py \
    file://www/dvl/index.html \
    file://www/acoustic/index.html \
    file://www/acoustic/map.css \
    file://www/acoustic/map.js \
    file://www/acoustic/attitude.js \
    file://www/acoustic/vendor/leaflet/LICENSE \
    file://www/acoustic/vendor/leaflet/leaflet.css \
    file://www/acoustic/vendor/leaflet/leaflet.js \
    file://www/acoustic/vendor/leaflet/images/layers-2x.png \
    file://www/acoustic/vendor/leaflet/images/layers.png \
    file://www/acoustic/vendor/leaflet/images/marker-icon-2x.png \
    file://www/acoustic/vendor/leaflet/images/marker-icon.png \
    file://www/acoustic/vendor/leaflet/images/marker-shadow.png \
    file://init/cora-navigation \
    file://config/cora-navigation \
    file://README.md \
"

S = "${WORKDIR}"

INITSCRIPT_NAME = "cora-navigation"
INITSCRIPT_PARAMS = "start 84 2 3 4 5 . stop 16 0 1 6 ."

do_install() {
    install -d ${D}${bindir} ${D}${sbindir} \
        ${D}${PYTHON_SITEPACKAGES_DIR}
    install -m 0644 ${WORKDIR}/cora_navigation.py \
        ${D}${PYTHON_SITEPACKAGES_DIR}/cora_navigation.py
    install -m 0755 ${WORKDIR}/cora_navigation_service.py \
        ${D}${sbindir}/cora-navigation
    install -m 0755 ${WORKDIR}/cora_navigation_smoke.py \
        ${D}${bindir}/cora-navigation-smoke

    install -d ${D}${datadir}/cora-navigation/dvl \
        ${D}${datadir}/cora-navigation/acoustic
    install -m 0644 ${WORKDIR}/www/dvl/index.html \
        ${D}${datadir}/cora-navigation/dvl/index.html
    install -m 0644 ${WORKDIR}/www/acoustic/index.html \
        ${D}${datadir}/cora-navigation/acoustic/index.html
    install -m 0644 ${WORKDIR}/www/acoustic/map.css \
        ${D}${datadir}/cora-navigation/acoustic/map.css
    install -m 0644 ${WORKDIR}/www/acoustic/map.js \
        ${D}${datadir}/cora-navigation/acoustic/map.js
    install -m 0644 ${WORKDIR}/www/acoustic/attitude.js \
        ${D}${datadir}/cora-navigation/acoustic/attitude.js
    install -d ${D}${datadir}/cora-navigation/acoustic/vendor/leaflet/images
    install -m 0644 ${WORKDIR}/www/acoustic/vendor/leaflet/LICENSE \
        ${D}${datadir}/cora-navigation/acoustic/vendor/leaflet/LICENSE
    install -m 0644 ${WORKDIR}/www/acoustic/vendor/leaflet/leaflet.css \
        ${D}${datadir}/cora-navigation/acoustic/vendor/leaflet/leaflet.css
    install -m 0644 ${WORKDIR}/www/acoustic/vendor/leaflet/leaflet.js \
        ${D}${datadir}/cora-navigation/acoustic/vendor/leaflet/leaflet.js
    install -m 0644 ${WORKDIR}/www/acoustic/vendor/leaflet/images/*.png \
        ${D}${datadir}/cora-navigation/acoustic/vendor/leaflet/images/

    install -d ${D}${sysconfdir}/init.d ${D}${sysconfdir}/default
    install -m 0755 ${WORKDIR}/init/cora-navigation \
        ${D}${sysconfdir}/init.d/cora-navigation
    install -m 0644 ${WORKDIR}/config/cora-navigation \
        ${D}${sysconfdir}/default/cora-navigation

    install -d ${D}${docdir}/cora-navigation
    install -m 0644 ${WORKDIR}/README.md \
        ${D}${docdir}/cora-navigation/README.md
}

CONFFILES:${PN} = "${sysconfdir}/default/cora-navigation"

FILES:${PN} += " \
    ${bindir}/cora-navigation-smoke \
    ${sbindir}/cora-navigation \
    ${PYTHON_SITEPACKAGES_DIR}/cora_navigation.py \
    ${datadir}/cora-navigation \
    ${sysconfdir}/init.d/cora-navigation \
    ${sysconfdir}/default/cora-navigation \
    ${docdir}/cora-navigation/README.md \
"
