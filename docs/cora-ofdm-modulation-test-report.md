# Cora acoustic OFDM modulation test report

Report date: 2026-07-25

## Executive summary

Every acoustic waveform tested so far uses real-valued OFDM audio with QPSK
data carriers, five pilots, a 48 ksample/s sample rate, and a 64-sample cyclic
prefix. We have not yet compared QPSK against BPSK, 8-PSK, or QAM over the
physical audio path. The experiments called v2, v3, rate-2/3, and uncoded are
different channel coding and framing modes around the same QPSK constellation.

The best repeatable physical result is:

- v3 punctured rate-2/3 FEC;
- 256-point FFT;
- 2.25--9.75 kHz carrier band;
- eight 384-byte packets in a continuous superframe;
- channel training refreshed every two packets;
- 3,072 bytes delivered and SHA-256 validated;
- 4,705.8 bit/s mean over three consecutive runs;
- zero packet errors and zero retries in all three runs.

This was 17.7% faster than the matched packet-framed rate-2/3 control at
3,997.6 bit/s. The best validated wide-band result remains v3 rate-1/2 at
1.5--12 kHz and 4,288.1 bit/s. Rate-2/3 failed both wide-band attempts, so its
current validation is limited to the standard band.

## Common physical layer

| Parameter | Value |
|---|---|
| Audio sample rate | 48,000 sample/s |
| Constellation | QPSK |
| Default FFT | 256 points |
| Default cyclic prefix | 64 samples, 1.333 ms |
| FFT256 carrier spacing | 187.5 Hz |
| Standard carrier band | 2.25--9.75 kHz |
| Standard FFT256 data carriers | 36 |
| Per-symbol tracking | Five pilot carriers |
| Acquisition | Three known training symbols |
| Header | Three protected copies with majority vote |
| Payload integrity | CRC-32 plus end-to-end SHA-256 |
| Test path | Cora Z7-10, USB audio dongle, physical audio loop |

All payload rates below are validated application payload, not gross QPSK
carrier rate. A PASS means the complete received byte stream matched the source
SHA-256. A successful run with retries is marked PASS but is not considered a
stable recommended profile.

## Coding modes tested

| Mode | Body protection | Purpose | Current assessment |
|---|---|---|---|
| v2 | Three complete interleaved body copies | Initial robust baseline | Reliable but slow |
| v3 rate-1/2 | K=7, 171/133 convolutional code and Viterbi decoding | Robust coded mode | Validated default |
| v3 rate-2/3 | v3 mother code punctured with repeating `1110` pattern | Higher coded throughput | Best standard-band result; wide band failed |
| Uncoded | Raw QPSK body with CRC-32 | Isolate FEC cost and channel threshold | Fast at 192 bytes; unreliable at 384 bytes |

Each mode has a distinct wire version, preventing a receiver configured for
one body code from silently accepting another.

## Main physical-air comparison

Unless otherwise stated, each row transferred 3,072 application bytes through
the physical audio path.

| Mode and profile | Packetization | Wall time | Payload rate | Errors / retries | Outcome |
|---|---:|---:|---:|---:|---|
| v2, FFT256, 2.25--9.75 kHz | 8 x 384 B | 20.406 s | 1,204 bit/s | 0 / 0 | PASS |
| v3 rate-1/2, FFT256, 2.25--9.75 kHz, before CPU optimization | 8 x 384 B | 15.217 s | 1,615 bit/s | 0 / 0 | PASS |
| v3 rate-1/2, FFT256, 2.25--9.75 kHz, optimized | 8 x 384 B | 7.381 s | 3,329 bit/s | 0 / 0 | PASS |
| v3 rate-1/2, FFT256, 1.5--12 kHz, optimized | 8 x 384 B | 5.731 s | 4,288 bit/s | 0 / 0 | PASS |
| v3 rate-1/2, FFT256, 1.5--15 kHz | 8 x 384 B planned | -- | -- | Exhausted three attempts on packet 1 | FAIL |
| v3 rate-1/2, FFT512, 2.25--9.75 kHz | 8 x 384 B | 20.065 s | 1,225 bit/s | 5 / 5 | PASS, unstable |
| v3 rate-2/3, FFT256, 2.25--9.75 kHz, packet framed | 8 x 384 B | 6.148 s | 3,998 bit/s | 0 / 0 | PASS |
| v3 rate-2/3, FFT256, 1.5--12 kHz | 8 x 384 B planned | -- | -- | Exhausted retries twice | FAIL |
| Uncoded, FFT256, 2.25--9.75 kHz | 16 x 192 B | 5.978 s | 4,111 bit/s | 0 / 0 | PASS |
| Uncoded, FFT256, 2.25--9.75 kHz | 8 x 384 B | 8.496 s | 2,893 bit/s | 3 / 3 | PASS, unstable |
| v3 rate-2/3 continuous, FFT256, 2.25--9.75 kHz | 8 x 384 B | 5.223 s mean | 4,706 bit/s mean | 0 / 0 across 3 runs | PASS |

