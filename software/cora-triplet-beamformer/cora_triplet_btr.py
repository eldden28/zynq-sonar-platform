#!/usr/bin/env python3
"""Real-time 21-channel triplet bearing-time record."""

from __future__ import annotations

import base64
from dataclasses import asdict, dataclass, replace
import threading
import time

import numpy as np

from cora_triplet_adc import AdcSourceStopped, AxiDmaAdcSource
from cora_triplet_beamformer import Optimizer, solve_empirical


SAMPLE_RATE_HZ = 48_000
MAX_ACOUSTIC_FREQUENCY_HZ = 20_000.0
CARDIOID_CROSSFADE_HALF_WIDTH_DEG = 3.0
SPECTRUM_FLOOR_DBFS = -100.0
SPECTRUM_REFERENCE_CHANNEL = 10


@dataclass
class BtrConfig:
    sample_rate_hz: int = SAMPLE_RATE_HZ
    block_size: int = 1024
    carrier_hz: float = 8_000.0
    sound_speed_m_s: float = 1500.0
    triplet_count: int = 7
    triplet_side_m: float = 0.030
    triplet_spacing_m: float = 0.0762
    bearing_min_deg: float = -180.0
    bearing_max_deg: float = 180.0
    bearing_count: int = 361
    history_columns: int = 600
    dynamic_range_db: float = 45.0
    noise_rms: float = 0.025
    primary_bearing_deg: float = 35.0
    primary_level: float = 0.62
    secondary_bearing_deg: float = -125.0
    secondary_level: float = 0.32
    motion_enabled: bool = True
    random_seed: int = 7010

    def validate(self) -> None:
        if self.sample_rate_hz != SAMPLE_RATE_HZ:
            raise ValueError("the live BTR sample rate is fixed at 48 ksample/s")
        if self.block_size not in (512, 1024, 2048, 4096):
            raise ValueError("block_size must be 512, 1024, 2048, or 4096")
        if not 250.0 <= self.carrier_hz <= MAX_ACOUSTIC_FREQUENCY_HZ:
            raise ValueError("carrier_hz must be between 250 Hz and 20 kHz")
        if self.carrier_hz >= self.sample_rate_hz / 2.0:
            raise ValueError("carrier_hz must remain below Nyquist")
        if self.sound_speed_m_s <= 0.0:
            raise ValueError("sound_speed_m_s must be positive")
        if self.triplet_count != 7:
            raise ValueError("this profile is fixed at seven triplets (21 channels)")
        if min(self.triplet_side_m, self.triplet_spacing_m) <= 0.0:
            raise ValueError("triplet dimensions must be positive")
        if not self.bearing_min_deg < self.bearing_max_deg:
            raise ValueError("bearing limits are invalid")
        if not 31 <= self.bearing_count <= 721:
            raise ValueError("bearing_count must be between 31 and 721")
        if not 60 <= self.history_columns <= 2400:
            raise ValueError("history_columns must be between 60 and 2400")
        if not 20.0 <= self.dynamic_range_db <= 80.0:
            raise ValueError("dynamic_range_db must be between 20 and 80 dB")
        if not 0.0 <= self.noise_rms <= 0.25:
            raise ValueError("noise_rms must be between zero and 0.25")
        for bearing in (self.primary_bearing_deg, self.secondary_bearing_deg):
            if not self.bearing_min_deg <= bearing <= self.bearing_max_deg:
                raise ValueError("source bearings must lie inside the displayed sector")
        for level in (self.primary_level, self.secondary_level):
            if not 0.0 <= level <= 1.0:
                raise ValueError("source levels must be between zero and one")


def triplet_local_positions(side_m: float) -> np.ndarray:
    """Return channel-ordered XYZ offsets in the transverse YZ plane.

    Ch1 is upper-starboard, Ch2 is down, and Ch3 is upper-port. The triangle
    is equilateral and its centroid is at the origin.
    """
    radius = side_m / np.sqrt(3.0)
    return np.asarray(
        (
            (0.0, side_m / 2.0, radius / 2.0),
            (0.0, 0.0, -radius),
            (0.0, -side_m / 2.0, radius / 2.0),
        ),
        dtype=np.float64,
    )


