# Conventional acoustic modulation modes

## Scope

The conventional acoustic modem is a separate application from the OFDM
modem. Its source, command, dashboard, service, packet format, transmitter,
and receiver all have distinct names:

| Item | Conventional modem | OFDM modem |
|---|---|---|
| Python module | `cora_conventional_acoustic.py` | `cora_ofdm_acoustic.py` |
| Command | `cora-conventional-acoustic` | `cora-ofdm-acoustic` |
| Dashboard service | `cora-conventional-dashboard` | `cora-ofdm-text-dashboard` |
| Default dashboard port | 8084 | 8082 |
| Waveform | Single-carrier PSK or one FSK tone per symbol | Parallel orthogonal subcarriers |
| Receiver | Coherent phase or noncoherent tone energy | FFT, pilots, and per-carrier equalization |

The conventional module must not import the OFDM module. This boundary keeps
comparisons honest and allows either modem to be removed from an image without
breaking the other.

## Implemented modes

The default symbol rate is 250 symbols/s at 48 ksample/s.

| Mode | Bits/symbol | Gross rate | Detection | Primary tradeoff |
|---|---:|---:|---|---|
| BPSK | 1 | 250 bit/s | Coherent carrier phase | Best PSK phase margin; lowest PSK rate |
| QPSK | 2 | 500 bit/s | Coherent I/Q phase | Twice the BPSK rate; smaller phase margin |
| 2-FSK | 1 | 250 bit/s | Noncoherent energy | No carrier-phase lock; two tones |
| 4-FSK | 2 | 500 bit/s | Noncoherent energy | QPSK bit rate with more occupied bandwidth |
| 8-FSK | 3 | 750 bit/s | Noncoherent energy | Higher rate and bandwidth |
| 16-FSK | 4 | 1,000 bit/s | Noncoherent energy | Highest current rate and widest tone bank |

MFSK order is restricted to powers of two so every tone carries an integral
number of bits. Two-tone FSK is exposed as `fsk`; `mfsk` accepts orders 4, 8,
and 16.

## Framing and synchronization

All modes use the same conventional frame envelope:

```text
leading silence
  -> 31-symbol common BPSK synchronization preamble
  -> 16 mode-specific training symbols
  -> protected mode/length/sequence header
  -> payload
  -> payload CRC-32
  -> trailing silence
```

The protected header contains a separate wire identifier for BPSK, QPSK,
2-FSK, and MFSK and records the tone order. A receiver configured for the
wrong waveform will fail header validation rather than accept an ambiguous
payload. The first release intentionally has no forward-error correction;
this makes modulation comparisons visible without mixing them with coding
gain. Header and payload CRC-32 values detect errors.

## PSK receiver

BPSK and QPSK use a single continuous carrier timeline. Each received symbol
is projected onto the configured complex carrier. The 16 known training
symbols provide a linear phase fit across symbol time:

```text
residual_phase(symbol) = phase_intercept + phase_slope * symbol_index
carrier_offset_hz = phase_slope * symbol_rate / (2 * pi)
```

The receiver extrapolates that fit through the header and payload before
making BPSK or QPSK decisions. This corrects a constant carrier-frequency
offset and static channel phase. It does not yet contain a decision-directed
tracking loop for rapidly changing phase or Doppler.

## FSK/MFSK receiver

The tone spacing equals the symbol rate. Tone differences are therefore
orthogonal over one symbol interval. The transmitter maintains continuous
phase while switching tones, reducing discontinuities without requiring the
receiver to know carrier phase.

The receiver correlates each symbol against every configured tone and chooses
the largest energy. Training cycles through all tones, measuring their
relative gain so speaker, channel, and microphone frequency response does not
bias the choice toward one part of the tone bank.

With default 6 kHz center frequency and 250 symbols/s:

| Mode | Tone centers | Approximate occupied band |
|---|---|---|
| 2-FSK | 5.875, 6.125 kHz | 5.75--6.25 kHz |
| 4-FSK | 5.625, 5.875, 6.125, 6.375 kHz | 5.50--6.50 kHz |
| 8-FSK | 5.125 through 6.875 kHz | 5.00--7.00 kHz |
| 16-FSK | 4.125 through 7.875 kHz | 4.00--8.00 kHz |

## Configuration and constraints

The selected symbol rate must divide 48,000 exactly so every symbol contains
an integer number of samples. The complete occupied band must remain between
375 Hz and the 24 kHz audio Nyquist frequency. The dashboard and CLI validate
these rules before transmitting.

Changing symbol rate affects several properties together:

- PSK and FSK gross bit rate scale directly with symbol rate.
- Orthogonal FSK tone spacing also equals symbol rate.
- Higher symbol rates shorten the integration interval and reduce processing
  gain.
- Lower symbol rates increase packet duration and sensitivity to motion over
  a long frame.

## Running it

The dashboard is intentionally separate from the OFDM page:

```text
http://BOARD_ADDRESS:8084/
```

The packaged Cora service exposes both the simulation and speaker/microphone
backends. Its USB audio defaults are ALSA playback and capture PCM
`plughw:0,0`, matching the USB codec's normal card assignment in the Cora
image. Air mode remains passive until **Transmit text** is pressed; selecting
it without a connected codec reports an ALSA error without affecting the
simulation backend. Device overrides live in
`/etc/default/cora-conventional-dashboard`.

The page limits text to 512 UTF-8 bytes. A default-rate BPSK frame near that
limit already occupies about 17 seconds, and the bound keeps correlation
memory appropriate for the Cora. The command-line modem permits payloads up
to 4,096 bytes for controlled offline work.

Representative CLI comparisons are:

```sh
cora-conventional-acoustic --modulation bpsk loopback --text "test"
cora-conventional-acoustic --modulation qpsk loopback --text "test"
cora-conventional-acoustic --modulation fsk loopback --text "test"
cora-conventional-acoustic --modulation mfsk --mfsk-order 8 \
  loopback --text "test"
```

See
[`software/cora-conventional-acoustic/README.md`](../software/cora-conventional-acoustic/README.md)
for WAV, ALSA, and Python API examples.

## Validation status

Automated tests cover constellation mapping, configuration rejection, WAV
round trips, mode mismatch, PSK carrier-offset recovery, dashboard loopback,
and clean plus colored multipath/noise packet decoding for all six selectable
modes. These are software validations, not physical over-air or underwater
performance claims. Each transducer set and motion envelope still needs a
measured packet-error-rate sweep.
