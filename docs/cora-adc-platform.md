# Cora Z7-10 ADC input platform

The initial ADC input is shield header **A0**. On the Cora Z7-10, A0 is XADC
auxiliary channel **VAUX[1]**. The board divides its 0–3.3 V range to the
XADC's 0–1.0 V input range, so do not apply more than 3.3 V to A0.

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

The device tree enables the XADC child `reg = <2>`, which is VAUX[1]. Linux's
Xilinx XADC driver publishes the enabled auxiliary channel as an unnamed
generic IIO voltage input. The current kernel image calls A0
`in_voltage8_raw` and `in_voltage8_scale`; the numeric suffix is
kernel-dependent. The smoke tools default to `channel=auto`, which selects
the first enabled generic auxiliary channel. This is an IIO filename
convention, not the physical VAUX number.

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
