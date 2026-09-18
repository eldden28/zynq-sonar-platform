#!/usr/bin/env python3

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cora_triplet_beamformer import (  # noqa: E402
    CwConfig,
    Optimizer,
    extract_cw_transfer,
    ideal_triplet_sweep,
    interpolate_response,
    phasors,
    solve_empirical,
)


class CwExtractionTests(unittest.TestCase):
    def test_off_bin_phasor_recovers_complex_amplitude(self):
        sample_rate_hz = 48_000.0
        frequency_hz = 1234.567
        time_s = np.arange(12_000) / sample_rate_hz
        expected = np.array([0.3 * np.exp(0.8j), 1.2 * np.exp(-0.4j)])
        samples = np.real(np.exp(2j * np.pi * frequency_hz * time_s[:, None]) * expected) + 0.18
        actual = phasors(samples, sample_rate_hz, frequency_hz, 871, 9712)
        np.testing.assert_allclose(actual, expected, atol=2e-12)

    def test_multichannel_ping_extracts_receiver_to_tx_transfer(self):
        sample_rate_hz = 96_000
        frequency_hz = 8_000.0
        sample_count = 2400
        time_s = np.arange(sample_count) / sample_rate_hz
        tx_start = 300
        rx_delay = 96
        duration = 1200
        phase = np.array([0.3, -0.5, 1.1])
        gain = np.array([0.2, 0.35, 0.5])
        samples = np.zeros((sample_count, 4), dtype=np.float64)
        tx_active = (np.arange(sample_count) >= tx_start) & (
            np.arange(sample_count) < tx_start + duration
        )
        rx_active = (np.arange(sample_count) >= tx_start + rx_delay) & (
            np.arange(sample_count) < tx_start + rx_delay + duration
        )
        samples[:, 0] = np.cos(2.0 * np.pi * frequency_hz * time_s) * tx_active
        samples[:, 1:] = (
            gain[None, :]
            * np.cos(2.0 * np.pi * frequency_hz * time_s[:, None] + phase[None, :])
            * rx_active[:, None]
        )
        config = CwConfig(
            sample_rate_hz=sample_rate_hz,
            range_m=1.5,
            frequency_hz=frequency_hz,
            gate_offset_ms=0.25,
            gate_duration_ms=6.0,
        )
        measurement = extract_cw_transfer(samples, config)
        expected = gain * np.exp(1j * phase)
        np.testing.assert_allclose(measurement.transfer, expected, atol=2e-3)
        self.assertGreater(measurement.spectral_concentration, 0.9)


class EmpiricalSolverTests(unittest.TestCase):
    def test_cardioid_fit_and_forward_constraint(self):
        bearings, transfer = ideal_triplet_sweep()
        settings = Optimizer(steering_deg=35.0)
        solutions = solve_empirical(bearings, transfer, settings)
        self.assertEqual([solution.name for solution in solutions], ["Cardioid fit", "Rear rejection"])
        forward = interpolate_response(bearings, transfer, settings.steering_deg)
        for solution in solutions:
            constrained = forward @ solution.weights / solution.reference_gain
            np.testing.assert_allclose(constrained, 1.0, atol=1e-10)
            self.assertGreater(solution.metrics["rear_rejection_db"], 10.0)
            self.assertTrue(np.isfinite(solution.weights).all())
        self.assertLess(solutions[0].metrics["cardioid_rms_error"], 0.03)

    def test_repeated_angles_are_averaged(self):
        bearings, transfer = ideal_triplet_sweep()
        repeated_bearings = np.r_[bearings, bearings]
        repeated_transfer = np.concatenate((transfer, transfer))
        solutions = solve_empirical(repeated_bearings, repeated_transfer)
        self.assertEqual(len(solutions[0].bearings_deg), len(bearings))

    def test_cli_json_round_trip(self):
        bearings, transfer = ideal_triplet_sweep(angle_step_deg=30.0)
        with tempfile.TemporaryDirectory() as folder:
            input_path = Path(folder) / "sweep.json"
            output_path = Path(folder) / "weights.json"
            input_path.write_text(
                json.dumps(
                    {
                        "bearings_deg": bearings.tolist(),
                        "transfer_real": transfer.real.tolist(),
                        "transfer_imag": transfer.imag.tolist(),
                        "optimizer": {"steering_deg": 20.0},
                    }
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "cora_triplet_beamformer.py"),
                    "solve",
                    str(input_path),
                    "--output",
                    str(output_path),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(len(result["solutions"]), 2)
            self.assertEqual(len(result["solutions"][0]["weights_real"]), 3)


if __name__ == "__main__":
    unittest.main()
