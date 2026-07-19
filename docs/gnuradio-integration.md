# GNU Radio target integration

The embedded image uses the upstream `meta-sdr` recipes pinned to commit
`563727209e5d8c7cdebc35157bab4dc07e8fb235`. Fetch the layer before running
PetaLinux configuration or a build:

```bash
scripts/fetch-meta-sdr.sh
source scripts/activate-tools.sh
petalinux-config --project software/petalinux/cora-z7-10-baseline --silentconfig
petalinux-config --project software/petalinux/cora-z7-10-baseline \
    --component rootfs --silentconfig
petalinux-build --project software/petalinux/cora-z7-10-baseline
```

For a complete walkthrough—including the dependency fixes, SD-card creation,
boot troubleshooting, networking, and target validation—open
[`gnuradio-petalinux-tutorial.html`](gnuradio-petalinux-tutorial.html) in a web
browser.

GNU Radio Companion remains on the Ubuntu host. The target configuration is
headless and initially enables the runtime, blocks, analog, digital, FFT,
filter, and network components with Python bindings. Qt GUI, GRC, audio, UHD,
and ZeroMQ are deliberately omitted to conserve RAM and storage. The fetch
script changes meta-sdr's GUI-oriented default before BitBake parses the
recipe; doing this only in a `.bbappend` is too late to prevent `cmake_qt5`
from being inherited.

The direct Ethernet link uses:

- Host: `192.168.10.1/24`
- Target: `192.168.10.2/24`
- SSH: `ssh petalinux@192.168.10.2`

The development credential used during bring-up was `petalinux` / `root`.
Change it before connecting the board to an untrusted network.

Copy and run the repository's hardware-independent target smoke test with:

```bash
scp scripts/gnuradio-smoke-test.py petalinux@192.168.10.2:/tmp/
ssh petalinux@192.168.10.2 python3 /tmp/gnuradio-smoke-test.py
```

This first integration milestone validates software-only flowgraphs on ARM.
The accelerator runtime, remote protocol, and local FPGA driver remain
separate layers so the flowgraph-facing API does not depend on one driver.
