# GRC remains on the development PC. Keep the Zynq target image headless and
# avoid Qt, GTK, UHD, and ZeroMQ until a flowgraph requires them.
PACKAGECONFIG:pn-gnuradio = ""

# gr-blocks imports variable_save_restore.py from its package initializer, so
# PyYAML is required even when the graphical companion is not installed.
RDEPENDS:${PN}:append = " python3-pyyaml"

# Keep the newer meta-sdr recipe compatible with Scarthgap's source-dir
# default for Git fetches.
S = "${WORKDIR}/git"

# Build the useful embedded runtime set while omitting display/audio/radio
# hardware integrations that are not present on the Cora Z7-10 baseline.
EXTRA_OECMAKE:append:pn-gnuradio = " \
    -DPYBIND11_FINDPYTHON=ON \
    -DENABLE_DEFAULT=OFF \
    -DENABLE_GNURADIO_RUNTIME=ON \
    -DENABLE_GR_BLOCKS=ON \
    -DENABLE_GR_ANALOG=ON \
    -DENABLE_GR_DIGITAL=ON \
    -DENABLE_GR_FFT=ON \
    -DENABLE_GR_FILTER=ON \
    -DENABLE_GR_NETWORK=ON \
    -DENABLE_PYTHON=ON \
    -DENABLE_TESTING=OFF \
"
