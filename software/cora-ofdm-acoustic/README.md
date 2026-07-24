# Cora acoustic OFDM v2

This is a half-duplex, speaker-to-microphone version of the Cora OFDM demo.
It produces real 48 ksample/s audio and keeps the original 256-point FFT,
64-sample cyclic prefix, and QPSK payload.

The initial robust profile occupies 2.25--9.75 kHz. Five pilot carriers track
common phase and timing drift on every symbol, while three known training
symbols estimate the frequency response of the speaker, room, and microphone.
Packets have a repeated header and CRC-32 payload validation.
The first robust profile also sends three interleaved copies of the payload
bits and majority-votes them at the receiver. This lowers the nominal payload
rate from 10.8 kbit/s to 3.6 kbit/s before packet overhead, but gives the first
over-air tests useful error correction without a large decoder dependency.

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
