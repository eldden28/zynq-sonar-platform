# Cora acoustic OFDM text terminal

This dashboard transfers pasted UTF-8 text as numbered, CRC-protected packets
through the acoustic OFDM modem. Validated chunks appear progressively in a
read-only receive terminal.

The version selector offers v2 triple repetition; v3 interleaved K=7
rate-1/2 convolutional FEC; an experimental punctured rate-2/3 variant; and an
experimental uncoded body with CRC detection. Both convolutional modes use the
64-state Viterbi decoder. The FFT selector offers the validated 256-point
default and an experimental 512-point mode.
Low and high carrier edges are independently programmable and snap to the
selected 187.5 or 93.75 Hz FFT-bin grid. Five pilots are redistributed across
the selected band. Standard 2.25--9.75 kHz, wide 1.5--12 kHz, experimental
1.5--15 kHz, voice-band 3--9 kHz, and custom profiles are available.

The service listens on port 8082:

```text
http://192.168.10.2:8082/
```

The safe default is the deterministic colored-room simulation. It exercises
real audio waveform generation, multipath, spectral color, noise,
synchronization, training, pilot correction, QPSK decoding, repetition
recovery, and CRC validation without emitting sound.

Air mode is available in the dashboard and uses the board's USB PnP Sound
Device at `plughw:0,0` for both playback and capture. It remains opt-in on the
page: select **Speaker → lapel microphone** before transmitting. Start with
low headphone/speaker volume and use a fixed microphone gain.

Air transfers use continuous superframes of up to eight fixed packet slots.
One `aplay` process feeds raw stereo PCM continuously while one `arecord`
process drains mono capture. Three-symbol channel training is refreshed every
two packets. Decoder workers acquire each refresh once and validate its two
slots as soon as the required samples arrive, while playback and capture
continue. A failed packet still falls back to the original stop-and-wait path
for an isolated retry, so per-packet headers and CRC behavior are unchanged.

The first live streaming checkpoint transferred 1,536 bytes as 16 packets in
13.96 seconds with no retries or packet errors. Validated payload throughput
was 880 bit/s. A two-burst 3,072-byte run then completed 32/32 packets in
28.15 seconds at 873 bit/s, again with no errors. This is about 2.2 times the
earlier 393 bit/s stop-and-wait result.

Live packet-size measurements used the same 3,072-byte payload:

| Chunk | Packets | Time | Payload rate | Errors/retries |
|---:|---:|---:|---:|---:|
| 96 bytes | 32 | 28.15 s | 873 bit/s | 0 / 0 |
| 384 bytes | 8 | 20.41 s | 1,204 bit/s | 0 / 0 |
| 768 bytes | 4 | 20.09 s | 1,224 bit/s | 0 / 0 |
| 1,024 bytes | 3 | 29.09 s | 845 bit/s | 1 / 1 |

The dashboard therefore defaults to 384-byte chunks. The 768-byte result was
only 1.6% faster, while an errored packet costs twice as much airtime and
delays twice as much validated text.

For a 384-byte chunk in the standard band, v3 reduces the acoustic body from
136 to 91 OFDM symbols and the complete waveform from 1.040 to 0.740 seconds.
The 1.5--12 kHz profile provides 52 QPSK data carriers and a 0.553-second
waveform. The 1.5--15 kHz profile remains available for experiments, although
real reliability depends on the physical audio response and per-carrier SNR.

Live v3 measurements used the same 3,072-byte payload and the packaged ARM
Viterbi accelerator:

| Version/profile | Time | Payload rate | Errors/retries | Result |
|---|---:|---:|---:|---|
| v2, 2.25--9.75 kHz | 20.41 s | 1,204 bit/s | 0 / 0 | PASS |
| v3, 2.25--9.75 kHz, pre-optimization | 15.22 s | 1,615 bit/s | 0 / 0 | PASS |
| v3 FFT256, 2.25--9.75 kHz, optimized | 7.38 s | 3,329 bit/s | 0 / 0 | PASS |
| v3 R2/3 FFT256, 2.25--9.75 kHz | 6.16 s | 3,993 bit/s | 0 / 0 | PASS |
| v3 R2/3 continuous, 2.25--9.75 kHz | 5.22 s mean | 4,706 bit/s mean | 0 / 0 across 3 runs | PASS |
| v3 R2/3 FFT256, 1.5--12 kHz | -- | -- | exhausted 3 attempts twice | FAIL |
| v3 FFT512, 2.25--9.75 kHz, optimized | 20.07 s | 1,225 bit/s | 5 / 5 | PASS |
| v3, 1.5--12 kHz, optimized | 5.73 s | 4,288 bit/s | 0 / 0 | PASS |
| v3, 1.5--15 kHz | -- | -- | exhausted 3 attempts | FAIL |

The v3 standard profile is 34% faster than the v2 baseline. After caching
carrier geometry and batching grid/IFFT generation, the validated wide profile
is 3.56 times as fast as the v2 baseline. On this USB audio path, the
experimental 15 kHz edge failed payload CRC, so it is not the default.

FFT512 works, but does not improve net throughput at 48 ksample/s. Its 76 data
carriers raise standard-band gross rate by 17.3%, from 10.8 to 12.67 kbit/s.
However, 93.75 Hz subcarrier spacing and 12 ms useful symbols are less tolerant
of residual frequency offset and time variation. One eight-packet run failed
on packet 7 after all three attempts; a repeat completed with five retries and
was 63% slower than the clean FFT256 control. The dashboard therefore labels
FFT512 experimental.

An FFT256 uncoded experiment retained the triplicated packet header and
CRC-32, but sent payload-body QPSK bits without FEC:

| Body / chunk | Time | Payload rate | Errors/retries | Result |
|---|---:|---:|---:|---|
| v3 convolutional, 192 bytes | 9.32 s | 2,637 bit/s | 0 / 0 | PASS |
| uncoded, 192 bytes | 5.98 s | 4,111 bit/s | 0 / 0 | PASS |
| v3 convolutional, 384 bytes | 7.38 s | 3,329 bit/s | 0 / 0 | PASS |
| uncoded, 384 bytes | 8.50 s | 2,893 bit/s | 3 / 3 | PASS |

A separate uncoded 384-byte run failed after packet 5 exhausted all three
attempts. At 192 bytes, removing FEC improved matched payload throughput by
55.9%; at 384 bytes, accumulated bit-error probability erased the airtime
gain. Uncoded mode is therefore useful for controlled clean-channel tests, but
v3 remains the default. The dashboard automatically uses the measured
192-byte chunk size for uncoded mode and retains its configured 384-byte
default for v2/v3.

The rate-2/3 mode uses the standard `1110` puncturing pattern over the existing
K=7 rate-1/2 mother code. Its accelerated Viterbi decoder ignores omitted
parity positions as erasures. At 384-byte chunks in the standard band it was
19.9% faster than rate-1/2 while retaining FEC and completing without a retry.
It is only 2.9% behind the clean uncoded 192-byte result. The same mode failed
twice in the wide band, so the dashboard labels it experimental and leaves
rate-1/2 as the default.

The continuous rate-2/3 result used three consecutive 3,072-byte,
eight-by-384-byte air transfers. The individual payload rates were 4,696,
4,699, and 4,723 bit/s with no errors or retries. Compared with an exact
packet-framed control at 3,998 bit/s, continuous framing reduced mean wall time
by 15.0% and increased validated throughput by 17.7%. The superframe audio
fell from 4.693 to 3.960 seconds while retaining a fresh channel estimate every
two packets.
