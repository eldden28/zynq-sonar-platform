#!/usr/bin/env python3

from __future__ import annotations

import base64
from pathlib import Path
import tempfile
import time
import unittest

import numpy as np

from cora_sonar import (
    SonarConfig,
    SonarEngine,
    SonarTarget,
    aperture_weights,
    array_metrics,
    array_pattern,
    element_positions,
    process_ping,
    range_gate_for_beamforming,
    scenario_targets,
    synthesize_channels,
)
from cora_sonar_service import resolve_web_asset


class GeometryAndArrayTests(unittest.TestCase):
    def test_element_positions_are_centered_with_expected_aperture(self):
        config = SonarConfig()
        positions = element_positions(config)
        self.assertEqual(len(positions), 32)
        self.assertAlmostEqual(float(np.mean(positions)), 0.0, places=12)
        self.assertAlmostEqual(float(np.diff(positions)[0]), 0.0015, places=12)
        self.assertAlmostEqual(float(positions[-1] - positions[0]), 0.0465, places=12)

    def test_rectangular_broadside_beamwidth_matches_450khz_estimate(self):
        metrics = array_metrics(SonarConfig(aperture_window="rectangular"))
        self.assertGreater(metrics["beamwidth_3db_deg"], 3.2)
        self.assertLess(metrics["beamwidth_3db_deg"], 4.1)
        self.assertAlmostEqual(metrics["peak_sidelobe_db"], -13.25, delta=0.8)

    def test_broadside_pattern_is_symmetric_and_steering_sign_is_correct(self):
        config = SonarConfig(aperture_window="rectangular")
        scan, broadside = array_pattern(config)
        np.testing.assert_allclose(broadside, broadside[::-1], atol=1e-8)
        scan, steered = array_pattern(config, steering_deg=20.0)
        self.assertAlmostEqual(float(scan[np.argmax(steered)]), 20.0, delta=0.1)

    def test_all_required_aperture_windows_are_finite_and_symmetric(self):
        for name in ("rectangular", "hann", "hamming", "blackman", "taylor"):
            values = aperture_weights(name, 32)
            self.assertTrue(np.all(np.isfinite(values)), name)
            np.testing.assert_allclose(values, values[::-1], atol=1e-12)


class TimeSeriesPipelineTests(unittest.TestCase):
    @staticmethod
    def ideal_config(**values) -> SonarConfig:
        return SonarConfig(
            noise_rms=0.0,
            enable_quantization=False,
            max_range_m=11.0,
            **values,
        )

    def test_synthesis_produces_distinct_simultaneous_channel_time_series(self):
        config = self.ideal_config()
        channels, reference = synthesize_channels(
            config, [SonarTarget(7.0, 25.0, label="test")]
        )
        self.assertEqual(channels.shape[0], 32)
        self.assertGreater(channels.shape[1], len(reference))
        self.assertGreater(float(np.max(np.abs(channels))), 0.5)
        self.assertFalse(np.array_equal(channels[0], channels[-1]))

    def test_matched_filter_and_beamformer_peak_at_target_range_and_angle(self):
        config = self.ideal_config(aperture_window="hann")
        target = SonarTarget(8.0, 18.0, label="known")
        result = process_ping(config, [target])
        magnitude = np.abs(result.beamformed)
        peak_beam, peak_range = np.unravel_index(int(np.argmax(magnitude)), magnitude.shape)
        self.assertAlmostEqual(float(result.range_axis_m[peak_range]), 8.0, delta=0.004)
        beam_spacing = 90.0 / (config.beam_count - 1)
        self.assertAlmostEqual(
            float(result.elevation_axis_deg[peak_beam]), 18.0, delta=beam_spacing
        )

    def test_public_image_is_decoded_512_by_512_time_series_product(self):
        config = self.ideal_config()
        result = process_ping(config, scenario_targets("point_targets"))
        image = result.public["image"]
        decoded = base64.b64decode(image["data"])
        self.assertEqual(len(decoded), config.beam_count * config.range_pixel_count)
        self.assertEqual(result.public["pipeline"][1], "per-channel FFT matched filter")
        self.assertEqual(
            result.public["pipeline"][2],
            "coherent range gating to 1024 processing bins",
        )
        self.assertEqual(
            result.public["pipeline"][3],
            "512-direction delay-and-sum beamformer",
        )
        self.assertEqual(len(result.public["target_errors"]), 3)
        for error in result.public["target_errors"]:
            self.assertLess(abs(error["range_error_m"]), 0.005)
            self.assertLess(abs(error["elevation_error_deg"]), 1.5)

    def test_failed_elements_change_the_beamformed_time_series(self):
        target = [SonarTarget(7.0, 8.0)]
        healthy = process_ping(self.ideal_config(), target).beamformed
        failed = process_ping(
            self.ideal_config(failed_channels=(0, 7, 16, 31)), target
        ).beamformed
        self.assertGreater(float(np.max(np.abs(healthy - failed))), 0.01)

    def test_range_gating_preserves_a_common_complex_channel_vector(self):
        ranges = np.arange(16, dtype=np.float64)
        matched = np.zeros((32, 16), dtype=np.complex64)
        matched[:, 6] = np.arange(1, 33) * (1.0 + 2.0j)
        gated, gated_ranges = range_gate_for_beamforming(matched, ranges, 4)
        self.assertEqual(gated.shape, (32, 4))
        peak_bin = int(np.argmax(np.sum(np.abs(gated) ** 2, axis=0)))
        self.assertEqual(float(gated_ranges[peak_bin]), 6.0)
        np.testing.assert_array_equal(gated[:, peak_bin], matched[:, 6])

    def test_512_beam_detections_are_condensed_to_local_peaks(self):
        result = process_ping(
            self.ideal_config(), scenario_targets("point_targets")
        )
        self.assertLessEqual(len(result.public["detections"]), 64)
        detected_ranges = [item["range_m"] for item in result.public["detections"]]
        for expected in (6.0, 9.0, 10.5):
            self.assertLess(min(abs(value - expected) for value in detected_ranges), 0.02)


