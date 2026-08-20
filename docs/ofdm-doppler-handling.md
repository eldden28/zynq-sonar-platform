# Doppler handling in the OFDM implementation

## Executive summary

This repository contains two OFDM receivers with different Doppler behavior:

| Implementation | Purpose | Doppler handling |
|---|---|---|
| `software/cora-ofdm` | Complex-baseband channel simulation and CPU benchmark | Explicitly injects a configured sample-rate scale and removes it with a polyphase resampler before synchronization. The receiver is given the exact simulated Doppler value; it does not estimate it. |
| `software/cora-ofdm-acoustic` | Real 48 ksample/s speaker/microphone modem used by the text and image demos | Uses packet synchronization, a cyclic prefix, per-carrier channel estimation, five pilots per data symbol, and channel-training refreshes. These track small residual phase and timing changes, but there is no wideband Doppler estimator or resampler. |

The complex-baseband benchmark therefore validates decoding when Doppler is
known. The acoustic modem is tolerant of modest, slowly changing Doppler and
independent audio-clock error, but it does **not** yet provide full Doppler
compensation for a moving underwater link.

## Doppler, carrier offset, and sample-clock error

These effects are related, but they are not interchangeable:

- A carrier-frequency offset (CFO) rotates the complex baseband waveform with
  time. Every subcarrier receives approximately the same frequency offset.
- Wideband Doppler stretches or compresses the waveform in time. A passband
  frequency `f` is shifted by approximately `d * f`, where `d` is the
  fractional Doppler scale.
- Sample-clock error also stretches or compresses the sampled waveform. It is
  mathematically similar to wideband Doppler after sampling, even though its
  physical cause is different.

For small one-way relative velocities,

```text
doppler_ppm = 1e6 * v_radial / c
frequency shift at f = f * doppler_ppm * 1e-6
sample drift per second = sample_rate * doppler_ppm * 1e-6
```

The sign depends on whether the endpoints are approaching or separating and
on the convention used by the channel model. For an active monostatic sonar
echo, the first-order shift is approximately twice the one-way value. The OFDM
communications implementation discussed here is a one-way link.

At the benchmark setting of 900 ppm:

- a 9 kHz carrier moves by about 8.1 Hz;
- a 48 ksample/s stream accumulates about 43.2 samples of timing drift per
  second;
- the equivalent one-way radial speed is about 0.31 m/s in air at 343 m/s, or
  1.35 m/s in water at 1,500 m/s.

This is why correcting only a single carrier offset is insufficient. The
receiver must also manage the accumulated change in sample timing.

## Receive processing at a glance

The two current paths are:

```text
Complex-baseband benchmark
  configured Doppler resampler
    -> training correlation
    -> CFO estimate and derotation
    -> per-carrier channel estimate
    -> per-symbol common pilot phase
    -> QPSK decisions

Real acoustic modem
  training correlation
    -> FFT-window selection inside the cyclic prefix
    -> per-carrier channel estimate
    -> per-symbol affine pilot phase versus carrier index
    -> QPSK or 8-PSK decisions
    -> FEC and CRC
```

## Complex-baseband benchmark

The benchmark implementation is in
[`software/cora-ofdm/cora_ofdm.py`](../software/cora-ofdm/cora_ofdm.py).

### Channel injection

`UnderwaterOfdmFlowgraph` configures GNU Radio's `channel_model` with two
independent impairments:

```python
frequency_offset = cfo_hz / sample_rate
epsilon = 1.0 + doppler_ppm * 1e-6
```

`frequency_offset` injects CFO. `epsilon` changes the channel's sample-time
scale and represents Doppler or sample-rate mismatch. Treating these as
separate controls is useful in a test fixture because each recovery stage can
be exercised independently. In a physical passband link, motion creates both
time scaling and a frequency-dependent carrier shift as parts of the same
effect.

### Doppler compensation

