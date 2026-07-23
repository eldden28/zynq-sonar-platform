# Cora 512-point spectral accelerator

## Data path

The Cora Z7-10 programmable logic contains a dedicated block-processing path:

```text
512 signed real Q1.15 samples in DDR
  -> AXI DMA MM2S (32-bit complex packing)
  -> Xilinx 512-point FFT
  -> configurable symmetric spectral mask
  -> Xilinx 512-point IFFT
  -> AXI DMA S2MM
  -> 512 signed real Q1.15 samples in DDR
```

The accelerator DMA is separate from the cyclic DMA feeding the PWM core. The
Linux driver exposes the block engine as `/dev/cora-dsp0`; GNU Radio exposes it
as `cora.hw_fft_filter`.

The forward FFT applies a nine-bit schedule, giving `FFT(x)/512`. The inverse
FFT is unscaled, so an all-pass FFT/IFFT pair has approximately unity gain.
Both FFTs use 16-bit fixed-point complex data. Filter limits are inclusive
positive-frequency bins from 0 through 256. Hardware automatically applies
the conjugate negative-frequency mirror so real input produces real output.

## Live A0-to-PWM demo

Connect the analog signal and ground to shield A0, then run:

```sh
cora-adc-fft-pwm --low-bin 0 --high-bin 64
```

The flowgraph is:

```text
Cora IIO ADC Source
  -> Cora Hardware FFT Filter
  -> Float to Short
  -> Cora Signed-16 PWM Sink
```

The current on-chip XADC backend is polled through IIO and held between new
measurements. This validates the full software/DMA/FFT/PWM path, but it is not
a phase-continuous high-rate acquisition source. A future external streaming
ADC can replace the source with `cora.dma_s16_source` without changing the
accelerator or PWM blocks.

## CPU versus FPGA benchmark

Run the repeatable two-tone benchmark on the board:

```sh
cora-dsp-benchmark
```

The default signal contains tones at bins 12 and 96, while the passband keeps
bins 8 through 24. It reports:

- ARM NumPy FFT/filter/IFFT latency
- FPGA end-to-end userspace latency
- DMA plus accelerator latency measured in the kernel
- speedup
- fixed-point RMSE and maximum error versus the CPU result
- stop-bin attenuation and DMA failures

Use more frames for a steadier measurement:

```sh
cora-dsp-benchmark --frames 1000 --low-bin 8 --high-bin 24
```

No other process may hold `/dev/cora-dsp0` while the demo or benchmark runs.

## Hardware register map

The spectral mask is mapped at `0x43c10000`; its dedicated simple-mode AXI DMA
is mapped at `0x40410000`.

| Offset | Access | Description |
|---:|:---:|---|
| `0x00` | R/W | Lowest positive-frequency pass bin |
| `0x04` | R/W | Highest positive-frequency pass bin |
| `0x08` | W | Write bit 0 to clear counters |
| `0x0c` | R | Current bin index |
| `0x10..0x14` | R | Completed frame count |
| `0x18..0x1c` | R | Accepted complex sample count |
| `0x20..0x24` | R | Zeroed-bin count |
| `0x28` | R | Core ID (`DSP1`) |
| `0x2c` | R | Core version |
