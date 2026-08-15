# The upstream patch only changes installed ptest launchers. The embedded
# integration disables tests, and VOLK 3.2 moved the patched CMake module, so
# carrying this obsolete patch would fail do_patch without affecting runtime.
SRC_URI:remove = " \
    file://0001-Modify-ctest-so-we-can-package-the-testfiles-and-ins.patch \
    file://0001-Do-not-compile-compiler-flags-into-volk.-This-leaks-.patch \
"

PACKAGECONFIG = ""

# Newer OE releases default Git recipes to WORKDIR/git. Scarthgap still
# derives S from BP, so state the checkout directory explicitly.
S = "${WORKDIR}/git"
