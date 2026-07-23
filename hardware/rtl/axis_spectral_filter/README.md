# 512-bin AXI-stream spectral mask

This core sits between fixed-point Xilinx FFT and IFFT blocks. It preserves
the inclusive positive-frequency bin range `low_bin..high_bin` and the
conjugate negative-frequency mirror required for real signals. Other complex
Q1.15 bins are replaced with zero without changing AXI-stream framing.

The default `0..256` mask is all-pass. The Linux `cora_dsp` driver changes the
mask only while its dedicated DMA is idle.
