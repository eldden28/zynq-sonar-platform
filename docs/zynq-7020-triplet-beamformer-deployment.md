# Zynq-7020 triplet beamformer deployment

This tutorial moves the 21-channel triplet beamformer from its synthetic input
to an AD4630/AXI-stream acquisition design on a custom Zynq-7020 board. It is
written for a known-good PetaLinux baseline and a Vivado XSA exported for the
target board.

The repository currently supplies the portable beamformer, BTR service,
dashboard, AXI packet reader, tests, and Yocto recipe. The final board-specific
DMAengine character driver is an integration requirement: it must expose the
receive stream as `/dev/cora-triplet-adc0` using the ABI in this document.

## 1. Freeze the interfaces

Before changing PetaLinux, verify that the Vivado design and ADC capture logic
implement this stream contract:

| Property | Required value |
| --- | --- |
| Channels | 21 |
| Sample rate | 48,000 samples/s/channel |
| AXI ordering | Sample-major: Ch1 through Ch21 for each time sample |
| ADC value | Signed 24-bit two's-complement |
| AXI container | Sign-extended little-endian 32-bit word |
| Samples per AXI packet | 512 per channel |
| Words per packet | 10,752 |
| Bytes per packet | 43,008 |
| `TLAST` | Once, on the final word of every 43,008-byte packet |
| Beamformer block | Two packets, or 1,024 samples/channel |
| Payload rate | 4.032 MB/s |

The 21 application channels are seven rotationally aligned triplets. Within
each triplet, Ch1 is starboard, Ch2 is down, and Ch3 is port. The elements have
30 mm equilateral spacing and triplet centers have 3 inch (76.2 mm) axial
spacing.

Check the Vivado design before exporting the XSA:

- the exact Zynq-7020 part, package, and speed grade are selected;
- DDR parameters match the board schematic and memory part;
- boot, SD/QSPI, UART, Ethernet, USB, and other PS MIO are board-correct;
- the AD4630 source feeds the AXI DMA S2MM input through the packet FIFO;
- DMA memory traffic reaches a PS high-performance port;
- the DMA AXI-Lite interface is reachable from a PS GP master;
- the S2MM interrupt reaches `IRQ_F2P`;
- clocks, resets, clock-domain crossings, pin locations, and I/O standards are
  constrained for the work board; and
- the AXI address editor reports no unassigned or overlapping segments.

Use an ILA if possible to verify `TVALID`, `TREADY`, `TDATA`, and `TLAST` with
the ADC capture running. A device tree cannot repair an incorrect stream,
pinout, clock, or DDR configuration.

## 2. Keep the working baseline intact

Do not convert the proven Cora project in place. Copy the work-machine baseline
or create a sibling project, for example:

```text
software/petalinux/
|-- cora-z7-10-baseline/
|-- work-zynq7020/
`-- meta-platform-common/
```

Use the same PetaLinux release as the Vivado release that produced the XSA.
This repository's helper scripts currently target PetaLinux 2025.1. If the
work baseline uses another release, preserve that toolchain and export the XSA
with its matching Vivado version.

For a new project:

```sh
source scripts/activate-tools.sh

cd software/petalinux
petalinux-create project --template zynq --name work-zynq7020
cd ../..

petalinux-config \
  --project software/petalinux/work-zynq7020 \
  --get-hw-description /absolute/path/to/work-zynq7020.xsa \
  --silentconfig
```

For an existing copied baseline, run only the `petalinux-config
--get-hw-description` command against the copied project. Re-run it whenever
the XSA changes.

Do not manually change a Cora `7010` identifier to `7020`. Both devices use the
Zynq-7000 ARMv7 PetaLinux template. The XSA supplies the actual part, PS setup,
clocks, interrupts, and PL address map. A custom board can continue to use the
`zynq-generic` Yocto machine unless it has a maintained board BSP of its own.

After import, review rather than assume:

```sh
grep -E 'MACHINE_NAME|PROCESSOR0|DDR|SERIAL|ETHERNET|SDHCI' \
  software/petalinux/work-zynq7020/project-spec/configs/config
