# Cora configurable-array forward-sonar laboratory

This package turns the Cora Z7-10 into a software laboratory for the
up-to-128-channel, 400 kHz–1 MHz forward-looking sonar derived from the design described in
`docs/32ch_400-500kHz_forward_sonar_simulator_spec.md`.

The range/elevation image is generated from receive time series. Targets are
not painted directly into image bins. Every ping runs this processing chain:

```text
point-scatterer scene
        |
complete TX-target-RX delay for every active element
        |
4–128 simultaneous quantized complex-baseband time series
        |
per-channel FFT matched filtering
        |
coherent range gating to 1,024 processing bins
        |
512-direction phase-steered delay-and-sum beamforming
        |
magnitude, range pooling, log compression, obstacle extraction
```

## Why the live profile starts at complex baseband

The design specification proposes 32 real 4 MSPS ADC streams. A 20 m raw
frame at that boundary is about 6.83 MiB, before working buffers. Repeated
passband synthesis and pulse compression would obscure the beamformer work on
the dual-core Zynq-7010.

The live Cora profile therefore models the output of a future FPGA DDC at
500 ksample/s complex baseband. It preserves:

- the 100 kHz LFM bandwidth and 7.5 mm theoretical pulse-compression limit;
- exact bistatic delay and center-frequency phase at every element;
- 4–128 simultaneous channel samples, adjustable without changing the scene;
- an adjustable 400 kHz–1 MHz center frequency used by propagation phase,
  steering, and array-pattern calculations;
- adjustable 0.50–4.00 mm element pitch, including half-wavelength spacing
  experiments at high frequency;
- channel gain, phase, timing, failed-element, noise, clipping, and
  quantization effects;
- the same per-channel matched filter and software beamformer expected after
  the future DMA boundary.

The default 12 m profile contains 8,254 complex samples per channel and a
2.02 MiB complex64 input frame. Maximum range remains configurable to 20 m.

The beamformer does not independently downsample the channels. It finds the
strongest common time sample in each processing-range interval and retains the
complete complex channel vector at that instant. This preserves inter-element
phase while bounding the default ping to a 512 by 32 by 1,024 matrix product.
The resulting 1,024 processing bins are about 11 mm apart over the
default range and are max-pooled to the dashboard's 512 range pixels.

The default physical pitch is 1.5 mm, but the dashboard can vary it from 0.50
to 4.00 mm. At 1 MHz in 1,500 m/s water, 0.75 mm is half-wavelength spacing.
The dashboard reports pitch/wavelength ratio and flags values above 0.5λ
instead of treating spatial grating lobes as improved angular resolution.

## Measured 512-beam Cora performance

With the navigation, OFDM, and OFDM-text simulations stopped, a six-ping run
on the Cora Z7-10 measured 2.78 seconds per ping, or 0.360 Hz. The steady-state
means were:

- time-series synthesis: 752 ms;
- 32-channel matched filtering: 650 ms;
- coherent range gating: 171 ms;
- 512-direction beamforming: 728 ms.

The earlier 64-beam full-range implementation measured 2.90 seconds per ping
under the same conditions. Static steering and array-pattern data are cached,
range max pooling uses NumPy reductions, and detections are condensed to local
image peaks rather than repeated for every oversampled look direction.

## Host use

Run the tests:

```sh
PYTHONPATH=software/cora-sonar \
  python3 -m unittest discover -s software/cora-sonar/tests -v
```

Run a bounded processing check:

```sh
PYTHONPATH=software/cora-sonar \
  python3 software/cora-sonar/cora_sonar_smoke.py \
  --scenario obstacle_course --pings 5
```

Start the dashboard:

```sh
PYTHONPATH=software/cora-sonar \
  python3 software/cora-sonar/cora_sonar_service.py \
  --bind 0.0.0.0 --port 8085 \
  --web-root software/cora-sonar/www
```

Open `http://localhost:8085`. The dashboard provides:

- Cartesian fan and range/elevation views;
- a Cartesian ground-truth scene view with detection overlays;
- an editor for selecting, dragging, adding, deleting, enabling, and changing
  the range, elevation, reflectivity, and label of as many as 64 targets;
- raw RX16 and matched-filter time traces;
- array pattern and center-beam range plots;
- point-target, resolution-pair, obstacle-course, and sloped-bottom scenes;
- rectangular, Hann, Hamming, Blackman, and Taylor aperture weighting;
- range, noise, and display controls;
- beamwidth, sidelobe, coherent-gain, timing, and target-error metrics;
- an NPZ capture containing the full raw, matched, and beamformed arrays.

Scene edits are staged in the browser so dragging does not start an expensive
ping. Press **Apply scene and ping** to validate the target list and run it
through the complete time-series synthesis, matched-filter, and beamformer
pipeline. Selecting a preset scene discards staged custom edits.

## Cora image

Build only the recipe or rebuild the selected root filesystem:

```sh
source scripts/activate-tools.sh
petalinux-build \
  --project software/petalinux/cora-z7-10-baseline \
  -c cora-sonar
petalinux-build \
  --project software/petalinux/cora-z7-10-baseline
```

After boot:

```sh
/etc/init.d/cora-sonar status
cora-sonar-smoke --scenario point_targets --pings 3
```

Open `http://192.168.10.2:8085` from the development PC.

## Existing FFT accelerator

The current bitstream's `/dev/cora-dsp0` path is a fixed 512-point real-input
FFT, binary symmetric spectral mask, and IFFT chain. On the Cora it measures
62 microseconds per DMA/core frame and 287 microseconds per userspace frame.
That is useful hardware, but its current wiring cannot return FFT bins or
apply the arbitrary complex coefficients required by a chirp matched filter.

The most useful programmable-logic revision is to add:

1. a forward-FFT output/bypass mode for spatial transforms;
2. complex Q1.15 input and output in the driver ABI;
3. a batch transfer mode carrying many TLAST-delimited 512-sample frames;
4. optional BRAM-backed complex coefficients for overlap-save matched filtering.

A batched forward mode could transform each active-element range vector padded to
512 points, returning all spatial-frequency samples in one stream. A complex
coefficient mode could also reduce the current 650 ms ARM matched-filter stage.

## Current boundary and next increments

The current milestone is a trustworthy time-series reference for static point
scatterers and simple composite scenes. It uses center-frequency phase
steering after matched filtering. The next useful comparison is a
frequency-dependent wideband beamformer operating on the same captured
channels, followed by the batched FFT accelerator modes above and calibrated
channel-error sweeps. Passband ADC synthesis should be retained as an offline
validation mode rather than the dashboard's continuous Cora workload.
