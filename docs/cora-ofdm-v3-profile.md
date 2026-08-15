# Cora acoustic OFDM v3 runtime profile

Profile date: 2026-07-24

The Cora Z7-10 ran one byte-exact speaker/microphone transfer using the v3
Wide profile:

- 3,072 application bytes;
- eight 384-byte packets;
- 1.5--12 kHz carrier band;
- 52 QPSK data subcarriers;
- K=7, rate-1/2 convolutional FEC;
- packaged ARM Viterbi accelerator;
- eight-packet continuous ALSA burst.

The transfer completed without a retry or packet error. Source and received
SHA-256 values matched.

## Wall-time breakdown

| Phase | Time | Share of 10.979 s wall time |
|---|---:|---:|
| Encode eight packets before playback | 5.743 s | 52.3% |
| Nominal transmitted audio | 4.427 s | 40.3% |
| Remaining launch, guard, capture, and tail time | 0.809 s | 7.4% |

Validated application throughput was 2,238.6 bit/s. `arecord` started at
5.749 s and `aplay` at 5.908 s. The first decoder began at 6.527 s and the last
finished at 10.975 s.

The eight decoder calls consumed 4.333 aggregate seconds, averaging 0.542
seconds per packet. One packet occupies 0.553 seconds of audio, so decoding
kept pace with capture and overlapped almost completely with transmission.
Encoding and decoding together represent approximately 10.076 CPU-seconds,
equivalent to 91.8% of one core or 45.9% of the two Cortex-A9 cores over the
wall interval. This is consistent with the approximately 55% utilization seen
externally after accounting for other modem, ALSA, kernel, and service work.

## Encoder profile

One representative 404-byte modem frame took 0.732 seconds to encode.
Function times below are inclusive, so nested rows must not be added together.

| Function | Calls | Inclusive time | Packet share |
|---|---:|---:|---:|
| `_bits_to_grid` | 66 | 0.416 s | 56.9% |
| `_encode_body` | 1 | 0.064 s | 8.7% |
| `convolutional_encode` | 1 | 0.062 s | 8.5% |
| `qpsk_map` | 66 | 0.031 s | 4.2% |
| `_grid_to_symbol` | 69 | 0.020 s | 2.7% |
| `_training_grids` | 1 | 0.003 s | 0.4% |

The FFT is not the encoder bottleneck. `_bits_to_grid` repeatedly evaluates
the configuration's active/data-bin properties. In particular, `data_bins`
currently recomputes a NumPy `setdiff1d` operation, and `bits_per_symbol`
requests that result again. The packet invokes these properties hundreds of
times through the per-symbol Python loop.

## Decoder profile

One captured packet took 0.513 seconds to decode:

| Function | Calls | Inclusive time | Packet share |
|---|---:|---:|---:|
| `_equalize_symbol` | 66 | 0.177 s | 34.5% |
| `_find_start` | 1 | 0.142 s | 27.6% |
| `_normalized_correlation` | 1 | 0.132 s | 25.8% |
| `_decode_body` | 1 | 0.032 s | 6.2% |
| accelerated `viterbi_decode` | 1 | 0.031 s | 6.1% |
| `qpsk_demap` | 66 | 0.016 s | 3.0% |
| `_fft_symbol` | 69 | 0.014 s | 2.8% |

The C Viterbi decoder is no longer dominant. Equalization performs a general
NumPy polynomial fit for five pilots on every OFDM symbol, while acquisition
performs the expected recording-wide normalized correlation. Recomputed
active/data-bin arrays also contribute outside the explicitly timed functions.

## Cached and batched encoder result

The first optimization cached immutable carrier geometry, generated all
header/body grids as matrices, and applied a batched inverse FFT. A regression
compared the new matrix path against the original per-symbol path, and the
complete streaming waveform remained byte-for-byte identical.

The identical clean air transfer was then repeated:

| Measurement | Baseline | Optimized | Change |
|---|---:|---:|---:|
| Total wall time | 10.979 s | 5.731 s | -47.8% |
| Payload throughput | 2,238.6 bit/s | 4,288.1 bit/s | +91.6% |
| Mean encode time | 0.718 s | 0.083 s | -88.5% |
| Mean decode time | 0.542 s | 0.396 s | -26.9% |
| Packet errors/retries | 0 / 0 | 0 / 0 | unchanged |

The optimized wall-time breakdown is:

| Phase | Time | Share of 5.731 s wall time |
|---|---:|---:|
| Encode eight packets before playback | 0.662 s | 11.5% |
| Nominal transmitted audio | 4.427 s | 77.2% |
| Remaining launch, guard, capture, and tail time | 0.643 s | 11.2% |

Encoding is now 8.7 times faster. Cached carrier geometry also reduced decoder
time even though the receiver algorithm itself was unchanged. Encoding plus
decoding now represents approximately 3.830 CPU-seconds, equivalent to 66.8%
of one core or 33.4% of the dual-core capacity over the wall interval.

