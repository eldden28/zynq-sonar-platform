#!/usr/bin/env python3
"""Time-series forward-looking sonar simulation for the Cora Z7-10.

The reference pipeline deliberately begins at a future FPGA/ADC boundary:
up to 128 simultaneous complex-baseband receive channels.  Each ping is synthesized
with the complete transmitter-target-receiver delay, then processed through a
matched filter and a phase-steered delay-and-sum beamformer.  No target is
painted directly into the output image.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import base64
import functools
import io
import json
import math
import threading
import time

import numpy as np


WINDOW_NAMES = ("rectangular", "hann", "hamming", "blackman", "taylor")
SCENARIO_NAMES = ("point_targets", "close_pair", "obstacle_course", "sloped_bottom")
MAX_SCENE_TARGETS = 64


@dataclass
class SonarTarget:
    range_m: float
    elevation_deg: float
    reflectivity: float = 1.0
    label: str = "target"
    enabled: bool = True


@dataclass
class SonarConfig:
    sound_speed_mps: float = 1500.0
    element_count: int = 32
    pitch_m: float = 0.0015
    center_frequency_hz: float = 450_000.0
    chirp_bandwidth_hz: float = 100_000.0
    chirp_duration_s: float = 0.0005
    baseband_sample_rate_hz: float = 500_000.0
    min_range_m: float = 0.5
    max_range_m: float = 12.0
    elevation_min_deg: float = -45.0
    elevation_max_deg: float = 45.0
    beam_count: int = 512
    beamforming_range_bins: int = 1024
    range_pixel_count: int = 512
    aperture_window: str = "hann"
    waveform_window: str = "tukey"
    tukey_alpha: float = 0.2
    noise_rms: float = 0.002
    adc_bits: int = 16
    enable_quantization: bool = True
    gain_error_std_db: float = 0.0
    phase_error_std_deg: float = 0.0
    timing_skew_std_ns: float = 0.0
    failed_channels: tuple[int, ...] = field(default_factory=tuple)
    dynamic_range_db: float = 50.0
    detection_threshold_db: float = -18.0
    ping_rate_hz: float = 1.0
    random_seed: int = 0xC04A

    def validate(self) -> None:
        if not 4 <= self.element_count <= 128:
            raise ValueError("element_count must be between 4 and 128")
        if not 400_000 <= self.center_frequency_hz <= 1_000_000:
            raise ValueError("center_frequency_hz must be between 400 kHz and 1 MHz")
        if not 0.0005 <= self.pitch_m <= 0.004:
            raise ValueError("pitch_m must be between 0.5 and 4 mm")
        if not 250_000 <= self.baseband_sample_rate_hz <= 1_000_000:
            raise ValueError("baseband_sample_rate_hz must be 250 kHz to 1 MHz")
        if self.chirp_bandwidth_hz >= 0.8 * self.baseband_sample_rate_hz:
            raise ValueError("chirp bandwidth needs baseband sample-rate margin")
        if not 0.1 <= self.min_range_m < self.max_range_m <= 20.0:
            raise ValueError("range limits must satisfy 0.1 <= min < max <= 20 m")
        if self.beam_count not in (32, 64, 128, 256, 512):
            raise ValueError("beam_count must be 32, 64, 128, 256, or 512")
        if not self.range_pixel_count <= self.beamforming_range_bins <= 4096:
            raise ValueError(
                "beamforming_range_bins must be between range_pixel_count and 4096"
            )
        if self.range_pixel_count < 128 or self.range_pixel_count > 1024:
            raise ValueError("range_pixel_count must be between 128 and 1024")
        if self.aperture_window not in WINDOW_NAMES:
            raise ValueError("unsupported aperture window")
        if not 0.0 <= self.noise_rms <= 0.2:
            raise ValueError("noise_rms must be between 0 and 0.2")
        if not 20.0 <= self.dynamic_range_db <= 80.0:
            raise ValueError("dynamic_range_db must be between 20 and 80 dB")
        if not -60.0 <= self.detection_threshold_db <= -3.0:
            raise ValueError("detection_threshold_db must be between -60 and -3 dB")
        if not 0.1 <= self.ping_rate_hz <= 10.0:
            raise ValueError("ping_rate_hz must be between 0.1 and 10 Hz")
        if any(channel < 0 or channel >= self.element_count for channel in self.failed_channels):
            raise ValueError("failed channel index is outside the array")


@dataclass
class SonarResult:
    sequence: int
    generated_at: float
    channels: np.ndarray
    matched_channels: np.ndarray
    beamformed: np.ndarray
    range_axis_m: np.ndarray
    elevation_axis_deg: np.ndarray
    public: dict


def element_positions(config: SonarConfig) -> np.ndarray:
    """Return centered element z positions in metres."""
    index = np.arange(config.element_count, dtype=np.float64)
    return (index - (config.element_count - 1) / 2.0) * config.pitch_m


def _taylor_window(length: int, nbar: int = 4, sidelobe_db: float = 30.0) -> np.ndarray:
    """Return a normalized Taylor window without requiring SciPy on target."""
    order = np.arange(1, nbar, dtype=np.float64)
    amplitude = 10.0 ** (sidelobe_db / 20.0)
    a_value = np.arccosh(amplitude) / np.pi
    scale = nbar**2 / (a_value**2 + (nbar - 0.5) ** 2)
    coefficients = np.empty(nbar - 1, dtype=np.float64)
    order_squared = order**2
    for offset, value in enumerate(order):
        sign = 1.0 if offset % 2 == 0 else -1.0
        numerator = sign * np.prod(
            1.0
            - order_squared[offset]
            / (scale * (a_value**2 + (order - 0.5) ** 2))
        )
        denominator = 2.0
        if offset:
            denominator *= np.prod(1.0 - order_squared[offset] / order_squared[:offset])
        if offset + 1 < len(order):
            denominator *= np.prod(
                1.0 - order_squared[offset] / order_squared[offset + 1 :]
            )
        coefficients[offset] = numerator / denominator
    samples = np.arange(length, dtype=np.float64) - length / 2.0 + 0.5
    window = 1.0 + 2.0 * np.sum(
        coefficients[:, None]
        * np.cos(2.0 * np.pi * order[:, None] * samples[None, :] / length),
        axis=0,
    )
    return window / np.max(window)


def aperture_weights(name: str, length: int) -> np.ndarray:
    if name == "rectangular":
        values = np.ones(length)
    elif name == "hann":
        values = np.hanning(length)
    elif name == "hamming":
        values = np.hamming(length)
    elif name == "blackman":
        values = np.blackman(length)
    elif name == "taylor":
        values = _taylor_window(length)
    else:
        raise ValueError(f"unsupported aperture window: {name}")
    return np.asarray(values, dtype=np.float64)


def tukey_window(length: int, alpha: float) -> np.ndarray:
    if alpha <= 0.0:
        return np.ones(length)
    if alpha >= 1.0:
        return np.hanning(length)
    position = np.linspace(0.0, 1.0, length)
    values = np.ones(length)
    first = position < alpha / 2.0
    last = position > 1.0 - alpha / 2.0
    values[first] = 0.5 * (
        1.0 + np.cos(np.pi * (2.0 * position[first] / alpha - 1.0))
    )
    values[last] = 0.5 * (
        1.0
        + np.cos(
            np.pi * (2.0 * position[last] / alpha - 2.0 / alpha + 1.0)
        )
    )
    return values


def chirp_reference(config: SonarConfig) -> np.ndarray:
    length = max(8, round(config.chirp_duration_s * config.baseband_sample_rate_hz))
    time_s = np.arange(length, dtype=np.float64) / config.baseband_sample_rate_hz
    slope = config.chirp_bandwidth_hz / config.chirp_duration_s
    phase = 2.0 * np.pi * (
        -0.5 * config.chirp_bandwidth_hz * time_s + 0.5 * slope * time_s**2
    )
    if config.waveform_window == "tukey":
        envelope = tukey_window(length, config.tukey_alpha)
    elif config.waveform_window == "hann":
        envelope = np.hanning(length)
    elif config.waveform_window == "rectangular":
        envelope = np.ones(length)
    else:
        raise ValueError("waveform_window must be rectangular, hann, or tukey")
    return np.asarray(envelope * np.exp(1j * phase), dtype=np.complex64)


@functools.lru_cache(maxsize=32)
def _cached_steering_matrix(
    element_count: int,
    pitch_m: float,
    center_frequency_hz: float,
    sound_speed_mps: float,
    elevation_min_deg: float,
    elevation_max_deg: float,
    beam_count: int,
    aperture_window: str,
) -> np.ndarray:
    positions = (
        np.arange(element_count, dtype=np.float64) - (element_count - 1) / 2.0
    ) * pitch_m
    angles = np.radians(
        np.linspace(elevation_min_deg, elevation_max_deg, beam_count)
    )
    phase = (
        -2.0
        * np.pi
        * center_frequency_hz
        / sound_speed_mps
        * np.sin(angles)[:, None]
        * positions[None, :]
    )
    weights = aperture_weights(aperture_window, element_count)
    weights /= np.sum(weights)
    values = np.asarray(weights[None, :] * np.exp(1j * phase), dtype=np.complex64)
    values.flags.writeable = False
    return values


def steering_matrix(config: SonarConfig, elevations_deg: np.ndarray) -> np.ndarray:
    expected = np.linspace(
        config.elevation_min_deg, config.elevation_max_deg, config.beam_count
    )
    requested = np.asarray(elevations_deg, dtype=np.float64)
    if requested.shape == expected.shape and np.array_equal(requested, expected):
        return _cached_steering_matrix(
            config.element_count,
            config.pitch_m,
            config.center_frequency_hz,
            config.sound_speed_mps,
            config.elevation_min_deg,
            config.elevation_max_deg,
            config.beam_count,
            config.aperture_window,
        )
    positions = element_positions(config)
    angles = np.radians(requested)
    phase = (
        -2.0
        * np.pi
        * config.center_frequency_hz
        / config.sound_speed_mps
        * np.sin(angles)[:, None]
        * positions[None, :]
    )
    weights = aperture_weights(config.aperture_window, config.element_count)
    weights /= np.sum(weights)
    return np.asarray(weights[None, :] * np.exp(1j * phase), dtype=np.complex64)


def array_pattern(
    config: SonarConfig,
    steering_deg: float = 0.0,
    scan_deg: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    if scan_deg is None:
        scan_deg = np.linspace(-90.0, 90.0, 3601)
    positions = element_positions(config)
    weights = aperture_weights(config.aperture_window, config.element_count)
    phase = (
        2.0
        * np.pi
        * config.center_frequency_hz
        / config.sound_speed_mps
        * positions[:, None]
        * (
            np.sin(np.radians(scan_deg))[None, :]
            - math.sin(math.radians(steering_deg))
        )
    )
    response = np.abs(np.sum(weights[:, None] * np.exp(1j * phase), axis=0))
    response /= max(float(np.max(response)), 1e-12)
    return np.asarray(scan_deg), 20.0 * np.log10(np.maximum(response, 1e-8))


def array_metrics(config: SonarConfig, steering_deg: float = 0.0) -> dict[str, float]:
    angles, response_db = array_pattern(config, steering_deg)
    # Spatially aliased arrays can have multiple equal-height lobes. Measure
    # beamwidth around the commanded look direction, not the first grating
    # lobe returned by argmax at the edge of the scan.
    peak = int(np.argmin(np.abs(angles - steering_deg)))
    left = peak
    while left > 0 and response_db[left] >= -3.0:
        left -= 1
    right = peak
    while right + 1 < len(response_db) and response_db[right] >= -3.0:
        right += 1
    beamwidth = float(angles[right] - angles[left])
    minima = np.where(
        (response_db[1:-1] <= response_db[:-2])
        & (response_db[1:-1] < response_db[2:])
    )[0] + 1
    left_minima = minima[minima < peak]
    right_minima = minima[minima > peak]
    main_left = int(left_minima[-1]) if len(left_minima) else left
    main_right = int(right_minima[0]) if len(right_minima) else right
    sidelobes = np.concatenate((response_db[:main_left], response_db[main_right + 1 :]))
    peak_sidelobe = float(np.max(sidelobes)) if len(sidelobes) else -160.0
    weights = aperture_weights(config.aperture_window, config.element_count)
    coherent_gain_db = 20.0 * math.log10(float(np.sum(weights)) / config.element_count)
    return {
        "beamwidth_3db_deg": beamwidth,
        "peak_sidelobe_db": peak_sidelobe,
        "coherent_gain_db": coherent_gain_db,
    }


def _record_length(config: SonarConfig, reference_length: int) -> int:
    propagation = 2.0 * config.max_range_m / config.sound_speed_mps
    return math.ceil(propagation * config.baseband_sample_rate_hz) + reference_length + 4


def synthesize_channels(
    config: SonarConfig,
    targets: list[SonarTarget],
    sequence: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    """Generate one configurable-array complex-baseband receive frame."""
    config.validate()
    reference = chirp_reference(config)
    sample_count = _record_length(config, len(reference))
    sample_time_s = np.arange(sample_count, dtype=np.float64) / config.baseband_sample_rate_hz
    channels = np.zeros((config.element_count, sample_count), dtype=np.complex64)
    positions = element_positions(config)
    rng = np.random.default_rng(config.random_seed + sequence)
    gain = 10.0 ** (
        rng.normal(0.0, config.gain_error_std_db, config.element_count) / 20.0
    )
    phase_error = np.radians(
        rng.normal(0.0, config.phase_error_std_deg, config.element_count)
    )
    skew_s = rng.normal(
        0.0, config.timing_skew_std_ns * 1e-9, config.element_count
    )
    slope = config.chirp_bandwidth_hz / config.chirp_duration_s
    for target in targets:
        if not target.enabled or target.range_m <= 0.0:
            continue
        theta = math.radians(target.elevation_deg)
        target_x = target.range_m * math.cos(theta)
        target_z = target.range_m * math.sin(theta)
        receive_distance = np.hypot(target_x, target_z - positions)
        delay_s = (target.range_m + receive_distance) / config.sound_speed_mps + skew_s
        for channel_index in range(config.element_count):
            local_time = sample_time_s - delay_s[channel_index]
            active = (local_time >= 0.0) & (local_time < config.chirp_duration_s)
            if not np.any(active):
                continue
            chirp_time = local_time[active]
            if config.waveform_window == "tukey":
                window_index = np.minimum(
                    (chirp_time * config.baseband_sample_rate_hz).astype(int),
                    len(reference) - 1,
                )
                envelope = np.abs(reference[window_index])
            elif config.waveform_window == "hann":
                envelope = 0.5 - 0.5 * np.cos(
                    2.0 * np.pi * chirp_time / config.chirp_duration_s
                )
            else:
                envelope = 1.0
            chirp_phase = 2.0 * np.pi * (
                -0.5 * config.chirp_bandwidth_hz * chirp_time
                + 0.5 * slope * chirp_time**2
            )
            carrier_phase = (
                -2.0 * np.pi * config.center_frequency_hz * delay_s[channel_index]
                + phase_error[channel_index]
            )
            channels[channel_index, active] += np.asarray(
                target.reflectivity
                * gain[channel_index]
                * envelope
                * np.exp(1j * (chirp_phase + carrier_phase)),
                dtype=np.complex64,
            )
    if config.noise_rms:
        noise = rng.normal(size=channels.shape) + 1j * rng.normal(size=channels.shape)
        channels += np.asarray(noise * (config.noise_rms / math.sqrt(2.0)), dtype=np.complex64)
    for channel_index in config.failed_channels:
        channels[channel_index] = 0.0
    if config.enable_quantization:
        limit = (1 << (config.adc_bits - 1)) - 1
        real = np.rint(np.clip(channels.real, -1.0, 1.0) * limit) / limit
        imaginary = np.rint(np.clip(channels.imag, -1.0, 1.0) * limit) / limit
        channels = np.asarray(real + 1j * imaginary, dtype=np.complex64)
    return channels, reference


def matched_filter(channels: np.ndarray, reference: np.ndarray) -> np.ndarray:
    """Pulse-compress every channel using one batched FFT correlation."""
    required = channels.shape[1] + len(reference) - 1
    fft_length = 1 << (required - 1).bit_length()
    channel_spectrum = np.fft.fft(channels, fft_length, axis=1)
    reference_spectrum = np.fft.fft(reference, fft_length)
    correlation = np.fft.ifft(
        channel_spectrum * np.conj(reference_spectrum)[None, :], axis=1
    )
    energy = max(float(np.vdot(reference, reference).real), 1e-12)
    return np.asarray(correlation[:, : channels.shape[1]] / energy, dtype=np.complex64)


def beamform(
    config: SonarConfig,
    matched_channels: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    elevations = np.linspace(
        config.elevation_min_deg, config.elevation_max_deg, config.beam_count
    )
    values = steering_matrix(config, elevations) @ matched_channels
    return np.asarray(values, dtype=np.complex64), elevations


def range_gate_for_beamforming(
    matched_channels: np.ndarray,
    ranges_m: np.ndarray,
    count: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Select one coherent channel vector from each displayed range interval.

    The common sample index is chosen from summed receive-channel power.  All
    complex channel values at that index are retained, so the inter-element
    phase needed by the beamformer is not disturbed.  This bounds beamforming
    work without independently max-pooling channels and corrupting phase.
    """
    if matched_channels.ndim != 2 or matched_channels.shape[1] != len(ranges_m):
        raise ValueError("matched channel and range dimensions do not agree")
    if count < 1 or count > matched_channels.shape[1]:
        raise ValueError("range gate count must fit the available samples")
    power = np.sum(np.abs(matched_channels) ** 2, axis=0)
    edges = np.linspace(0, matched_channels.shape[1], count + 1, dtype=int)
    indices = np.empty(count, dtype=int)
    for index in range(count):
        start = edges[index]
        stop = max(start + 1, edges[index + 1])
        indices[index] = start + int(np.argmax(power[start:stop]))
    return matched_channels[:, indices], ranges_m[indices]