`receive()` calls `compensate_doppler()` before attempting synchronization.
That function configures GNU Radio's polyphase arbitrary resampler with the
matching `epsilon` value and cached anti-alias filter taps. Correcting the
whole received stream first prevents the FFT windows from progressively
walking away from the transmitted symbol boundaries. Batch processing also
performs this correction once on the combined stream before it divides the
result into packet windows.

This stage is parameter-aided rather than blind: the same `doppler_ppm` value
used to configure the simulated channel is passed to the receiver. There is no
search, preamble-based scale estimate, or tracking loop in this path.

### Residual recovery after resampling

After time-scale correction, the receiver performs four additional steps:

1. It locates the known training waveform by FFT-based normalized
   correlation.
2. It compares two repeated training symbols. Their phase advance estimates
   CFO, and the complete time stream is derotated by that estimate.
3. It averages the two training FFTs to estimate one complex channel gain for
   each active subcarrier.
4. It uses six alternating BPSK pilots to estimate and remove one common phase
   error from every payload OFDM symbol.

The reported `cfo_estimate_hz` is the result of step 2. It is not a Doppler
estimate.

### What the benchmark proves

The unit tests in
[`software/cora-ofdm/tests/test_cora_ofdm.py`](../software/cora-ofdm/tests/test_cora_ofdm.py)
exercise a 900 ppm sample-rate scale together with 5 Hz CFO, multipath, and
noise. The high-SNR case is expected to decode without bit errors, and the
18 dB SNR case must remain below 3% BER while estimating CFO within 1 Hz. A
separate test verifies reuse of the Doppler-resampler filter taps.

These tests prove the correction path works for a **known, constant** scale.
They do not demonstrate Doppler acquisition, time-varying acceleration, or a
closed-loop estimate on real audio.

## Real acoustic modem

The deployed audio waveform and receiver are in
[`software/cora-ofdm-acoustic/cora_ofdm_acoustic.py`](../software/cora-ofdm-acoustic/cora_ofdm_acoustic.py).
They use real, Hermitian-symmetric OFDM sampled at 48 ksample/s. FFT lengths of
256 and 512 are supported; FFT-256 with a 64-sample cyclic prefix is the
validated default.

### Initial timing and multipath

`_find_start()` correlates the recording with the first known training
symbol. When multipath produces several strong correlation peaks, it selects
the earliest nearly equivalent peak within one cyclic-prefix interval. This
places the useful FFT window before later reflections while keeping those
reflections inside the cyclic prefix.

The cyclic prefix handles bounded delay spread and small timing error. It does
not remove sustained time scaling: Doppler can continue to move the correct
FFT window from one symbol to the next.

### Channel estimate

`_estimate_channel()` averages three known training-symbol estimates. The
result is one complex coefficient for each active carrier, absorbing the
speaker, propagation path, room or water channel, and microphone response at
the training instant.

In a continuous superframe, the transmitter inserts a new three-symbol
training block every two packets. The decoder refreshes the channel estimate
at the same cadence. This limits how long a stale channel estimate is used,
but it does not alter the nominal sample timeline between refreshes.

### Per-symbol pilot tracking

Every header and payload symbol contains five known BPSK pilots spread across
the occupied band. `_equalize_symbol()` calculates pilot phase after channel
equalization and fits

```text
pilot_phase(k) = slope * k + intercept
```

where `k` is the subcarrier-bin index. It applies the negative of that fitted
line to all active carriers.

- The intercept removes common phase error, including modest residual CFO
  accumulated since the previous symbol.
- The slope removes the phase ramp caused by a residual FFT-window timing
  offset. Because sample-clock mismatch and Doppler make that offset change
  over time, fitting it independently on every symbol provides useful
  first-order tracking.

This is a phase-domain correction at the FFT output. It cannot reconstruct
energy already leaked between subcarriers, correct a changing symbol length,
or prevent the nominal FFT window from eventually leaving the cyclic-prefix
region.

### Present limits

The acoustic receiver currently has no:

