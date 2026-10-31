# AXI-stream acoustic router

`axis_acomms_router_axi` places a runtime-selectable hardware insertion slot
between a bidirectional AXI DMA and an acoustic accelerator chain. The host
side always remains connected to the DMA. Set control bit 0 to `0` for a
byte-exact loopback or `1` to route through the ports labeled
`M_AXIS_ACCELERATOR` and `S_AXIS_ACCELERATOR`.

The return stream is paced with an AXI-safe one-sample buffer. On the Cora
100 MHz PL clock, the reset divider is 400, giving 250,000 samples/s. TLAST
and backpressure are preserved in both modes.

| Offset | Access | Meaning |
| --- | --- | --- |
| `0x00` | R/W | bit 0: accelerator path; bit 1: clear counters (pulse) |
| `0x04` | R/W | PL clock cycles per output sample; zero writes ignored |
| `0x08` | R/W | effective integer sample rate metadata (written with divider) |
| `0x0c` | R | bit 0: active path; bit 1: output buffer occupied |
| `0x10..0x14` | R | completed sample count, 64-bit |
| `0x18..0x1c` | R | completed TLAST/frame count, 64-bit |
| `0x20` | R | core ID `0x41434f4d` (`ACOM`) |
| `0x24` | R | core version |
| `0x28` | R | PL clock frequency |
| `0x2c` | R | reset/default sample rate |
