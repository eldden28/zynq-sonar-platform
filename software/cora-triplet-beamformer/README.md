# Cora triplet cardioid beamformer

This component brings the empirical narrowband beamforming core from the
Triplet Cardioid Beamformer application into the Cora platform repository.
It is intentionally independent of the desktop GUI and recording readers.

The module provides two layers:

- `extract_cw_transfer()` estimates complex receiver/TX transfer values from
  one multichannel CW capture.
- `solve_empirical()` consumes measurements from an angular sweep and returns
  constrained complex weights for a cardioid fit and maximum rear rejection.

It also includes a live synthetic BTR application for seven line-arrayed
triplets. The BTR profile is fixed at 48 ksample/s, uses 1,024-sample frames,
and accepts carrier frequencies from 250 Hz through 20 kHz. Each physical
triplet is an equilateral 30 mm cross-section: Ch1 is upper-starboard, Ch2 is
down, and Ch3 is upper-port. The aligned triplet centers are spaced 3 inches
(76.2 mm) along the array axis. Each triplet forms simultaneous starboard and
port cardioid modes so the BTR can scan -180 through +180 degrees.
The modes use a six-degree smooth power overlap centered on each endfire axis,
which avoids a hard display discontinuity at -90 and +90 degrees.

At a 1,500 m/s sound speed, the 30 mm transverse baselines are unaliased
through 25 kHz. The 76.2 mm axial spacing is unaliased only through about
9.84 kHz, so higher-frequency operation can produce axial grating lobes even
though it remains below the 48 ksample/s Nyquist limit.

The Cora implementation preserves the desktop application's complex angular
interpolation, nonuniform angular quadrature, regularized covariance solve,
unity forward constraint, and beam-pattern metrics. SciPy operations were
replaced with NumPy equivalents because the deployed Cora image includes
NumPy 1.26 but not SciPy.

## Host test

```sh
PYTHONPATH=software/cora-triplet-beamformer \
  python3 -m unittest discover \
  -s software/cora-triplet-beamformer/tests -v
```

Run an ideal three-element smoke test:

```sh
python3 software/cora-triplet-beamformer/cora_triplet_beamformer.py \
  demo --steering-deg 30
```

Start the 21-channel BTR dashboard on the host:

```sh
PYTHONPATH=software/cora-triplet-beamformer \
  python3 software/cora-triplet-beamformer/cora_triplet_btr_service.py \
  --bind 0.0.0.0 --port 8088 \
  --web-root software/cora-triplet-beamformer/www
```

Open `http://localhost:8088`. The dashboard shows a rolling 360-degree
bearing-time record, two movable synthetic sources, carrier and noise controls,
processing time, real-time margin, peak bearing, and the equivalent 21-channel
16-bit ADC payload rate. Its conventional BTR orientation places bearing on the
horizontal axis and time history vertically, with the newest data at the top.
Operator display controls can narrow the visible level window and emphasize
instantaneous spatial-spectrum ridges. Ridge enhancement is display-only: it
does not increase physical array resolution or perform contact tracking. Color
dynamic range controls the dB span mapped across the palette, while the
minimum-relative-level threshold completely culls cells below its dB value.
Below the BTR, the operator display includes a short-time 1,024-point spectrum
and rolling spectral waterfall from Ch11 (the center triplet's down element),
plus dBFS RMS power meters for all 21 ADC elements.
The synthetic-scene card also includes a top-view bearing diagram showing the
seven-station array and both sources; source radius is schematic because the
simulator currently models bearing and level, not range.

The 21 physical coordinates span X, Y, and Z. An edge processor can therefore
scan signed elevation without an artificial top/bottom ambiguity, provided it
retains the individual complex channel values and the calibrated channel phase
and orientation map.

## AXI/DMA ADC input

For the complete XSA import, device-tree, driver, image-build, and board
bring-up sequence, see
[`docs/zynq-7020-triplet-beamformer-deployment.md`](../../docs/zynq-7020-triplet-beamformer-deployment.md).

The BTR service can use either its synthetic generator or a platform-provided
AXI DMA character device. The hardware-independent reader expects the stream
shown in the AD4630 Vivado design:

- 21 channels in sample-major order;
- 512 time samples per AXI packet;
- one signed 24-bit sample sign-extended into each little-endian 32-bit word;
- 10,752 words / 43,008 bytes per packet; and
- two packets combined into each 1,024-sample beamformer block.

Start the service against a DMA device such as `/dev/cora-triplet-adc0`:

```sh
PYTHONPATH=software/cora-triplet-beamformer \
  python3 software/cora-triplet-beamformer/cora_triplet_btr_service.py \
  --source axi --axi-device /dev/cora-triplet-adc0 \
  --bind 0.0.0.0 --port 8088 \
  --web-root software/cora-triplet-beamformer/www
```

The reader accepts partial device reads but validates packet length and 24-bit
sign extension. Selecting AXI mode never falls back to synthetic samples: a
missing device, timeout, truncated packet, or incorrectly padded value is
reported through the status API and dashboard. The dashboard disables the
synthetic-scene controls in AXI mode and labels its geometry marker as the
strongest instantaneous beam rather than a known source position.

The default device name is only the userspace ABI target. The Zynq-7020
PetaLinux image still needs a board-specific DMAengine character driver whose
`read()` returns the fixed 43,008-byte S2MM packets. A regular raw capture file
can be used before that driver is available; add `--axi-loop-file` to replay it
continuously.

## Solve a measured sweep

The command accepts JSON arrays because JSON does not have a complex-number
type:

```json
{
  "bearings_deg": [0, 30, 60, 90],
  "transfer_real": [[1, 1, 1], [1, 0.9, 0.8], [1, 0.7, 0.5], [1, 0.5, 0.2]],
  "transfer_imag": [[0, 0, 0], [0, 0.1, 0.2], [0, 0.2, 0.3], [0, 0.3, 0.4]],
  "optimizer": {
    "steering_deg": 0,
    "rear_width_deg": 90,
    "regularization": 0.001,
    "rear_fit_weight": 2
  }
}
```

A production sweep should cover 360 degrees; the short example only documents
the schema. Solve it with:

```sh
cora-triplet-beamformer solve sweep.json --output weights.json
```

The result contains complex weights, normalized response amplitude and dB,
front-to-back ratio, rear rejection, white-noise gain, directivity estimate,
cardioid fit error, and data-quality warnings.

## PetaLinux recipe

Build the package with:

```sh
source scripts/activate-tools.sh
petalinux-build \
  --project software/petalinux/cora-z7-10-baseline \
  -c cora-triplet-beamformer
```

The recipe installs the CLI as `/usr/bin/cora-triplet-beamformer` and the
importable module as `cora_triplet_beamformer`. It also installs the BTR service
as `/usr/sbin/cora-triplet-btr`, its dashboard on port 8088, and a SysV init
script. Add
`cora-triplet-beamformer` to `IMAGE_INSTALL:append` when the package should be
included in a full SD-card image.
