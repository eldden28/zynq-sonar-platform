#!/usr/bin/env python3

import importlib.util
import pathlib
import sys
import unittest
from types import SimpleNamespace

import numpy as np


ROOT = pathlib.Path(__file__).parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ACOUSTIC = load(
    "cora_ofdm_acoustic",
    ROOT / "cora-ofdm-acoustic" / "cora_ofdm_acoustic.py",
)
TRANSFER = load(
    "cora_ofdm_text_transfer",
    pathlib.Path(__file__).parents[1] / "cora_ofdm_text_transfer.py",
)
DASHBOARD = load(
    "cora_ofdm_text_dashboard",
    pathlib.Path(__file__).parents[1] / "cora_ofdm_text_dashboard.py",
)


class TextTransferTest(unittest.TestCase):
    def test_frame_round_trip_and_crc_detection(self):
        payload = "packet ✓".encode()
        frame = TRANSFER.encode_text_frame(
            payload,
            transfer_id=0x12345678,
            sequence=3,
            total_packets=9,
        )
        self.assertEqual(
            TRANSFER.decode_text_frame(
                frame,
                transfer_id=0x12345678,
                sequence=3,
                total_packets=9,
            ),
            payload,
        )
        corrupted = bytearray(frame)
        corrupted[-1] ^= 1
        with self.assertRaisesRegex(ValueError, "CRC"):
            TRANSFER.decode_text_frame(
                bytes(corrupted),
                transfer_id=0x12345678,
                sequence=3,
                total_packets=9,
            )

    def test_progressive_unicode_transfer_is_byte_exact(self):
        text = (
            "Cora acoustic terminal — packetized UTF-8 ✓\n"
            "The receiver only commits validated chunks.\n"
        ) * 3
        updates = []
        output, state = TRANSFER.transfer_text(
            text,
            backend=TRANSFER.ColoredRoomBackend(-48.0),
            chunk_bytes=47,
            packet_callback=lambda values, chunk: updates.append(
                (values, chunk)
            ),
        )
        self.assertEqual(output, text.encode())
        self.assertEqual(state["status"], "complete")
        self.assertEqual(state["source_sha256"], state["output_sha256"])
        self.assertEqual(
            b"".join(chunk for _values, chunk in updates),
            text.encode(),
        )
        self.assertEqual(
            updates[-1][0]["packets_received"],
            state["packets_total"],
        )

    def test_v3_wide_band_transfer_reports_selected_profile(self):
        cfg = ACOUSTIC.make_acoustic_config("v3", 1500.0, 15000.0)
        text = "Wide-band v3 convolutional OFDM ✓\n" * 12
        output, state = TRANSFER.transfer_text(
            text,
            backend=TRANSFER.ColoredRoomBackend(-60.0, cfg=cfg),
            chunk_bytes=192,
        )
        self.assertEqual(output, text.encode())
        self.assertEqual(state["status"], "complete")
        self.assertEqual(state["modem_version"], "v3")
        self.assertEqual(state["body_code"], "convolutional-k7-r1/2")
        self.assertEqual(state["low_frequency_hz"], 1500.0)
        self.assertEqual(state["high_frequency_hz"], 15000.0)
        self.assertGreater(state["data_subcarriers"], 36)

    def test_v3_512_point_transfer_reports_fft_metadata(self):
        cfg = ACOUSTIC.make_acoustic_config(
            "v3",
            1500.0,
            12000.0,
            fft_len=512,
        )
        text = "FFT 512 acoustic OFDM ✓\n" * 12
        output, state = TRANSFER.transfer_text(
            text,
            backend=TRANSFER.ColoredRoomBackend(-60.0, cfg=cfg),
            chunk_bytes=192,
        )
        self.assertEqual(output, text.encode())
        self.assertEqual(state["fft_len"], 512)
        self.assertEqual(state["cp_len"], 64)
        self.assertEqual(state["subcarrier_spacing_hz"], 93.75)
        self.assertEqual(state["data_subcarriers"], 108)
        self.assertEqual(state["gross_bit_rate"], 18_000.0)

    def test_uncoded_transfer_reports_crc_only_body(self):
        cfg = ACOUSTIC.make_acoustic_config(
            "uncoded",
            2250.0,
            9750.0,
            fft_len=256,
        )
        text = "Uncoded FFT256 experiment ✓\n" * 12
        output, state = TRANSFER.transfer_text(
            text,
            backend=TRANSFER.ColoredRoomBackend(-60.0, cfg=cfg),
            chunk_bytes=192,
        )
        self.assertEqual(output, text.encode())
        self.assertEqual(state["modem_version"], "uncoded")
        self.assertEqual(state["body_code"], "none")
        self.assertEqual(state["fft_len"], 256)
        self.assertEqual(state["gross_bit_rate"], 10_800.0)

    def test_rate_two_thirds_transfer_reports_punctured_body(self):
        cfg = ACOUSTIC.make_acoustic_config(
            "v3-r2/3",
            2250.0,
            9750.0,
            fft_len=256,
        )
        text = "Punctured rate two thirds FFT256 ✓\n" * 12
        output, state = TRANSFER.transfer_text(
            text,
            backend=TRANSFER.ColoredRoomBackend(-60.0, cfg=cfg),
            chunk_bytes=192,
        )
        self.assertEqual(output, text.encode())
        self.assertEqual(state["modem_version"], "v3-r2/3")
        self.assertEqual(
            state["body_code"],
            "punctured-convolutional-k7-r2/3",
        )
        self.assertEqual(state["fft_len"], 256)

    def test_dashboard_uses_measured_uncoded_chunk_size(self):
        args = SimpleNamespace(
            allow_air=False,
            port=8082,
            burst_packets=8,
            playback_device="plughw:0,0",
            capture_device="plughw:0,0",
            noise_dbfs=-60.0,
            chunk_bytes=384,
            max_retries=2,
        )
        controller = DASHBOARD.TextDemoController(args)
        cfg = ACOUSTIC.make_acoustic_config("uncoded", 2250.0, 9750.0)
        observed = {}
        original = DASHBOARD.transfer_text

        def fake_transfer(text, *, packet_callback, chunk_bytes, **_kwargs):
            payload = text.encode()
            observed["chunk_bytes"] = chunk_bytes
            values = {"status": "complete"}
            packet_callback(values, payload)
            return payload, values

        DASHBOARD.transfer_text = fake_transfer
        try:
            controller._run("uncoded dashboard", "loopback", cfg)
        finally:
            DASHBOARD.transfer_text = original

        self.assertEqual(observed["chunk_bytes"], 192)
        self.assertEqual(controller.state["status"], "complete")

    def test_streaming_burst_windows_decode_delayed_colored_audio(self):
        cfg = ACOUSTIC.AcousticConfig(
            leading_silence_s=0.04,
            trailing_silence_s=0.04,
        )
        payloads = [bytes([index + 1]) * 114 for index in range(4)]
        packets = [
            ACOUSTIC.encode_packet(payload, sequence=index, cfg=cfg)
            for index, payload in enumerate(payloads)
        ]
        pre_roll = round(0.15 * cfg.sample_rate)
        device_delay = 731
        transmitted = np.concatenate(
            [packet.samples for packet in packets]
        )
        captured = np.concatenate(
            (
                np.zeros(pre_roll + device_delay),
                ACOUSTIC.colored_room_channel(
                    transmitted,
                    noise_dbfs=-52.0,
                    seed=19,
                ),
                np.zeros(round(0.25 * cfg.sample_rate)),
            )
        )

        cumulative = 0
        for index, packet in enumerate(packets):
            begin, end = TRANSFER.AlsaAirBackend._window_bounds(
                packet,
                nominal_start=pre_roll + cumulative,
                capture_samples=captured.size,
                sample_rate=cfg.sample_rate,
            )
            result = ACOUSTIC.decode_packet(captured[begin:end], cfg=cfg)
            self.assertTrue(result.valid, result.error)
            self.assertEqual(result.sequence, index)
            self.assertEqual(result.payload, payloads[index])
            cumulative += packet.samples.size

    def test_transfer_uses_bursts_and_preserves_progress_order(self):
        class PerfectBurstBackend:
            name = "air"
            burst_size = 4

            def __init__(self):
                self.batch_lengths = []

            def transceive_batch(self, items):
                self.batch_lengths.append(len(items))
                return [
                    ACOUSTIC.DecodeResult(
                        valid=True,
                        sequence=sequence,
                        payload=payload,
                        payload_length=len(payload),
                        start_sample=0,
                        sync_metric=1.0,
                        payload_symbols=1,
                        crc_expected=0,
                        crc_received=0,
                    )
                    for payload, sequence in items
                ]

            def transceive(self, payload, sequence):
                raise AssertionError("single-packet fallback was not expected")

        backend = PerfectBurstBackend()
        updates = []
        text = "0123456789" * 10
        output, state = TRANSFER.transfer_text(
            text,
            backend=backend,
            chunk_bytes=11,
            packet_callback=lambda values, chunk: updates.append(
                (values["packets_received"], chunk)
            ),
        )
        self.assertEqual(output, text.encode())
        self.assertEqual(state["status"], "complete")
        self.assertEqual(backend.batch_lengths, [4, 4, 2])
        self.assertEqual(
            [sequence for sequence, _chunk in updates],
            list(range(1, 11)),
        )


if __name__ == "__main__":
    unittest.main()
