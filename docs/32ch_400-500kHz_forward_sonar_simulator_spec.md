# 32-Channel 400–500 kHz Forward-Looking Sonar Array Simulator Specification

## 1. Purpose

This document defines a first-pass simulation target for a compact, two-dimensional forward-looking sonar intended for underwater vehicle obstacle detection.

The sonar produces a **range-versus-elevation image**. It resolves targets in:

- Forward range
- Elevation angle

It does **not** resolve port/starboard azimuth. The horizontal acoustic beam is intentionally broad enough to cover the vehicle's forward safety corridor.

The receive architecture uses:

- 32 physical receive elements
- 32 simultaneous analog receive channels
- No analog receive-array multiplexing
- A separate broad-beam transmit projector
- Digital receive beamforming in elevation

This specification is intended to be used as project context for Codex while building a physics-based simulator. It is not a final transducer, electronics, or production design.

> Patent note: this architecture deliberately avoids time-multiplexing multiple receive elements into fewer analog receive channels. This statement is only a design description and is not a freedom-to-operate opinion.

---

## 2. Primary Design Goals

The initial simulator should answer the following questions:

1. What elevation resolution is obtained from a 32-element vertical array?
2. How does resolution vary from 400 to 500 kHz?
3. How much does beamwidth grow near the edges of a ±45° field of view?
4. What array sidelobes are produced by rectangular, Hann, Hamming, and Taylor weighting?
5. What range and angle accuracy can be expected for isolated targets?
6. How do two nearby targets merge in range and elevation?
7. How do channel gain, phase, timing, and position errors affect the image?
8. What receive SNR is required for useful obstacle detection?
9. How much processing and memory are required per ping?
10. What image quality is achievable without receive-channel multiplexing?

---

## 3. Baseline Sonar Configuration

### 3.1 System type

- Active forward-looking sonar
- Separate transmit projector and receive array
- One-dimensional receive array
- Range/elevation image
- Broad unresolved horizontal beam
- Digital delay-and-sum beamforming
- Linear FM chirp transmission
- Pulse compression by matched filtering

### 3.2 Baseline numerical parameters

| Parameter | Baseline value |
|---|---:|
| Number of receive elements | 32 |
| Receive architecture | 32 simultaneous channels |
| Analog multiplexing | None |
| Array orientation | Vertical |
| Center frequency | 450 kHz |
| Frequency range | 400–500 kHz |
| Chirp bandwidth | 100 kHz |
| Chirp start frequency | 400 kHz |
| Chirp stop frequency | 500 kHz |
| Chirp duration | 0.5 ms |
| Sample rate | 2.0 MSPS minimum |
| Preferred simulation sample rate | 4.0 MSPS |
| ADC resolution model | 16 bit |
| Sound speed | 1500 m/s, configurable |
| Element pitch | 1.50 mm |
| Active receive aperture | 46.5 mm |
| Elevation field of view | -45° to +45° |
| Number of displayed beams | 64 |
| Beam spacing | Approximately 1.43° |
| Primary simulated range | 0.5–20 m |
| Horizontal full beamwidth target | 20–30° |
| Pulse repetition rate | 10–30 Hz, range dependent |

The simulator must keep all these parameters configurable.

---

## 4. Coordinate System

Use a right-handed coordinate system:

- `x`: forward from the sonar
- `y`: starboard
- `z`: upward
- Elevation angle `theta`: measured in the x-z plane
- `theta = 0°`: straight ahead
- Positive elevation: upward
- Negative elevation: downward

The receive array is centered at the origin and extends along the `z` axis.

The separate transmit projector is initially modeled as being at the array center:

```text
                    +z
                     ^
                     |
              RX31   |
              RX30   |
               ...   |
              RX16 --+----> +x, forward
              RX15   |
               ...   |
              RX01   |
              RX00   |
```

Later versions may assign the transmitter a configurable offset from the receive-array center.

---

## 5. Receive Array Geometry

### 5.1 Element positions

For `N = 32` elements and pitch `d`:

\[
z_n = \left(n-\frac{N-1}{2}\right)d
\]

where:

- \(n = 0,1,\dots,N-1\)
- \(d = 1.50\text{ mm}\)

The active aperture is:

\[
L = (N-1)d
\]

For 32 elements:

\[
L = 31(1.50\text{ mm}) = 46.5\text{ mm}
\]

### 5.2 Why 1.50 mm pitch is used