The separate first uncoded 384-byte attempt failed after packet 5 exhausted
all retries. The first complete FFT512 attempt failed at packet 7; the
successful row above is the repeat and still needed five isolated retries.
These failures are part of the assessment even though the retained JSON files
record the successful complete transfers.

## Continuous-superframe repeatability

The final continuous mode shares quiet guards and acquisition overhead across
eight fixed packet slots. It retains a separate header and CRC for each packet,
refreshes channel training every two packets, and decodes slots while later
audio is still being captured. An invalid slot falls back to an isolated
standalone retry.

| Run | Wall time | Payload rate | Nominal audio | Errors / retries | SHA-256 |
|---:|---:|---:|---:|---:|---|
| 1 | 5.234 s | 4,695.7 bit/s | 3.960 s | 0 / 0 | Match |
| 2 | 5.230 s | 4,698.7 bit/s | 3.960 s | 0 / 0 | Match |
| 3 | 5.203 s | 4,723.1 bit/s | 3.960 s | 0 / 0 | Match |
| Mean | 5.223 s | 4,705.8 bit/s | 3.960 s | 0 / 0 | Match |

The matched packet-framed control took 6.148 seconds at 3,997.6 bit/s with
4.693 seconds of nominal audio. Continuous framing therefore reduced mean wall
time by 15.0%, reduced nominal audio by 15.6%, and increased payload throughput
by 17.7%.

An earlier version refreshed channel training every four packets. The final
per-slot implementation then produced two isolated retries in three trials.
Changing the refresh interval to two packets added only 40 ms to an
eight-packet waveform and completed three consecutive trials without an error.

## Packet-size experiment

The early v2 physical tests established the packet-size tradeoff:

| Chunk | Packets | Wall time | Payload rate | Errors / retries |
|---:|---:|---:|---:|---:|
| 96 B | 32 | 28.154 s | 873 bit/s | 0 / 0 |
| 384 B | 8 | 20.406 s | 1,204 bit/s | 0 / 0 |
| 768 B | 4 | 20.09 s | 1,224 bit/s | 0 / 0 |
| 1,024 B | 3 | 29.09 s | 845 bit/s | 1 / 1 |

The 768-byte result gained only 1.6% over 384 bytes, while a failed 1,024-byte
packet imposed a large retransmission penalty. The coded modes therefore use
384-byte application chunks. Uncoded mode uses 192-byte chunks because its
384-byte packet error probability erased the airtime benefit.

## FFT numerology experiment

| FFT | Spacing | Useful symbol | CP | Standard-band data carriers | Gross QPSK rate |
|---:|---:|---:|---:|---:|---:|
| 256 | 187.5 Hz | 5.333 ms | 1.333 ms | 36 | 10.800 kbit/s |
| 512 | 93.75 Hz | 10.667 ms | 1.333 ms | 76 | 12.667 kbit/s |

FFT512 raises nominal gross rate by 17.3%, but its narrower subcarriers and
longer symbols were less tolerant of the real channel. The successful run was
63.2% slower than its clean FFT256 rate-1/2 control and required five retries.
FFT256 remains the validated numerology.

