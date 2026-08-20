# Zynq Sonar Platform

Zynq Sonar Platform is a multi-board FPGA/Linux laboratory for forward-looking
sonar, acoustic OFDM and conventional communication, underwater navigation,
remote dashboards, and reusable streaming DSP accelerators. It keeps
algorithms, web interfaces, drivers, and Yocto recipes common while isolating
the hardware details that must differ between a Zynq-7000 board and a Zynq
UltraScale+ MPSoC platform.

The repository currently supports the Digilent Cora Z7-10 and contains a
development hardware build for an Avnet UltraZed-EG 3EG SOM on the Avnet PCIe
carrier. The Cora platform is physically validated. The UltraZed design passes
Vivado implementation and timing but has not yet been booted on its target.

## What is here

- A time-series forward-sonar simulator with up to 128 receive elements,
  512 delay-and-sum beams, adjustable center frequency and pitch, multiple
  scenes, a scene editor, pan/zoom, raw-channel views, matched filtering, and
  captured NumPy arrays.
- Acoustic OFDM modems and progressive image/text demonstrations with measured
  Cora profiles, convolutional coding, puncturing, interleaving, and 8-PSK
  experiments.
- A separate conventional acoustic modem and dashboard for coherent BPSK and
  QPSK plus noncoherent 2-FSK and configurable 4/8/16-FSK. It shares no OFDM
  modulation or receiver path. See
  [`docs/conventional-acoustic-modulations.md`](docs/conventional-acoustic-modulations.md).
- DVL and LBL/iUSBL navigation simulations and browser dashboards.
- Reusable AXI4-Stream RTL for PWM and a 512-bin spectral filter.
- Vivado Tcl builds with pinned Digilent and Avnet board definitions.
- A common PetaLinux/Yocto application layer shared by ARMv7-A and AArch64
  targets.
- Platform descriptions, release manifests, snapshots, and a documented path
  for adding a custom carrier without Codex access.

## Platforms

| Platform | SoC | State | Current programmable-logic path |
|---|---|---|---|
| Digilent Cora Z7-10 Rev. B | `xc7z010clg400-1` | Validated | DDR/DMA, 512-point FFT, spectral filter, IFFT, PWM |
| Avnet UltraZed-EG 3EG + `AES-ZU-PCIECC-G` | `xczu3eg-sfva625-1-i` | Development | DDR/DMA, 512-point FFT, spectral filter, IFFT |

The platform registry is under [`platforms/`](platforms/). Run
`python3 scripts/platformctl.py list` for the machine-readable status.

## Architecture

```text
                         common source
       +------------------------------------------------+
       | sonar, acoustic modems, navigation, dashboards |
       | reusable RTL and software-visible ABI contracts |
       | software/petalinux/meta-platform-common        |
       +-------------------------+----------------------+
                                 |
                  +--------------+--------------+
                  |                             |
        Cora Z7-10 platform             UltraZed 3EG platform
        Zynq-7000 / ARMv7-A             ZynqMP / AArch64
        PS preset + Cora pins           Avnet preset + carrier policy
        Cora device tree/boot           ZynqMP device tree/boot
                  |                             |
             Cora image                 UltraZed image
```

Applications consume common APIs and capabilities, not board names. Vivado PS
configuration, carrier pins, device trees, boot media, and deployment policy
remain platform-specific. See
[`docs/multi-platform-architecture.md`](docs/multi-platform-architecture.md)
and [`docs/adding-custom-platform.md`](docs/adding-custom-platform.md).

## Reproduced toolchain

The known-good build host is:

| Component | Version |
|---|---|
| Host | Ubuntu 22.04.5 LTS, x86-64 |
| Vivado | 2025.1 build 6140274 |
| PetaLinux | 2025.1, ARM and AArch64 platforms |
| Vitis | 2025.1 build 6137779; optional for the current scripted builds |
| Python | Ubuntu Python 3.10 |
| Host GNU Radio | Ubuntu 3.10.1.1 |
| Target GNU Radio | 3.10.12.0 |

Vivado and PetaLinux must have the same release number. PetaLinux 2025.1
officially supports Ubuntu 22.04.2 through 22.04.5 and requires at least 8 GB
RAM, eight CPU cores, and 100 GB free disk. In practice, 16 GB or more RAM and
at least 250 GB free disk make the Yocto and Vivado builds much more
comfortable. The successful 8 GB host used a persistent 24 GB swap file.

AMD references:

- [Vivado 2025.1 downloads](https://www.amd.com/en/support/downloads/adaptive-socs-and-fpgas/development-tools/2025-1.html)
- [Vivado 2025.1 supported operating systems](https://docs.amd.com/r/2025.1-English/ug973-vivado-release-notes-install-license/Supported-Operating-Systems)
- [PetaLinux 2025.1 installation requirements](https://docs.amd.com/r/2025.1-English/ug1144-petalinux-tools-reference-guide/Installation-Requirements)
- [PetaLinux 2025.1 installation procedure](https://docs.amd.com/r/2025.1-English/ug1144-petalinux-tools-reference-guide/Installing-the-PetaLinux-Tool)

## Fresh Ubuntu 22.04 setup

### 1. Clone the repository

Install Git, clone the project, and enter it:

```bash
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/eldden28/zynq-sonar-platform.git
cd zynq-sonar-platform
```

### 2. Install Ubuntu prerequisites

The repository provides the exact package set used by the working host:

```bash
scripts/install-ubuntu-22.04-prerequisites.sh
```

The script verifies Ubuntu 22.04 before invoking `apt-get`. It installs the
standard Vivado/Vitis libraries, Yocto/PetaLinux build tools, 32-bit compiler
support, terminal libraries, Python helpers, and repository build utilities.
It deliberately does not change the login shell or create swap.

PetaLinux 2025.1 requires `/bin/sh` to resolve to Bash. Ubuntu normally points
it to Dash. Check it:

```bash
readlink -f /bin/sh
```

If the result is not `/usr/bin/bash`, run the vendor-recommended configuration
and answer **No** when asked whether Dash should be the default system shell:

```bash
sudo dpkg-reconfigure dash
```

Ensure a UTF-8 locale is available:

```bash
sudo locale-gen en_US.UTF-8
export LANG=en_US.UTF-8
export LC_ALL=en_US.UTF-8
```

On a host with only about 8 GB RAM, add swap before starting Vivado or Yocto.
Skip this if sufficient swap already exists:

```bash
swapon --show
sudo fallocate -l 24G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
printf '/swapfile none swap sw 0 0\n' | sudo tee -a /etc/fstab
```

Do not append a second `/swapfile` entry if one is already present in
`/etc/fstab`.

### 3. Install Vivado 2025.1

Download the **AMD Unified Installer for FPGAs & Adaptive SoCs 2025.1** Linux
installer from AMD. Install Vivado 2025.1 at `/tools/Xilinx/2025.1` and select
device support for:

- Zynq-7000, including `xc7z010clg400-1`;
- Zynq UltraScale+ MPSoC, including `xczu3eg-sfva625-1-i`.

Vitis 2025.1 is useful for future standalone applications but is not required
by the present Vivado and PetaLinux command-line builds. If `/tools/Xilinx` is
used as a per-user installation, create it with suitable ownership before
running the installer as that user:

```bash
sudo install -d -o "$USER" -g "$(id -gn)" /tools/Xilinx
```

After installation, the AMD-provided dependency helper may be run once:

```bash
sudo /tools/Xilinx/2025.1/Vivado/scripts/installLibs.sh
```

The prerequisite script in this repository should be run first. On the
original host, Vivado installer post-processing stalled because
`libtinfo.so.5` was missing. Installing Ubuntu packages `libtinfo5`,
`libncurses5`, and `libncurses5-dev` fixed the launch and allowed the installed
device list to be regenerated. The vendor `installLibs.sh` also attempts to
install `compat-openssl10`, which is not an Ubuntu 22.04 package; that failed
package lookup was nonfatal on the validated host.

### 4. Install PetaLinux 2025.1

Download the PetaLinux 2025.1 installer from AMD. PetaLinux must be installed
as a normal user, never as root, and its installed directory must not be moved
afterward. Both target architectures are needed: `arm` for Cora and `aarch64`
for UltraZed.

Create a writable destination and run the downloaded installer, substituting
its real filename:

```bash
install -d /tools/Xilinx/2025.1/PetaLinux/tool
chmod 755 ./petalinux-v2025.1-*-installer.run
./petalinux-v2025.1-*-installer.run \
  --dir /tools/Xilinx/2025.1/PetaLinux/tool \
  --platform "arm aarch64"
```

Accept the AMD and third-party license prompts. A missing TFTP service warning
is harmless for SD-card builds. Do not use `sudo` for installation or builds;
BitBake intentionally rejects root builds.

### 5. Activate and inspect the toolchain

The project does not modify shell startup files. Activate the tools in every
new build shell:

```bash
source scripts/activate-tools.sh
scripts/check-host.sh
```

Expected version checks include:

```bash
vivado -version
printf 'PetaLinux %s\n' "$PETALINUX_VER"
python3 scripts/platformctl.py validate --strict
```

The activation script defaults to `/tools/Xilinx/2025.1`. Override a different
location without editing it:

```bash
FPGA_TOOLS_ROOT=/opt/Xilinx FPGA_TOOLS_VERSION=2025.1 \
  source scripts/activate-tools.sh
```

## Build programmable logic

Board-definition repositories are fetched automatically at pinned commits and
stored under the ignored `third_party/` directory.

### Cora Z7-10

Validate the generated block design without implementation:

```bash
scripts/build-pwm-hardware.sh validate
```

Run synthesis, implementation, timing checks, bitstream generation, and XSA
export:

```bash
scripts/build-pwm-hardware.sh build
```

The XSA is written to:

```text
hardware/export/cora-z7-10-pwm/cora-z7-10-pwm.xsa
```

The 2026-08-15 reference build met all timing constraints with +0.405 ns setup
slack and no DRC errors. See
[`platforms/cora-z7-10/hardware/build-2026-08-15.md`](platforms/cora-z7-10/hardware/build-2026-08-15.md).

### UltraZed-EG 3EG on the Avnet PCIe carrier

```bash
scripts/build-ultrazed-3eg-pcie-hardware.sh validate
scripts/build-ultrazed-3eg-pcie-hardware.sh build
```

The XSA is written to:

```text
hardware/export/ultrazed-3eg-pcie/ultrazed-3eg-pcie.xsa
```

The reference implementation met timing with +6.000 ns setup slack. It assumes
SOM `AES-ZU3EG-1-SOM-I-G` and carrier `AES-ZU-PCIECC-G`; confirm the physical
labels before programming hardware. See
[`platforms/ultrazed-3eg-pcie/hardware/build-2026-08-15.md`](platforms/ultrazed-3eg-pcie/hardware/build-2026-08-15.md).

## Build embedded Linux

Fetch the pinned SDR layer and activate PetaLinux first:

```bash
scripts/fetch-meta-sdr.sh
source scripts/activate-tools.sh
```

### Existing Cora project

Import the newly generated XSA, update configuration non-interactively, and
build the image:

```bash
petalinux-config \
  --project software/petalinux/cora-z7-10-baseline \
  --get-hw-description "$PWD/hardware/export/cora-z7-10-pwm/cora-z7-10-pwm.xsa" \
  --silentconfig
scripts/build-petalinux-baseline.sh
```

Generated images appear under:

```text
software/petalinux/cora-z7-10-baseline/images/linux/
```

Use [`docs/sd-image.md`](docs/sd-image.md) or the guarded
`scripts/write-sd-image-gui` helper to write the `.wic` image. Writing an SD
image destroys the selected device, so verify the whole-device path carefully.

### New UltraZed project

After the UltraZed XSA exists, create its platform project once and build it:

```bash
python3 scripts/platformctl.py bootstrap ultrazed-3eg-pcie
petalinux-build --project software/petalinux/ultrazed-3eg-pcie
```

The UltraZed PetaLinux image is not yet board-validated. Treat it as bring-up
material, not a production release.

## Host-side tests and demos

Run the forward-sonar test suite:

```bash
PYTHONPATH=software/cora-sonar \
  python3 -m unittest discover -s software/cora-sonar/tests -v
```

Run a bounded sonar processing test:

```bash
PYTHONPATH=software/cora-sonar \
  python3 software/cora-sonar/cora_sonar_smoke.py \
  --scenario obstacle_course --pings 3
```

Start the sonar dashboard locally:

```bash
PYTHONPATH=software/cora-sonar \
  python3 software/cora-sonar/cora_sonar_service.py \
  --bind 127.0.0.1 --port 8085 \
  --web-root software/cora-sonar/www
```

Then open `http://127.0.0.1:8085/`.

Component-specific commands and measured results are documented under
[`software/`](software/), [`docs/`](docs/), and [`results/`](results/).

## Repository layout

```text
hardware/                 reusable RTL, constraints, Vivado Tcl, local exports
platforms/                platform registry, overlays, manifests, build evidence
schemas/                  platform and release-manifest schemas
software/cora-*           applications, drivers, dashboards, GNU Radio blocks
software/petalinux/       Cora project and common cross-platform Yocto layer
scripts/                  host checks, dependency fetchers, builds, SD tooling
docs/                     architecture, platform, protocol, and bring-up guides
results/                  measured benchmark and validation records
```

Vivado projects, XSA files, bitstreams, PetaLinux build trees, images, fetched
vendor repositories, and credentials are intentionally ignored. Release
binaries should be attached to a tagged GitHub release and accompanied by a
manifest containing their SHA-256 hashes.

## Development status and safety

This is an experimental research platform, not a production sonar or safety
system. Confirm board revisions, power, I/O voltage, transducer drive limits,
and pin constraints before connecting custom hardware. The development image
uses intentionally simple laboratory networking and must be hardened before
being connected to an untrusted network.

Platform compatibility is governed by
[`docs/platform-identity.md`](docs/platform-identity.md). Add new carriers by
following [`docs/adding-custom-platform.md`](docs/adding-custom-platform.md)
rather than forking common applications.