def array_positions(config: BtrConfig) -> np.ndarray:
    """Return 21 XYZ locations along X with aligned transverse triplets."""
    local = triplet_local_positions(config.triplet_side_m)
    center_x = (
        np.arange(config.triplet_count, dtype=np.float64)
        - (config.triplet_count - 1) / 2.0
    ) * config.triplet_spacing_m
    centers = np.column_stack(
        (center_x, np.zeros(config.triplet_count), np.zeros(config.triplet_count))
    )
    return (centers[:, None, :] + local[None, :, :]).reshape(-1, 3)


def direction_vector(bearing_deg: float, elevation_deg: float = 0.0) -> np.ndarray:
    """Return XYZ arrival direction; 0 deg bearing is starboard (+Y)."""
    bearing = np.deg2rad(bearing_deg)
    elevation = np.deg2rad(elevation_deg)
    horizontal = np.cos(elevation)
    return np.asarray(
        (
            horizontal * np.sin(bearing),
            horizontal * np.cos(bearing),
            np.sin(elevation),
        ),
        dtype=np.float64,
    )


def cardioid_starboard_mix(
    bearings_deg: np.ndarray, half_width_deg: float = CARDIOID_CROSSFADE_HALF_WIDTH_DEG
) -> np.ndarray:
    """Return a smooth starboard-mode power fraction around both endfire axes."""
    if not 0.0 < half_width_deg < 90.0:
        raise ValueError("cardioid crossfade half-width must be between 0 and 90 degrees")
    boundary = np.sin(np.deg2rad(half_width_deg))
    normalized = np.clip(
        (np.cos(np.deg2rad(bearings_deg)) + boundary) / (2.0 * boundary),
        0.0,
        1.0,
    )
    return normalized * normalized * (3.0 - 2.0 * normalized)


def ideal_transverse_triplet_sweep(config: BtrConfig) -> tuple[np.ndarray, np.ndarray]:
    """Return an ideal 360-degree sweep around one physical triplet cross-section."""
    bearings = np.arange(0.0, 360.0, 5.0)
    radians = np.deg2rad(bearings)
    directions = np.column_stack(
        (np.zeros(len(bearings)), np.cos(radians), np.sin(radians))
    )
    wave_number = 2.0 * np.pi * config.carrier_hz / config.sound_speed_m_s
    response = np.exp(
        1j
        * wave_number
        * directions
        @ triplet_local_positions(config.triplet_side_m).T
    )
    return bearings, response


def source_bearings(config: BtrConfig, elapsed_s: float) -> tuple[float, float]:
    if not config.motion_enabled:
        return config.primary_bearing_deg, config.secondary_bearing_deg
    primary = config.primary_bearing_deg + 24.0 * np.sin(2.0 * np.pi * elapsed_s / 18.0)
    secondary = config.secondary_bearing_deg + 12.0 * np.sin(2.0 * np.pi * elapsed_s / 27.0 + 1.1)
    low, high = config.bearing_min_deg + 2.0, config.bearing_max_deg - 2.0
    return float(np.clip(primary, low, high)), float(np.clip(secondary, low, high))