The shortest wavelength occurs at 500 kHz:

\[
\lambda_{\min} = \frac{1500}{500000}=3.0\text{ mm}
\]

Therefore:

\[
\frac{\lambda_{\min}}{2}=1.50\text{ mm}
\]

A 1.50 mm pitch prevents the pitch from exceeding half a wavelength anywhere in the 400–500 kHz band. This is the conservative choice for wide-angle electronic steering.

### 5.3 Wavelength over the operating band

| Frequency | Wavelength | Pitch in wavelengths |
|---:|---:|---:|
| 400 kHz | 3.75 mm | 0.40 λ |
| 450 kHz | 3.33 mm | 0.45 λ |
| 500 kHz | 3.00 mm | 0.50 λ |

### 5.4 Expected receive beamwidth

A useful broadside approximation for a uniformly weighted linear aperture is:

\[
\theta_{-3\text{dB}} \approx 0.886\frac{\lambda}{L}
\]

At 450 kHz:

\[
\theta_{-3\text{dB}}
\approx 0.886\frac{3.33}{46.5}
\approx 0.0635\text{ rad}
\approx 3.64^\circ
\]

Approximate broadside beamwidths:

| Frequency | Approximate -3 dB beamwidth |
|---:|---:|
| 400 kHz | 4.1° |
| 450 kHz | 3.6° |
| 500 kHz | 3.3° |

When steering to elevation angle \(\theta_s\), beamwidth grows approximately as:

\[
\theta_{\text{BW,steered}}
\approx
\frac{\theta_{\text{BW,broadside}}}{\cos\theta_s}
\]

At ±45°, beamwidth is approximately 1.41 times the broadside value.

### 5.5 Estimated vertical resolution

For small angular widths, vertical resolution at range \(R\) is approximately:

\[
\Delta z \approx R\theta_{\text{BW}}
\]

Using a 3.6° beamwidth:

| Range | Approximate vertical resolution |
|---:|---:|
| 2 m | 0.13 m |
| 5 m | 0.31 m |
| 10 m | 0.63 m |
| 20 m | 1.26 m |

This should be treated as a beamwidth estimate rather than a guaranteed target-separation value.

---

## 6. Element Model

The first simulator version may model each receiver as an ideal point sensor at its center location.

Later versions should support a rectangular-element directivity model.

### 6.1 Suggested physical receive-element dimensions

A plausible starting geometry is:

- Vertical pitch: 1.50 mm
- Active vertical element dimension: approximately 1.1–1.3 mm
- Kerf: approximately 0.2–0.4 mm
- Horizontal element width: approximately 6–8 mm
- Material: PZT or 1-3 piezocomposite
- Common backing and matching structure

The horizontal element dimension controls the unresolved horizontal beam. The transmit projector must also illuminate the intended horizontal sector.

### 6.2 Optional rectangular-piston directivity

For an element with dimension \(D\) in a given plane:

\[
H(\theta,f)
=
\operatorname{sinc}
\left(
\frac{\pi D}{\lambda}\sin\theta
\right)
\]

Use normalized sinc consistently. The simulator must document whether:

\[
\operatorname{sinc}(x)=\frac{\sin x}{x}
\]

or:

\[
\operatorname{sinc}(x)=\frac{\sin(\pi x)}{\pi x}
\]

is being used.

For the first simulator milestone, receive-element directivity can be disabled so array behavior is easier to validate.

---

## 7. Transmit Model

### 7.1 Separate transmit projector

Use a separate projector rather than transmitting through the 32 receive elements.

Benefits include:

- Simpler receive protection
- Independent transmit beam design
- Higher transmit power
- Receive-only array optimization
- No transmit beamforming requirement
- No transmit/receive switch on every receive element

### 7.2 Initial transmit beam

The first model should use a separable transmit pattern:

\[
H_{\text{TX}}(\theta,\phi)
=
H_{\text{TX,elev}}(\theta)
H_{\text{TX,az}}(\phi)
\]

Suggested baseline:

- Elevation coverage: at least ±45°
- Horizontal full width: 25°
- Smooth roll-off outside the nominal sector
- Configurable Gaussian, sinc, or measured beam pattern

A simple Gaussian approximation is acceptable:

\[
H_{\text{TX,elev}}(\theta)
=
\exp\left[
-\frac{1}{2}
\left(
\frac{\theta}{\sigma_\theta}
\right)^2
\right]
\]

