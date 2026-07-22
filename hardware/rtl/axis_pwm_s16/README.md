# Signed-16 AXI-stream PWM sink

`axis_pwm_s16` converts a continuous signed 16-bit AXI4-Stream into a
fixed-carrier PWM output. The software/data ABI stays signed 16-bit even when
the physical clock cannot provide 16 bits of instantaneous PWM resolution.

## Defaults

| Parameter | Default |
|---|---:|
| PWM-domain clock | 200 MHz |
| Carrier/sample rate | 100 kHz |
| Period | 2,000 clock ticks |
| Duty states | 2,001, including 0% and 100% |
| Instantaneous resolution | approximately 10.97 bits |
| Stream FIFO | 64 samples / 640 microseconds |

The clock frequency is an integration constraint rather than an RTL input.
The default period is `2000`; driving `aclk` at 200 MHz therefore produces the
100 kHz carrier. Software may change `cfg_period_ticks`, which is applied at the
next PWM boundary. The default 16-bit period counter accepts 2 through 65,535
ticks, corresponding to carriers from 100 MHz down to approximately 3.05 kHz
with a 200 MHz clock. Larger values are clamped to 65,535; increase
`PERIOD_WIDTH` and rerun timing checks if a lower carrier is required.

## Sample mapping

The default bipolar mapping is:

| Signed sample | Duty |
|---:|---:|
| `-32768` | 0% |
| `0` | 50% |
| `32767` | 100% |

The module converts two's-complement ordering to offset-binary with
`sample ^ 16'h8000`, then scales that unsigned value to the active period.
With fractional dithering enabled, the low 16 bits of the scaled result are
carried into the following period. This improves long-term average accuracy
when the requested value falls between physical counter ticks.

## Timing and flow control

- One sample is consumed at each PWM boundary.
- Duty changes are committed only when the counter rolls over.
- A 64-entry FIFO absorbs AXI burstiness.
- `TREADY` deasserts while the FIFO is full.
- An empty FIFO holds the last duty and increments the underrun counter.
- Deasserting `cfg_enable` forces the output low and flushes queued samples.
- `TLAST` is accepted as a DMA-buffer marker but does not alter PWM timing.

The module clock must be the PWM-domain clock. If AXI DMA uses another clock,
place an AXI4-Stream Clock Converter or asynchronous AXIS Data FIFO between the
DMA and this module.

## Cora Z7-10 output

The platform assignment uses Pmod connector JA signal pin 1:

| Signal | Pmod pin | FPGA pin | I/O standard |
|---|---|---|---|
| `pwm_out` | JA1 | Y18 | LVCMOS33 (3.3 V) |

The constraint is in
`hardware/constraints/cora-z7-10-axis-pwm.xdc`. The output is configured for
8 mA drive and slow slew. It is intended for a 3.3 V-compatible logic input or
an external buffer/gate driver, not for directly driving a power load.

## Integration ports

The initial core exposes simple configuration and status signals so its data
path can be verified independently. The platform integration should place a
small AXI-Lite register wrapper around:

- `cfg_enable`
- `cfg_invert`
- `cfg_dither_enable`
- `cfg_period_ticks`
- `clear_status`
- `status_period_ticks`
- `status_duty_ticks`
- `status_fifo_level`
- `status_underrun_count`
- `status_sample_count`

The Cora Z7-10 platform wraps these signals in AXI4-Lite registers at
`0x43c00000` and feeds the stream from an AXI DMA MM2S channel at `0x40400000`.
See `docs/axis-pwm-platform.md` for the complete address and register maps.

## Simulation

From the repository root:

```bash
scripts/test-axis-pwm.sh
```

The self-check verifies endpoint and midpoint mapping, a boundary-safe runtime
period change, fractional dithering, underrun accounting, and FIFO
backpressure.

Run the 200 MHz out-of-context synthesis/timing check with:

```bash
scripts/check-axis-pwm-synthesis.sh
```

Reports are written beneath `build/reports/axis_pwm_s16/`.

The checked default configuration meets the 200 MHz target on the Cora Z7-10
device (`xc7z010clg400-1`) with 0.522 ns worst setup slack after synthesis,
including 0.200 ns clock uncertainty. The synthesized core uses 327 slice LUTs,
323 slice registers, one DSP48E1, and no block RAM; the 64-sample FIFO maps to
distributed RAM. These figures are an out-of-context baseline and may change
after platform integration and implementation.