```

In particular, confirm detected RAM size, serial console, Ethernet controller,
and boot/root device. The SD root partition is not necessarily
`/dev/mmcblk0p2` on the new board.

## 3. Add the portable application layer

The beamformer recipe is in
`software/petalinux/meta-platform-common/recipes-sonar/`. Register that shared
layer from the new project's PetaLinux configuration. The resulting config
entry should resolve to the repository layer, for example:

```text
CONFIG_USER_LAYER_1="${PROOT}/../meta-platform-common"
```

The numeric suffix can differ when other external layers are already present.
Run `petalinux-config` and use **Yocto Settings > User Layers**, or update the
project config using the same convention as `scripts/platformctl.py`.

Add the application to the target image in
`project-spec/meta-user/conf/petalinuxbsp.conf`:

```bitbake
IMAGE_INSTALL:append = " cora-triplet-beamformer"
```

Alternatively enable the package in `petalinux-config -c rootfs`. The recipe
pulls in Python 3 and NumPy runtime dependencies.

Remove Cora-only applications from the copied image if their hardware is not
present. In particular, do not start the old PWM, DSP, or acoustic-router
drivers against absent peripherals or against the DMA reserved for ADC input.

## 4. Rebuild the board-specific device tree

Allow the XSA import to generate the base processing-system, AXI DMA, clock,
interrupt, and PL peripheral nodes. Put durable changes only in:

```text
project-spec/meta-user/recipes-bsp/device-tree/files/system-user.dtsi
```

Do not edit files below `components/plnx_workspace`; they are generated.

Do not copy the Cora `system-user.dtsi` wholesale. Its USB reset GPIO, Ethernet
PHY, XADC channels, PWM, and acoustic DSP/router nodes describe the Cora board.
Recreate only entries verified against the work-board schematic and XSA.

The ADC driver needs to request the DMA receive channel. A minimal logical
client looks like the following, but the DMA label and channel specifier must
match the generated `pl.dtsi` and the driver's binding:

```dts
/ {
    triplet_adc0: triplet-adc {
        compatible = "yourcompany,triplet-adc-1.0";
        dmas = <&axi_dma_adc 1>;
        dma-names = "rx";
        channel-count = <21>;
        samples-per-frame = <512>;
        sample-container-bits = <32>;
        valid-sample-bits = <24>;
        status = "okay";
    };
};
```

If the capture block has AXI-Lite registers, use its generated node and include
the correct `reg`, clocks, resets, and interrupts. Do not add `dma-coherent`
for a normal Zynq-7000 HP-port path; use the Linux DMA API for cache ownership.

## 5. Supply the DMAengine character driver

The driver is the board-specific boundary. It should:

1. bind to `yourcompany,triplet-adc-1.0`;
2. request the `rx` DMA channel through DMAEngine;
3. allocate a coherent or correctly synchronized receive ring;
4. continuously queue S2MM buffers;
5. expose `/dev/cora-triplet-adc0`;
6. make blocking `read()` and `poll()` available;
7. preserve packet and sample-major channel order; and
8. report frame, overrun, dropped-frame, and DMA-error counters.

The userspace boundary is deliberately small:

- one logical packet is exactly 43,008 bytes;
- a read may return a partial packet, because the application assembles short
  reads;
- bytes from two different packets must never be interleaved;
- the driver must not silently overwrite an unread frame; and
- closing the file must stop or release that reader's DMA activity cleanly.

The application rejects words outside the signed 24-bit range. Validate the
hardware representation with these known cases:

| ADC value | Required 32-bit word |
| --- | --- |
| `0x000001` | `0x00000001` |
| `0x7fffff` | `0x007fffff` |
| `0x800000` | `0xff800000` |
| `0xffffff` | `0xffffffff` |

Enable `CONFIG_DMADEVICES`, the Xilinx AXI DMA driver, and the new character
driver module in the kernel configuration. Package the module in the target
project's `meta-user` layer and add its package name to `IMAGE_INSTALL`.

Only one kernel client can own a DMA channel. The existing Cora
`/dev/cora-dsp0` driver uses a different 16-bit bidirectional ABI and cannot be
used for this ADC stream.

## 6. Validate userspace before the driver is ready

Run the complete host test suite:

```sh
PYTHONPATH=software/cora-triplet-beamformer \
  python3 -m unittest discover \
  -s software/cora-triplet-beamformer/tests -v
```

A raw file containing consecutive 43,008-byte frames can exercise the board
application before `/dev/cora-triplet-adc0` exists:

```sh
/usr/sbin/cora-triplet-btr \
  --source axi \
  --axi-device /tmp/triplet-capture.raw \
  --axi-loop-file \
  --bind 0.0.0.0 \
  --port 8088 \
  --web-root /usr/share/cora-triplet-beamformer/www
