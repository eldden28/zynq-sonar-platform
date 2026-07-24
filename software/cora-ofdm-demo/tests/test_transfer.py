#!/usr/bin/env python3

import importlib.util
import pathlib
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).parents[2]
OFDM_SOURCE = ROOT / "cora-ofdm" / "cora_ofdm.py"
OFDM_SPEC = importlib.util.spec_from_file_location("cora_ofdm", OFDM_SOURCE)
OFDM = importlib.util.module_from_spec(OFDM_SPEC)
assert OFDM_SPEC.loader is not None
sys.modules[OFDM_SPEC.name] = OFDM
OFDM_SPEC.loader.exec_module(OFDM)

TRANSFER_SOURCE = pathlib.Path(__file__).parents[1] / "cora_ofdm_transfer.py"
TRANSFER_SPEC = importlib.util.spec_from_file_location(
    "cora_ofdm_transfer", TRANSFER_SOURCE
)
TRANSFER = importlib.util.module_from_spec(TRANSFER_SPEC)
assert TRANSFER_SPEC.loader is not None
sys.modules[TRANSFER_SPEC.name] = TRANSFER
TRANSFER_SPEC.loader.exec_module(TRANSFER)


class CoraOfdmTransferTest(unittest.TestCase):
    def test_frame_round_trip_and_crc_detection(self):
        config = OFDM.ModemConfig()
        frame_bytes = TRANSFER.frame_size_bytes(config, payload_symbols=4)
        payload = bytes(range(100))
        bits = TRANSFER.encode_frame(payload, 7, 19, frame_bytes)
        self.assertEqual(
            TRANSFER.decode_frame(bits, 7, 19),
            payload,
        )
        corrupted = bits.copy()
        corrupted[TRANSFER.HEADER.size * 8 + 11] ^= 1
        with self.assertRaisesRegex(ValueError, "CRC"):
            TRANSFER.decode_frame(corrupted, 7, 19)

    def test_tiny_rgb_transfer_is_byte_exact(self):
        source = bytes((index * 37) & 0xFF for index in range(10 * 5 * 3))
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            input_path = root / "source.rgb"
            output_path = root / "received.rgb"
            state_path = root / "status.json"
            input_path.write_bytes(source)
            args = TRANSFER.parse_args(
                [
                    "--input",
                    str(input_path),
                    "--output",
                    str(output_path),
                    "--state",
                    str(state_path),
                    "--width",
                    "10",
                    "--height",
                    "5",
                    "--payload-symbols",
                    "4",
                    "--batch-size",
                    "2",
                    "--snr-db",
                    "60",
                ]
            )
            result = TRANSFER.transfer(args)
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["retries"], 0)
            self.assertEqual(output_path.read_bytes(), source)
            self.assertEqual(
                result["source_sha256"], result["output_sha256"]
            )


if __name__ == "__main__":
    unittest.main()