class BtrEngine:
    """Acquire synchronized ADC blocks and append beam powers to a BTR ring buffer."""

    def __init__(
        self,
        config: BtrConfig | None = None,
        adc_source: AxiDmaAdcSource | None = None,
    ):
        self.config = config or BtrConfig()
        self.config.validate()
        self.adc_source = adc_source
        if self.adc_source is not None:
            self.adc_source.layout.validate(self.config.block_size)
            if self.adc_source.block_size != self.config.block_size:
                raise ValueError("AXI source block size does not match BTR configuration")
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.running = False
        self.paused = False
        self.sequence = 0
        self.sample_index = 0
        self.started_monotonic = time.monotonic()
        self.rng = np.random.default_rng(self.config.random_seed)
        self.source_phase_offsets = self.rng.uniform(-np.pi, np.pi, 2)
        self.history = np.zeros(
            (self.config.bearing_count, self.config.history_columns), dtype=np.uint8
        )
        self.history_cursor = 0
        self.history_count = 0
        self.latest_spectrum = np.zeros(
            self.config.block_size // 2 + 1, dtype=np.uint8
        )
        self.latest: dict | None = None
        self.last_error: str | None = None
        self._prepare_beamformer()

    def _prepare_beamformer(self) -> None:
        config = self.config
        bearings, response = ideal_transverse_triplet_sweep(config)
        starboard_solution = solve_empirical(
            bearings,
            response,
            Optimizer(steering_deg=0.0, regularization=0.001),
        )[0]
        port_solution = solve_empirical(
            bearings,
            response,
            Optimizer(steering_deg=180.0, regularization=0.001),
        )[0]
        self.triplet_weights = np.asarray(
            [starboard_solution.weights, port_solution.weights], dtype=np.complex64
        )
        self.bearings_deg = np.linspace(
            config.bearing_min_deg,
            config.bearing_max_deg,
            config.bearing_count,
            dtype=np.float64,
        )
        center_x = (
            np.arange(config.triplet_count, dtype=np.float64)
            - (config.triplet_count - 1) / 2.0
        ) * config.triplet_spacing_m
        wave_number = 2.0 * np.pi * config.carrier_hz / config.sound_speed_m_s
        self.line_weights = np.asarray(
            np.exp(
                -1j
                * wave_number
                * np.sin(np.deg2rad(self.bearings_deg))[:, None]
                * center_x
            )
            / config.triplet_count,
            dtype=np.complex64,
        )
        self.starboard_power_mix = cardioid_starboard_mix(self.bearings_deg).astype(
            np.float32
        )
        taper = np.hanning(config.block_size).astype(np.float32)
        self.spectrum_window = taper
        self.spectrum_scale = 2.0 / max(float(taper.sum()), 1e-12)
        sample_offset = np.arange(config.block_size, dtype=np.float64)
        oscillator = np.exp(
            -2j * np.pi * config.carrier_hz * sample_offset / config.sample_rate_hz
        ).astype(np.complex64)
        self.extractor = oscillator * taper * (2.0 / max(float(taper.sum()), 1e-12))
        carrier_phase = 2.0 * np.pi * config.carrier_hz * sample_offset / config.sample_rate_hz
        self.carrier_cos = np.cos(carrier_phase).astype(np.float32)
        self.carrier_sin = np.sin(carrier_phase).astype(np.float32)
        self.carrier_basis = np.column_stack((self.carrier_cos, self.carrier_sin))
        # Generating Gaussian noise is disproportionately expensive on the Cortex-A9.
        # A small deterministic int8 ring preserves a sample-domain ADC path while
        # keeping the synthetic source from consuming the real-time budget that the
        # physical ADC will eventually replace.
        self.noise_ring = np.clip(
            self.rng.normal(0.0, 32.0, (16, config.block_size, 21)),
            -127.0,
            127.0,
        ).astype(np.int8)
        self.positions = array_positions(config)

    def _synthesize(
        self,
        config: BtrConfig,
        bearings: tuple[float, float],
        positions: np.ndarray,
        sequence: int,
    ) -> np.ndarray:
        samples = self.noise_ring[sequence % len(self.noise_ring)].astype(np.float32)
        samples *= config.noise_rms / 32.0
        coefficients = np.zeros((2, 21), dtype=np.float32)
        block_phase = (
            2.0
            * np.pi
            * config.carrier_hz
            * self.sample_index
            / config.sample_rate_hz
        )
        for bearing_deg, level, phase_offset in zip(
            bearings,
            (config.primary_level, config.secondary_level),
            self.source_phase_offsets,
        ):
            if level <= 0.0:
                continue
            direction = direction_vector(bearing_deg)
            spatial_phase = (
                2.0
                * np.pi
                * config.carrier_hz
                / config.sound_speed_m_s
                * (positions @ direction)
            )
            source_phase = block_phase + phase_offset + spatial_phase
            coefficients[0] += level * np.cos(source_phase)
            coefficients[1] -= level * np.sin(source_phase)
        samples += self.carrier_basis @ coefficients
        peak = float(np.max(np.abs(samples)))
        if peak > 0.98:
            samples *= 0.98 / peak
        adc = np.rint(samples * 32767.0).astype(np.int16)
        return adc

    def process_once(self) -> dict:
        started = time.perf_counter()
        with self.lock:
            config = replace(self.config)
            sequence = self.sequence
            sample_index = self.sample_index
            positions = self.positions
            extractor = self.extractor
            triplet_weights = self.triplet_weights
            line_weights = self.line_weights
            starboard_power_mix = self.starboard_power_mix
            bearings_deg = self.bearings_deg
            spectrum_window = self.spectrum_window
            spectrum_scale = self.spectrum_scale
        if self.adc_source is None:
            elapsed_s = sample_index / config.sample_rate_hz
            sources = source_bearings(config, elapsed_s)
            adc = self._synthesize(config, sources, positions, sequence)
            full_scale = 32768.0
            sample_bytes = 2
        else:
            sources = None
            adc = self.adc_source.read_block(self.stop_event)
            full_scale = self.adc_source.full_scale
            sample_bytes = self.adc_source.sample_bytes
        after_acquisition = time.perf_counter()
        if adc.shape != (config.block_size, config.triplet_count * 3):
            raise ValueError(
                "ADC source returned shape "
                f"{adc.shape}; expected {(config.block_size, config.triplet_count * 3)}"
            )
        normalized = adc.astype(np.float32) / full_scale
        element_rms = np.sqrt(np.mean(normalized * normalized, axis=0))
        element_rms_dbfs = 20.0 * np.log10(np.maximum(element_rms, 1e-12))
        reference_spectrum = np.abs(
            np.fft.rfft(
                normalized[:, SPECTRUM_REFERENCE_CHANNEL] * spectrum_window
            )
        ) * spectrum_scale
        reference_spectrum[0] *= 0.5
        reference_spectrum[-1] *= 0.5
        spectrum_dbfs = 20.0 * np.log10(np.maximum(reference_spectrum, 1e-12))
        spectrum_u8 = np.rint(
            np.clip(
                (spectrum_dbfs - SPECTRUM_FLOOR_DBFS) / -SPECTRUM_FLOOR_DBFS,
                0.0,
                1.0,
            )
            * 255.0
        ).astype(np.uint8)
        channel_phasors = extractor @ normalized
        triplet_modes = channel_phasors.reshape(config.triplet_count, 3) @ triplet_weights.T
        starboard_beams = line_weights @ triplet_modes[:, 0]
        port_beams = line_weights @ triplet_modes[:, 1]
        starboard_power = np.abs(starboard_beams) ** 2
        port_power = np.abs(port_beams) ** 2
        power = (
            starboard_power_mix * starboard_power
            + (1.0 - starboard_power_mix) * port_power
        )
        peak_power = max(float(np.max(power)), 1e-20)
        response_db = 10.0 * np.log10(np.maximum(power / peak_power, 1e-12))
        intensity = np.rint(
            np.clip((response_db + config.dynamic_range_db) / config.dynamic_range_db, 0.0, 1.0)
            * 255.0
        ).astype(np.uint8)
        finished = time.perf_counter()
        acquisition_s = after_acquisition - started
        beamforming_s = finished - after_acquisition
        processing_s = finished - started if self.adc_source is None else beamforming_s
        frame_period_s = config.block_size / config.sample_rate_hz
        peak_index = int(np.argmax(power))
        peak_starboard_mix = float(starboard_power_mix[peak_index])
        if peak_starboard_mix >= 0.999:
            peak_mode = "starboard"
        elif peak_starboard_mix <= 0.001:
            peak_mode = "port"
        else:
            peak_mode = "blend"
        result = {
            "peak_bearing_deg": round(float(bearings_deg[peak_index]), 3),
            "peak_power": peak_power,
            "peak_cardioid_mode": peak_mode,
            "element_rms_dbfs": [round(float(value), 2) for value in element_rms_dbfs],
            "acquisition_ms": round(acquisition_s * 1000.0, 3),
            "synthesis_ms": (
                round(acquisition_s * 1000.0, 3) if self.adc_source is None else 0.0
            ),
            "beamforming_ms": round(beamforming_s * 1000.0, 3),
            "processing_ms": round(processing_s * 1000.0, 3),
            "frame_period_ms": round(frame_period_s * 1000.0, 3),
            "realtime_margin_x": round(frame_period_s / max(processing_s, 1e-12), 3),
            "input_payload_mb_s": round(
                21 * config.sample_rate_hz * sample_bytes / 1_000_000.0, 3
            ),
            "sequence": sequence,
        }
        if sources is not None:
            result["source_bearings_deg"] = [round(value, 3) for value in sources]
        with self.lock:
            self.history[:, self.history_cursor] = intensity
            self.history_cursor = (self.history_cursor + 1) % config.history_columns
            self.history_count = min(self.history_count + 1, config.history_columns)
            self.sequence += 1
            self.sample_index += config.block_size
            self.latest_spectrum = spectrum_u8
            self.latest = result
            self.last_error = None
        return result

    def _ordered_history(self) -> np.ndarray:
        if self.history_count < self.config.history_columns:
            return self.history[:, : self.history_count]
        return np.concatenate(
            (self.history[:, self.history_cursor :], self.history[:, : self.history_cursor]), axis=1
        )

    def status(self) -> dict:
        with self.lock:
            config = replace(self.config)
            latest = dict(self.latest) if self.latest else {}
            history = self._ordered_history().copy()
            spectrum = self.latest_spectrum.copy()
            state = "paused" if self.paused else ("running" if self.running else "idle")
            error = self.last_error
        if self.adc_source is None:
            input_status = {
                "mode": "synthetic",
                "sample_format": "signed-16-le",
                "channel_order": "sample-major",
                "channel_count": 21,
                "frame_samples": config.block_size,
                "frame_bytes": config.block_size * 21 * 2,
                "frames_per_block": 1,
                "word_bytes": 2,
                "payload_mb_s": round(
                    21 * config.sample_rate_hz * 2 / 1_000_000.0, 3
                ),
            }
        else:
            input_status = self.adc_source.status(config.sample_rate_hz)
        return {
            "schema_version": 2,
            "application": "cora-triplet-btr",
            "status": state,
            "sequence": latest.get("sequence", 0),
            "config": asdict(config),
            "input": input_status,
            "array": {
                "channel_count": 21,
                "triplet_count": 7,
                "azimuth_coverage_deg": 360,
                "cardioid_modes": ["starboard", "port"],
                "cardioid_fusion": "smooth-power-crossfade",
                "cardioid_crossfade_half_width_deg": CARDIOID_CROSSFADE_HALF_WIDTH_DEG,
                "coordinate_frame": {
                    "x": "array-axis-forward",
                    "y": "starboard",
                    "z": "up",
                },
                "channel_positions": [
                    "ch1-upper-starboard",
                    "ch2-down",
                    "ch3-upper-port",
                ],
                "triplet_side_m": config.triplet_side_m,
                "triplet_spacing_m": config.triplet_spacing_m,
                "axial_aperture_m": round(
                    (config.triplet_count - 1) * config.triplet_spacing_m, 6
                ),
                "maximum_unaliased_frequency_hz": round(
                    config.sound_speed_m_s / (2.0 * config.triplet_spacing_m), 3
                ),
                "axial_maximum_unaliased_frequency_hz": round(
                    config.sound_speed_m_s / (2.0 * config.triplet_spacing_m), 3
                ),
                "triplet_maximum_unaliased_frequency_hz": round(
                    config.sound_speed_m_s / (2.0 * config.triplet_side_m), 3
                ),
                "signed_elevation_observable": True,
            },
            "metrics": latest,
            "btr": {
                "encoding": "base64-u8",
                "data": base64.b64encode(history.tobytes()).decode("ascii"),
                "bearings": history.shape[0],
                "columns": history.shape[1],
                "bearing_min_deg": config.bearing_min_deg,
                "bearing_max_deg": config.bearing_max_deg,
                "seconds_per_column": config.block_size / config.sample_rate_hz,
            },
            "spectrum": {
                "encoding": "base64-u8",
                "data": base64.b64encode(spectrum.tobytes()).decode("ascii"),
                "bins": len(spectrum),
                "minimum_dbfs": SPECTRUM_FLOOR_DBFS,
                "maximum_dbfs": 0.0,
                "frequency_min_hz": 0.0,
                "frequency_max_hz": config.sample_rate_hz / 2.0,
                "reference_channel": SPECTRUM_REFERENCE_CHANNEL + 1,
                "frame_duration_s": config.block_size / config.sample_rate_hz,
            },
            "error": error,
        }

    def configure(self, values: dict) -> None:
        allowed = {
            "carrier_hz": float,
            "noise_rms": float,
            "primary_bearing_deg": float,
            "primary_level": float,
            "secondary_bearing_deg": float,
            "secondary_level": float,
            "motion_enabled": bool,
            "dynamic_range_db": float,
        }
        with self.lock:
            candidate = replace(self.config)
            for name, value in values.items():
                if name not in allowed:
                    raise ValueError(f"unsupported configuration field: {name}")
                if name == "motion_enabled":
                    if not isinstance(value, bool):
                        raise ValueError("motion_enabled must be a boolean")
                    converted = value
                else:
                    converted = allowed[name](value)
                setattr(candidate, name, converted)
            candidate.validate()
            carrier_changed = candidate.carrier_hz != self.config.carrier_hz
            self.config = candidate
            if carrier_changed:
                self._prepare_beamformer()
            self.history.fill(0)
            self.history_cursor = 0
            self.history_count = 0

    def reset(self) -> None:
        with self.lock:
            self.sequence = 0
            self.sample_index = 0
            self.latest = None
            self.latest_spectrum.fill(0)
            self.last_error = None
            self.history.fill(0)
            self.history_cursor = 0
            self.history_count = 0
            self.rng = np.random.default_rng(self.config.random_seed)
            if self.adc_source is not None:
                self.adc_source.reset_stats()

    def start(self) -> None:
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                self.paused = False
                self.running = True
                return
            self.stop_event.clear()
            self.paused = False
            self.running = True
            self.thread = threading.Thread(target=self._run, name="cora-triplet-btr", daemon=True)
            self.thread.start()

    def set_paused(self, paused: bool) -> None:
        with self.lock:
            self.paused = bool(paused)
            self.running = not self.paused

    def stop(self) -> None:
        self.stop_event.set()
        thread = self.thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        with self.lock:
            self.running = False
            self.thread = None
        if self.adc_source is not None:
            self.adc_source.close()

    def _run(self) -> None:
        deadline = time.monotonic()
        while not self.stop_event.is_set():
            with self.lock:
                paused = self.paused
                period = self.config.block_size / self.config.sample_rate_hz
            if paused:
                if self.adc_source is not None and self.adc_source.externally_paced:
                    try:
                        self.adc_source.read_block(self.stop_event)
                    except AdcSourceStopped:
                        break
                    except Exception as error:
                        with self.lock:
                            self.last_error = str(error)
                        self.stop_event.wait(0.25)
                else:
                    self.stop_event.wait(0.05)
                deadline = time.monotonic()
                continue
            try:
                self.process_once()
            except AdcSourceStopped:
                break
            except Exception as error:
                with self.lock:
                    self.last_error = str(error)
                self.stop_event.wait(0.25)
            if self.adc_source is None or not self.adc_source.externally_paced:
                deadline += period
                delay = deadline - time.monotonic()
                if delay > 0.0:
                    self.stop_event.wait(delay)
                else:
                    deadline = time.monotonic()
        with self.lock:
            self.running = False


# Preserve the original public name for callers that explicitly want the
# default synthetic source.
SyntheticBtrEngine = BtrEngine