Choose \(\sigma_\theta\) to obtain the requested -3 dB beamwidth.

### 7.3 Transmit waveform

Use a complex or real linear FM chirp.

Real passband waveform:

\[
s(t)
=
w(t)
\cos
\left[
2\pi
\left(
f_0t+\frac{K}{2}t^2
\right)
\right]
\]

where:

- \(f_0=400\text{ kHz}\)
- \(f_1=500\text{ kHz}\)
- \(T_p=0.5\text{ ms}\)
- \(K=(f_1-f_0)/T_p\)
- \(w(t)\) is an optional Tukey or Hann amplitude window

The simulator should support:

- Up-chirp and down-chirp
- Configurable duration
- Configurable bandwidth
- Rectangular, Hann, and Tukey transmit windows
- Optional tone-burst mode for comparison

---

## 8. Range Resolution

For an ideal matched-filtered chirp with bandwidth \(B\):

\[
\Delta R
\approx
\frac{c}{2B}
\]

For \(B=100\text{ kHz}\):

\[
\Delta R
\approx
\frac{1500}{2(100000)}
=
7.5\text{ mm}
\]

This is the theoretical waveform-limited resolution. Practical resolution will be degraded by:

- Transducer bandwidth
- Transmit and receive impulse responses
- Chirp windowing
- Sampling rate
- Matched-filter sidelobe suppression
- Target extent
- Multipath
- SNR

The simulator should report both:

- Theoretical range resolution
- Measured -3 dB compressed-pulse width from the simulated waveform

---

## 9. Scene Model

### 9.1 Initial two-dimensional scene

The first implementation should simulate point targets in the x-z plane.

Each target should include:

```text
range_m
elevation_deg
reflectivity
radial_velocity_mps
label
enabled
```

A target coordinate may be derived from range and elevation:

\[
x_k = R_k\cos\theta_k
\]

\[
z_k = R_k\sin\theta_k
\]

Set \(y_k=0\) for the first 2D simulator.

### 9.2 Later scene types

Add these after point-target validation:

- Horizontal bottom plane
- Sloped bottom
- Vertical wall
- Cylinder
- Sphere
- Cable or thin line
- Box
- Overhang
- Multiple-scatterer obstacle
- Rough seabed
- Vehicle-sized opening or tunnel
- Surface boundary
- Random volume clutter

### 9.3 Two-way propagation delay

For a separate transmitter at position \(\mathbf{p}_{TX}\), receive element \(n\) at \(\mathbf{p}_n\), and target \(k\) at \(\mathbf{p}_k\):

\[
\tau_{n,k}
=
\frac{
\left\lVert
\mathbf{p}_k-\mathbf{p}_{TX}
\right\rVert
+
\left\lVert
\mathbf{p}_k-\mathbf{p}_n
\right\rVert
}{c}
\]

Do not assume identical receive delay for every element.

### 9.4 Amplitude model

A simple bistatic spherical-spreading model is:

\[
A_{n,k}
=
\frac{
\rho_k
H_{\text{TX}}(\theta_k,\phi_k)
H_{\text{RX},n}(\theta_{n,k},\phi_{n,k})
}{
R_{TX,k}R_{RX,n,k}
}
10^{-\alpha(f)(R_{TX,k}+R_{RX,n,k})/20}
\]

where:

- \(\rho_k\) is complex reflectivity
- \(\alpha(f)\) is absorption in dB per unit distance
- \(R_{TX,k}\) is transmitter-to-target distance
- \(R_{RX,n,k}\) is target-to-receiver distance

Allow the user to disable spreading and absorption for validation tests.

### 9.5 Received channel signal

For element \(n\):

\[
x_n(t)
=
\sum_k
A_{n,k}
s(t-\tau_{n,k})
+
v_n(t)
\]

where \(v_n(t)\) includes noise and optional interference.

---

## 10. Receive Electronics Model

The hardware concept is:

```text
32 receive elements
        |
32 protection networks
        |
32 low-noise amplifiers
        |
32 gain / time-gain channels
        |
32 anti-alias filters
        |
32 simultaneous ADC channels
        |
synchronized digital interface
        |
FPGA / processor
```

There are no analog receive multiplexers.

### 10.1 ADC assumptions

Baseline simulator model:

- 32 simultaneous channels
- 16-bit nominal resolution
- 4 MSPS preferred
- Common sampling clock
- Common conversion trigger
- Configurable full-scale input
- Configurable input-referred noise
- Configurable DC offset
- Configurable clipping
- Optional aperture jitter

