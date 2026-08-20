# Cora acoustic OFDM modem

This is a half-duplex, speaker-to-microphone version of the Cora OFDM demo.
It produces real 48 ksample/s audio with a 64-sample cyclic prefix and QPSK
payload. The validated default remains the original 256-point FFT. A selectable
512-point experimental mode is available for numerology and channel tests.

The initial robust profile occupies 2.25--9.75 kHz. Five pilot carriers track
common phase and timing drift on every symbol, while three known training
symbols estimate the frequency response of the speaker, room, and microphone.
Packets have a repeated header and CRC-32 payload validation.
These mechanisms tolerate modest residual drift but do not constitute a
wideband Doppler estimator or resampler. See the project-level
[`OFDM Doppler handling`](../../docs/ofdm-doppler-handling.md) document for
the processing order, limits, equations, and recommended extension.
The first robust profile also sends three interleaved copies of the payload
bits and majority-votes them at the receiver. This lowers the nominal payload
rate from 10.8 kbit/s to 3.6 kbit/s before packet overhead, but gives the first
over-air tests useful error correction without a large decoder dependency.

The text dashboard exposes four wire-distinct choices:

- **v2** preserves that original three-copy body code and majority vote.
- **v3** uses an interleaved, terminated K=7, rate-1/2 convolutional code with
  171/133 octal generators and a 64-state hard-decision Viterbi decoder.
- **v3 rate-2/3 experimental** punctures that mother code with a repeating
  `1110` pattern. The Viterbi decoder treats omitted parity bits as erasures.
- **uncoded experimental** sends the payload body as raw QPSK bits with CRC-32
  detection but no body error correction. The triplicated header remains.

All retain the triplicated protected header and payload CRC-32. Distinct
wire-version bytes prevent a receiver configured for one body code from
silently accepting another.
On PetaLinux, the Viterbi hot loop runs in the packaged
`libcora_ofdm_fec.so.1` helper; the same Python module automatically uses its
portable NumPy fallback on systems where that library is unavailable.
The packaged C decoder supports both complete rate-1/2 symbols and the
erasure mask needed by the punctured rate-2/3 mode.

Carrier edges are programmable on the selected FFT grid: 187.5 Hz for FFT256
or 93.75 Hz for FFT512. Five pilots are spread across the selected band with
the same 750 Hz physical edge margin. The dashboard offers standard
2.25--9.75 kHz, validated wide 1.5--12 kHz, experimental 1.5--15 kHz,
voice-band 3--9 kHz, and custom profiles.

FFT512 is functional but not recommended for this 48 ksample/s acoustic path.
In the standard band it raises the nominal gross rate from 10.8 to 12.67
kbit/s, but doubles the useful symbol duration and halves subcarrier spacing.
A 3,072-byte air test completed at 1,225 bit/s only after five retries, while
the otherwise identical FFT256 test reached 3,329 bit/s with no retries.

Uncoded FFT256 can improve throughput on a clean, stationary path, but packet
size matters. At 192-byte chunks, a 3,072-byte air transfer reached 4,111
bit/s with no retries versus 2,637 bit/s for the matched v3 coded control.
At 384-byte chunks, one uncoded run failed and a repeat needed three retries,
finishing at 2,893 bit/s versus 3,329 bit/s for v3. Use uncoded mode as a
channel experiment, not as the robust default.

Punctured rate-2/3 FFT256 provides a useful middle ground. In the standard
band, a 3,072-byte, eight-packet air transfer completed at 3,993 bit/s with no
retries, 19.9% faster than rate-1/2. Two attempts in the 1.5--12 kHz wide band
exhausted retries, so rate-2/3 is currently validated only for the standard
2.25--9.75 kHz profile.

The text-transfer layer can continuously frame up to eight packets. It emits
one uninterrupted waveform, refreshes the three training symbols every two
packets, and decodes each fixed slot while later audio is still being
captured. Headers and CRCs remain per-packet, and only an invalid packet is
retried. Three consecutive 3,072-byte standard-band rate-2/3 air tests
completed without errors at a mean 4,706 bit/s, 17.7% above the matched
packet-framed control.

Start with the offline smoke test:

```sh
cora-ofdm-acoustic info
cora-ofdm-acoustic loopback --text "hello through the colored room"
```

Generate or decode WAV files:

```sh
cora-ofdm-acoustic encode --text "hello" --wav /tmp/cora-tx.wav
cora-ofdm-acoustic decode --wav /tmp/cora-rx.wav
```

For two boards, start the receiver first and then send:

```sh
cora-ofdm-acoustic receive --seconds 5
cora-ofdm-acoustic send --text "hello across the air"
```

For one board with a speaker and microphone connected to the USB audio dongle:

```sh
alsamixer
cora-ofdm-acoustic selftest --text "Cora acoustic OFDM v2"
```

The defaults use `plughw:0,0` for playback and capture. Override them before the
subcommand when ALSA assigns another card:

```sh
cora-ofdm-acoustic --playback-device plughw:1,0 \
  --capture-device plughw:1,0 selftest
```

Use moderate speaker volume and place the microphone about 20--50 cm away for
the first test. The waveform peak is limited to 18 percent of full scale so
there is headroom for mixer gain and imperfect hardware.