def _max_pool(values: np.ndarray, count: int) -> np.ndarray:
    edges = np.linspace(0, values.shape[-1], count + 1, dtype=int)
    if np.any(np.diff(edges) < 1):
        raise ValueError("pool count cannot exceed the input length")
    return np.maximum.reduceat(values, edges[:-1], axis=-1)


@functools.lru_cache(maxsize=32)
def _cached_array_products(
    element_count: int,
    pitch_m: float,
    center_frequency_hz: float,
    sound_speed_mps: float,
    aperture_window: str,
) -> tuple[dict[str, float], np.ndarray, np.ndarray]:
    config = SonarConfig(
        element_count=element_count,
        pitch_m=pitch_m,
        center_frequency_hz=center_frequency_hz,
        sound_speed_mps=sound_speed_mps,
        aperture_window=aperture_window,
    )
    metrics = array_metrics(config)
    angles, response_db = array_pattern(
        config, scan_deg=np.linspace(-90.0, 90.0, 721)
    )
    angles.flags.writeable = False
    response_db.flags.writeable = False
    return metrics, angles, response_db


def _find_detections(
    image_db: np.ndarray,
    elevations: np.ndarray,
    ranges_m: np.ndarray,
    threshold_db: float,
    maximum_count: int = 64,
) -> list[dict]:
    """Return local image peaks instead of one duplicate per sampled beam."""
    candidates = image_db >= threshold_db
    candidates[1:, :] &= image_db[1:, :] > image_db[:-1, :]
    candidates[:-1, :] &= image_db[:-1, :] >= image_db[1:, :]
    candidates[:, 1:] &= image_db[:, 1:] > image_db[:, :-1]
    candidates[:, :-1] &= image_db[:, :-1] >= image_db[:, 1:]
    beam_indices, range_indices = np.nonzero(candidates)
    if not len(beam_indices):
        return []
    strengths = image_db[beam_indices, range_indices]
    order = np.argsort(strengths)[::-1][:maximum_count]
    detections = []
    for candidate_index in order:
        beam_index = int(beam_indices[candidate_index])
        range_index = int(range_indices[candidate_index])
        peak_db = float(strengths[candidate_index])
        detections.append(
            {
                "elevation_deg": round(float(elevations[beam_index]), 2),
                "range_m": round(float(ranges_m[range_index]), 3),
                "peak_db": round(peak_db, 2),
                "confidence": round(
                    float(
                        np.clip(
                            (peak_db - threshold_db) / max(-threshold_db, 1.0),
                            0.0,
                            1.0,
                        )
                    ),
                    3,
                ),
            }
        )
    return detections


