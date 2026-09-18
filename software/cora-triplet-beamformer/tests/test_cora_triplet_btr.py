#!/usr/bin/env python3

from __future__ import annotations

import base64
from pathlib import Path
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cora_triplet_btr import (  # noqa: E402
    BtrConfig,
    CARDIOID_CROSSFADE_HALF_WIDTH_DEG,
    SAMPLE_RATE_HZ,
    SyntheticBtrEngine,
    array_positions,
    cardioid_starboard_mix,
    direction_vector,
    triplet_local_positions,
)
from cora_triplet_btr_service import resolve_web_asset  # noqa: E402


class BtrConfigurationTests(unittest.TestCase):
    def test_profile_is_fixed_at_48k_and_21_channels(self):
        config = BtrConfig()
        config.validate()
        self.assertEqual(config.sample_rate_hz, SAMPLE_RATE_HZ)
        self.assertEqual((config.bearing_min_deg, config.bearing_max_deg), (-180.0, 180.0))
        self.assertEqual(config.bearing_count, 361)
        positions = array_positions(config)
        self.assertEqual(positions.shape, (21, 3))
        self.assertAlmostEqual(float(np.ptp(positions[:, 0])), 6 * config.triplet_spacing_m)
        self.assertAlmostEqual(float(np.ptp(positions[:, 1])), config.triplet_side_m)
        self.assertAlmostEqual(
            float(np.ptp(positions[:, 2])), np.sqrt(3.0) * config.triplet_side_m / 2.0
        )
        with self.assertRaisesRegex(ValueError, "48 ksample"):
            BtrConfig(sample_rate_hz=96_000).validate()
        with self.assertRaisesRegex(ValueError, "20 kHz"):
            BtrConfig(carrier_hz=20_001).validate()

    def test_physical_geometry_and_spatial_alias_limits(self):
        config = BtrConfig()
        self.assertEqual(config.triplet_side_m, 0.030)
        self.assertEqual(config.triplet_spacing_m, 0.0762)
        local = triplet_local_positions(config.triplet_side_m)
        distances = [
            np.linalg.norm(local[0] - local[1]),
            np.linalg.norm(local[1] - local[2]),
            np.linalg.norm(local[2] - local[0]),
        ]
        np.testing.assert_allclose(distances, config.triplet_side_m)
        self.assertGreater(local[0, 1], 0.0)
        self.assertLess(local[1, 2], 0.0)
        self.assertLess(local[2, 1], 0.0)
        axial_maximum = config.sound_speed_m_s / (2.0 * config.triplet_spacing_m)
        triplet_maximum = config.sound_speed_m_s / (2.0 * config.triplet_side_m)
        self.assertAlmostEqual(axial_maximum, 9842.519685, places=6)
        self.assertEqual(triplet_maximum, 25_000.0)

    def test_vertical_baseline_distinguishes_above_and_below(self):
        config = BtrConfig()
        positions = array_positions(config)
        wave_number = 2.0 * np.pi * config.carrier_hz / config.sound_speed_m_s
        above = np.exp(1j * wave_number * positions @ direction_vector(0.0, 20.0))
        below = np.exp(1j * wave_number * positions @ direction_vector(0.0, -20.0))
        self.assertFalse(np.allclose(above, below))

    def test_cardioid_modes_crossfade_smoothly_at_both_endfire_axes(self):
        bearings = np.arange(-180.0, 181.0)
        mix = cardioid_starboard_mix(bearings)
        for sign in (-1.0, 1.0):
            center = int(sign * 90.0 + 180.0)
            self.assertAlmostEqual(float(mix[center]), 0.5)
            self.assertEqual(
                float(mix[int(sign * (90.0 - CARDIOID_CROSSFADE_HALF_WIDTH_DEG) + 180.0)]),
                1.0,
            )
            self.assertEqual(
                float(mix[int(sign * (90.0 + CARDIOID_CROSSFADE_HALF_WIDTH_DEG) + 180.0)]),
                0.0,
            )

    def test_endfire_transition_has_no_hard_power_step(self):
        config = BtrConfig(
            carrier_hz=16_750.0,
            primary_bearing_deg=102.0,
            primary_level=0.8,
            secondary_level=0.0,
            motion_enabled=False,
            noise_rms=0.0,
        )
        engine = SyntheticBtrEngine(config)
        engine.process_once()
        column = engine.history[:, 0].astype(np.int16)
        transition = column[267:274]
        self.assertLessEqual(int(np.max(np.abs(np.diff(transition)))), 6)