class EngineAndServiceTests(unittest.TestCase):
    def test_engine_configuration_and_capture(self):
        engine = SonarEngine(
            SonarConfig(noise_rms=0.0, enable_quantization=False, max_range_m=6.0)
        )
        engine.reset()
        engine.configure({"aperture_window": "taylor", "dynamic_range_db": 60})
        self.assertEqual(engine.status()["config"]["aperture_window"], "taylor")
        self.assertGreater(len(engine.capture_npz()), 1000)
        with self.assertRaisesRegex(ValueError, "unsupported"):
            engine.configure({"not_a_setting": 1})

    def test_engine_accepts_validated_custom_scene_targets(self):
        engine = SonarEngine(
            SonarConfig(noise_rms=0.0, enable_quantization=False, max_range_m=6.0)
        )
        engine.set_targets(
            [
                {
                    "label": "editor target",
                    "range_m": 4.25,
                    "elevation_deg": -12.5,
                    "reflectivity": 0.7,
                    "enabled": True,
                }
            ]
        )
        status = engine.status()
        self.assertEqual(status["scenario"], "custom")
        self.assertEqual(status["targets"][0]["label"], "editor target")
        with self.assertRaisesRegex(ValueError, "range"):
            engine.set_targets(
                [{"range_m": 7.0, "elevation_deg": 0.0, "reflectivity": 1.0}]
            )
        with self.assertRaisesRegex(ValueError, "at most"):
            engine.set_targets(
                [
                    {"range_m": 3.0, "elevation_deg": 0.0, "reflectivity": 1.0}
                    for _ in range(65)
                ]
            )

    def test_engine_reconfigures_active_elements_and_center_frequency(self):
        engine = SonarEngine(
            SonarConfig(noise_rms=0.0, enable_quantization=False, max_range_m=4.0)
        )
        engine.configure(
            {
                "element_count": 128,
                "center_frequency_hz": 1_000_000,
                "pitch_m": 0.0015,
            }
        )
        status = engine.status()
        self.assertEqual(status["config"]["element_count"], 128)
        self.assertEqual(status["config"]["center_frequency_hz"], 1_000_000.0)
        self.assertEqual(engine.latest.channels.shape[0], 128)
        self.assertEqual(status["pipeline"][0], "128-channel complex baseband time series")
        self.assertTrue(status["metrics"]["spatial_aliasing"])
        self.assertLess(status["metrics"]["beamwidth_3db_deg"], 1.0)
        self.assertGreater(status["metrics"]["peak_sidelobe_db"], -0.1)
        engine.configure({"pitch_m": 0.00075})
        status = engine.status()
        self.assertEqual(status["config"]["pitch_m"], 0.00075)
        self.assertAlmostEqual(status["metrics"]["pitch_wavelength_ratio"], 0.5)
        self.assertFalse(status["metrics"]["spatial_aliasing"])
        self.assertLess(status["metrics"]["peak_sidelobe_db"], -20.0)
        with self.assertRaisesRegex(ValueError, "element_count"):
            engine.configure({"element_count": 129})
        with self.assertRaisesRegex(ValueError, "center_frequency_hz"):
            engine.configure({"center_frequency_hz": 1_050_000})
        with self.assertRaisesRegex(ValueError, "pitch_m"):
            engine.configure({"pitch_m": 0.00025})

    def test_engine_background_thread_advances_sequence(self):
        engine = SonarEngine(
            SonarConfig(
                noise_rms=0.0,
                enable_quantization=False,
                max_range_m=4.0,
                ping_rate_hz=10.0,
            )
        )
        engine.start()
        try:
            deadline = time.monotonic() + 2.0
            while engine.status().get("sequence", -1) < 1 and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertGreaterEqual(engine.status()["sequence"], 1)
        finally:
            engine.stop()

    def test_static_asset_resolution_blocks_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "index.html").write_text("ok", encoding="utf-8")
            self.assertEqual(resolve_web_asset(root, "/index.html"), root / "index.html")
            self.assertIsNone(resolve_web_asset(root, "/../etc/passwd"))
            self.assertIsNone(resolve_web_asset(root, "/missing.js"))


if __name__ == "__main__":
    unittest.main()
