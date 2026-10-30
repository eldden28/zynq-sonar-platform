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

For a fresh source checkout, fetch the pinned EDF release with
`scripts/setup-edf.py` first. Hardware import runs once per workspace;
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

## Board settings

The serial console uses PS UART0 through J11's enhanced USB-UART port, at
115200 baud, 8N1. The network address is `192.168.10.3/24`, with the development
workstation at `192.168.10.1`; Cora retains `.2`. Login is `amd-edf`, with an
initial password change required. The SD boot script resolves root partition 2
by PARTUUID to avoid confusing the carrier's SD slot with the SOM's eMMC.

Board policy covers GEM3 with the TI PHY at MDIO address 9, MAC EEPROM behind
the SOM's PCA9543 I2C mux, the SD level shifter, and the I/O carrier's GTR clock
and lane routing. These are hardware definitions, not evidence of successful
board execution. Production silicon and the physical module/carrier revisions
still need checking before hardware validation.

References:

- [Avnet board definition](https://github.com/Avnet/bdf/tree/e9723b55a68b106fb4cdb645974d096cd3594a2a/ultrazed_3eg_iocc/1.3)
- [Avnet I/O carrier device-tree reference](https://github.com/Avnet/meta-avnet/blob/8a0946581dbc37475459414b7a85e75916a0e226/recipes-bsp/device-tree/files/uz3eg-iocc/system-bsp.dtsi)
