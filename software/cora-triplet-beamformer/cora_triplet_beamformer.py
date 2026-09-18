#!/usr/bin/env python3
"""NumPy-only empirical cardioid beamforming for Cora targets.

The solver is adapted from the Triplet Cardioid Beamformer application's
portable DSP core.  It intentionally has no GUI, SciPy, or recording-format
dependency so it can run in the Cora Z7-10 PetaLinux image.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Any, Sequence

import numpy as np


@dataclass
class Optimizer:
    steering_deg: float = 0.0
    rear_width_deg: float = 90.0
    regularization: float = 0.001
    rear_fit_weight: float = 2.0


@dataclass
class CwConfig:
    sample_rate_hz: float
    tx_channel: int = 0
    receiver_channels: tuple[int, ...] = (1, 2, 3)
    range_m: float = 1.0
    sound_speed_m_s: float = 1500.0
    frequency_hz: float = 0.0
    gate_offset_ms: float = 0.5
    gate_duration_ms: float = 2.0
    onset_threshold: float = 0.2
    tx_correction: float = 1.0


@dataclass
class TransferMeasurement:
    transfer: np.ndarray
    frequency_hz: float
    tx_rms: float
    receiver_rms: np.ndarray
    onset_s: float
    tx_gate_s: float
    rx_gate_s: float
    spectral_concentration: float
    onset_snr_db: float


@dataclass
class BeamSolution:
    name: str
    weights: np.ndarray
    bearings_deg: np.ndarray
    response: np.ndarray
    metrics: dict[str, float | None]
    warnings: list[str]
    reference_gain: float

    def to_json(self) -> dict[str, Any]:
        amplitude = np.abs(self.response)
        peak = max(float(np.max(amplitude)), 1e-30)
        return {
            "name": self.name,
            "weights_real": self.weights.real.tolist(),
            "weights_imag": self.weights.imag.tolist(),
            "bearings_deg": self.bearings_deg.tolist(),
            "response_amplitude": amplitude.tolist(),
            "response_db": (20.0 * np.log10(np.maximum(amplitude / peak, 1e-15))).tolist(),
            "metrics": self.metrics,
            "warnings": self.warnings,
            "reference_gain": self.reference_gain,
        }


def _periodic_hann(length: int) -> np.ndarray:
    if length < 1:
        raise ValueError("window length must be positive")
    return 0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(length) / length)


def _analytic_signal(samples: np.ndarray) -> np.ndarray:
    """Return the Hilbert analytic signal using NumPy's FFT."""
    values = np.asarray(samples, dtype=np.float64)
    spectrum = np.fft.fft(values)
    multiplier = np.zeros(len(values), dtype=np.float64)
    multiplier[0] = 1.0
    if len(values) % 2 == 0:
        multiplier[1 : len(values) // 2] = 2.0
        multiplier[len(values) // 2] = 1.0
    else:
        multiplier[1 : (len(values) + 1) // 2] = 2.0
    return np.fft.ifft(spectrum * multiplier)


def onset(samples: np.ndarray, sample_rate_hz: float, threshold: float) -> tuple[int, float]:
    values = np.asarray(samples, dtype=np.float64)
    if values.ndim != 1 or not len(values):
        raise ValueError("TX samples must be a non-empty one-dimensional array")
    if sample_rate_hz <= 0 or not 0.0 < threshold < 1.0:
        raise ValueError("sample rate must be positive and threshold must be between zero and one")
    centered = values - np.median(values)
    envelope = np.abs(_analytic_signal(centered))
    smooth = max(1, min(len(envelope) // 20, int(sample_rate_hz * 0.00005)))
    envelope = np.convolve(envelope, np.ones(smooth) / smooth, mode="same")
    peak = float(np.max(envelope))
    if peak <= 1e-15:
        raise ValueError("TX channel contains no detectable ping")
    start = int(np.flatnonzero(envelope >= peak * threshold)[0])
    if start <= smooth * 2:
        return start, 0.0
    noise = float(np.median(envelope[: max(1, start - smooth)]))
    confidence = float(20.0 * np.log10(peak / max(noise, 1e-15)))
    return start, confidence


def detect_frequency(samples: np.ndarray, sample_rate_hz: float) -> tuple[float, float]:
    values = np.asarray(samples, dtype=np.float64)
    if values.ndim != 1 or len(values) < 8 or sample_rate_hz <= 0:
        raise ValueError("frequency detection requires at least eight samples and a positive sample rate")
    centered = values - np.mean(values)
    power = np.abs(np.fft.rfft(centered * np.hanning(len(centered)))) ** 2
    power[0] = 0.0
    peak_bin = int(np.argmax(power))
    if peak_bin < 1 or peak_bin >= len(power) - 1 or power[peak_bin] < 1e-25:
        raise ValueError("no valid CW carrier below Nyquist was detected")
    logs = np.log(np.maximum(power[peak_bin - 1 : peak_bin + 2], 1e-30))
    denominator = logs[0] - 2.0 * logs[1] + logs[2]
    delta = 0.5 * (logs[0] - logs[2]) / denominator if abs(denominator) > 1e-12 else 0.0
    frequency = (peak_bin + np.clip(delta, -0.5, 0.5)) * sample_rate_hz / len(centered)
    concentration = float(
        power[max(1, peak_bin - 2) : peak_bin + 3].sum() / max(float(power.sum()), 1e-30)
    )
    return float(frequency), concentration


def phasors(
    samples: np.ndarray,
    sample_rate_hz: float,
    frequency_hz: float,
    start: int,
    stop: int,
) -> np.ndarray:
    values = np.asarray(samples)
    if start < 0 or stop > len(values) or stop - start < 8:
        raise ValueError("analysis gate lies outside the recording or is too short")
    if (
        frequency_hz <= 0
        or frequency_hz >= sample_rate_hz / 2.0
        or (stop - start) * frequency_hz / sample_rate_hz < 3.0
    ):
        raise ValueError("CW analysis requires at least three cycles and a carrier below Nyquist")
    phase = 2.0 * np.pi * frequency_hz * np.arange(start, stop) / sample_rate_hz
    basis = np.column_stack((np.cos(phase), np.sin(phase), np.ones(len(phase))))
    taper = np.sqrt(_periodic_hann(len(phase)))
    target = values[start:stop]
    if target.ndim == 1:
        target = target[:, None]
    if target.ndim != 2:
        raise ValueError("samples must be a one- or two-dimensional array")
    coefficients = np.linalg.lstsq(
        basis * taper[:, None], target * taper[:, None], rcond=None
    )[0]
    return coefficients[0] - 1j * coefficients[1]


def extract_cw_transfer(samples: np.ndarray, config: CwConfig) -> TransferMeasurement:
    """Extract receiver/TX complex transfer values from one multichannel CW ping."""
    values = np.asarray(samples)
    if values.ndim != 2:
        raise ValueError("samples must have shape [time, channels]")
    channels = (config.tx_channel, *config.receiver_channels)
    positive = (
        config.sample_rate_hz,
        config.range_m,
        config.sound_speed_m_s,
        config.gate_duration_ms,
        config.tx_correction,
    )
    if not np.isfinite(positive).all() or min(positive) <= 0:
        raise ValueError("sample rate, range, sound speed, duration, and TX correction must be positive")
    if len(config.receiver_channels) < 2 or min(channels) < 0 or len(set(channels)) != len(channels):
        raise ValueError("select distinct TX and receiver channels, with at least two receivers")
    if max(channels) >= values.shape[1]:
        raise ValueError("selected channel does not exist")

    tx = np.asarray(values[:, config.tx_channel], dtype=np.float64) * config.tx_correction
    start, onset_snr = onset(tx, config.sample_rate_hz, config.onset_threshold)
    tx_start = start + round(config.gate_offset_ms * config.sample_rate_hz / 1000.0)
    size = round(config.gate_duration_ms * config.sample_rate_hz / 1000.0)
    if tx_start < 0 or tx_start + size > len(tx):
        raise ValueError("TX gate is outside the recording")
    detected, concentration = detect_frequency(tx[tx_start : tx_start + size], config.sample_rate_hz)
    carrier = detected if config.frequency_hz == 0 else config.frequency_hz
    if abs(detected - carrier) > max(0.02 * carrier, 0.25 * config.sample_rate_hz / size):
        raise ValueError("detected carrier does not match the configured CW frequency")
    rx_start = tx_start + round(config.range_m / config.sound_speed_m_s * config.sample_rate_hz)
    tx_phasor = phasors(tx, config.sample_rate_hz, carrier, tx_start, tx_start + size)[0]
    receiver = values[:, list(config.receiver_channels)]
    rx_phasor = phasors(receiver, config.sample_rate_hz, carrier, rx_start, rx_start + size)
    if abs(tx_phasor) < 1e-12:
        raise ValueError("TX phasor is too weak to normalize reliably")
    return TransferMeasurement(
        transfer=rx_phasor / tx_phasor,
        frequency_hz=float(carrier),
        tx_rms=float(abs(tx_phasor) / np.sqrt(2.0)),
        receiver_rms=abs(rx_phasor) / np.sqrt(2.0),
        onset_s=start / config.sample_rate_hz,
        tx_gate_s=tx_start / config.sample_rate_hz,
        rx_gate_s=rx_start / config.sample_rate_hz,
        spectral_concentration=concentration,
        onset_snr_db=onset_snr,
    )


def angular_difference(angles: np.ndarray | float, reference_deg: float) -> np.ndarray:
    return (np.asarray(angles) - reference_deg + 180.0) % 360.0 - 180.0


def angular_average(angles: np.ndarray, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    unique = np.unique(angles % 360.0)
    averaged = np.asarray([values[np.isclose(angles % 360.0, angle)].mean(axis=0) for angle in unique])
    return unique, averaged


def angular_weights(angles: np.ndarray) -> np.ndarray:
    gaps = np.diff(np.r_[angles, angles[0] + 360.0])
    return (gaps + np.roll(gaps, 1)) / 720.0


def interpolate_response(angles: np.ndarray, values: np.ndarray, angle_deg: float) -> np.ndarray:
    return np.array(
        [
            np.interp(angle_deg % 360.0, angles, values[:, index].real, period=360.0)
            + 1j * np.interp(angle_deg % 360.0, angles, values[:, index].imag, period=360.0)
            for index in range(values.shape[1])
        ]
    )


def _validate_sweep(bearings_deg: np.ndarray, transfer: np.ndarray) -> None:
    if bearings_deg.ndim != 1 or transfer.ndim != 2 or transfer.shape[0] != len(bearings_deg):
        raise ValueError("bearings must be one-dimensional and match the transfer rows")
    if transfer.shape[1] < 2:
        raise ValueError("at least two receiver channels are required")
    if len(np.unique(bearings_deg % 360.0)) < 4:
        raise ValueError("at least four distinct source bearings are required")
    if not np.isfinite(bearings_deg).all() or not np.isfinite(transfer).all():
        raise ValueError("bearings and transfer values must be finite")


def pattern_metrics(
    angles: np.ndarray,
    response: np.ndarray,
    settings: Optimizer,
    weights: np.ndarray,
    reference_gain: float,
) -> dict[str, float | None]:
    amplitude = np.abs(response)
    power = amplitude**2
    relative = angular_difference(angles, settings.steering_deg)
    rear = np.abs(angular_difference(angles, settings.steering_deg + 180.0)) <= (
        settings.rear_width_deg / 2.0
    )
    measure = angular_weights(angles)
    back = float(
        np.interp((settings.steering_deg + 180.0) % 360.0, angles, amplitude, period=360.0)
    )

    def db(value: float) -> float:
        return float(10.0 * np.log10(max(value, 1e-30)))

    full_coverage = np.max(np.diff(np.r_[angles, angles[0] + 360.0])) <= 90.0
    target = (1.0 + np.cos(np.deg2rad(relative))) / 2.0
    return {
        "front_to_back_db": -db(back**2),
        "rear_rejection_db": (
            -db(float(np.average(power[rear], weights=measure[rear]))) if rear.any() else None
        ),
        "deepest_null_db": -db(float(np.min(power))),
        "null_angle_deg": float(angles[np.argmin(power)]),
        "azimuth_di_db": -db(float(np.dot(measure, power))) if full_coverage else None,
        "cardioid_rms_error": float(np.sqrt(np.dot(measure, (amplitude - target) ** 2))),
        "weight_norm": float(np.linalg.norm(weights)),
        "wng_db": -db(float(np.vdot(weights, weights).real)),
        "reference_gain": reference_gain,
    }


def solve_empirical(
    bearings_deg: Sequence[float] | np.ndarray,
    transfer: Sequence[Sequence[complex]] | np.ndarray,
    settings: Optimizer | None = None,
) -> list[BeamSolution]:
    """Fit cardioid and rear-rejection weights to an empirical angular sweep."""
    config = settings or Optimizer()
    bearings = np.asarray(bearings_deg, dtype=np.float64)
    values = np.asarray(transfer, dtype=np.complex128)
    _validate_sweep(bearings, values)
    if config.regularization <= 0 or not 0.0 < config.rear_width_deg <= 360.0:
        raise ValueError("regularization must be positive and rear width must be in (0, 360]")
    if config.rear_fit_weight <= 0:
        raise ValueError("rear fit weight must be positive")

    angles, response = angular_average(bearings, values)
    forward = interpolate_response(angles, response, config.steering_deg)
    reference = float(np.sqrt(np.mean(abs(forward) ** 2)))
    if reference < 1e-15:
        raise ValueError("forward reference is too weak to form a stable beam")
    matrix = response / reference
    constraint = forward / reference
    desired_cardioid = (
        1.0 + np.cos(np.deg2rad(angular_difference(angles, config.steering_deg)))
    ) / 2.0
    rear = np.abs(angular_difference(angles, config.steering_deg + 180.0)) <= (
        config.rear_width_deg / 2.0
    )
    if not rear.any():
        raise ValueError("no measurements lie in the selected rear sector")
    quadrature = angular_weights(angles)
    fit_weight = quadrature * np.where(rear, config.rear_fit_weight, 1.0)

    solutions: list[BeamSolution] = []
    objectives = (
        ("Cardioid fit", fit_weight, desired_cardioid),
        ("Rear rejection", quadrature * rear, np.zeros(len(angles))),
    )
    for name, measure, desired in objectives:
        measure = measure / measure.sum()
        covariance = matrix.conj().T @ (measure[:, None] * matrix)
        covariance += config.regularization * np.eye(matrix.shape[1])
        rhs = matrix.conj().T @ (measure * desired)
        unconstrained = np.linalg.solve(covariance, rhs)
        direction = np.linalg.solve(covariance, constraint.conj())
        denominator = constraint @ direction
        if abs(denominator) < 1e-15:
            raise ValueError("forward constraint is singular; increase regularization")
        weights = unconstrained + direction * (1.0 - constraint @ unconstrained) / denominator
        beam = matrix @ weights
        metrics = pattern_metrics(angles, beam, config, weights, reference)
        warnings = ["In-sample empirical result; validate weights on independent measurements."]
        if metrics["wng_db"] is not None and metrics["wng_db"] < -10.0:
            warnings.append("White-noise gain is below -10 dB; increase regularization.")
        if np.max(np.diff(np.r_[angles, angles[0] + 360.0])) > 45.0:
            warnings.append("Angular coverage is sparse; interpolated metrics may be unreliable.")
        solutions.append(BeamSolution(name, weights, angles, beam, metrics, warnings, reference))
    return solutions


def ideal_triplet_sweep(
    frequency_hz: float = 8_000.0,
    side_m: float = 0.012,
    sound_speed_m_s: float = 1500.0,
    angle_step_deg: float = 15.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Create a deterministic far-field triplet sweep for smoke testing."""
    if min(frequency_hz, side_m, sound_speed_m_s, angle_step_deg) <= 0:
        raise ValueError("demo parameters must be positive")
    element_angles = np.deg2rad(np.array([0.0, 120.0, 240.0]))
    radius = side_m / np.sqrt(3.0)
    positions = np.column_stack(
        (radius * np.cos(element_angles), radius * np.sin(element_angles), np.zeros(3))
    )
    bearings = np.arange(0.0, 360.0, angle_step_deg)
    directions = np.column_stack(
        (
            np.cos(np.deg2rad(bearings)),
            np.sin(np.deg2rad(bearings)),
            np.zeros(len(bearings)),
        )
    )
    transfer = np.exp(1j * 2.0 * np.pi * frequency_hz / sound_speed_m_s * directions @ positions.T)
    return bearings, transfer


def _read_sweep(path: str | Path) -> tuple[np.ndarray, np.ndarray, Optimizer]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    real = np.asarray(payload["transfer_real"], dtype=np.float64)
    imaginary = np.asarray(payload["transfer_imag"], dtype=np.float64)
    if real.shape != imaginary.shape:
        raise ValueError("transfer_real and transfer_imag must have the same shape")
    optimizer = Optimizer(**payload.get("optimizer", {}))
    return np.asarray(payload["bearings_deg"], dtype=np.float64), real + 1j * imaginary, optimizer


def _write_output(solutions: list[BeamSolution], destination: str | None) -> None:
    document = json.dumps({"solutions": [solution.to_json() for solution in solutions]}, indent=2)
    if destination:
        Path(destination).write_text(document + "\n", encoding="utf-8")
    else:
        print(document)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    solve = commands.add_parser("solve", help="solve weights from a JSON angular sweep")
    solve.add_argument("input", help="JSON file containing bearings_deg and transfer_real/imag")
    solve.add_argument("--output", help="write results to this JSON file instead of stdout")
    solve.add_argument("--steering-deg", type=float)
    solve.add_argument("--rear-width-deg", type=float)
    solve.add_argument("--regularization", type=float)
    solve.add_argument("--rear-fit-weight", type=float)
    demo = commands.add_parser("demo", help="solve a deterministic ideal triplet sweep")
    demo.add_argument("--frequency-hz", type=float, default=8_000.0)
    demo.add_argument("--side-mm", type=float, default=12.0)
    demo.add_argument("--steering-deg", type=float, default=0.0)
    demo.add_argument("--output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "solve":
            bearings, transfer, settings = _read_sweep(args.input)
            for name in ("steering_deg", "rear_width_deg", "regularization", "rear_fit_weight"):
                value = getattr(args, name)
                if value is not None:
                    setattr(settings, name, value)
        else:
            bearings, transfer = ideal_triplet_sweep(args.frequency_hz, args.side_mm / 1000.0)
            settings = Optimizer(steering_deg=args.steering_deg)
        _write_output(solve_empirical(bearings, transfer, settings), args.output)
    except (KeyError, OSError, TypeError, ValueError, np.linalg.LinAlgError) as exc:
        print(f"cora-triplet-beamformer: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
