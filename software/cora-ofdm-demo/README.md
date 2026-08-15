# Cora OFDM progressive picture demo

This demo transfers a 1024×1024 raw RGB image through the CPU-only Cora OFDM
modem and paints validated pixels into a browser canvas as they arrive. It
runs separately from the hardware control dashboard:

```text
http://192.168.10.2:8081/
```

The source payload is exactly 3,145,728 bytes. It is divided into 2,098
application frames. Each default 40-symbol OFDM packet carries a 20-byte
header and up to 1,500 image bytes. The header contains:

- magic and sequence fields;
- the total frame count;
- the valid payload length; and
- CRC-32 of the image data.

A frame is written to the reconstructed file only after its metadata and CRC
pass. Failed frames are retransmitted with a new deterministic channel seed.
At completion, the transfer also requires the source and reconstructed
SHA-256 values to match.

The actual modem path retains QPSK, pilots, two training symbols,
256-point FFTs, a 64-sample cyclic prefix, four-path multipath, AWGN, CFO,
Doppler scaling and correction, synchronization, channel estimation, pilot
phase correction, equalization, CRC, and retry handling.

## Dashboard controls

- **Start fast** runs the unthrottled CPU benchmark mode.
- **Start 48k** enables the 48 ksample/s GNU Radio throttle.
- **Stop** sends an interrupt to the transfer process.
- **Reset** removes the reconstructed runtime image and progress state.
- **Reveal source** displays the packaged reference PNG for comparison.

The dashboard reads only the committed prefix of
`/run/cora-ofdm-demo/received.rgb`. It converts incoming RGB triplets directly
into canvas pixels, so a partial image is visible without requiring a
partially valid PNG or JPEG.

The dashboard intentionally has no login because it is a development demo on
the board's direct Ethernet link. Its state-changing API requires a custom
same-origin header, but it should not be exposed to an untrusted network.

## Command-line transfer

The same transfer can run without the dashboard:

```sh
cora-ofdm-transfer --mode benchmark
```

Runtime files are:

```text
/run/cora-ofdm-demo/status.json
/run/cora-ofdm-demo/received.rgb
/var/log/cora-ofdm-demo.log
```

The 3 MiB transfer is intentionally substantial. Based on the preceding Cora
CPU baseline, fast mode is expected to take roughly 20 minutes on the
Zynq-7010. The dashboard continuously reports measured payload rate and ETA.

A 64×64 target smoke transfer reconstructed 12,288 bytes across nine OFDM
frames at 21,896 bit/s with zero retries, zero detected bit errors, and an
exact SHA-256 match. The machine-readable record is
`results/cora-ofdm-demo-smoke.json`.

## Reproducible packaging

The PetaLinux image installs `cora-ofdm-demo`, which depends on `cora-ofdm`.
The base package installs the modem both as `/usr/bin/cora-ofdm` and as the
importable Python module used by the transfer application.

```sh
cd software/petalinux/cora-z7-10-baseline
source /tools/Xilinx/2025.1/PetaLinux/tool/settings.sh
petalinux-build -c cora-ofdm-demo
petalinux-build
./package-wic.sh
```

The generated checkpoint WIC is:

```text
images/linux/petalinux-sdimage.wic
size: 2,684,358,656 bytes
SHA-256: 5333366fdaca8f24e7bd04955e81c24d071d9002e816463c2f01cfa0b2be5d13
```

## Demo asset

The project asset was created with the built-in image-generation workflow,
then converted to a 1024×1024 RGB stream without changing its composition.
The final prompt requested an original, playful 1980s pop-video homage with a
red-haired singer, beige trench coat, vintage microphone, blue geometric
windows, no text, no logos, and no copied video frame.

```text
assets/rickroll.png
  SHA-256 996910dae50a876c8436d8b40f9c0e30dd7c419cab20543a11ec0bad3b2e48da

assets/rickroll.rgb
  size     3,145,728 bytes
  SHA-256 794c16b48e0b60099132b4f9b1a3c57daad54d8fe63d1be7296a4349d602b663
```