A 2 MSPS model is acceptable for early work, but 4 MSPS is preferred because it provides:

- 8–10 samples per carrier cycle
- More margin for digital filtering
- Easier waveform inspection
- Easier fractional-delay validation
- Better representation of analog bandwidth

### 10.2 Channel mismatch model

Each receive channel should optionally include:

- Gain error
- Phase error
- Time skew
- DC offset
- Noise density
- Element position error
- Element sensitivity variation
- Polarity reversal
- Failed or muted channel

Suggested default Monte Carlo ranges:

| Error | Initial standard deviation |
|---|---:|
| Gain error | 0.5 dB |
| Phase error | 3° |
| Timing skew | 2 ns |
| Position error | 0.05 mm |
| Sensitivity variation | 5% |
| DC offset | Configurable |
| Failed elements | 0 by default |

These are simulation assumptions, not hardware requirements.

### 10.3 Analog filtering

Model an analog bandpass response centered near 450 kHz.

Suggested initial response:

- Passband: 375–525 kHz
- Flatness: configurable
- Stopband attenuation: configurable
- Filter order: configurable
- Identical response for all channels by default
- Per-channel pole variation as an optional impairment

The filter may initially be modeled with a digital IIR or FIR approximation applied before quantization.

---

## 11. Digital Signal-Processing Chain

Recommended reference processing sequence:

```text
Raw 32-channel ADC data
        |
Remove offset
        |
Apply channel calibration
        |
Complex digital downconversion
        |
Low-pass filtering / decimation
        |
Matched filtering
        |
Beamforming or pixel focusing
        |
Magnitude / power detection
        |
Range compensation
        |
Log compression
        |
Range-elevation image
        |
Obstacle extraction
```

The simulator should keep intermediate data accessible for debugging.

---

## 12. Digital Downconversion

Use a complex local oscillator:

\[
z_n[m]
=
x_n[m]
e^{-j2\pi f_c m/f_s}
\]

where:

- \(f_c=450\text{ kHz}\)
- \(f_s\) is ADC sample rate

Low-pass filter the complex signal to retain the chirp bandwidth.

Alternative implementation:

- Generate an analytic version of the transmit signal
- Perform complex matched filtering directly on real ADC data
- Compare numerical equivalence

The initial implementation should favor clarity over optimization.

---

## 13. Matched Filtering

The matched-filter impulse response is:

\[
h[m]=s^*[M-1-m]
\]

where \(s[m]\) is the sampled reference chirp.

Implement matched filtering using:

1. Direct convolution for small validation cases
2. FFT convolution for full records
3. Optional overlap-save for streaming simulation

The simulator should plot:

- Transmit waveform
- Matched-filter impulse response
- Compressed pulse
- Range sidelobes
- Effect of chirp windowing
- Effect of Doppler mismatch

---

## 14. Beamforming

Implement at least two beamforming modes.

### 14.1 Far-field narrowband beamforming

For steering angle \(\theta_b\), apply:

\[
w_n(\theta_b,f_c)
=
a_n
\exp
\left[
+j\frac{2\pi f_c}{c}z_n\sin\theta_b
\right]
\]

where \(a_n\) is the aperture weight.

The beam output is:

\[
y_b[r]
=
\sum_{n=0}^{N-1}
w_n(\theta_b)x_n[r]
\]

The sign convention must be validated using a known point target.

### 14.2 Frequency-domain wideband beamforming

Because the chirp spans 400–500 kHz, phase steering only at 450 kHz may cause beam squint.

The simulator should therefore include an optional wideband mode:

1. Transform each receive channel to the frequency domain.
2. For each frequency bin \(f_q\), apply:

\[
w_n(\theta_b,f_q)
=
a_n
\exp
\left[
+j\frac{2\pi f_q}{c}z_n\sin\theta_b
\right]
\]

3. Sum across channels.
4. Transform back or perform matched processing in frequency.

Compare:

- Center-frequency phase steering
- True-time-delay interpolation
- Frequency-dependent steering

### 14.3 Near-field pixel focusing

For close targets, calculate the expected transmit-plus-receive propagation delay for each image pixel.

For pixel \(\mathbf{p}\):

\[
\tau_n(\mathbf{p})
=
\frac{
\left\lVert
\mathbf{p}-\mathbf{p}_{TX}
\right\rVert
+
\left\lVert
\mathbf{p}-\mathbf{p}_n
\right\rVert
}{c}
\]

