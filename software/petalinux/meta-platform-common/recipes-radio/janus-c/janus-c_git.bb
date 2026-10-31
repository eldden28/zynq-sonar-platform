SUMMARY = "CMRE JANUS underwater acoustic communications reference implementation"
DESCRIPTION = "Reference transmitter, receiver, library, and codecs for STANAG 4748 / ANEP-87 JANUS"
HOMEPAGE = "https://github.com/mission-systems-pty-ltd/janus-c"
LICENSE = "GPL-3.0-only"
LIC_FILES_CHKSUM = "file://COPYING;md5=d32239bcb673463ab874e80d47fae504"

SRC_URI = " \
    git://github.com/mission-systems-pty-ltd/janus-c.git;protocol=https;branch=master \
    file://0001-cmake-avoid-regex-on-module-path.patch \
"
SRCREV = "65f6c54065a9f6a359be99749e6a90631c3d7d9d"
PV = "3.0.5+git${SRCPV}"

S = "${WORKDIR}/git"

inherit cmake pkgconfig

DEPENDS = "fftw"

EXTRA_OECMAKE += " \
    -DJANUS_PLUGINS_DEBUG=0 \
    -DJANUS_PROGRAM_DOXYGEN=OFF \
"

do_install:append() {
    install -d ${D}${docdir}/janus-c
    mv ${D}${prefix}/COPYING ${D}${docdir}/janus-c/COPYING
}

FILES:${PN} += " \
    ${datadir}/janus \
    ${docdir}/janus-c/COPYING \
"

# Upstream intentionally discovers application codec modules under its shared
# data tree rather than the ELF library directory.
INSANE_SKIP:${PN} += "libdir"
INSANE_SKIP:${PN}-dbg += "libdir"
