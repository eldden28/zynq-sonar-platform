# Cora acoustic PL loopback

This small Python module connects acoustic modem waveforms to the hardware
AXI4-Stream router at `/dev/cora-dsp0`. It resamples 48 ksample/s dashboard
waveforms to the PL rate (250 ksample/s by default), transfers them through
the MM2S → router → S2MM loopback in 512-sample frames, then returns them to
the modem rate.

Use `AxisLoopbackDevice` for native 250 ksample/s frames and
`roundtrip_waveform` for existing modem waveforms. Selecting this backend
proves the PS memory, DMA, AXIS handshakes, TLAST, sample pacing, and Linux
driver before a custom accelerator is connected.