Then coherently sample or interpolate each receive channel at the corresponding delay.

This produces a range-elevation focused image and naturally includes near-field curvature.

Near-field focusing should be a later milestone, not required for the first functional version.

---

## 15. Aperture Weighting

Support at least:

- Rectangular
- Hann
- Hamming
- Blackman
- Taylor

For every window, measure and report:

- -3 dB beamwidth
- Peak sidelobe level
- Integrated sidelobe level
- Main-lobe widening
- Loss in coherent array gain

Default product-oriented weighting should initially be Hann or Taylor rather than rectangular.

---

## 16. Beam Grid

Baseline beam grid:

```text
elevation_min_deg = -45
elevation_max_deg = +45
number_of_beams = 64
```

Beam spacing:

\[
\Delta\theta
=
\frac{90^\circ}{63}
\approx1.43^\circ
\]

This oversamples the expected physical beamwidth. The image therefore appears smooth, but adjacent displayed beams are not independent resolution cells.

The simulator should allow:

- 32 beams
- 64 beams
- 128 beams
- Arbitrary custom angle lists

---

## 17. Range Grid and Record Length

The round-trip time for maximum range \(R_{\max}\) is approximately:

\[
T_{\max}
=
\frac{2R_{\max}}{c}
\]

At 20 m:

\[
T_{\max}
=
\frac{40}{1500}
=
26.67\text{ ms}
\]

At 4 MSPS, this is approximately:

\[
106667\text{ samples per channel}
\]

For 32 channels:

\[
3.41\text{ million samples per ping}
\]

At signed 16-bit storage:

\[
6.83\text{ MB per ping}
\]

At 20 pings/s, uncompressed input data is approximately:

\[
136.5\text{ MB/s}
\]

This is an internal data-rate estimate. A production FPGA would reduce the data before forwarding a display image.

The simulator should support shorter maximum ranges to reduce runtime.

---

## 18. Display Processing

The primary display is a polar or Cartesian range-elevation image.

### 18.1 Polar display

Axes:

- Radius: range
- Angle: elevation

### 18.2 Cartesian display

Axes:

- Horizontal: forward distance \(x\)
- Vertical: height \(z\)

Convert from range/elevation:

\[
x=R\cos\theta
\]

\[
z=R\sin\theta
\]

### 18.3 Intensity

Display:

\[
I_{\text{dB}}
=
20\log_{10}
\left(
\frac{|y|}{y_{\text{ref}}}
\right)
\]

or use power:

\[
I_{\text{dB}}
=
10\log_{10}
\left(
\frac{|y|^2}{P_{\text{ref}}}
\right)
\]

Both are equivalent if references are handled consistently.

Suggested display controls:

- Dynamic range: 30–80 dB
- Gain
- Time-varying gain
- Background subtraction
- Median filtering
- Persistence
- Threshold
- Colormap
- Polar/Cartesian toggle

---

## 19. Obstacle-Extraction Output

In addition to the sonar image, compute a machine-oriented obstacle profile.

For each elevation beam, report:

```text
elevation_deg
nearest_detection_range_m
peak_intensity_db
confidence
```

Optional outputs:

- Minimum forward clearance
- Minimum vertical clearance
- Detected bottom profile
- Detected overhead profile
- Centerline obstruction flag
- Suggested climb/dive response
- Unknown or low-confidence sectors

The initial extraction algorithm may use:

1. Range-dependent threshold
2. Local peak detection
3. Minimum target width
4. Connected-component grouping
5. Confidence based on SNR and beam consistency

---

## 20. Noise and Interference Models

Support the following selectable impairments:

- White Gaussian electronic noise
- Band-limited noise
- Channel-correlated noise
- Impulsive electrical interference
- Transmit ring-down
- Reverberation tail
- Surface multipath
- Bottom multipath
- Narrowband external sonar interference
- Doppler shift
- Clock-frequency error
- Sound-speed mismatch

Start with independent white noise, then add more realistic impairments.

---

## 21. Doppler Model

For a target radial velocity \(v_r\), use an approximate monostatic Doppler shift:

\[
f_D
\approx
\frac{2v_r}{c}f_c
\]

For bistatic geometry, use the sum of transmit and receive radial components.

The simulator should evaluate:

- Matched-filter loss versus target velocity
- Range bias
- Beamforming phase error
- Whether a chirp bank or Doppler-tolerant processing is needed

