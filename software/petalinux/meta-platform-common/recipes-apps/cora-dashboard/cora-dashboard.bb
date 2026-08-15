SUMMARY = "Authenticated Cora hardware dashboard and web terminal"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

inherit update-rc.d

RDEPENDS:${PN} = " \
    python3-core \
    python3-crypt \
    python3-fcntl \
    python3-io \
    python3-json \
    python3-netclient \
    python3-netserver \
    python3-terminal \
    python3-threading \
"

FILESEXTRAPATHS:prepend := "${PLATFORM_COMMON_SOURCE_ROOT}/cora-dashboard:"
SRC_URI = " \
    file://cora_dashboard.py \
    file://www/index.html \
    file://init/cora-dashboard \
    file://config/cora-dashboard \
    file://config/token \
"

S = "${WORKDIR}"

INITSCRIPT_NAME = "cora-dashboard"
INITSCRIPT_PARAMS = "start 80 2 3 4 5 . stop 20 0 1 6 ."

do_install() {
    install -d ${D}${sbindir}
    install -m 0755 ${WORKDIR}/cora_dashboard.py ${D}${sbindir}/cora-dashboard

    install -d ${D}${datadir}/cora-dashboard
    install -m 0644 ${WORKDIR}/www/index.html ${D}${datadir}/cora-dashboard/index.html

    install -d ${D}${sysconfdir}/init.d ${D}${sysconfdir}/default
    install -m 0755 ${WORKDIR}/init/cora-dashboard ${D}${sysconfdir}/init.d/cora-dashboard
    install -m 0644 ${WORKDIR}/config/cora-dashboard ${D}${sysconfdir}/default/cora-dashboard

    install -d -m 0750 ${D}${localstatedir}/lib/cora-dashboard
    install -m 0600 ${WORKDIR}/config/token \
        ${D}${localstatedir}/lib/cora-dashboard/token
}

CONFFILES:${PN} = "${sysconfdir}/default/cora-dashboard"

FILES:${PN} += " \
    ${sbindir}/cora-dashboard \
    ${datadir}/cora-dashboard/index.html \
    ${sysconfdir}/init.d/cora-dashboard \
    ${sysconfdir}/default/cora-dashboard \
    ${localstatedir}/lib/cora-dashboard \
"
