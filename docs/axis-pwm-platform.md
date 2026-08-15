# Cora Z7-10 DMA-to-PWM platform

This platform streams signed 16-bit samples from Zynq DDR to the PWM output on
Pmod JA pin 1. The Vivado design is generated entirely from source-controlled
Tcl and RTL.

## Data path

```text
                              100 MHz AXI                         200 MHz PWM

Zynq DDR <--HP0-- SmartConnect <-- AXI DMA MM2S --AXI4-Stream--> clock converter --> PWM --> JA1
                  ^                |                                                   |
                  `-- DMA SG ------+-- IRQ_F2P[0]                                     |
                                                                                      |
PS M_AXI_GP0 --> SmartConnect --> DMA control                                          |
                         `--> AXI-Lite clock converter ------------------------> PWM registers
```

- The DMA MM2S memory interface is 64 bits wide and uses the Zynq S_AXI_HP0
  port.
- The DMA scatter-gather descriptor interface is 32 bits wide and reaches the
  same DDR through S_AXI_HP0.
- The DMA stream is 16 bits wide and carries packed two's-complement samples.
- AXI DMA and the PS-facing fabric run at 100 MHz.
- The stream and AXI-Lite clock converters cross into the 200 MHz PWM domain.
- The default 2,000-tick period produces a 100 kHz carrier.
- DMA `TLAST` marks a transfer boundary but does not interrupt PWM timing.
- DMA backpressure propagates from the PWM core's 64-sample FIFO.

## Physical output

| Signal | Pmod | FPGA package pin | Electrical standard |
|---|---|---|---|
| `pwm_out` | JA pin 1 | Y18 | LVCMOS33, 8 mA, slow slew |

Use JA pin 5 or pin 11 as the matching ground when probing the output. JA1 is
a 3.3 V logic signal and must feed a compatible logic input or an external
buffer/gate driver rather than a power load directly.

## Address map

| Peripheral | Base address | Range |
|---|---:|---:|
| AXI DMA control | `0x40400000` | 64 KiB |
| Signed-16 PWM control/status | `0x43c00000` | 64 KiB |

The DMA is configured for MM2S scatter-gather mode with the data realignment
engine enabled. Its relevant standard AXI DMA offsets are MM2S control `0x00`,
status `0x04`, current descriptor `0x08`, and tail descriptor `0x10`. The
kernel DMAengine client should own these registers and the descriptor ring;
GNU Radio should use the character-device API instead of programming them
directly.

### PWM registers

All offsets are relative to `0x43c00000`.

| Offset | Access | Name | Description |
|---:|---|---|---|
| `0x00` | R/W | CONTROL | Bit 0 enable, bit 1 invert, bit 2 fractional dither |
| `0x04` | R/W | PERIOD_TICKS | PWM period; clamped by the core to 2 through 65,535 |
| `0x08` | W | COMMAND | Write bit 0 as one to clear sample and underrun counters |
| `0x0c` | R | STATE | Bits 0-2 control, bit 3 period start, bit 4 FIFO empty, bit 5 FIFO full |
| `0x10` | R | ACTIVE_PERIOD | Period currently applied at the output |
| `0x14` | R | ACTIVE_DUTY | High ticks currently applied at the output |
| `0x18` | R | FIFO_LEVEL | Samples currently queued inside the PWM core |
| `0x1c` | R | UNDERRUN_COUNT | Number of empty PWM boundaries |
| `0x20` | R | SAMPLE_COUNT_LO | Accepted-output sample count bits 31:0 |
| `0x24` | R | SAMPLE_COUNT_HI | Accepted-output sample count bits 63:32 |
| `0x28` | R | CORE_ID | `0x50574d31` (`PWM1`) |
| `0x2c` | R | CORE_VERSION | `0x00010000` (1.0) |

Reset leaves the output disabled, dithering enabled, and the period set to
2,000 ticks. A safe startup sequence is to verify `CORE_ID`, configure the
period, prepare/start the DMA transfer, and then write `0x00000005` to CONTROL
to enable the output with dithering.

## Reproduce the hardware

Validate the block design without implementation:

```bash
scripts/build-pwm-hardware.sh validate
```

Build the routed bitstream and bitstream-bearing XSA:

```bash
scripts/build-pwm-hardware.sh
```

Generated products:

- Vivado project: `build/vivado/cora-z7-10-pwm/cora_z7_10_pwm.xpr`
- Bitstream: `build/vivado/cora-z7-10-pwm/cora_z7_10_pwm.runs/impl_1/system_wrapper.bit`
- XSA: `hardware/export/cora-z7-10-pwm/cora-z7-10-pwm.xsa`
- Reports: `build/reports/cora-z7-10-pwm/`

The verified scatter-gather implementation uses 4,787 slice LUTs, 7,408
registers, 1.5 block RAM tiles, and one DSP48E1. Signoff timing is met with
0.341 ns worst setup slack and 0.015 ns worst hold slack. The routed bus-skew
requirements also pass. Vivado continues to emit the two known negative DDR
DQS-skew critical warnings inherited from Digilent's Cora Z7-10 Rev. B board
preset.

## Software boundary

The XSA describes the SG-capable DMA, its interrupt, and both control regions
for PetaLinux. Importing this XSA and adding the DMA buffer/driver path are
separate from the hardware build. The planned kernel DMAengine client will own
a cyclic descriptor ring and expose `/dev/cora-pwm0`; the GNU Radio sink will
write packed `int16` buffers and use `poll()` for flow control. This keeps DMA
register access, cache coherency, and underrun recovery out of userspace.