Doppler is not required for the first static-scene milestone.

---

## 22. Calibration Model

Represent each channel with a complex calibration factor:

\[
C_n(f)
=
G_n(f)e^{j\phi_n(f)}
\]

Initial calibration may use one complex coefficient per channel at center frequency.

Later calibration may use:

- Frequency-dependent FIR correction
- Measured channel transfer functions
- Measured element sensitivity
- Measured element position
- Water-tank point-source calibration

The simulator should include a calibration utility that estimates channel correction from a known broadside point source.

---

## 23. Expected Hardware Architecture

A practical nonmultiplexed hardware implementation could use:

```text
32-element receive array
        |
32 low-capacitance protection networks
        |
32 low-noise fixed-gain stages
        |
32 variable-gain or TGC stages
        |
32 anti-alias filters
        |
8 quad simultaneous-sampling ADCs
        |
shared low-jitter sample clock
        |
FPGA deserialization and DSP
        |
embedded processor
        |
Ethernet / ROS 2 / custom API
```

Candidate ADC class:

- Quad simultaneous-sampling
- 14- or 16-bit
- At least 2 MSPS per channel
- Preferably 4 MSPS per channel
- Deterministic simultaneous conversion
- Synchronized start-of-conversion
- Serial digital interface

The simulator must not depend on one specific ADC part.

---

## 24. FPGA-Oriented Processing Partition

A likely embedded partition is:

### FPGA fabric

- ADC capture
- Channel alignment
- Offset removal
- Digital downconversion
- FIR filtering
- Decimation
- Matched filtering
- Beamforming
- Magnitude calculation
- Range compensation
- Frame buffering

### Embedded CPU

- Configuration
- Calibration management
- Display compression
- Obstacle extraction
- Networking
- Logging
- Health monitoring
- GUI communication

The software simulator should keep the DSP stages modular so they can later be translated into FPGA blocks.

---

## 25. Simulator Software Requirements

### 25.1 Preferred implementation language

Use Python for the reference simulator.

Recommended dependencies:

- Python 3.11 or newer
- NumPy
- SciPy
- Matplotlib
- PyYAML
- pytest
- Optional: Numba
- Optional: CuPy
- Optional GUI: PySide6 or a browser-based frontend

Do not require GPU acceleration for correctness.

### 25.2 Design principles

- Deterministic random seed
- SI units internally
- Type hints
- Dataclasses for configuration
- Vectorized numerical processing
- Clear separation between physics, DSP, and GUI
- Unit tests for every major mathematical operation
- No hidden global state
- Configurable logging
- Save intermediate arrays for debugging
- Support headless operation

---

## 26. Suggested Repository Structure

```text
forward_sonar_sim/
├── README.md
├── pyproject.toml
├── requirements.txt
├── configs/
│   ├── baseline_450khz.yaml
│   ├── point_targets.yaml
│   ├── sloped_bottom.yaml
│   └── channel_errors.yaml
├── src/
│   └── forward_sonar/
│       ├── __init__.py
│       ├── config.py
│       ├── geometry.py
│       ├── waveform.py
│       ├── propagation.py
│       ├── transducers.py
│       ├── electronics.py
│       ├── ddc.py
│       ├── matched_filter.py
│       ├── beamforming.py
│       ├── focusing.py
│       ├── imaging.py
│       ├── detection.py
│       ├── calibration.py
│       ├── metrics.py
│       └── cli.py
├── tests/
│   ├── test_geometry.py
│   ├── test_waveform.py
│   ├── test_delays.py
│   ├── test_matched_filter.py
│   ├── test_beamforming.py
│   ├── test_calibration.py
│   └── test_end_to_end.py
├── scripts/
│   ├── run_point_target_demo.py
│   ├── plot_array_factor.py
│   ├── run_resolution_sweep.py
│   ├── run_channel_error_monte_carlo.py
│   └── benchmark_processing.py
└── outputs/
    └── .gitkeep
```

---

## 27. Baseline YAML Configuration

