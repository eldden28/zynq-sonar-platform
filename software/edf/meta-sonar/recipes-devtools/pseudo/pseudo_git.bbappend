# EDF 25.05 predates the pseudo fix required by updated host tar builds.
# Keep the pinned vendor source and backport the narrow upstream host fix.
FILESEXTRAPATHS:prepend := "${THISDIR}/files:"
SRC_URI:append:class-native = " file://0001-Avoid-openat2-usage-via-syscall.patch file://0002-Define-SYS_openat2-on-older-host-headers.patch"
SRC_URI:append:class-nativesdk = " file://0001-Avoid-openat2-usage-via-syscall.patch file://0002-Define-SYS_openat2-on-older-host-headers.patch"