```

This validates parsing, channel ordering, sign extension, beamforming, and the
dashboard, but it does not validate DMA continuity or timing.

## 7. Select live AXI input

After the driver creates `/dev/cora-triplet-adc0`, edit the image's installed
defaults or the recipe source at
`software/cora-triplet-beamformer/config/cora-triplet-btr`:

```sh
BTR_ARGS="--bind 0.0.0.0 --port 8088 --web-root /usr/share/cora-triplet-beamformer/www --carrier-hz 8000 --source axi --axi-device /dev/cora-triplet-adc0"
```

On a running development image:

```sh
/etc/init.d/cora-triplet-btr restart
/etc/init.d/cora-triplet-btr status
```

Browse to `http://BOARD_ADDRESS:8088/`. AXI mode intentionally does not fall
back to synthetic samples when the device fails; the status API and dashboard
will show the acquisition error.

## 8. Build and package the 7020 image

Build the package alone first, followed by the complete image:

```sh
source scripts/activate-tools.sh

petalinux-build \
  --project software/petalinux/work-zynq7020 \
  -c cora-triplet-beamformer

petalinux-build --project software/petalinux/work-zynq7020
```

Package a new Zynq-7020 boot image using the FSBL, bitstream, and U-Boot from
this build. Never reuse the Cora FSBL, bitstream, device tree, or `BOOT.BIN`:

```sh
petalinux-package --boot \
  --project software/petalinux/work-zynq7020 \
  --format BIN \
  --fsbl software/petalinux/work-zynq7020/images/linux/zynq_fsbl.elf \
  --fpga software/petalinux/work-zynq7020/images/linux/system.bit \
  --u-boot \
  --force
```

Confirm the XSA export included a bitstream or supply the matching `.bit`
explicitly. Copy `BOOT.BIN`, `boot.scr`, `image.ub`, and any separate root
filesystem artifacts required by the baseline's SD-card layout.

## 9. Bring-up checklist

Bring up the board in layers so ADC problems are not confused with basic board
problems.

1. Boot with a serial console and confirm U-Boot and Linux detect the expected
   DDR size.
2. Verify the root filesystem, UART, Ethernet, and SSH.
3. Confirm the generated model, DMA probe, and interrupt registration:

   ```sh
   tr -d '\000' </proc/device-tree/model; echo
   dmesg | grep -Ei 'dma|xilinx|triplet'
   cat /proc/interrupts
   ls -l /dev/cora-triplet-adc0
   ```

4. Use an ILA to confirm one `TLAST` per 10,752 accepted stream words and that
   `TREADY` backpressure is handled without corrupting framing.
5. Capture one packet and verify byte count, channel order, and sign extension.
6. Apply a per-channel ramp or single-channel stimulus before using acoustics.
7. Run continuous capture for at least 30 minutes and verify no overruns,
   interrupt loss, DMA stalls, or memory growth.
8. Start the service in AXI mode and confirm a known physical source appears at
   the expected bearing.

Useful rate checks are:

```text
93.75 packets/s
4,032,000 payload bytes/s
1,008,000 32-bit words/s
```

At this rate the Zynq-7020 has ample memory bandwidth. Correct framing,
interrupt handling, cache ownership, and channel calibration are the practical
integration limits.

## 10. Release and rollback

Archive the exact XSA, bitstream hash, PetaLinux version, Git commit, device
tree, kernel configuration, and SD image used for a successful deployment.
Keep the known-good baseline SD card unchanged. A board image is not considered
validated until it passes the long capture test and a known-bearing acoustic
test, even if Vivado implementation and `petalinux-build` both succeed.

Useful AMD references:

- [Importing a hardware configuration](https://docs.amd.com/r/2025.1-English/ug1144-petalinux-tools-reference-guide/Importing-a-Hardware-Configuration)
- [Configuring the device tree](https://docs.amd.com/r/en-US/ug1144-petalinux-tools-reference-guide/Configuring-Device-Tree)
- [`petalinux-package --boot` examples](https://docs.amd.com/r/2023.2-English/ug1144-petalinux-tools-reference-guide/petalinux-package-boot-Examples)
- [AXI DMA product guide](https://docs.amd.com/r/en-US/pg021_axi_dma/Core-Overview)