```yaml
simulation:
  sound_speed_mps: 1500.0
  sample_rate_hz: 4000000.0
  max_range_m: 20.0
  random_seed: 12345

array:
  element_count: 32
  pitch_m: 0.0015
  axis: z
  center_xyz_m: [0.0, 0.0, 0.0]
  element_width_horizontal_m: 0.007
  element_height_vertical_m: 0.0012
  include_element_directivity: false

transmitter:
  position_xyz_m: [0.0, 0.0, 0.0]
  elevation_beamwidth_deg: 100.0
  azimuth_beamwidth_deg: 25.0
  source_level_db: 0.0

waveform:
  type: lfm
  start_frequency_hz: 400000.0
  stop_frequency_hz: 500000.0
  duration_s: 0.0005
  window: tukey
  tukey_alpha: 0.2

receiver:
  adc_bits: 16
  adc_full_scale_vpp: 4.096
  analog_band_low_hz: 350000.0
  analog_band_high_hz: 550000.0
  input_noise_rms_v: 0.00001
  gain_db: 40.0
  enable_quantization: true
  enable_clipping: true

beamformer:
  method: frequency_domain
  elevation_min_deg: -45.0
  elevation_max_deg: 45.0
  beam_count: 64
  aperture_window: hann

display:
  coordinate_mode: cartesian
  dynamic_range_db: 50.0
  min_range_m: 0.5
  max_range_m: 20.0

targets:
  - label: center_target
    range_m: 8.0
    elevation_deg: 0.0
    reflectivity: 1.0
    radial_velocity_mps: 0.0
  - label: upper_target
    range_m: 12.0
    elevation_deg: 18.0
    reflectivity: 0.7
    radial_velocity_mps: 0.0
```

---

## 28. Development Milestones

### Milestone 1: Array-factor calculator

Implement:

- Element coordinates
- Array factor versus elevation
- Frequency sweep from 400 to 500 kHz
- Steering from -45° to +45°
- Window comparison
- Beamwidth and sidelobe measurements

Acceptance criteria:

- Broadside beamwidth is close to analytical estimates.
- Pattern is symmetric at 0° steering.
- Main lobe points to the requested steering angle.
- No visible grating lobe enters the ±90° visible region at 500 kHz.

### Milestone 2: Ideal point-target beam response

Do not synthesize passband ADC waveforms yet.

Implement direct complex target response using propagation phase.

Acceptance criteria:

- A single target peaks in the correct beam.
- Angle error is less than one displayed beam spacing.
- Range may initially be represented as an ideal target bin.

### Milestone 3: Passband chirp and 32 raw channels

Generate:

- Transmit chirp
- Per-element two-way delay
- Per-element amplitude
- 32 real ADC waveforms
- Noise-free operation first

Acceptance criteria:

- Measured delay matches target range.
- Relative channel phase matches geometry.
- Raw channels can be plotted and inspected.

### Milestone 4: Matched filtering

Implement pulse compression per channel.

Acceptance criteria:

- Compressed pulse peaks at the correct range.
- Measured compressed-pulse width is reported.
- Windowing reduces sidelobes as expected.

### Milestone 5: Range-elevation image

Implement 64-beam processing.

Acceptance criteria:

- Point target appears at correct range and elevation.
- Intensity image is normalized consistently.
- Multiple targets can be displayed.

### Milestone 6: Wideband beamforming comparison

Compare:

- Center-frequency phase steering
- Frequency-domain steering
- True-time-delay interpolation

Acceptance criteria:

- Quantify beam squint across the 400–500 kHz chirp.
- Select a default implementation based on image quality and compute cost.

### Milestone 7: Electronics and calibration errors

Add:

- Quantization
- Noise
- Gain mismatch
- Phase mismatch
- Timing skew
- Failed elements
- Calibration correction

Acceptance criteria:

- Calibration improves beam peak, sidelobes, and coherent gain.
- Monte Carlo plots quantify sensitivity to each error source.

### Milestone 8: Extended scenes

Add:

- Sloped bottom
- Wall
- Cable
- Overhang
- Multiple obstacles
- Simple multipath

### Milestone 9: Real-time GUI

Provide:

- Start/stop simulation
- Parameter controls
- Polar and Cartesian image views
- Raw channel view
- Beam-pattern view
- Range profile
- Obstacle-profile output
- Export of PNG, CSV, NPZ, and configuration YAML

---

## 29. Required Validation Cases

### Case A: Broadside point target

- Range: 10 m
- Elevation: 0°
- High SNR

Expected:

- Image peak near 10 m and 0°
- Symmetric elevation response
- Beamwidth close to prediction

### Case B: Steered point target

- Range: 10 m
- Elevation: +35°

Expected:

- Correct peak angle
- Wider beam than broadside
- Reduced coherent gain if the element pattern attenuates the edge