## Bandwidth experiment

| Band | FFT256 data carriers | Tested coding | Result |
|---|---:|---|---|
| 2.25--9.75 kHz | 36 | v2, v3 rate-1/2, v3 rate-2/3, uncoded | Validated; rate-2/3 continuous is fastest repeatable result |
| 1.5--12 kHz | 52 | v3 rate-1/2 | PASS at 4,288 bit/s |
| 1.5--12 kHz | 52 | v3 rate-2/3 | FAIL in two attempts |
| 1.5--15 kHz | 68 | v3 rate-1/2 | FAIL after all retries |
| 3--9 kHz | Configuration available | None | Not yet physically benchmarked |

Wider programmed bandwidth is beneficial only while per-carrier SNR and the
audio hardware response remain usable. The 15 kHz edge and wide-band
rate-2/3 results show that nominal carrier count is not sufficient evidence of
usable throughput.

## Modeled CPU-only OFDM baseline

The separate headless CPU baseline retains QPSK, a 256-point complex FFT,
64-sample CP, pilots, multipath, AWGN, CFO, and Doppler. It is a software
throughput and CPU-optimization test, not a different acoustic modulation.
All benchmark and real-time checkpoints decoded every expected packet.

The unthrottled benchmark improved from approximately 3,231 bit/s in the first
measured implementation to 24,226 bit/s after cached channel taps, batched
processing, vectorization, and FFT-based synchronization. The real-time mode
improved from approximately 3,056 to 15,299 bit/s while preserving the modeled
48 ksample/s timing. Those numbers must not be compared directly with the
physical audio tests because the modeled path excludes ALSA, speaker,
microphone, and acoustic retransmissions.

## Conclusions and next tests

1. Keep QPSK and FFT256 for the current USB audio path.
2. Use v3 rate-1/2 when robustness or the 1.5--12 kHz wide profile matters.
3. Use v3 rate-2/3 continuous framing for the fastest demonstrated standard
   band configuration; keep its experimental label until it is exercised over
   longer runs and changing channels.
4. Do not use FFT512, uncoded 384-byte packets, the 15 kHz edge, or wide-band
   rate-2/3 as defaults.
5. The next true modulation comparison should test BPSK versus QPSK under a
   controlled SNR sweep. Later candidates are differential QPSK and 16-QAM,
   but 16-QAM should wait until gain stability and soft-decision decoding are
   available.
6. Repeat the selected modes with the intended underwater transducer path.
   The current report validates the bench audio hardware, not underwater
   performance.

## Machine-readable records

- `results/cora-ofdm-v3-profile-fft256-standard-2026-07-24.json`
- `results/cora-ofdm-v3-profile-fft512-standard-2026-07-24.json`
- `results/cora-ofdm-v3-profile-optimized-2026-07-24.json`
- `results/cora-ofdm-v3-fft256-standard-192-2026-07-24.json`
- `results/cora-ofdm-uncoded-fft256-standard-192-2026-07-24.json`
- `results/cora-ofdm-uncoded-fft256-standard-384-2026-07-24.json`
- `results/cora-ofdm-v3-r23-fft256-standard-384-2026-07-24.json`
- `results/cora-v3-r23-packet-control-standard-air.json`
- `results/cora-v3-r23-continuous-refresh2-run1.json`
- `results/cora-v3-r23-continuous-refresh2-run2.json`
- `results/cora-v3-r23-continuous-refresh2-run3.json`
- `results/cora-ofdm-benchmark.json`
- `results/cora-ofdm-benchmark-tap-cache.json`
- `results/cora-ofdm-benchmark-batch10.json`
- `results/cora-ofdm-benchmark-vector.json`
- `results/cora-ofdm-benchmark-fft-sync.json`

Detailed profiling and implementation analysis is in
`docs/cora-ofdm-v3-profile.md`. The original v2 development history and raw
packet-size notes are in `docs/cora-ofdm-acoustic-v2.md`.
