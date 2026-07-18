# Cora Z7-10 Base Hardware Platform

## Scope

The first hardware platform contains only the Zynq-7000 processing system,
DDR, and fixed processing-system I/O configured by the verified Digilent Cora
Z7-10 Rev. B board preset. It intentionally contains no accelerator, DMA,
programmable-logic peripheral, or bitstream.

## Reproduce the platform

```bash
source scripts/activate-tools.sh
scripts/build-base-hardware.sh
```

Source-controlled inputs:

- `hardware/vivado/tcl/create_base_platform.tcl`
- Digilent board files fetched by `scripts/fetch-board-files.sh`
- Board-files commit `36f34ab687b7fa9c778b779d027f3bce63b3ace9`
- Vivado 2025.1 build 6140274

Generated outputs:

- Vivado project: `build/vivado/cora-z7-10/cora_z7_10_base.xpr`
- Hardware export: `hardware/export/cora-z7-10/cora_z7_10_base.xsa`

Generated products are ignored by Git and must be reproduced from the Tcl
input. The XSA is a pre-bitstream hardware description for initial PetaLinux
configuration; it is not a bootable or hardware-validated release bundle.

## Current validation

- Vivado recognizes `digilentinc.com:cora-z7-10:part0:1.1`.
- The board maps to `xc7z010clg400-1`.
- IP Integrator validates the PS-only block design.
- Vivado successfully generates the block-design targets and XSA.
- The XSA contains PS7 initialization data and hardware handoff metadata.

No synthesis, implementation, bitstream generation, board programming, DDR
test, UART test, Ethernet test, microSD boot, or Linux boot has occurred yet.

## DDR preset warning

Vivado 2025.1 reports critical warnings for two negative DDR DQS-to-clock delay
values:

```text
PCW_UIPARAM_DDR_DQS_TO_CLK_DELAY_2 = -0.009
PCW_UIPARAM_DDR_DQS_TO_CLK_DELAY_3 = -0.033
```

These values come directly from Digilent's Cora Z7-10 B.0 `preset.xml`; they
were not introduced by the project generator. Do not suppress or alter them
without board-specific evidence. DDR remains unverified until a memory test or
Linux workload runs successfully on the physical board.