- estimate of fractional Doppler or sample-rate error in ppm;
- resampling stage before symbol extraction;
- timing loop that adjusts future FFT-window positions;
- inter-carrier-interference cancellation;
- Doppler, pilot-slope, common-phase-error, or EVM telemetry in `DecodeResult`;
- automated acoustic-waveform Doppler sweep in its test suite.

Accordingly, successful stationary speaker/microphone tests validate clock and
channel tolerance for those particular endpoints. They should not be quoted
as a maximum supported vehicle speed.

## Numerology and tolerance

For sample rate `Fs`, FFT length `N`, cyclic prefix `Ncp`, and fractional
Doppler `d`:

```text
subcarrier spacing       = Fs / N
OFDM symbol duration     = (N + Ncp) / Fs
timing drift per symbol  = d * (N + Ncp) samples
shift in carrier bins    = d * f / (Fs / N)
```

At 48 ksample/s and 900 ppm:

| Numerology | Carrier spacing | Symbol duration | Drift per symbol | 9 kHz shift |
|---|---:|---:|---:|---:|
| FFT-256, CP-64 | 187.5 Hz | 6.667 ms | 0.288 sample | 0.0432 bin |
| FFT-512, CP-128 | 93.75 Hz | 13.333 ms | 0.576 sample | 0.0864 bin |

The smaller carrier shift in Hz may appear harmless, but drift accumulates
across symbols. FFT-512 is more sensitive because its subcarriers are closer
together and each symbol is longer. The cyclic prefix gives timing margin;
the exact usable margin is smaller than the full prefix when multipath already
occupies part of it.

These calculations are engineering indicators, not a guaranteed Doppler
limit. Error rate also depends on SNR, delay spread, occupied bandwidth,
packet duration, acceleration, pilot quality, and how frequently training is
refreshed.

## Recommended full-Doppler receiver

For a moving acoustic link, extend the real acoustic receiver in this order:

1. **Acquire scale from the training block.** Search a bounded ppm grid by
   resampling the known preamble or recording and maximizing normalized
   correlation plus cross-training-symbol phase coherence.
2. **Resample the continuous recording.** Apply the winning wideband scale
   before choosing FFT windows. Preserve resampler delay and input/output
   sample-index mappings in the acquisition result.
3. **Track residual scale.** Convert the per-symbol pilot-phase slopes into a
   timing-error measurement and use a low-bandwidth loop to update the
   resampler or upcoming FFT offsets. Refresh the estimate at every training
   block.
4. **Keep common phase tracking.** Continue the existing pilot intercept
   correction after resampling; it handles residual CFO and phase noise.
5. **Expose telemetry.** Report estimated Doppler ppm, equivalent radial
   velocity for a configured sound speed, residual timing slope, common phase
   error, EVM, resampler ratio, and any timing slips.
6. **Add deterministic tests.** Resample encoded real waveforms over positive
   and negative ppm sweeps, combine that with multipath/CFO/noise, and test
   constant velocity, acceleration, long superframes, FFT-256, and FFT-512.

A practical acceptance matrix should state occupied band, packet duration,
FFT/CP, SNR, channel taps, Doppler ppm, Doppler rate in ppm/s, packet error
rate, and residual EVM. That makes the supported motion envelope reproducible
instead of tying it to one room or transducer setup.

## Guidance for developers

- Use `doppler_ppm` for a dimensionless time-scale error, not as another name
  for `cfo_hz`.
- Preserve the correction order: wideband resampling, synchronization, CFO or
  common-phase correction, channel equalization, then symbol decisions.
- Apply resampling continuously across a superframe. Restarting a resampler at
  each packet loses filter history and makes sample coordinates ambiguous.
- Do not infer Doppler from one pilot intercept. Common phase cannot separate
  motion, oscillator error, and channel phase.
- Treat pilot slope as a residual timing measurement after acquisition, not as
  a complete replacement for wideband resampling.
- Record the Doppler sign convention and the exact GNU Radio resampler
  convention in any API that exposes an estimate.
