# Cora 512-point spectral accelerator

`cora_dsp` exposes the FPGA's bidirectional acoustic AXI-stream DMA as
`/dev/cora-dsp0`. ABI v2 can select either a byte-exact loopback or the
fixed-point FFT → symmetric bin mask → IFFT accelerator chain. Both paths are
paced at 250 ksample/s by default.

Each transaction writes exactly 512 signed little-endian Q1.15 real samples
and reads exactly 512 processed samples. The driver packs/unpacks the complex
AXI-stream format and runs a dedicated bidirectional AXI DMA. Filter limits
are inclusive positive-frequency bins from 0 through 256; the FPGA applies
the conjugate negative-frequency mirror automatically.

The accelerator is intentionally separate from `/dev/cora-pwm0`, preserving
the proven continuous cyclic PWM DMA interface.

Run `cora-axis-loopback-test` for a deterministic data-integrity, TLAST,
counter, and sample-pacing check. See `docs/axis-acomms-loopback.md` for the
register map and the manual custom-IP connection procedure.
