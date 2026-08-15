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
    def test_dashboard_defaults_to_validated_fast_profile(self):
        state = DASHBOARD.idle_state(True, 8082)
        self.assertEqual(state["modem_version"], "v3-r2/3")
        self.assertEqual(
            state["body_code"],
            "punctured-convolutional-k7-r2/3",
        )
        self.assertEqual(state["fft_len"], 256)
        self.assertEqual(state["low_frequency_hz"], 2062.5)
        self.assertEqual(state["high_frequency_hz"], 15000.0)
        self.assertEqual(state["data_subcarriers"], 65)
        self.assertEqual(state["gross_bit_rate"], 19_500.0)
        self.assertEqual(state["output_channel"], "left")
        self.assertEqual(state["modulation"], "qpsk")
        self.assertEqual(state["bits_per_carrier"], 2)

    def test_8psk_gray_constellation_round_trip(self):
        labels = np.arange(8, dtype=np.uint8)
        bits = np.unpackbits(labels[:, None], axis=1)[:, -3:].reshape(-1)
        symbols = ACOUSTIC.psk8_map(bits)

        self.assertTrue(
            np.array_equal(ACOUSTIC.psk8_demap(symbols), bits)
        )
        np.testing.assert_allclose(np.abs(symbols), 1.0)
        phases = np.mod(np.angle(symbols), 2.0 * np.pi)
        nearest = np.round(phases / (np.pi / 4.0)).astype(int) % 8
        around_ring = np.empty(8, dtype=np.uint8)
        around_ring[nearest] = labels
        adjacent_changes = np.count_nonzero(
            np.unpackbits(
                np.bitwise_xor(
                    around_ring,
                    np.roll(around_ring, -1),
                )[:, None],
                axis=1,
            ),
            axis=1,
        )
        np.testing.assert_array_equal(adjacent_changes, 1)

    def test_air_output_routes_mono_to_one_speaker(self):
        samples = np.array((-1.0, -0.25, 0.0, 0.25, 1.0))

        left_backend = TRANSFER.AlsaAirBackend(output_channel="left")
        left = np.frombuffer(
            left_backend._stereo_pcm(samples),
            dtype="<i2",
        ).reshape(-1, 2)
        np.testing.assert_array_equal(left[:, 1], 0)
        self.assertTrue(np.any(left[:, 0]))

        right_backend = TRANSFER.AlsaAirBackend(output_channel="right")
        right = np.frombuffer(
            right_backend._stereo_pcm(samples),
            dtype="<i2",
        ).reshape(-1, 2)
        np.testing.assert_array_equal(right[:, 0], 0)
        np.testing.assert_array_equal(right[:, 1], left[:, 0])

        stereo_backend = TRANSFER.AlsaAirBackend(output_channel="stereo")
        stereo = np.frombuffer(
            stereo_backend._stereo_pcm(samples),
            dtype="<i2",
        ).reshape(-1, 2)
        np.testing.assert_array_equal(stereo[:, 0], stereo[:, 1])

        with self.assertRaisesRegex(ValueError, "output channel"):
            TRANSFER.AlsaAirBackend(output_channel="center")

    def test_receiver_waterfall_tracks_pcm_tone_and_cursor(self):
        monitor = DASHBOARD.ReceiverSpectrumMonitor(
            fft_size=1024,
            hop_size=1024,
            history_rows=4,
        )
        sample_count = 4096
        time_axis = np.arange(sample_count) / 48_000.0
        samples = 0.25 * np.sin(2.0 * np.pi * 6000.0 * time_axis)
        pcm = np.rint(samples * 32767.0).astype("<i2").tobytes()
        monitor.ingest_pcm(pcm[:3073])
        monitor.ingest_pcm(pcm[3073:])

        snapshot = monitor.snapshot(-1)
        self.assertEqual(snapshot["sample_rate"], 48_000)
        self.assertEqual(snapshot["fft_size"], 1024)
        self.assertEqual(len(snapshot["rows"]), 4)
        self.assertEqual(len(snapshot["rows"][-1]["values"]), 513)
        self.assertAlmostEqual(
            snapshot["rows"][-1]["peak_hz"],
            6000.0,
            delta=48.0,
        )
        cursor = snapshot["latest_id"]
        self.assertEqual(monitor.snapshot(cursor)["rows"], [])

        monitor.reset()
        reset_snapshot = monitor.snapshot(cursor)
        self.assertTrue(reset_snapshot["reset"])
        self.assertEqual(reset_snapshot["latest_id"], -1)

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

    def test_8psk_rate_two_thirds_transfer_reports_higher_gross_rate(self):
        cfg = ACOUSTIC.make_acoustic_config(
            "v3-r2/3",
            1500.0,
            15000.0,
            fft_len=256,
            modulation="8psk",
        )
        text = "Constant-envelope 8-PSK experiment ✓\n" * 20
        output, state = TRANSFER.transfer_text(
            text,
            backend=TRANSFER.ColoredRoomBackend(-60.0, cfg=cfg),
            chunk_bytes=192,
        )

        self.assertEqual(output, text.encode())
        self.assertEqual(state["status"], "complete")
        self.assertEqual(state["modulation"], "8psk")
        self.assertEqual(state["bits_per_carrier"], 3)
        self.assertEqual(state["data_subcarriers"], 68)
        self.assertEqual(state["gross_bit_rate"], 30_600.0)

    def test_8psk_rate_half_transfer_uses_stronger_fec(self):
        cfg = ACOUSTIC.make_acoustic_config(
            "v3",
            1500.0,
            15000.0,
            fft_len=256,
            modulation="8psk",
        )
        text = "Rate-half 8-PSK experiment ✓\n" * 20
        output, state = TRANSFER.transfer_text(
            text,
            backend=TRANSFER.ColoredRoomBackend(-60.0, cfg=cfg),
            chunk_bytes=192,
        )

        self.assertEqual(output, text.encode())
        self.assertEqual(state["modulation"], "8psk")
        self.assertEqual(state["body_code"], "convolutional-k7-r1/2")
        self.assertEqual(state["packet_errors"], 0)
        self.assertEqual(state["gross_bit_rate"], 30_600.0)

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

    def test_streaming_results_reach_dashboard_before_batch_returns(self):
        updates = []

        class StreamingBackend:
            name = "air"
            framing = "continuous-superframe"
            burst_size = 64

            def __init__(self):
                self.returned = False
                self.commits_seen_inside_batch = []

            @staticmethod
            def _result(payload, sequence):
                return ACOUSTIC.DecodeResult(
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

            def transceive_batch(self, _items):
                raise AssertionError("streaming batch method was not used")

            def transceive_batch_stream(self, items, result_callback):
                results = [
                    self._result(payload, sequence)
                    for payload, sequence in items
                ]
                for index, result in enumerate(results):
                    result_callback(index, result)
                    self.commits_seen_inside_batch.append(len(updates))
                self.returned = True
                return results

            def transceive(self, _payload, _sequence):
                raise AssertionError("single-packet fallback was not expected")

        backend = StreamingBackend()
        text = "stream these chunks while the sound is playing"
        output, state = TRANSFER.transfer_text(
            text,
            backend=backend,
            chunk_bytes=12,
            packet_callback=lambda values, chunk: updates.append(
                (
                    values["packets_received"],
                    chunk,
                    backend.returned,
                )
            ),
        )

        self.assertEqual(output, text.encode())
        self.assertEqual(state["status"], "complete")
        self.assertEqual(
            backend.commits_seen_inside_batch,
            list(range(1, state["packets_total"] + 1)),
        )
        self.assertTrue(all(not returned for _, _, returned in updates))

    def test_streaming_failure_defers_retry_and_preserves_order(self):
        updates = []

        class RetryBackend:
            name = "air"
            framing = "continuous-superframe"
            burst_size = 64

            def __init__(self):
                self.session_open = False

            @staticmethod
            def _result(payload, sequence, valid=True):
                return ACOUSTIC.DecodeResult(
                    valid=valid,
                    sequence=sequence,
                    payload=payload if valid else b"",
                    payload_length=len(payload) if valid else 0,
                    start_sample=0,
                    sync_metric=1.0,
                    payload_symbols=1,
                    crc_expected=0,
                    crc_received=0,
                    error=None if valid else "injected CRC failure",
                )

            def transceive_batch(self, _items):
                raise AssertionError("streaming batch method was not used")

            def transceive_batch_stream(self, items, result_callback):
                results = [
                    self._result(*items[0], valid=False),
                    self._result(*items[1]),
                ]
                self.session_open = True
                result_callback(1, results[1])
                result_callback(0, results[0])
                self.session_open = False
                return results

            def transceive(self, payload, sequence):
                self.assert_session_closed()
                return self._result(payload, sequence)

            def assert_session_closed(self):
                if self.session_open:
                    raise AssertionError("retry overlapped the main session")

        backend = RetryBackend()
        text = "abcdefgh"
        output, state = TRANSFER.transfer_text(
            text,
            backend=backend,
            chunk_bytes=4,
            packet_callback=lambda values, chunk: updates.append(
                (values["packets_received"], chunk)
            ),
        )

        self.assertEqual(output, text.encode())
        self.assertEqual([number for number, _chunk in updates], [1, 2])
        self.assertEqual(state["retries"], 1)
        self.assertEqual(state["packet_errors"], 1)

    def test_default_session_covers_maximum_coded_dashboard_transfer(self):
        class PerfectSessionBackend:
            name = "air"
            framing = "continuous-superframe"
            burst_size = TRANSFER.DEFAULT_BURST_PACKETS

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

        backend = PerfectSessionBackend()
        text = "x" * 16_184
        output, state = TRANSFER.transfer_text(
            text,
            backend=backend,
            chunk_bytes=384,
        )

        self.assertEqual(TRANSFER.DEFAULT_BURST_PACKETS, 64)
        self.assertEqual(state["packets_total"], 43)
        self.assertEqual(backend.batch_lengths, [43])
        self.assertEqual(output, text.encode())
        self.assertEqual(state["status"], "complete")


if __name__ == "__main__":
    unittest.main()
