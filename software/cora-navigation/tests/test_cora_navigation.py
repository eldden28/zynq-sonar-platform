#!/usr/bin/env python3

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from cora_navigation import (
    AcousticConfig,
    AcousticProcessor,
    AcousticSimulator,
    DvlConfig,
    DvlProcessor,
    DvlSimulator,
    FrameRecorder,
    NAV_MODE_IUSBL,
    NAV_MODE_LBL,
    NavPayload,
    NavigationEngine,
    NAVIGATION_ORIGIN_LATITUDE_DEG,
    NAVIGATION_ORIGIN_LONGITUDE_DEG,
    SensorFrame,
    SimulatedClock,
    SUSQUEHANNA_CENTERLINE_WGS84,
    SUSQUEHANNA_MISSION_WAYPOINTS_NED_M,
    VehicleSimulator,
    bits_to_bytes,
    bytes_to_bits,
    convolutional_decode_hard,
    convolutional_encode,
    quat_to_matrix,
)
from cora_navigation_service import resolve_web_asset


class SensorFrameTests(unittest.TestCase):
    def test_ci16_frame_round_trip_and_crc(self):
        rng = np.random.default_rng(7)
        samples = rng.normal(size=(64, 4)) + 1j * rng.normal(size=(64, 4))
        frame = SensorFrame.from_ci16(
            samples,
            sequence=17,
            timestamp_tai_ns=123456789,
            clock_uncertainty_ns=50000,
            sample_rate_hz=40000,
            center_frequency_hz=600000,
        )
        encoded = frame.to_bytes()
        decoded = SensorFrame.from_bytes(encoded)
        self.assertEqual(decoded.sequence, 17)
        self.assertEqual(decoded.ci16().shape, (64, 4))
        damaged = bytearray(encoded)
        damaged[-1] ^= 0x01
        with self.assertRaisesRegex(ValueError, "CRC"):
            SensorFrame.from_bytes(bytes(damaged))

    def test_s16_frame_record_and_replay(self):
        frame = SensorFrame.from_s16(
            np.arange(80, dtype=np.int16).reshape(20, 4),
            sequence=2,
            timestamp_tai_ns=3,
            clock_uncertainty_ns=4,
            sample_rate_hz=96000,
            center_frequency_hz=24000,
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "capture.csf"
            recorder = FrameRecorder(path)
            recorder.open()
            recorder.write(frame)
            recorder.close()
            replayed = list(FrameRecorder.replay(path))
        self.assertEqual(len(replayed), 1)
        np.testing.assert_array_equal(replayed[0].s16(), frame.s16())


class CodingAndPayloadTests(unittest.TestCase):
    def test_navigation_payload_round_trip_and_crc(self):
        source = NavPayload(
            NAV_MODE_IUSBL,
            100,
            93,
            41.5261234,
            -70.6725678,
            12.345,
            1_800_000_000_000_000_000,
            50_000_000,
        )
        encoded = source.encode()
        decoded = NavPayload.decode(encoded)
        self.assertEqual(decoded.transponder_id, 100)
        self.assertAlmostEqual(decoded.latitude_deg, source.latitude_deg, 7)
        self.assertAlmostEqual(decoded.depth_m, source.depth_m, 3)
        damaged = bytearray(encoded)
        damaged[12] ^= 0x10
        with self.assertRaisesRegex(ValueError, "CRC"):
            NavPayload.decode(bytes(damaged))

    def test_rate_half_convolutional_code_corrects_sparse_errors(self):
        source = b"Cora navigation sensor ABI"
        encoded = convolutional_encode(bytes_to_bits(source))
        encoded[[27, 199, 311]] ^= 1
        decoded = convolutional_decode_hard(encoded, len(source) * 8)
        self.assertEqual(bits_to_bytes(decoded), source)


class DvlTests(unittest.TestCase):
    @staticmethod
    def truth_after(seconds: float = 2.0):
        world = VehicleSimulator()
        clock = SimulatedClock()
        truth = None
        for _ in range(round(seconds / 0.02)):
            status = clock.step(0.02)
            truth = world.step(0.02, status.tai_ns)
        return truth

    def test_nominal_raw_echo_velocity_solution(self):
        config = DvlConfig()
        truth = self.truth_after()
        frame, _ = DvlSimulator(config).generate(truth, 50000)
        solution = DvlProcessor(config).process(
            SensorFrame.from_bytes(frame.to_bytes())
        )
        velocity_body = (
            quat_to_matrix(truth.quaternion_body_to_ned).T
            @ truth.velocity_ned_mps
        )
        self.assertTrue(solution.bottom_lock)
        self.assertLess(
            float(np.linalg.norm(solution.velocity_body_mps - velocity_body)),
            0.05,
        )
        self.assertIsNotNone(solution.altitude_m)
        self.assertLess(solution.processing_ms, 100.0)

    def test_one_lost_beam_is_degraded_and_two_are_invalid(self):
        truth = self.truth_after()
        one_config = DvlConfig(dropout_mask=1)
        one_frame, _ = DvlSimulator(one_config).generate(truth, 50000)
        one = DvlProcessor(one_config).process(one_frame)
        self.assertTrue(one.valid)
        self.assertTrue(one.degraded)
        two_config = DvlConfig(dropout_mask=3)
        two_frame, _ = DvlSimulator(two_config).generate(truth, 50000)
        two = DvlProcessor(two_config).process(two_frame)
        self.assertFalse(two.valid)
        self.assertFalse(two.bottom_lock)


class AcousticTests(unittest.TestCase):
    def setUp(self):
        self.config = AcousticConfig()
        self.world = VehicleSimulator()
        self.clock_source = SimulatedClock()
        self.clock = self.clock_source.step(0.02)
        self.truth = self.world.step(0.02, self.clock.tai_ns)
        self.transponder = self.truth.position_ned_m + np.asarray(
            (35.0, 25.0, -14.5)
        )

    def process(self, mode: int, waveform: str = "dedicated"):
        simulator = AcousticSimulator(self.config)
        frame, _, metadata = simulator.generate(
            self.truth,
            self.transponder,
            100,
            mode,
            self.clock,
            waveform=waveform,
            turnaround_ns=50_000_000 if mode == NAV_MODE_LBL else 0,
        )
        interrogation = None
        if mode == NAV_MODE_LBL:
            interrogation = int(
                self.truth.timestamp_tai_ns
                - (2.0 * metadata["truth_range_m"] / 1500.0) * 1e9
                - 50_000_000
            )
        observation = AcousticProcessor(self.config).process(
            SensorFrame.from_bytes(frame.to_bytes()),
            self.truth.quaternion_body_to_ned,
            waveform=waveform,
            vehicle_position_ned_m=self.truth.position_ned_m,
            interrogation_tai_ns=interrogation,
        )
        return observation, metadata

    def test_dedicated_ping_decodes_and_ranges(self):
        observation, metadata = self.process(NAV_MODE_LBL)
        self.assertTrue(observation.valid)
        self.assertTrue(observation.payload_valid)
        self.assertAlmostEqual(
            observation.range_m, metadata["truth_range_m"], delta=0.25
        )

    def test_iusbl_tdoa_produces_position_fix(self):
        observation, metadata = self.process(NAV_MODE_IUSBL)
        self.assertTrue(observation.range_valid)
        self.assertIsNotNone(observation.position_fix_ned_m)
        self.assertLess(
            float(
                np.linalg.norm(
                    observation.position_fix_ned_m
                    - self.truth.position_ned_m
                )
            ),
            4.0,
        )
        self.assertAlmostEqual(
            observation.range_m, metadata["truth_range_m"], delta=0.25
        )

    def test_clock_uncertainty_disables_one_way_range(self):
        self.config.clock_range_threshold_ns = 10_000
        observation, _ = self.process(NAV_MODE_IUSBL)
        self.assertTrue(observation.payload_valid)
        self.assertFalse(observation.range_valid)
        self.assertIsNone(observation.position_fix_ned_m)

    def test_corrupt_payload_is_rejected(self):
        self.config.corrupt_packet = True
        observation, _ = self.process(NAV_MODE_LBL)
        self.assertFalse(observation.valid)
        self.assertFalse(observation.payload_valid)
        self.assertIn("CRC", observation.error)

    def test_ofdm_compatibility_transport(self):
        try:
            import cora_ofdm_acoustic  # noqa: F401
        except ImportError:
            self.skipTest("cora_ofdm_acoustic is not on PYTHONPATH")
        observation, metadata = self.process(NAV_MODE_IUSBL, "ofdm")
        self.assertTrue(observation.payload_valid)
        self.assertAlmostEqual(
            observation.range_m, metadata["truth_range_m"], delta=0.5
        )


class EngineTests(unittest.TestCase):
    def test_susquehanna_mission_runs_parallel_upstream_and_downstream(self):
        mission = SUSQUEHANNA_MISSION_WAYPOINTS_NED_M
        centerline_count = len(SUSQUEHANNA_CENTERLINE_WGS84)
        self.assertEqual(mission.shape, (centerline_count * 2, 2))
        self.assertAlmostEqual(
            float(np.linalg.norm(mission[0] - mission[-1])),
            60.0,
            delta=0.1,
        )
        self.assertAlmostEqual(
            float(
                np.linalg.norm(
                    mission[centerline_count - 1] - mission[centerline_count]
                )
            ),
            60.0,
            delta=0.1,
        )
        self.assertEqual(NAVIGATION_ORIGIN_LATITUDE_DEG, 41.2250)
        self.assertEqual(NAVIGATION_ORIGIN_LONGITUDE_DEG, -77.0445)

    def test_fused_navigation_converges_with_dvl_and_lbl(self):
        with tempfile.TemporaryDirectory() as temporary:
            engine = NavigationEngine(run_directory=Path(temporary))
            engine.reset()
            errors = []
            for step in range(600):
                status = engine.step(
                    generate_dvl=step % 10 == 0,
                    generate_acoustic=step % 50 == 0,
                )
                if step >= 400:
                    errors.append(status["errors"]["position_norm_m"])
        self.assertLess(float(np.mean(errors)), 2.0)
        self.assertEqual(status["stats"]["dvl_invalid"], 0)
        self.assertEqual(status["stats"]["acoustic_invalid"], 0)
        self.assertGreater(status["stats"]["dvl_frames"], 50)

    def test_status_and_history_are_versioned_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            engine = NavigationEngine(run_directory=Path(temporary))
            engine.reset()
            engine.running = True
            engine.step(generate_dvl=True, generate_acoustic=True)
            engine._append_history()
            status = engine.status()
            history = engine.history_after(-1)
            json.dumps(status)
            json.dumps(history)
        self.assertEqual(status["schema_version"], 1)
        self.assertEqual(history["schema_version"], 1)
        self.assertEqual(len(history["history"]), 1)
        self.assertEqual(len(status["ins"]["horizontal_covariance_m2"]), 2)
        self.assertEqual(
            len(status["mission"]["waypoints_ned_m"]),
            len(SUSQUEHANNA_CENTERLINE_WGS84) * 2,
        )
        self.assertIsInstance(status["mission"]["active_waypoint"], int)
        self.assertGreater(status["truth"]["latitude_deg"], 41.21)
        self.assertLess(status["truth"]["latitude_deg"], 41.24)
        self.assertGreater(status["truth"]["longitude_deg"], -77.07)
        self.assertLess(status["truth"]["longitude_deg"], -77.02)


class DashboardAssetTests(unittest.TestCase):
    def test_dashboard_assets_resolve_inside_web_root_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "map.js").write_text("const map = true;", encoding="utf-8")
            (root / "private.txt").write_text("not public", encoding="utf-8")
            self.assertEqual(resolve_web_asset(root, "/map.js"), root / "map.js")
            self.assertIsNone(resolve_web_asset(root, "/private.txt"))
            self.assertIsNone(resolve_web_asset(root, "/../outside.js"))
            self.assertIsNone(resolve_web_asset(root, "/%2e%2e/outside.js"))

    def test_acoustic_dashboard_packages_leaflet_and_map_assets(self):
        web_root = Path(__file__).parents[1] / "www" / "acoustic"
        index = (web_root / "index.html").read_text(encoding="utf-8")
        map_js = (web_root / "map.js").read_text(encoding="utf-8")
        for relative in (
            "map.css",
            "map.js",
            "vendor/leaflet/leaflet.css",
            "vendor/leaflet/leaflet.js",
            "vendor/leaflet/LICENSE",
        ):
            self.assertTrue((web_root / relative).is_file(), relative)
        self.assertIn('id="navMap"', index)
        self.assertIn('value="satellite"', index)
        self.assertIn(
            "https://api.maptiler.com/maps/satellite-v4/256/{z}/{x}/{y}.jpg?key=",
            map_js,
        )
        self.assertIn("Restored the previous map", map_js)
        self.assertIn('src="/map.js"', index)
        self.assertIn("window.navigationMap=new CoraNavigationMap", index)
        self.assertIn("Follow: OFF", index)
        self.assertIn("#arrayCanvas{display:block", index)
        self.assertNotIn("canvas{display:block", index)


if __name__ == "__main__":
    unittest.main()