One optimized representative frame took 0.087 seconds to encode:

| Function | Inclusive time | Packet share |
|---|---:|---:|
| `convolutional_encode` | 0.061 s | 69.8% |
| `_bit_rows_to_grids` | 0.007 s | 7.7% |
| `_grids_to_symbols` | 0.005 s | 6.2% |
| `_training_grids` | 0.003 s | 3.3% |

One optimized captured packet took 0.392 seconds to decode:

| Function | Inclusive time | Packet share |
|---|---:|---:|
| `_equalize_symbol` | 0.181 s | 46.2% |
| `_find_start` | 0.123 s | 31.5% |
| accelerated `viterbi_decode` | 0.032 s | 8.1% |
| `qpsk_demap` | 0.018 s | 4.6% |
| `_fft_symbol` | 0.015 s | 3.9% |

## 512-point FFT experiment

The modem and dashboard now accept either the validated 256-point FFT or an
experimental 512-point FFT while retaining 48 ksample/s, CP64, QPSK, pilots,
packet format, and FEC. The standard 2.25--9.75 kHz band gives the following
numerologies:

| FFT | Bin spacing | Useful symbol | CP | Data carriers | Gross rate |
|---:|---:|---:|---:|---:|---:|
| 256 | 187.5 Hz | 5.333 ms | 1.333 ms | 36 | 10.800 kbit/s |
| 512 | 93.75 Hz | 10.667 ms | 1.333 ms | 76 | 12.667 kbit/s |

Both modes were tested through the same board, USB audio dongle, speaker, and
microphone with a 3,072-byte payload split into eight 384-byte packets:

| FFT | Wall time | Payload rate | Errors/retries | Result |
|---:|---:|---:|---:|---|
| 256 | 7.381 s | 3,329.4 bit/s | 0 / 0 | PASS |
| 512 | 20.065 s | 1,224.8 bit/s | 5 / 5 | PASS |

The first full FFT512 run reached packet 7 and then exhausted all three
attempts with a header CRC mismatch. A repeat delivered all eight packets, but
required five isolated retries. A separate single-packet test also passed
cleanly, confirming that the implementation is functional rather than
systematically incompatible.

At this fixed sample rate, FFT512's 17.3% gross-rate increase does not offset
the physical-channel penalty from halving subcarrier spacing and doubling
useful symbol duration. Its successful repeat delivered 63.2% less payload
throughput than the clean FFT256 control. FFT256 remains the default; FFT512 is
kept as an experimental dashboard option for channel and numerology work.

## Uncoded FFT256 experiment

An experimental wire mode was added to isolate body FEC cost. It retains the
three-copy protected header and CRC-32 payload validation, but maps body bits
directly to QPSK without convolutional encoding, interleaving, or Viterbi
decoding. A distinct wire identifier prevents accidental coded/uncoded
cross-decoding.

All physical tests used FFT256, CP64, 48 ksample/s, the 2.25--9.75 kHz band,
and the same 3,072 application bytes:

| Body code | Chunk | Packets | Wall time | Payload rate | Errors/retries |
|---|---:|---:|---:|---:|---:|
| v3 K=7 rate-1/2 | 192 bytes | 16 | 9.319 s | 2,637.2 bit/s | 0 / 0 |
| none | 192 bytes | 16 | 5.978 s | 4,111.3 bit/s | 0 / 0 |
| v3 K=7 rate-1/2 | 384 bytes | 8 | 7.381 s | 3,329.4 bit/s | 0 / 0 |
| none | 384 bytes | 8 | 8.496 s | 2,892.6 bit/s | 3 / 3 |

The first uncoded 384-byte run stopped at packet 5 after three consecutive
payload CRC failures. Its successful repeat still needed three isolated
retries. Halving the chunk to 192 bytes eliminated retries and made uncoded
mode 55.9% faster than the matched 192-byte coded control. It was also 23.5%
faster than the coded 384-byte standard-band baseline.

This demonstrates a useful packet-error threshold rather than a universal FEC
penalty. Removing FEC cuts body airtime and ARM work when the uncoded
packet-error rate is effectively zero. Once packets are long enough to collect
occasional raw bit errors, whole-packet retransmission costs more than the
convolutional redundancy. The robust dashboard default therefore remains v3
with 384-byte chunks; uncoded mode is explicitly experimental.

## Punctured rate-2/3 FEC experiment

The existing K=7, 171/133 rate-1/2 code now supports a repeating `1110`
puncture pattern. Every two source bits therefore produce three transmitted
coded bits instead of four. The receiver reconstructs the mother-code layout
with a validity mask, and both the NumPy fallback and packaged C Viterbi
decoder assign zero branch cost to omitted parity positions. The header uses a
distinct wire identifier, so rate-1/2 and rate-2/3 packets cannot be confused.

The target confirmed that the masked C decoder was loaded and recovered a test
vector containing five isolated errors in the transmitted punctured stream.
For a 384-byte application chunk, the acoustic body falls from approximately
91 rate-1/2 OFDM symbols to 68 rate-2/3 symbols; uncoded uses approximately 46.