class SyntheticBtrTests(unittest.TestCase):
    def test_static_primary_source_produces_correct_peak(self):
        config = BtrConfig(
            primary_bearing_deg=31.0,
            secondary_level=0.0,
            motion_enabled=False,
            noise_rms=0.0,
        )
        engine = SyntheticBtrEngine(config)
        result = engine.process_once()
        self.assertAlmostEqual(result["peak_bearing_deg"], 31.0, delta=1.0)
        self.assertEqual(result["input_payload_mb_s"], 2.016)
        self.assertLess(result["processing_ms"], result["frame_period_ms"])

    def test_port_cardioid_resolves_rear_mirror(self):
        config = BtrConfig(
            primary_bearing_deg=145.0,
            secondary_level=0.0,
            motion_enabled=False,
            noise_rms=0.0,
        )
        result = SyntheticBtrEngine(config).process_once()
        self.assertAlmostEqual(result["peak_bearing_deg"], 145.0, delta=1.0)
        self.assertEqual(result["peak_cardioid_mode"], "port")

    def test_history_is_base64_uint8_with_bearing_rows(self):
        engine = SyntheticBtrEngine(BtrConfig(motion_enabled=False))
        for _ in range(4):
            engine.process_once()
        status = engine.status()
        btr = status["btr"]
        self.assertEqual((btr["bearings"], btr["columns"]), (361, 4))
        self.assertEqual(len(base64.b64decode(btr["data"])), 361 * 4)
        self.assertEqual(status["array"]["channel_count"], 21)
        self.assertEqual(status["input"]["mode"], "synthetic")
        self.assertEqual(status["input"]["payload_mb_s"], 2.016)
        self.assertEqual(status["array"]["azimuth_coverage_deg"], 360)
        self.assertEqual(status["array"]["cardioid_modes"], ["starboard", "port"])
        self.assertEqual(
            status["array"]["cardioid_fusion"], "smooth-power-crossfade"
        )
        self.assertTrue(status["array"]["signed_elevation_observable"])
        self.assertEqual(status["array"]["triplet_spacing_m"], 0.0762)

    def test_spectrum_and_element_power_telemetry(self):
        config = BtrConfig(
            carrier_hz=8_000.0,
            primary_level=0.6,
            secondary_level=0.0,
            motion_enabled=False,
            noise_rms=0.0,
        )
        engine = SyntheticBtrEngine(config)
        engine.process_once()
        status = engine.status()
        spectrum = status["spectrum"]
        values = np.frombuffer(base64.b64decode(spectrum["data"]), dtype=np.uint8)
        self.assertEqual(len(values), config.block_size // 2 + 1)
        self.assertEqual(spectrum["bins"], len(values))
        self.assertEqual(spectrum["reference_channel"], 11)
        frequency_resolution = config.sample_rate_hz / config.block_size
        peak_frequency = int(np.argmax(values)) * frequency_resolution
        self.assertAlmostEqual(peak_frequency, config.carrier_hz, delta=frequency_resolution)
        element_levels = status["metrics"]["element_rms_dbfs"]
        self.assertEqual(len(element_levels), 21)
        self.assertTrue(all(-100.0 < level <= 0.0 for level in element_levels))

    def test_runtime_configuration_enforces_frequency_limit(self):
        engine = SyntheticBtrEngine()
        engine.configure({"carrier_hz": 19_750, "motion_enabled": False})
        self.assertEqual(engine.status()["config"]["carrier_hz"], 19_750)
        with self.assertRaisesRegex(ValueError, "20 kHz"):
            engine.configure({"carrier_hz": 20_250})

    def test_web_asset_resolution_blocks_traversal(self):
        root = ROOT / "www"
        self.assertEqual(resolve_web_asset(root, "/").name, "index.html")
        self.assertIsNone(resolve_web_asset(root, "/../../etc/passwd"))

    def test_dashboard_uses_conventional_btr_orientation(self):
        html = (ROOT / "www" / "index.html").read_text(encoding="utf-8")
        self.assertIn("newest data at top", html)
        self.assertIn("const time=columns-1-y", html)
        self.assertIn("raw[x*columns+time]", html)
        self.assertIn("Enhance instantaneous ridges", html)
        self.assertIn('id="display-range"', html)
        self.assertIn('id="min-db"', html)
        self.assertIn("if(db<minimumDb)", html)
        self.assertIn('id="spectrum"', html)
        self.assertIn('id="waterfall"', html)
        self.assertIn('id="element-meters"', html)
        self.assertIn('id="geometry"', html)
        self.assertIn("data-synthetic", html)
        self.assertIn("input.mode==='axi'", html)
        self.assertIn("AXI acquisition", html)
        self.assertIn("drawSpectrum", html)
        self.assertIn("drawWaterfall", html)
        self.assertIn("drawGeometry", html)
        self.assertIn("renderElementMeters", html)


if __name__ == "__main__":
    unittest.main()
