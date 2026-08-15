# Cora acoustic OFDM v2

## Goal

Version 2 carries validated OFDM packets through ordinary air using a speaker,
room, and microphone. It is a separate application from the CPU channel
simulation, so the existing OFDM demo, PWM path, and spectral accelerator are
unchanged.

The first milestone is deliberately audible and conservative. A
near-ultrasonic waveform would be quieter to people, but inexpensive speakers,
lapel microphones, USB audio codecs, and their anti-alias filters commonly
lose useful and repeatable response near 20 kHz. Starting lower lets us
measure the actual channel before moving the band upward.

## Initial waveform

| Parameter | Value |
|---|---:|
| Sample rate | 48,000 sample/s |
| FFT | 256 points |
| Cyclic prefix | 64 samples / 1.333 ms |
| Occupied band | 2,250--9,750 Hz |
| Active positive-frequency carriers | 41 |
| Pilot carriers | 5 |
| Data carriers | 36 |
| Modulation | QPSK |
| Raw physical payload | 10.8 kbit/s |
| Repetition-coded payload | 3.6 kbit/s before framing |
| Default waveform peak | 0.18 full scale |

The transmitter uses Hermitian frequency-domain symmetry so its IFFT output is
real audio. One unique synchronization symbol and two more known training
symbols precede every packet. The receiver uses them for packet timing and a
one-tap complex estimate of every occupied carrier. This estimate absorbs the
frequency response and phase of the speaker, room, and microphone.

Five known pilot carriers in every data symbol fit a linear phase correction.
The intercept tracks common phase rotation; the slope tracks residual sample
timing drift between independent audio clocks. The eight-byte header is sent
three times and majority-voted. Payload bits are also sent in three interleaved
copies for the first robust profile. CRC-32 is the final packet-validity test.

## Commands

The offline colored-room test does not require audio hardware:

```sh
cora-ofdm-acoustic info
cora-ofdm-acoustic loopback --text "hello through a colored room"
```

Create a WAV packet:

```sh
cora-ofdm-acoustic encode \
  --text "hello through the air" \
  --wav /tmp/cora-tx.wav
```

Receive first on one board:

```sh
cora-ofdm-acoustic receive --seconds 5
```

Then transmit from another:

```sh
cora-ofdm-acoustic send --text "hello through the air"
```

One board can record its lapel microphone while playing a packet:

```sh
cora-ofdm-acoustic selftest --text "Cora acoustic OFDM v2"
```

The default playback and capture PCM is `plughw:0,0`. Device overrides go
before the subcommand:

```sh
cora-ofdm-acoustic \
  --playback-device plughw:1,0 \
  --capture-device plughw:1,0 \
  selftest
```

For the first over-air attempt, put the speaker 20--50 cm from the microphone,
use moderate speaker volume, disable microphone AGC, and set a fixed capture
gain. The target is a received OFDM peak around -12 to -6 dBFS with no
clipping. Increase range only after short packets validate consistently.

## Reproducible PetaLinux build

```sh
cd /home/eldden28/code/test-project/software/petalinux/cora-z7-10-baseline
source /tools/Xilinx/2025.1/PetaLinux/tool/settings.sh
petalinux-build -c cora-ofdm-acoustic
petalinux-build
./package-wic.sh
```

The recipe is:

```text
project-spec/meta-user/recipes-radio/cora-ofdm-acoustic/cora-ofdm-acoustic.bb
```

The image configuration installs `cora-ofdm-acoustic` alongside all existing
Cora packages. The kernel fragment also enables
`CONFIG_USB_EHCI_TT_NEWSCHED=y`; this is required by the tested full-speed USB
audio codec to allocate its periodic capture endpoint reliably.

## 2026-07-24 checkpoint

Host validation passed:

- clean NumPy encode/decode with arbitrary recording offsets;
- 16-bit stereo WAV encode and mono decode;
- deterministic speaker/microphone spectral color;
- five-path multipath inside the cyclic prefix;
- additive noise and three-copy payload recovery;
- header and payload CRC validation.

The Yocto recipe passed package QA, the full 7,157-task PetaLinux build
completed without errors, and the WIC was generated with 512 MiB boot and
2 GiB root partitions.

On the Cora Z7-10, the live colored-channel test decoded a 26-byte packet with
a valid CRC in 2.75 seconds. After booting the rebuilt kernel, the PCM2902-class
USB dongle recorded five uninterrupted seconds of mono 48 ksample/s S16_LE
audio. The old `error -28: not enough bandwidth` did not recur:

```text
frames=240000
duration=5.000 s
peak=-24.6 dBFS
overall RMS=-45.3 dBFS
2.25--9.75 kHz RMS=-76.4 dBFS
clipped samples=0
```

Only a lapel microphone was present at this checkpoint. No acoustic playback
or over-air decoding was attempted. The next checkpoint is a short-packet
speaker-to-microphone test, followed by measured per-carrier channel response
and packet success rate.

## Progressive text-terminal dashboard

The separate text dashboard is available at:

```text
http://192.168.10.2:8082/
```

