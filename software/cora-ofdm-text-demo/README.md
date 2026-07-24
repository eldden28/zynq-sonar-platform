# Cora acoustic OFDM text terminal

This dashboard transfers pasted UTF-8 text as numbered, CRC-protected packets
through the acoustic OFDM v2 modem. Validated chunks appear progressively in a
read-only receive terminal.

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

Air transfers use eight-packet streaming bursts. One `aplay` process feeds raw
stereo PCM continuously while one `arecord` process drains mono capture.
Two decoder workers validate packet windows while playback continues. A failed
packet still falls back to the original stop-and-wait path for an isolated
retry, so CRC behavior is unchanged.

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
