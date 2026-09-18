#!/usr/bin/env python3

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cora_triplet_adc import AxiDmaAdcSource, AxiStreamLayout  # noqa: E402
from cora_triplet_btr import BtrConfig, BtrEngine  # noqa: E402


class AxiDmaAdcSourceTests(unittest.TestCase):
    def make_capture(self) -> np.ndarray:
        words = BtrConfig().block_size * 21
        return (
            np.arange(words, dtype=np.int32) % (1 << 24) - (1 << 23)
        ).reshape(BtrConfig().block_size, 21)

    def test_layout_matches_vivado_frame(self):
        layout = AxiStreamLayout()
        layout.validate(1024)
        self.assertEqual(layout.frame_words, 512 * 21)
        self.assertEqual(layout.frame_bytes, 43_008)
        with self.assertRaisesRegex(ValueError, "multiple"):
            layout.validate(1000)

    def test_reads_two_sign_extended_packets_as_one_beamformer_block(self):
        expected = self.make_capture()
        with tempfile.TemporaryDirectory() as directory:
            capture = Path(directory) / "capture.raw"
            capture.write_bytes(expected.astype("<i4").tobytes())
            source = AxiDmaAdcSource(capture)
            try:
                actual = source.read_block()
                status = source.status(48_000)
            finally:
                source.close()
        np.testing.assert_array_equal(actual, expected)
        self.assertEqual(actual.shape, (1024, 21))
        self.assertEqual(status["frames_received"], 2)
        self.assertEqual(status["bytes_received"], 86_016)
        self.assertEqual(status["frame_bytes"], 43_008)
        self.assertEqual(status["payload_mb_s"], 4.032)

    def test_rejects_zero_padded_negative_sample(self):
        words = np.zeros(1024 * 21, dtype="<u4")
        words[0] = 0x00FFFFFF
        with tempfile.TemporaryDirectory() as directory:
            capture = Path(directory) / "bad.raw"
            capture.write_bytes(words.tobytes())
            source = AxiDmaAdcSource(capture)
            try:
                with self.assertRaisesRegex(ValueError, "not sign-extended"):
                    source.read_block()
            finally:
                source.close()

    def test_engine_processes_axi_block_and_reports_input_contract(self):
        samples = np.zeros((1024, 21), dtype="<i4")
        samples[:, 10] = np.rint(
            np.sin(2.0 * np.pi * 8_000.0 * np.arange(1024) / 48_000.0)
            * 2_000_000
        ).astype(np.int32)
        with tempfile.TemporaryDirectory() as directory:
            capture = Path(directory) / "capture.raw"
            capture.write_bytes(samples.tobytes())
            source = AxiDmaAdcSource(capture)
            engine = BtrEngine(BtrConfig(), adc_source=source)
            try:
                result = engine.process_once()
                status = engine.status()
            finally:
                source.close()
        self.assertNotIn("source_bearings_deg", result)
        self.assertEqual(result["input_payload_mb_s"], 4.032)
        self.assertEqual(status["schema_version"], 2)
        self.assertEqual(status["input"]["mode"], "axi")
        self.assertEqual(status["input"]["sample_format"], "signed-24-sign-extended-le32")
        self.assertEqual(status["input"]["frames_per_block"], 2)
        self.assertEqual(status["input"]["frames_received"], 2)


if __name__ == "__main__":
    unittest.main()
