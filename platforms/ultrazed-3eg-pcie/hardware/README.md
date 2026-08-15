# UltraZed 3EG PCIe carrier hardware overlay

This directory contains only platform-specific hardware inputs. Reusable RTL
and interface contracts remain under `hardware/rtl` and `docs`.

The initial platform uses Avnet carrier `AES-ZU-PCIECC-G` and the pinned Avnet
Vivado board definition `ultrazed_3eg_pciecc/1.4`. That definition selects the
industrial `xczu3eg-sfva625-1-i` device and supplies the SOM DDR4, MIO, clock,
reset, PCIe, and power-related PS preset. Export the resulting hardware handoff
as:

```text
hardware/export/ultrazed-3eg-pcie/ultrazed-3eg-pcie.xsa
```

Reproduce the Vivado 2025.1 build with:

```sh
source scripts/activate-tools.sh
scripts/build-ultrazed-3eg-pcie-hardware.sh
```

The initial implemented design contains a 40-bit simple-mode AXI DMA connected
to the ZynqMP `S_AXI_HPC0_FPD` port, forward and inverse 512-point FFT cores,
and the common programmable spectral filter. The build has no carrier PL I/O,
so it relies on the Avnet preset only for the SOM/PS configuration. See
`build-2026-08-15.md` for implementation evidence.

Before declaring the platform validated, check:

- the SOM label confirms `AES-ZU3EG-1-SOM-I-G` before hardware deployment;
- `xczu3eg-sfva625-1-i` is the selected part;
- DDR4, QSPI, eMMC, UART, Ethernet and boot-mode settings match the SOM and
  exact carrier revision;
- AXI DMA masters use an appropriate PS HP/HPC port;
- ZynqMP high addressing is enabled and AXI DMA address width is 40 bits;
- clocks, resets and PL-to-PS interrupts are explicit;
- the common identity register block is present with platform ID
  `0x00020001`;
- the CPU-only sonar image boots before FFT or acquisition IP is added.

The PCIe carrier and a future custom carrier are separate platforms even when
they use the same SOM. They may share a SOM-level Tcl helper, but must not
share pin constraints, board presets, device trees, or platform IDs.