The matched standard-band physical result was:

| Body code | Chunk | Wall time | Payload rate | Errors/retries |
|---|---:|---:|---:|---:|
| K=7 rate-1/2 | 384 bytes | 7.381 s | 3,329.4 bit/s | 0 / 0 |
| K=7 punctured rate-2/3 | 384 bytes | 6.156 s | 3,992.5 bit/s | 0 / 0 |
| none | 192 bytes | 5.978 s | 4,111.3 bit/s | 0 / 0 |

Rate-2/3 increased matched standard-band throughput by 19.9% and came within
2.9% of the clean uncoded result while retaining convolutional error
correction. However, two rate-2/3 attempts in the 1.5--12 kHz wide band failed:
one exhausted retries at packet 3 and the repeat at packet 1. The stronger
rate-1/2 code remains the validated wide-band choice; rate-2/3 is currently a
standard-band experimental profile.

## Continuous-superframe experiment

Packet framing previously repeated the quiet guards and three-symbol channel
training for every packet. The continuous mode now groups up to eight
fixed-slot packets under one uninterrupted ALSA playback/capture session.
Each packet retains its triplicated header and CRC-32 body, so an invalid slot
can still be retried independently through the existing standalone packet
path.

Channel training is refreshed every two packets. The receiver acquires each
refresh once, then decodes both known slots as soon as their samples arrive.
Capture, playback, acquisition, and slot decoding therefore overlap instead
of waiting for the complete superframe. Fixed-size padded slots allow a later
packet to remain locatable even when an earlier header or payload is corrupt.
Validated results are now committed to the dashboard as each ordered decoder
future completes; the live waterfall remains attached directly to PCM capture.
Invalid slots are retained for retry only after the primary ALSA session has
closed, preventing retry playback from contending with the continuous stream.

The matched control and continuous tests used rate-2/3, FFT256, CP64,
48 ksample/s, 2.25--9.75 kHz, and 3,072 bytes split into eight 384-byte
application chunks:

| Framing | Runs | Mean wall time | Mean payload rate | Audio | Errors/retries |
|---|---:|---:|---:|---:|---:|
| Packet burst | 1 | 6.148 s | 3,997.6 bit/s | 4.693 s | 0 / 0 |
| Continuous, refresh every 2 | 3 | 5.223 s | 4,705.8 bit/s | 3.960 s | 0 / 0 |

The three continuous runs measured 4,695.7, 4,698.7, and 4,723.1 bit/s.
Continuous framing reduced mean wall time by 15.0%, reduced nominal audio by
15.6%, and increased validated throughput by 17.7%. An earlier four-packet
refresh experiment produced two isolated retries in three trials; shortening
the refresh interval added only 40 ms of training per superframe and completed
three consecutive trials without an error.

## C compilation candidates

The convolutional encoder is now the clear C candidate. It consumes about
0.061 seconds per packet, or 0.486 seconds for this burst, and can be added to
the existing FEC library beside the Viterbi decoder while retaining a Python
fallback.

The next receiver work should remain algorithmic and vectorized initially:

- replace the general five-point `polyfit` call on every symbol with a
  precomputed closed-form slope/intercept calculation;
- batch the packet FFT and equalization operations where practical;
- evaluate a coarse-to-fine acquisition search before replacing normalized
  correlation.

NumPy already executes the FFT and normalized-correlation arithmetic in
compiled code. Their measured wrapper time is too small to justify a custom C
FFT, and merely rewriting the same correlation in C is unlikely to provide the
benefit of reducing the search itself. QPSK mapping, demapping, interleaving,
CRC, and training generation are also too small to prioritize. Viterbi is
already accelerated.

The recommended next increment is therefore the C convolutional encoder,
followed by closed-form/vectorized equalization. Both should be reprofiled
independently.

The raw machine-readable results are
`results/cora-ofdm-v3-profile-2026-07-24.json` and
`results/cora-ofdm-v3-profile-optimized-2026-07-24.json`. The FFT comparison
records are
`results/cora-ofdm-v3-profile-fft256-standard-2026-07-24.json` and
`results/cora-ofdm-v3-profile-fft512-standard-2026-07-24.json`. The reusable
target-side harness is `scripts/profile-cora-ofdm-v3.py`. The uncoded
comparison records are
`results/cora-ofdm-uncoded-fft256-standard-192-2026-07-24.json`,
`results/cora-ofdm-v3-fft256-standard-192-2026-07-24.json`, and
`results/cora-ofdm-uncoded-fft256-standard-384-2026-07-24.json`. The punctured
result is
`results/cora-ofdm-v3-r23-fft256-standard-384-2026-07-24.json`. The continuous
framing control is
`results/cora-v3-r23-packet-control-standard-air.json`; the three repeat
measurements are
`results/cora-v3-r23-continuous-refresh2-run1.json` through
`results/cora-v3-r23-continuous-refresh2-run3.json`.
