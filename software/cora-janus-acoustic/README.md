# Cora JANUS acoustic modem

This component adds an interoperable JANUS physical-layer mode to the shared
Zynq sonar platform. It wraps the CMRE JANUS reference command-line
implementation rather than approximating the waveform in Python.

JANUS is standardized as STANAG 4748 / ANEP-87. It is intended for robust
discovery, presence announcements, and initial contact between otherwise
dissimilar underwater systems. It is separate from both the project OFDM modem
and the conventional BPSK/QPSK/FSK modem.

## Scope

- Generate and decode the 64-bit JANUS baseline packet.
- Select reference parameter sets 1 through 4, all compatible with the current
  48 ksample/s audio path.
- Configure class ID, application type, the 34-bit application-data field,
  mobility, TX/RX, forwarding, and wake-up-tone flags.
- Exercise deterministic multipath/noise loopback from the CLI or dashboard.
- Play and capture packets through the configured USB ALSA device.
- Decode with reference-implementation Doppler correction enabled.

Generic arbitrary text is deliberately not presented as standards-compatible
cargo. JANUS cargo interpretation belongs to a registered application class and
its codec plug-in. Use the existing project OFDM or conventional modem when both
ends are project-controlled and a general data channel is required.

## Commands

Inspect the installation and selected packet:

```sh
cora-janus-acoustic --parameter-set 1 --application-type 3 \
  --application-data 0x12345 info
```

Run the reference transmitter and receiver through the simulated channel:

```sh
cora-janus-acoustic --parameter-set 1 --class-id 16 \
  --application-type 3 --application-data 0x12345 loopback
```

Create or decode a 48 ksample/s, mono, signed-16-bit WAV:

```sh
cora-janus-acoustic --application-type 3 --application-data 0x12345 \
  encode --wav /tmp/janus.wav
cora-janus-acoustic --application-type 3 decode --wav /tmp/janus.wav
```

Exercise the speaker-to-microphone path:

```sh
cora-janus-acoustic --playback-device plughw:0,0 \
  --capture-device plughw:0,0 --application-type 3 \
  --application-data 0x12345 selftest
```

The dashboard listens on port `8086` by default.

## Licensing and reproducibility

The project wrapper and dashboard are MIT licensed. `janus-c` is built as a
separate GPLv3 package from a pinned upstream commit. The recipe applies one
documented CMake module-discovery portability fix; it does not change waveform
or packet processing. The package retains the upstream license and installs its
source-derived binaries, plug-ins, parameter sets, headers, static library, and
license file without copying JANUS code into the MIT wrapper.
