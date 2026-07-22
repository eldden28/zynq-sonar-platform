# Cora Z7 signed-16 PWM stream

This directory contains the Linux and GNU Radio side of the AXI-stream PWM
path implemented in the Cora Z7-10 FPGA image.

The data contract is one signed 16-bit sample per PWM carrier period. The
hardware maps `-32768` to 0% duty, `0` to approximately 50%, and `32767` to
approximately 100%. With the default 200 MHz PWM clock and 2,000-tick period,
the carrier and sample rate are both 100 kHz. Fractional dithering preserves
the long-term signed-16 value even though one individual carrier period has
only about 11 bits of duty resolution.

## Components

- `kernel/cora_pwm.c` exposes the AXI DMA transmit path as `/dev/cora-pwm0`.
  It owns an eight-period coherent cyclic DMA ring, provides `poll()`
  backpressure, and disables the PWM output if the stream underruns.
- `include/cora_pwm_ioctl.h` is the shared userspace ABI for configuration,
  status, and stream control.
- `gnuradio/gr-cora` provides the GNU Radio `cora.pwm_sink` block and the
  **Cora Signed-16 PWM Sink** block for GNU Radio Companion.

The device accepts complete 4,096-byte periods (2,048 signed-16 samples). The
GNU Radio sink buffers scheduler chunks into that transfer size, so a flowgraph
still uses an ordinary stream of shorts.

## GNU Radio use

Python flowgraphs can construct the sink directly:

```python
from gnuradio import cora

sink = cora.pwm_sink(
    "/dev/cora-pwm0",
    2000,   # PWM period ticks: 200 MHz / 2000 = 100 kHz
    False,  # do not invert the output
    True,   # enable fractional duty dithering
)
```

Only one process may open `/dev/cora-pwm0`. Closing the block or stopping the
flowgraph stops DMA and forces the PWM output inactive.

On the board, run `cora-pwm-smoke` for a one-second repeating five-level test
waveform. It verifies the driver ABI, starts cyclic DMA, and exercises GNU
Radio backpressure. Observe JA1 (FPGA pin Y18) with a scope or logic analyzer.

```sh
cora-pwm-smoke
```

Use `cora-pwm-smoke --help` for the device, duration, and period options.
