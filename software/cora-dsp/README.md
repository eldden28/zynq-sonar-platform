# Cora 512-point spectral accelerator

`cora_dsp` exposes the FPGA's fixed-point
FFT → symmetric bin mask → IFFT chain as `/dev/cora-dsp0`.

Each transaction writes exactly 512 signed little-endian Q1.15 real samples
and reads exactly 512 processed samples. The driver packs/unpacks the complex
AXI-stream format and runs a dedicated bidirectional AXI DMA. Filter limits
are inclusive positive-frequency bins from 0 through 256; the FPGA applies
the conjugate negative-frequency mirror automatically.

The accelerator is intentionally separate from `/dev/cora-pwm0`, preserving
the proven continuous cyclic PWM DMA interface.
