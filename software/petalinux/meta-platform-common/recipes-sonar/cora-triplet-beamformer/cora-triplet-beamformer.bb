SUMMARY = "Empirical triplet cardioid beamformer for Cora"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

inherit python3-dir update-rc.d

RDEPENDS:${PN} = " \
    python3-core \
    python3-io \
    python3-json \
    python3-netserver \
    python3-numpy \
    python3-threading \
"

FILESEXTRAPATHS:prepend := "${PLATFORM_COMMON_SOURCE_ROOT}/cora-triplet-beamformer:"
SRC_URI = " \
    file://cora_triplet_beamformer.py \
    file://cora_triplet_adc.py \
    file://cora_triplet_btr.py \
    file://cora_triplet_btr_service.py \
    file://www/index.html \
    file://init/cora-triplet-btr \
    file://config/cora-triplet-btr \
    file://README.md \
"

S = "${WORKDIR}"

INITSCRIPT_NAME = "cora-triplet-btr"
INITSCRIPT_PARAMS = "start 86 2 3 4 5 . stop 14 0 1 6 ."

do_install() {
    install -d ${D}${bindir} ${D}${sbindir} ${D}${PYTHON_SITEPACKAGES_DIR}
    install -m 0755 ${WORKDIR}/cora_triplet_beamformer.py \
        ${D}${bindir}/cora-triplet-beamformer
    install -m 0644 ${WORKDIR}/cora_triplet_beamformer.py \
        ${D}${PYTHON_SITEPACKAGES_DIR}/cora_triplet_beamformer.py
    install -m 0644 ${WORKDIR}/cora_triplet_adc.py \
        ${D}${PYTHON_SITEPACKAGES_DIR}/cora_triplet_adc.py
    install -m 0644 ${WORKDIR}/cora_triplet_btr.py \
        ${D}${PYTHON_SITEPACKAGES_DIR}/cora_triplet_btr.py
    install -m 0755 ${WORKDIR}/cora_triplet_btr_service.py \
        ${D}${sbindir}/cora-triplet-btr

    install -d ${D}${datadir}/cora-triplet-beamformer/www
    install -m 0644 ${WORKDIR}/www/index.html \
        ${D}${datadir}/cora-triplet-beamformer/www/index.html

    install -d ${D}${sysconfdir}/init.d ${D}${sysconfdir}/default
    install -m 0755 ${WORKDIR}/init/cora-triplet-btr \
        ${D}${sysconfdir}/init.d/cora-triplet-btr
    install -m 0644 ${WORKDIR}/config/cora-triplet-btr \
        ${D}${sysconfdir}/default/cora-triplet-btr

    install -d ${D}${docdir}/cora-triplet-beamformer
    install -m 0644 ${WORKDIR}/README.md \
        ${D}${docdir}/cora-triplet-beamformer/README.md
}

CONFFILES:${PN} = "${sysconfdir}/default/cora-triplet-btr"

FILES:${PN} += " \
    ${bindir}/cora-triplet-beamformer \
    ${sbindir}/cora-triplet-btr \
    ${PYTHON_SITEPACKAGES_DIR}/cora_triplet_beamformer.py \
    ${PYTHON_SITEPACKAGES_DIR}/cora_triplet_adc.py \
    ${PYTHON_SITEPACKAGES_DIR}/cora_triplet_btr.py \
    ${datadir}/cora-triplet-beamformer \
    ${sysconfdir}/init.d/cora-triplet-btr \
    ${sysconfdir}/default/cora-triplet-btr \
    ${docdir}/cora-triplet-beamformer/README.md \
"
