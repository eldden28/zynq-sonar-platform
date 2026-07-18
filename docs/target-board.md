# Initial Target Board

## Selected hardware

| Field | Value | Status |
|---|---|---|
| Manufacturer | Digilent | Selected |
| Board | Cora Z7-10 | Selected |
| SoC family | AMD/Xilinx Zynq-7000 | Verified |
| Device | XC7Z010-1CLG400C | Verified from Digilent documentation |
| Processing system | Dual-core ARM Cortex-A9, 32-bit ARMv7-A | Device-defined |
| Board revision | Rev. B | Verified from underside PCB silkscreen in user-supplied photo; use Digilent `B.0` board definition |
| Vivado board files | Digilent commit `36f34ab687b7fa9c778b779d027f3bce63b3ace9` | Cora definition loaded successfully by Vivado 2025.1 |
| PetaLinux BSP | TBD | Do not assume a current vendor BSP exists |
| Initial boot source | TBD | microSD is the likely development choice but is not yet selected |
| Serial settings | TBD | Confirm from the board documentation and hardware test |

The exact device is supported by Digilent's Cora Z7 statement of volatility.
Digilent also publishes a Cora Z7-10 master constraints file explicitly marked
for revision B and a repository of Vivado board files. The board-files source
is pinned to commit `36f34ab687b7fa9c778b779d027f3bce63b3ace9`; use
`scripts/fetch-board-files.sh` to reproduce the local checkout.

## Architectural implications

- PetaLinux and GNU Radio target builds will be 32-bit ARM, not AArch64.
- Target memory is constrained, so the embedded GNU Radio build should begin
  headless and omit GUI components.
- Host GNU Radio remains a separate x86_64 build. Host and target artifacts are
  not ABI-compatible even if they use the same GNU Radio source version.
- The first hardware platform should validate the processing system, DDR,
  microSD, serial console, Ethernet, clocks, and resets before accelerator IP is
  introduced.
- Any board preset or constraints must match the physical board revision.

## Next information to record

1. Preferred initial boot source (microSD is recommended for bring-up).
2. Available microSD capacity and USB cable for JTAG/UART.
3. Whether the board will use wired Ethernet and DHCP during development.
4. Whether an existing Cora Z7 BSP or known-good Linux image is available.

## Primary references

- Digilent Cora Z7 device statement:
  <https://files.digilent.com/resources/programmable-logic/cora-z7/SoV-Cora-Z7.pdf>
- Digilent Vivado board-files repository:
  <https://github.com/Digilent/vivado-boards>
- Digilent Cora Z7-10 revision-B master constraints:
  <https://github.com/Digilent/digilent-xdc/blob/master/Cora-Z7-10-Master.xdc>
