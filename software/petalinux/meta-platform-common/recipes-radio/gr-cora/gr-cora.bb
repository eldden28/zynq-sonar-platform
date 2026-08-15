SUMMARY = "GNU Radio signed-16 sink for the Cora PWM DMA device"
LICENSE = "GPL-3.0-only"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/GPL-3.0-only;md5=c79ff39f19dfec6d293b95dea7b07891"

inherit cmake pkgconfig python3native python3-dir

DEPENDS = "gnuradio python3 python3-pybind11-native"
RDEPENDS:${PN} = "gnuradio python3-core python3-numpy cora-pwm cora-dsp"

FILESEXTRAPATHS:prepend := "${PLATFORM_COMMON_SOURCE_ROOT}/cora-pwm:"
SRC_URI = " \
    file://gnuradio/gr-cora \
    file://include/cora_pwm_ioctl.h \
"

S = "${WORKDIR}/gnuradio/gr-cora"

EXTRA_OECMAKE += " \
    -DCORA_PWM_UAPI_DIR=${WORKDIR}/include \
    -DGR_PYTHON_DIR=${PYTHON_SITEPACKAGES_DIR} \
    -DPYBIND11_INCLUDE_DIR=${STAGING_INCDIR_NATIVE} \
"

FILES:${PN}:append = " \
    ${PYTHON_SITEPACKAGES_DIR}/gnuradio/cora \
    ${datadir}/gnuradio/grc/blocks/cora_pwm_sink.block.yml \
    ${datadir}/gnuradio/grc/blocks/cora_iio_adc_source.block.yml \
    ${datadir}/gnuradio/grc/blocks/cora_hw_fft_filter.block.yml \
    ${datadir}/gnuradio/grc/blocks/cora_tcp_float_sink.block.yml \
"
