# Cora Z7-10 ADC input platform

The Cora Z7-10 exposes ten user-facing XADC inputs. A0-A5 are single-ended
0–3.3 V inputs with board-level scaling, A6-A11 form three differential
0–1.0 V pairs, and VP/VN is the dedicated differential 0–1.0 V input.

| Header input | Physical XADC channel | Mode |
|---|---|---|
| A0 | VAUX1 | single-ended |
| A1 | VAUX9 | single-ended |
| A2 | VAUX6 | single-ended |
| A3 | VAUX15 | single-ended |
| A4 | VAUX5 | single-ended |
| A5 | VAUX13 | single-ended |
| A6-A7 | VAUX12 | differential/bipolar |
| A8-A9 | VAUX0 | differential/bipolar |
| A10-A11 | VAUX8 | differential/bipolar |
| VP-VN | VP/VN | differential/bipolar |

## Common source contract

All Cora ADC sources deliver a GNU Radio float stream normalized to `-1.0`
through `+1.0`:

| Physical input | Normalized sample | PWM duty |
|---:|---:|---:|
| 0 V | -1.0 | approximately 0% |
| 1.65 V | 0.0 | approximately 50% |
| 3.3 V | +1.0 | approximately 100% |

`cora.iio_adc_source` is the current XADC and IIO-ADC backend. It acquires a
new measurement every 100 ms by default and repeats that value at the
downstream rate. This lets the 10 Hz control signal drive the 100 kHz PWM
stream without PWM underruns.

`cora.dma_s16_source` defines the high-rate external-converter backend. Its
standard Cora ADC DMA ABI is a nonblocking `/dev/cora-adc0` character device
containing contiguous little-endian signed-16 samples. It converts those
samples to the exact same normalized float contract, so the downstream
flowgraph is unchanged when an AXI-stream external ADC and its DMA driver are
added.

## XADC device tree mapping

The XADC driver always publishes its dedicated VP/VN input as
`in_voltage8_raw`. It appends the device-tree-configured channels in the
table's order beginning at `in_voltage9_raw`: voltage9 is A0, voltage10 is A1,
through voltage18 for the explicitly configured bipolar VP/VN view. The
dashboard applies the board labels and plots the underlying 12-bit codes from
0 through 4095. Bipolar channels have signed IIO values; the dashboard masks
those values to their 12-bit two's-complement representation for a consistent
raw-code plot.

The smoke and GNU Radio tools still default to `channel=auto`, which selects
A0 as the first external channel.

Linux reports XADC `scale` in millivolts per raw count. The ADC module converts
that to volts, and the A0 tools' default `scale_multiplier=3.3` compensates
the Cora analog divider. Thus their displayed voltage and normalization refer
to the signal at shield A0 rather than the lower voltage at the XADC pin.

## On-board smoke test

After booting the new image, connect a 0–3.3 V signal to shield A0 and board
ground, then run:

```sh
cora-adc-smoke
```

When regenerating the SD image, use the project wrapper:

```sh
./package-wic.sh
```

It includes `system.dtb` in the FAT boot partition and uses the standard
512 MiB boot plus 2 GiB root layout. The PetaLinux 2025.1 packaging defaults
omit the external DTB and allocate oversized 2 GiB plus 4 GiB partitions.

It redraws a vertical history every 0.1 seconds, with oldest samples at the
top and the most recent at the bottom. To capture a finite, line-oriented
sample set for logs:

```sh
cora-adc-smoke --samples 20
```

## ADC-to-PWM flowgraph

The installed `cora-adc-to-pwm` GNU Radio flowgraph is:

```text
Cora IIO ADC Source (A0, 10 Hz polling) -> Float to Short (x32767) -> Cora Signed-16 PWM Sink
```

Run it with:

```sh
cora-adc-to-pwm
```

Use `Ctrl-C` to stop; it stops DMA and makes the PWM output inactive. For a
future external signed-16 DMA ADC, keep the PWM half unchanged and select the
new input endpoint:

```sh
cora-adc-to-pwm --backend dma-s16 --adc-device /dev/cora-adc0
```
