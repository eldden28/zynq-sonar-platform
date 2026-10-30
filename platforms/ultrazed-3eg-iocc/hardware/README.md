# UltraZed-EG 3EG on the I/O carrier

This target uses a production `xczu3eg-sfva625-1-i` UltraZed-EG SOM and the
Avnet `AES-ZU-IOCC-G` carrier. It is separate from the PCIe carrier target.
The pinned Avnet BDF is `ultrazed_3eg_iocc/1.3` at commit
`e9723b55a68b106fb4cdb645974d096cd3594a2a`.

Build the 100 MHz DMA -> FFT -> spectral-filter -> IFFT hardware:

```sh
scripts/build-ultrazed-3eg-iocc-hardware.sh build
```

Output: `hardware/export/ultrazed-3eg-iocc/ultrazed-3eg-iocc.xsa`, including
the bitstream. DMA is 40-bit on `S_AXI_HPC0_FPD`, with control registers at
`0xa0000000` and the spectral filter at `0xa0010000`. The maintained
`cora-dsp-1.0` binding supports this pipeline. Cora's PWM hardware is absent.

The 2026-10-30 Vivado 2025.1 implementation completed with setup slack
6.348 ns and hold slack 0.011 ns, with zero failing endpoints. Reports are
under `build/reports/ultrazed-3eg-iocc/`. The exported XSA SHA-256 is:

```text
90928d27fe9fdbb7069bc501777992387c163d612b004376ef2ef8053a45b7c5
```

## EDF setup

Use native Linux and Vivado/Vitis 2025.1, without sourcing PetaLinux. To reuse
the existing EDF sources, downloads, and sstate cache with an independent
build directory:

```sh
SONAR_REPO="$PWD"
export EDF_WORKSPACE="$SONAR_REPO/software/edf/workspace/ultrazed-3eg-iocc"
export EDF_HOST_TOOLS="$SONAR_REPO/software/edf/workspace/host-tools"
python3 scripts/configure-edf.py --workspace "$EDF_WORKSPACE" \
  --platform ultrazed-3eg-iocc --shared-workspace "$SONAR_REPO/software/edf/workspace"
source scripts/activate-edf.sh
python3 "$SONAR_REPO/scripts/import-edf-hardware.py" \
  "$SONAR_REPO/hardware/export/ultrazed-3eg-iocc/ultrazed-3eg-iocc.xsa" \
  --workspace "$EDF_WORKSPACE" --platform ultrazed-3eg-iocc
bitbake sonar-edf-image
```

For a fresh workspace without an existing cache, replace the shared-workspace
configuration command above with these two commands, then continue with
activation, hardware import, and the image build:

```sh
python3 scripts/setup-edf.py --workspace "$EDF_WORKSPACE"
python3 scripts/configure-edf.py --workspace "$EDF_WORKSPACE" \
  --platform ultrazed-3eg-iocc
```

Hardware import runs once per workspace;
re-importing into an already generated machine is rejected. Platform selection
is saved in `.edf-platform`, and changing platforms requires another workspace.
`platformctl.py bootstrap` remains a PetaLinux-only command and rejects this
EDF target without creating a PetaLinux project.

Verify the WIC with:

```sh
python3 "$SONAR_REPO/scripts/verify-edf-wic.py" \
  "$EDF_WORKSPACE/build/tmp/deploy/images/ultrazed-3eg-iocc-edf/sonar-edf-image-ultrazed-3eg-iocc-edf.rootfs.wic" \
  --platform ultrazed-3eg-iocc \
  --manifest "$EDF_WORKSPACE/build/tmp/deploy/images/ultrazed-3eg-iocc-edf/sonar-edf-image-ultrazed-3eg-iocc-edf.rootfs.manifest"
```

## Built image

The corrected 2026-10-30 build, from source commit `699aa4a`, completed all
11,007 tasks successfully, reusing 9,657. The 82 nonfatal warnings concern
empty ROS layers, disabled QEMU PMU-ROM support, and deferred multiconfig tasks
that BitBake resolved automatically.

Artifacts are under
`software/edf/workspace/ultrazed-3eg-iocc/build/tmp/deploy/images/ultrazed-3eg-iocc-edf/`:

- Raw SD image: `sonar-edf-image-ultrazed-3eg-iocc-edf.rootfs-20261030202735.wic`
  (884,664,320 bytes).
- Compressed image: the same filename with `.gz` (84,428,797 bytes).
- Block map: the same filename with `.bmap`; checksums: `SHA256SUMS`.

Raw WIC SHA-256:

```text
e2eda070ddfedd8e91e27126514b265e426aec40a0f88390e5087cc9254192fd
```

Image inspection passed for the partition table, boot-file headers and CRCs,
raw ARM64 kernel, ZynqMP device-tree compatibility, ARM64 Python and DSP module,
application files, hostname, and static Ethernet configuration. The deployed
device tree contains all eight required CEVA SATA timing properties. Bootgen
readback confirms FSBL, PMU firmware, FPGA bitstream, TF-A, device tree, and
U-Boot in the boot image.

The gzip expands to the same WIC hash, all 15 block-map ranges passed their
checksums, and the four boot files extracted from the WIC match their deployed
sources. Validation JSON, Bootgen readback, the build log, and source provenance
are saved alongside the image. Provenance records the inherited local
application changes included in this build. Board boot and peripheral runtime
tests remain pending.

## Board settings

The serial console uses PS UART0 through J11's enhanced USB-UART port, at
115200 baud, 8N1. The network address is `192.168.10.3/24`, with the development
workstation at `192.168.10.1`; Cora retains `.2`. Login is `amd-edf`, with an
initial password change required. The SD boot script resolves root partition 2
by PARTUUID to avoid confusing the carrier's SD slot with the SOM's eMMC.

For a microSD boot, power off with carrier switch SW8, insert the programmed
card into J4, and set the SOM's SW2 switches **1, 2, 3, 4** to **OFF, ON, OFF,
ON** (SD1 boot mode `0x5`). Connect the console through J11 before powering on.
Avnet's guide lists these switches in the reverse order, `SW2[4:1]`.

Board policy covers GEM3 with the TI PHY at MDIO address 9, MAC EEPROM behind
the SOM's PCA9543 I2C mux, the SD level shifter, and the I/O carrier's GTR clock
and lane routing. These are hardware definitions, not evidence of successful
board execution. Production silicon and the physical module/carrier revisions
still need checking before hardware validation.

References:

- [Avnet board definition](https://github.com/Avnet/bdf/tree/e9723b55a68b106fb4cdb645974d096cd3594a2a/ultrazed_3eg_iocc/1.3)
- [Avnet I/O carrier device-tree reference](https://github.com/Avnet/meta-avnet/blob/8a0946581dbc37475459414b7a85e75916a0e226/recipes-bsp/device-tree/files/uz3eg-iocc/system-bsp.dtsi)
- [Avnet starter-kit guide, pages 8–9](https://www.infineon.com/assets/row/public/documents/24/57/infineon-ultrazed-eg-starter-kit-getting-started-guide-v1-1-ati-en.pdf)