It leaves the picture dashboard on port 8081 unchanged. The left terminal
accepts up to 16 KiB of pasted UTF-8 text. The transfer engine splits it into
96-byte chunks with an outer transfer ID, 16-bit sequence and packet count,
length, and CRC-32. Each outer frame is carried inside an acoustic OFDM packet,
which has its own protected header, repetition recovery, and CRC. The receive
terminal commits a chunk only after both layers validate it.

The browser displays progressive text plus packets, bytes, payload rate, ETA,
retries, packet errors, and synchronization metric. The safe default is the
colored-room simulation; real speaker-to-microphone mode is packaged but
locked until `--allow-air` is added to:

```text
/etc/default/cora-ofdm-text-dashboard
```

The live Cora test transferred a 255-byte, three-packet Unicode message in
2.14 seconds. The browser/API observed commits at 96, 192, and 255 bytes. It
completed with zero retries, zero packet errors, approximately 953 bit/s
validated payload throughput, and matching source/output SHA-256.

FFT-based synchronization replaced the original direct time-domain
correlation. This preserved all synchronization regression results and reduced
the live Cortex-A9 colored-room decode time from roughly 2.75 seconds for a
small packet to approximately 0.8 seconds for a 96-byte text packet.

The service is:

```sh
/etc/init.d/cora-ofdm-text-dashboard status
/etc/init.d/cora-ofdm-text-dashboard restart
```

### Continuous ALSA burst checkpoint

The first air-mode implementation launched `arecord` and `aplay`, wrote and
read temporary WAV files, and waited for an integer-second recording for every
96-byte packet. A 12,809-byte transfer completed correctly, but 134 accepted
packets plus four retries took 260.52 seconds: 393 bit/s. Each attempt averaged
1.89 seconds even though its waveform lasted 0.52 seconds.

The streaming backend now keeps one raw-PCM playback and capture pair open for
each configured burst. Packets play back-to-back with 40 ms leading and trailing
guards, capture is drained continuously, and two decoder workers process
packet windows while later audio is still playing. CRC validation and isolated
packet retries are unchanged.

The first live streaming burst produced:

```text
payload bytes:      1536
packets:            16 / 16
elapsed:            13.956 s
payload throughput: 880.48 bit/s
retries:            0
packet errors:      0
```

This is approximately 2.2 times the validated payload rate of the original
air backend. A second live test crossed the burst boundary: 3,072 bytes and
32/32 packets completed in 28.154 seconds at 872.91 bit/s with zero retries
and zero packet errors. The remaining limit is primarily the triple body
repetition code and Cortex-A9 FFT/equalizer work rather than ALSA process gaps.

Packet-size sweeps then used the same 3,072-byte text payload. The 384-byte
configuration completed eight packets in 20.406 seconds at 1,204.33 bit/s
without errors. A 768-byte configuration completed four packets in 20.086
seconds at 1,223.54 bit/s without errors, only 1.6% faster. At the configured
maximum of 1,024 bytes, one of three packets required a retry and throughput
fell to 844.96 bit/s. The dashboard now defaults to 384-byte packets in
eight-packet streaming bursts, which is the measured efficiency/retry knee.

### Validated 2--15 kHz fast default

A later live dashboard run combined the punctured v3 rate-2/3 code with a
requested 2--15 kHz carrier band. FFT-256 bin alignment realizes the lower
edge at 2,062.5 Hz and the upper edge exactly at 15,000 Hz. Five adaptive
pilots leave 65 QPSK data carriers and a 19.5 kbit/s gross body-symbol rate.

```text
modem:              v3-r2/3
FFT / CP:           256 / 64
requested band:     2--15 kHz
realized band:      2.0625--15.000 kHz
chunk / packets:    384 bytes / 7
payload bytes:      2415
elapsed:            3.195 s
payload throughput: 6046.94 bit/s
retries / errors:   0 / 0
SHA-256:            matched
```

This complete profile is now the text-dashboard default. The earlier standard
band, rate-1/2 FEC, uncoded mode, FFT-512 mode, and custom frequency controls
remain selectable for comparison and diagnostics.

A longer follow-up transferred 16,184 bytes as 43 packets in 19.786 seconds:

```text
payload throughput: 6543.77 bit/s
retries / errors:   0 / 0
SHA-256:            matched
```

That run exposed an audible pause after each eight-packet group. The pause was
not required by the waveform: the dashboard was closing and reopening ALSA
after each configured group, adding a 150 ms pre-roll and 250 ms tail at five
internal boundaries. The default continuous-session capacity is now 64
packets, so the entire maximum coded dashboard payload fits under one
playback/capture session while channel training still refreshes every two
packets.

## V3 FEC and programmable carrier band

The text dashboard now offers a wire-version selector:

- v2 retains the rate-1/3 three-copy body code and majority vote;
- v3 uses a terminated K=7, rate-1/2 convolutional code with 171/133 octal
  generators, depth-eight block interleaving, and a 64-state hard-decision
  Viterbi decoder.

The protected header remains triplicated in both versions. V3 uses a distinct
wire-version byte, and both versions retain the inner payload CRC-32 and outer
text-frame CRC-32.