### Case C: Two targets separated in elevation

- Range: 10 m
- Elevations: -3° and +3°

Sweep separation to determine when two peaks become resolvable.

### Case D: Two targets separated in range

- Elevation: 0°
- Range separation: 5–100 mm

Measure practical range resolution.

### Case E: Sloped bottom

Create a line or surface rising into the vehicle path.

Expected:

- Continuous bottom profile
- Nearest-clearance extraction
- Pitch compensation can be added later

### Case F: Channel failure

Disable one, two, four, and eight elements.

Measure:

- Main-lobe change
- Sidelobe increase
- Angle bias
- Array-gain loss

---

## 30. Metrics

The simulator should automatically calculate:

- Broadside -3 dB beamwidth
- Steered -3 dB beamwidth
- Peak sidelobe level
- Integrated sidelobe level
- Coherent array gain
- Angle error
- Range error
- Compressed-pulse width
- Matched-filter sidelobe level
- Detection probability
- False-alarm probability
- Image dynamic range
- Processing time per ping
- Memory per ping
- Raw input data rate
- Output image data rate

---

## 31. Initial Simplifications

The first working version may assume:

- Constant sound speed
- Static targets
- No horizontal position
- No multipath
- No volume reverberation
- Ideal transmitter position
- Identical receive-element patterns
- Identical channel frequency responses
- White Gaussian noise
- Point targets
- Far-field beamforming
- No vehicle motion

Each simplification should be isolated behind an interface so it can be replaced later.

---

## 32. Important Implementation Warnings

1. **Do not confuse beam count with physical resolution.**
   Sixty-four displayed beams do not provide sixty-four independent angular resolution cells.

2. **Use one consistent phase convention.**
   Validate steering signs with a known target before adding complexity.

3. **Do not ignore the chirp's fractional bandwidth.**
   The 400–500 kHz signal has approximately 22% fractional bandwidth around 450 kHz. Center-frequency phase steering may show measurable squint.

4. **Use the complete bistatic delay.**
   The receive element is not exactly at the transmitter location.

5. **Keep amplitude and power dB definitions consistent.**

6. **Avoid normalizing every processing stage independently.**
   That can hide real array-gain and SNR effects.

7. **Treat interpolation carefully.**
   Fractional-delay errors can become angle errors.

8. **Keep raw, calibrated, baseband, matched-filtered, and beamformed data as separate objects.**

9. **Add noise only after the ideal model passes all geometry tests.**

10. **Do not optimize prematurely.**
    Establish a trustworthy reference implementation before using GPU, FPGA-style fixed point, or streaming approximations.

---

## 33. Suggested First Codex Task

Use the following as the first implementation instruction:

```text
Create a Python project implementing Milestone 1 of the attached
32-channel forward-looking sonar simulator specification.

Requirements:
- Use Python 3.11+, NumPy, SciPy, Matplotlib, PyYAML, and pytest.
- Implement dataclass-based configuration.
- Model a 32-element vertical linear array with 1.50 mm pitch.
- Plot array factor versus elevation from -90° to +90°.
- Support 400, 450, and 500 kHz.
- Support steering angles of 0°, ±20°, and ±45°.
- Support rectangular, Hann, Hamming, Blackman, and Taylor windows.
- Calculate -3 dB beamwidth, peak sidelobe level, and coherent gain.
- Load the baseline configuration from YAML.
- Include unit tests for element coordinates, steering phase, pattern symmetry,
  beam peak angle, and approximate broadside beamwidth.
- Save plots and CSV metric summaries under outputs/.
- Include a README with setup and command examples.
- Do not implement raw passband channel simulation yet.
```

---

## 34. Recommended Baseline Decision

Use this as the default design point until simulation results justify changing it:

```text
32 simultaneous receive channels
450 kHz center frequency
400–500 kHz LFM chirp
1.50 mm vertical pitch
46.5 mm active aperture
64 displayed elevation beams
-45° to +45° elevation field
Hann or Taylor aperture weighting
4 MSPS simulation sample rate
16-bit ADC model
Separate broad-beam transmit projector
20–30° unresolved horizontal beam
0.5–20 m working range
```

This design should provide approximately 3.3–4.1° broadside elevation beamwidth over the operating band, with wider beams near the ±45° steering limits. It is a reasonable starting point for a compact obstacle-detection sonar and is straightforward to model without analog receive multiplexing.