def _u8_base64(values: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(values, dtype=np.uint8)).decode("ascii")


def process_ping(
    config: SonarConfig,
    targets: list[SonarTarget],
    sequence: int = 0,
) -> SonarResult:
    """Run synthesis, matched filtering, and beamforming for one ping."""
    started = time.perf_counter()
    channels, reference = synthesize_channels(config, targets, sequence)
    after_synthesis = time.perf_counter()
    matched = matched_filter(channels, reference)
    after_matched = time.perf_counter()
    range_axis = (
        np.arange(matched.shape[1], dtype=np.float64)
        * config.sound_speed_mps
        / (2.0 * config.baseband_sample_rate_hz)
    )
    selected = (range_axis >= config.min_range_m) & (range_axis <= config.max_range_m)
    selected_matched = matched[:, selected]
    selected_ranges = range_axis[selected]
    range_bin_count = min(config.beamforming_range_bins, selected_matched.shape[1])
    beamformer_input, beamformer_ranges = range_gate_for_beamforming(
        selected_matched, selected_ranges, range_bin_count
    )
    after_range_gating = time.perf_counter()
    selected_beams, elevations = beamform(config, beamformer_input)
    after_beamforming = time.perf_counter()
    selected_ranges = beamformer_ranges
    magnitude = np.abs(selected_beams)
    reference_magnitude = max(float(np.max(magnitude)), 1e-12)
    image_db = 20.0 * np.log10(np.maximum(magnitude / reference_magnitude, 1e-8))
    pooled = _max_pool(image_db, config.range_pixel_count)
    image_u8 = np.asarray(
        np.clip(
            (pooled + config.dynamic_range_db) * 255.0 / config.dynamic_range_db,
            0.0,
            255.0,
        ),
        dtype=np.uint8,
    )
    detections = _find_detections(
        image_db,
        elevations,
        selected_ranges,
        config.detection_threshold_db,
    )
    metrics, pattern_angles, pattern_db = _cached_array_products(
        config.element_count,
        config.pitch_m,
        config.center_frequency_hz,
        config.sound_speed_mps,
        config.aperture_window,
    )
    target_errors = []
    for target in targets:
        if not target.enabled:
            continue
        angle_index = int(np.argmin(np.abs(elevations - target.elevation_deg)))
        search_half_width = max(2, round(0.15 / (config.sound_speed_mps / (2.0 * config.baseband_sample_rate_hz))))
        expected = int(np.argmin(np.abs(selected_ranges - target.range_m)))
        start = max(0, expected - search_half_width)
        stop = min(len(selected_ranges), expected + search_half_width + 1)
        local = magnitude[:, start:stop]
        peak_flat = int(np.argmax(local))
        peak_beam, peak_range_local = np.unravel_index(peak_flat, local.shape)
        peak_range = start + peak_range_local
        target_errors.append(
            {
                "label": target.label,
                "range_error_m": round(float(selected_ranges[peak_range] - target.range_m), 4),
                "elevation_error_deg": round(float(elevations[peak_beam] - target.elevation_deg), 3),
                "nearest_display_beam_deg": round(float(elevations[angle_index]), 3),
            }
        )
    pattern_u8 = np.asarray(
        np.clip((np.maximum(pattern_db, -60.0) + 60.0) * 255.0 / 60.0, 0, 255),
        dtype=np.uint8,
    )
    raw_trace = _max_pool(np.abs(channels[config.element_count // 2]), 512)
    matched_trace = _max_pool(np.abs(matched[config.element_count // 2]), 512)
    raw_trace = np.asarray(raw_trace / max(float(np.max(raw_trace)), 1e-12) * 255.0, dtype=np.uint8)
    matched_trace = np.asarray(
        matched_trace / max(float(np.max(matched_trace)), 1e-12) * 255.0,
        dtype=np.uint8,
    )
    finished = time.perf_counter()
    public = {
        "schema_version": 1,
        "application": "cora-sonar",
        "status": "running",
        "sequence": sequence,
        "generated_at": time.time(),
        "pipeline": [
            f"{config.element_count}-channel complex baseband time series",
            "per-channel FFT matched filter",
            f"coherent range gating to {range_bin_count} processing bins",
            f"{config.beam_count}-direction delay-and-sum beamformer",
            "magnitude, log compression, detection",
        ],
        "config": asdict(config),
        "targets": [asdict(target) for target in targets],
        "image": {
            "encoding": "base64-u8",
            "data": _u8_base64(image_u8),
            "beams": config.beam_count,
            "range_pixels": config.range_pixel_count,
            "range_min_m": config.min_range_m,
            "range_max_m": config.max_range_m,
            "elevation_min_deg": config.elevation_min_deg,
            "elevation_max_deg": config.elevation_max_deg,
        },
        "traces": {
            "raw_channel_16": _u8_base64(raw_trace),
            "matched_channel_16": _u8_base64(matched_trace),
            "sample_count": int(channels.shape[1]),
            "samples_per_trace": 512,
        },
        "array_pattern": {
            "data": _u8_base64(pattern_u8),
            "points": len(pattern_u8),
            "angle_min_deg": float(pattern_angles[0]),
            "angle_max_deg": float(pattern_angles[-1]),
            "floor_db": -60.0,
        },
        "detections": detections,
        "target_errors": target_errors,
        "metrics": {
            **metrics,
            "theoretical_range_resolution_m": config.sound_speed_mps
            / (2.0 * config.chirp_bandwidth_hz),
            "range_sample_spacing_m": config.sound_speed_mps
            / (2.0 * config.baseband_sample_rate_hz),
            "array_aperture_m": (config.element_count - 1) * config.pitch_m,
            "wavelength_m": config.sound_speed_mps / config.center_frequency_hz,
            "pitch_wavelength_ratio": config.pitch_m
            * config.center_frequency_hz
            / config.sound_speed_mps,
            "spatial_aliasing": config.pitch_m * config.center_frequency_hz
            / config.sound_speed_mps
            > 0.5,
            "raw_channel_samples": int(channels.size),
            "raw_frame_bytes_complex64": int(channels.nbytes),
            "equivalent_4msps_adc_mib_per_ping_20m": round(
                6.83 * config.element_count / 32.0, 3
            ),
            "synthesis_ms": round((after_synthesis - started) * 1000.0, 2),
            "matched_filter_ms": round((after_matched - after_synthesis) * 1000.0, 2),
            "range_gating_ms": round((after_range_gating - after_matched) * 1000.0, 2),
            "beamforming_ms": round((after_beamforming - after_range_gating) * 1000.0, 2),
            "beamforming_range_bins": int(range_bin_count),
            "total_processing_ms": round((finished - started) * 1000.0, 2),
        },
    }
    return SonarResult(
        sequence=sequence,
        generated_at=public["generated_at"],
        channels=channels,
        matched_channels=matched,
        beamformed=selected_beams,
        range_axis_m=selected_ranges,
        elevation_axis_deg=elevations,
        public=public,
    )


def scenario_targets(name: str) -> list[SonarTarget]:
    if name == "point_targets":
        return [
            SonarTarget(6.0, 0.0, 1.0, "center obstacle"),
            SonarTarget(9.0, 18.0, 0.75, "upper obstacle"),
            SonarTarget(10.5, -24.0, 0.65, "lower obstacle"),
        ]
    if name == "close_pair":
        return [
            SonarTarget(8.0, -3.0, 1.0, "lower member"),
            SonarTarget(8.0, 3.0, 1.0, "upper member"),
            SonarTarget(8.06, 20.0, 0.8, "range-separated target"),
        ]
    if name == "obstacle_course":
        return [
            SonarTarget(3.8, -28.0, 0.45, "near lower edge"),
            SonarTarget(5.2, -10.0, 0.9, "centerline hazard"),
            SonarTarget(7.4, 9.0, 0.65, "mid-water obstacle"),
            SonarTarget(10.8, 31.0, 0.8, "overhang"),
        ]
    if name == "sloped_bottom":
        return [
            SonarTarget(
                4.0 + 0.17 * index,
                -42.0 + 1.45 * index,
                0.24 + 0.04 * math.cos(index),
                f"bottom-{index:02d}",
            )
            for index in range(24)
        ]
    raise ValueError(f"unknown scenario: {name}")


def editable_targets(config: SonarConfig, values: object) -> list[SonarTarget]:
    """Validate JSON-compatible scene targets supplied by the dashboard."""
    if not isinstance(values, list):
        raise ValueError("scene targets must be a list")
    if len(values) > MAX_SCENE_TARGETS:
        raise ValueError(f"a scene may contain at most {MAX_SCENE_TARGETS} targets")
    targets = []
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            raise ValueError(f"target {index + 1} must be an object")
        label = str(value.get("label", f"target-{index + 1:02d}")).strip()
        if not label or len(label) > 64:
            raise ValueError(f"target {index + 1} label must contain 1 to 64 characters")
        try:
            range_m = float(value["range_m"])
            elevation_deg = float(value["elevation_deg"])
            reflectivity = float(value.get("reflectivity", 1.0))
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"target {index + 1} has invalid numeric fields") from error
        enabled = value.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError(f"target {index + 1} enabled must be true or false")
        if not config.min_range_m <= range_m <= config.max_range_m:
            raise ValueError(
                f"target {index + 1} range must be {config.min_range_m:g} to "
                f"{config.max_range_m:g} m"
            )
        if not config.elevation_min_deg <= elevation_deg <= config.elevation_max_deg:
            raise ValueError(
                f"target {index + 1} elevation must be "
                f"{config.elevation_min_deg:g} to {config.elevation_max_deg:g} degrees"
            )
        if not 0.0 <= reflectivity <= 2.0:
            raise ValueError(f"target {index + 1} reflectivity must be 0 to 2")
        targets.append(
            SonarTarget(
                range_m=range_m,
                elevation_deg=elevation_deg,
                reflectivity=reflectivity,
                label=label,
                enabled=enabled,
            )
        )
    return targets


class SonarEngine:
    """Background ping engine shared by the dashboard and smoke test."""

    def __init__(self, config: SonarConfig | None = None):
        self.config = config or SonarConfig()
        self.config.validate()
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.running = False
        self.paused = False
        self.sequence = 0
        self.scenario = "point_targets"
        self.targets = scenario_targets(self.scenario)
        self.latest: SonarResult | None = None
        self.last_error: str | None = None

    def process_once(self) -> SonarResult:
        with self.lock:
            config = replace(self.config)
            targets = [replace(target) for target in self.targets]
            sequence = self.sequence
            self.sequence += 1
        result = process_ping(config, targets, sequence)
        with self.lock:
            self.latest = result
            self.last_error = None
        return result

    def reset(self) -> None:
        with self.lock:
            self.sequence = 0
            self.latest = None
            self.last_error = None
        self.process_once()

    def start(self) -> None:
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                self.paused = False
                self.running = True
                return
            self.stop_event.clear()
            self.paused = False
            self.running = True
            self.thread = threading.Thread(target=self._run, name="cora-sonar", daemon=True)
            self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        thread = self.thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5.0)
        with self.lock:
            self.running = False
            self.thread = None

    def set_paused(self, paused: bool) -> None:
        with self.lock:
            self.paused = bool(paused)
            self.running = not self.paused

    def set_scenario(self, name: str) -> None:
        targets = scenario_targets(name)
        with self.lock:
            self.scenario = name
            self.targets = targets
        self.process_once()

    def set_targets(self, values: object) -> None:
        with self.lock:
            config = replace(self.config)
        targets = editable_targets(config, values)
        with self.lock:
            self.scenario = "custom"
            self.targets = targets
        self.process_once()

    def configure(self, values: dict) -> None:
        allowed = {
            "max_range_m": float,
            "noise_rms": float,
            "dynamic_range_db": float,
            "detection_threshold_db": float,
            "ping_rate_hz": float,
            "gain_error_std_db": float,
            "phase_error_std_deg": float,
            "timing_skew_std_ns": float,
            "aperture_window": str,
            "element_count": int,
            "center_frequency_hz": float,
            "pitch_m": float,
            "beam_count": int,
            "beamforming_range_bins": int,
        }
        with self.lock:
            candidate = replace(self.config)
            for name, value in values.items():
                if name not in allowed:
                    raise ValueError(f"unsupported configuration field: {name}")
                setattr(candidate, name, allowed[name](value))
            candidate.validate()
            self.config = candidate
        self.process_once()

    def status(self) -> dict:
        with self.lock:
            if self.latest is None:
                return {
                    "schema_version": 1,
                    "application": "cora-sonar",
                    "status": "starting" if self.running else "idle",
                    "scenario": self.scenario,
                    "error": self.last_error,
                }
            values = dict(self.latest.public)
            values["status"] = "paused" if self.paused else ("running" if self.running else "idle")
            values["scenario"] = self.scenario
            values["error"] = self.last_error
            return values

    def capture_npz(self) -> bytes:
        with self.lock:
            if self.latest is None:
                raise RuntimeError("no sonar ping is available")
            latest = self.latest
            config_json = json.dumps(asdict(self.config), separators=(",", ":"))
        output = io.BytesIO()
        np.savez_compressed(
            output,
            channels=latest.channels,
            matched_channels=latest.matched_channels,
            beamformed=latest.beamformed,
            range_axis_m=latest.range_axis_m,
            elevation_axis_deg=latest.elevation_axis_deg,
            config_json=np.asarray(config_json),
        )
        return output.getvalue()

    def _run(self) -> None:
        deadline = time.monotonic()
        while not self.stop_event.is_set():
            with self.lock:
                paused = self.paused
                rate = self.config.ping_rate_hz
            if paused:
                self.stop_event.wait(0.1)
                deadline = time.monotonic()
                continue
            try:
                self.process_once()
            except Exception as error:  # keep the service observable on target
                with self.lock:
                    self.last_error = str(error)
                self.stop_event.wait(0.5)
            deadline += 1.0 / rate
            delay = deadline - time.monotonic()
            if delay > 0:
                self.stop_event.wait(delay)
            else:
                deadline = time.monotonic()
        with self.lock:
            self.running = False
