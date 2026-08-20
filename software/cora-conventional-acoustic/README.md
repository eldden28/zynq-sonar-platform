# Cora conventional acoustic modem

This application provides traditional narrowband acoustic modulation as a
standalone modem. It does not call the OFDM transmitter or receiver and does
not use OFDM FFT bins, cyclic prefixes, subcarrier pilots, or one-tap
equalization.

## Waveforms

| Mode | Detector | Bits/symbol | Default gross rate |
|---|---|---:|---:|
| BPSK | Coherent phase | 1 | 250 bit/s |
| QPSK | Coherent I/Q phase | 2 | 500 bit/s |
| 2-FSK | Noncoherent tone energy | 1 | 250 bit/s |
| 4-FSK | Noncoherent tone energy | 2 | 500 bit/s |
| 8-FSK | Noncoherent tone energy | 3 | 750 bit/s |
| 16-FSK | Noncoherent tone energy | 4 | 1,000 bit/s |

The defaults are 48 ksample/s, 250 symbols/s, a 6 kHz center frequency, and
250 Hz orthogonal FSK tone spacing. BPSK and QPSK use a continuous carrier
timeline with symbol phase changes. FSK uses continuous phase when changing
tones and does not require carrier phase recovery.

Every frame contains:

1. a common 31-symbol BPSK synchronization preamble;
2. 16 mode-specific training symbols;
3. a versioned header identifying the waveform and payload length;
4. the payload;
5. separate header and payload CRC-32 values.

For PSK, training estimates carrier phase and a linear carrier-frequency
offset. For FSK/MFSK, training visits every tone and normalizes the detector
for frequency-dependent channel gain. This first version intentionally has no
FEC so comparisons show the modulation behavior directly.

## Offline use

Display a configuration:

```sh
cora-conventional-acoustic --modulation bpsk info
cora-conventional-acoustic --modulation qpsk info
cora-conventional-acoustic --modulation fsk info
cora-conventional-acoustic --modulation mfsk --mfsk-order 8 info
```

Run the deterministic colored-channel tests:

```sh
cora-conventional-acoustic --modulation bpsk loopback --text "BPSK test"
cora-conventional-acoustic --modulation qpsk loopback --text "QPSK test"
cora-conventional-acoustic --modulation fsk loopback --text "2-FSK test"
cora-conventional-acoustic --modulation mfsk --mfsk-order 4 \
  loopback --text "4-FSK test"
```

Create or decode mono 16-bit, 48 ksample/s WAV files:

```sh
cora-conventional-acoustic --modulation mfsk --mfsk-order 8 \
  encode --text "eight tones" --wav /tmp/mfsk.wav
cora-conventional-acoustic --modulation mfsk --mfsk-order 8 \
  decode --wav /tmp/mfsk.wav
```

The configuration must be supplied out of band when decoding. A mismatched
configuration is rejected by the protected header rather than silently
returning a payload.

## Audio hardware

The ALSA defaults match the Cora USB audio setup:

```sh
cora-conventional-acoustic --modulation qpsk selftest \
  --text "speaker and microphone"
```

Override devices before the subcommand when necessary:

```sh
cora-conventional-acoustic \
  --playback-device plughw:1,0 \
  --capture-device plughw:1,0 \
  --modulation fsk selftest --text "alternate codec"
```

## API boundary

The reusable Python API is `cora_conventional_acoustic.py`. Its public entry
points are `make_config()`, `encode_packet()`, and `decode_packet()`. Keep the
module independent of `cora_ofdm_acoustic.py`; shared utilities may be moved
to a neutral package later, but neither modem should depend on the other.
