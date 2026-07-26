# Cora DVL and acoustic navigation laboratory

This package runs hardware-shaped DVL, LBL, iUSBL, and INS processing on the
Cora Z7-10 before the external sensors exist. It does not change the FPGA
bitstream.

## Processing boundaries

The DVL simulator emits Sensor Frame ABI v1 records containing four
interleaved complex-int16 baseband beams at 40 ksample/s. The model uses a
600 kHz effective acoustic carrier, four 30-degree Janus beams, pulse delay,
Doppler, noise, multipath, bottom slope, and beam dropout. The processor
performs envelope ranging, complex Doppler estimation, beam validation, and a
weighted least-squares body-velocity solve. A future PL mixer/decimator and
DMA driver can replace the simulator with `/dev/cora-dvl0`.

The acoustic simulator emits four interleaved signed-int16 hydrophone
channels. Its dedicated waveform uses a 96 ksample/s 18-30 kHz chirp for
fractional-sample arrival timing followed by a 12 ksymbol/s, rate-1/2
convolutionally coded BPSK navigation payload. The same payload can be sent
with the existing 48 ksample/s V3 QPSK acoustic OFDM modem. A future ADC/DMA
driver can replace the simulator with `/dev/cora-hydrophone0`.

Every frame carries the first-sample TAI timestamp, uncertainty, sample rate,
center frequency, sequence, format, dimensions, flags, payload length, and
CRC. Raw recordings are length-prefixed frame streams and can be replayed on
the PC and target.

The real-time service uses separate DVL and acoustic DSP worker processes.
On the dual-core Zynq-7010, the 100 Hz INS/control process is kept on one CPU
and the lower-priority sensor DSP workers use the other. Bounded queues model
the future DMA producer/consumer boundary and expose overload as explicit
queue-drop counters rather than blocking the INS.

## Navigation model

One deterministic vehicle simulation drives:

- 100 Hz IMU propagation;
- 10 Hz pressure and heading aiding;
- 5 Hz bottom-track DVL;
- 1 Hz acoustic observations.

The 15-error-state INS estimates local NED position, NED velocity, body
attitude, accelerometer bias, gyro bias, and covariance. LBL mode alternates
two fixed transponders and applies two-way range updates. iUSBL mode uses a
10 cm square hydrophone array for TDOA bearing and a clock-qualified one-way
range to produce a position update. If simulated clock uncertainty exceeds
100 microseconds, iUSBL bearing remains available but absolute range is
rejected.

The simulation origin is `41.2250, -77.0445` on the West Branch Susquehanna
River beside Susquehanna State Park in Williamsport, Pennsylvania. Its
mission follows mapped river centerline points for about 2.3 km past the park,
crosses 60 m, and returns on a parallel lane. Both lanes remain within 30 m of
the mapped centerline. This is a software simulation route, not a surveyed
navigation channel or a substitute for current charts, bathymetry, flow,
bridges, dams, and local operating rules.

## Dashboards and commands

After boot:

```sh
/etc/init.d/cora-navigation status
cora-navigation-smoke --duration 10 --mode lbl
cora-navigation-smoke --duration 10 --mode iusbl --waveform ofdm
```

Open:

- DVL laboratory: `http://192.168.10.2:8083`
- LBL/iUSBL laboratory: `http://192.168.10.2:8084`

Both lab consoles show the fused INS and simulation truth. They provide
mission pause/reset, waveform selection, noise, multipath, dropouts, clock
drift, clock lock, array geometry, and log download controls.

The acoustic console includes a mobile-friendly mission map with live WGS-84
position, heading, mission waypoints, truth and fused-INS tracks, DVL
bottom-lock samples, transponder locations, acoustic fixes, and a 2-sigma
horizontal covariance ellipse. Use `Follow` to track the vehicle or
`Fit mission` to inspect the complete operating area. Individual overlays can
be toggled from the map layer control.

The street map uses OpenStreetMap tiles and the offline local-grid view needs
no Internet connection. Satellite view uses MapTiler's `satellite-v4` tiles:

1. create a MapTiler API key at `https://cloud.maptiler.com/account/keys/`;
2. open **Satellite setup** under the mission map;
3. paste and save the key, then select **Satellite imagery**.

The key is stored only in that browser's local storage. It is not sent to the
Cora service, written to telemetry, or embedded in the PetaLinux image. Map
tiles are fetched by the viewing browser, so map rendering does not consume
Cora CPU time. Leaflet 1.9.4 is vendored in the package under its BSD-2-Clause
license so the map UI itself remains available offline.

Raw capture is deliberately opt-in. Add `--raw-recording` to
`NAVIGATION_ARGS` in `/etc/default/cora-navigation`, restart the service, run
the desired scenario briefly, then download `/api/log/raw`.

## Host tests

```sh
PYTHONPATH=software/cora-navigation:software/cora-ofdm-acoustic \
  python3 -m unittest discover -s software/cora-navigation/tests -v
```

## PetaLinux build and image

```sh
source scripts/activate-tools.sh
petalinux-build \
  --project software/petalinux/cora-z7-10-baseline \
  -c cora-navigation
petalinux-build \
  --project software/petalinux/cora-z7-10-baseline
cd software/petalinux/cora-z7-10-baseline
./package-wic.sh
```

The reproducible image is
`software/petalinux/cora-z7-10-baseline/images/linux/petalinux-sdimage.wic`.

## Cora Z7-10 acceptance results

Measured on July 26, 2026, using the CPU-only implementation and the existing
FPGA bitstream:

| Test | Effective INS rate | Valid DVL | Valid acoustic | Queue drops | Position error |
| --- | ---: | ---: | ---: | ---: | ---: |
| Dedicated LBL, 10 s | 97.4 Hz | 49/49 | 9/9 | 0 | 1.79 m |
| OFDM iUSBL, 15 s | 98.87 Hz | 74/74 | 15/15 | 0 | 0.83 m |

Dedicated four-channel acoustic processing measured about 0.57 s per ping;
the OFDM-compatible iUSBL path measured 0.29 s for the final packet. The
minimal non-PREEMPT_RT image showed bounded Linux scheduling jitter, reported
separately as full-period misses and late wake-ups. Sensor processing did not
block the INS or lose a queued frame in either acceptance test.