Carrier low/high edges are request parameters rather than compile-time
constants. The server snaps them to the 187.5 Hz FFT-bin grid, requires at
least a 3 kHz span, keeps the band below Nyquist, and places five pilot bins
with a four-bin edge margin. The page exposes standard 2.25--9.75 kHz, wide
1.5--12 kHz, experimental 1.5--15 kHz, voice-band 3--9 kHz, and custom
profiles.

For the 384-byte text chunk plus its outer frame:

| Profile | Data carriers | Body symbols | Waveform | Useful waveform ceiling |
|---|---:|---:|---:|---:|
| v2, 2.25--9.75 kHz | 36 | 136 | 1.040 s | 2.95 kbit/s |
| v3, 2.25--9.75 kHz | 36 | 91 | 0.740 s | 4.15 kbit/s |
| v3, 1.5--12 kHz | 52 | 63 | 0.553 s | 5.55 kbit/s |
| v3, 1.5--15 kHz | 68 | 48 | 0.453 s | 6.78 kbit/s |

These are waveform-occupancy ceilings, not validated wall-clock throughput.
Wider profiles divide the fixed output peak among more carriers and may expose
speaker/microphone roll-off. Host regressions validate both wire versions,
isolated-error correction, custom pilot placement, and byte-exact dashboard
reconstruction. Live bandwidth results must be recorded separately for each
physical audio path.

The Cortex-A9 initially spent 1.82 seconds decoding one 3,232-bit
convolutional input in the NumPy Viterbi loop. The packaged
`libcora_ofdm_fec.so.1` implementation reduced that measurement to 0.032
seconds (about 57 times faster), while the NumPy implementation remains as a
portable fallback.

Live speaker/microphone-loop measurements used 3,072 bytes in eight
384-byte packets:

| Version/profile | Wall time | Validated payload rate | Errors/retries |
|---|---:|---:|---:|
| v2, 2.25--9.75 kHz | 20.406 s | 1,204 bit/s | 0 / 0 |
| v3, 2.25--9.75 kHz, pre-optimization | 15.217 s | 1,615 bit/s | 0 / 0 |
| v3, 1.5--12 kHz, optimized | 5.731 s | 4,288 bit/s | 0 / 0 |

The experimental v3 1.5--15 kHz profile initially failed the first packet
after all three attempts when the same waveform drove two speakers. The
microphone received two spatially delayed versions of the signal. Routing the
waveform to the left output only, with digital silence on the right output,
removed that avoidable second path. A 6,474-byte follow-up then validated all
17 packets:

```text
output routing:      left speaker only
realized band:       1.500--15.000 kHz
payload bytes:       6474
packets:             17
elapsed:             6.681 s
payload throughput:  7752.40 bit/s
sync metric:         0.822
retries / errors:    0 / 0
SHA-256:             matched
```

This is the highest validated physical acoustic payload rate recorded so far.
The v3 standard profile improved payload throughput by 34%. After cached
carrier geometry and batched grid/IFFT generation, the validated wide profile
improved by 256% relative to the v2 standard baseline.

## Experimental 8-PSK

The dashboard now permits Gray-coded 8-PSK with either v3 convolutional body.
It retains unit magnitude on every data subcarrier, unlike QAM, so the OFDM
carrier loading and peak normalization are unchanged. Training, pilots, and
the triplicated control header remain on robust BPSK/QPSK; only the protected
payload body uses 8-PSK. In the 1.5--15 kHz FFT-256 geometry:

```text
data carriers:             68
bits per carrier:          3
OFDM symbols per second:   150
gross coded-body rate:     30.6 kbit/s
post-FEC rate, R1/2:       15.3 kbit/s before framing
post-FEC rate, R2/3:       20.4 kbit/s before framing
```

The constellation decision margin is 22.5 degrees rather than QPSK's 45
degrees. Clean packet and superframe round trips pass. A deterministic
6,474-byte colored-room rate-2/3 test passed without retries at -65 dBFS; at
-60 dBFS it completed after three isolated packet retries.

Physical mono-left tests confirmed that rate-2/3 is too aggressive. A
four-packet run completed with one retry, but an eight-packet run failed on
packet three after five detected errors. Switching only the 8-PSK body to the
full rate-1/2 mother code produced two clean results:

| Payload | Packets | Wall time | Payload rate | Errors/retries |
|---:|---:|---:|---:|---:|
| 3,072 bytes | 8 | 3.393 s | 7,242.09 bit/s | 0 / 0 |
| 6,450 bytes | 17 | 6.346 s | 8,130.71 bit/s | 0 / 0 |

Both source/output hashes matched, and the long run's final sync metric was
0.823. This is the highest validated physical payload rate so far, 4.9% above
the 7,752.40 bit/s QPSK rate-2/3 result. QPSK rate-2/3 remains the page
default; selecting 8-PSK automatically selects v3 rate-1/2.

The reproducible recipe is:

```text
project-spec/meta-user/recipes-radio/cora-ofdm-text-demo/cora-ofdm-text-demo.bb
```
