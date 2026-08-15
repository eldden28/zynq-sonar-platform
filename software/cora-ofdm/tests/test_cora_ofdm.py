#!/usr/bin/env python3

import contextlib
import importlib.util
import io
import pathlib
import sys
import unittest


SOURCE = pathlib.Path(__file__).parents[1] / "cora_ofdm.py"
SPEC = importlib.util.spec_from_file_location("cora_ofdm", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class CoraOfdmTest(unittest.TestCase):
    def test_default_packet_decodes_with_low_ber(self):
        config = MODULE.ModemConfig()
        packet = MODULE.make_packet(config, payload_symbols=4, seed=20260723)
        taps = MODULE.acoustic_taps(config)
        flowgraph = MODULE.UnderwaterOfdmFlowgraph(
            cfg=config,
            packet=packet,
            taps=taps,
            snr_db=18.0,
            doppler_ppm=900.0,
            cfo_hz=5.0,
            noise_seed=20260723,
            realtime=False,
        )
        flowgraph.run()
        received = MODULE.np.asarray(
            flowgraph.sink.data(), dtype=MODULE.np.complex64
        )
        result = MODULE.receive(config, packet, received, 900.0)
        self.assertLess(result.ber, 0.03)
        self.assertAlmostEqual(result.cfo_estimate_hz, 5.0, delta=1.0)

    def test_high_snr_packet_is_error_free(self):
        config = MODULE.ModemConfig()
        payload_bits = MODULE.np.tile(
            MODULE.np.array([0, 1, 1, 0], dtype=MODULE.np.uint8),
            config.data_bins.size * 2,
        )
        packet = MODULE.make_packet(
            config,
            payload_symbols=4,
            seed=20260723,
            payload_bits=payload_bits,
        )
        flowgraph = MODULE.UnderwaterOfdmFlowgraph(
            cfg=config,
            packet=packet,
            taps=MODULE.acoustic_taps(config),
            snr_db=40.0,
            doppler_ppm=900.0,
            cfo_hz=5.0,
            noise_seed=20260723,
            realtime=False,
        )
        flowgraph.run()
        received = MODULE.np.asarray(
            flowgraph.sink.data(), dtype=MODULE.np.complex64
        )
        result = MODULE.receive(config, packet, received, 900.0)
        self.assertEqual(result.bit_errors, 0)
        MODULE.np.testing.assert_array_equal(
            result.decoded_bits, payload_bits
        )

    def test_packet_batch_decodes_each_guarded_packet(self):
        config = MODULE.ModemConfig()
        packets = [
            MODULE.make_packet(config, payload_symbols=4, seed=20260723 + index)
            for index in range(3)
        ]
        executions = MODULE.run_packet_batch(
            cfg=config,
            packets=packets,
            taps=MODULE.acoustic_taps(config),
            snr_db=18.0,
            doppler_ppm=900.0,
            cfo_hz=5.0,
            noise_seed=20260723,
            realtime=False,
        )
        self.assertEqual(len(executions), 3)
        self.assertTrue(all(item.receiver is not None for item in executions))
        self.assertTrue(
            all(item.receiver.ber < 0.03 for item in executions)
        )

    def test_symbol_matrix_matches_scalar_extraction(self):
        config = MODULE.ModemConfig()
        samples = MODULE.np.arange(
            config.symbol_len * 4, dtype=MODULE.np.float32
        ).astype(MODULE.np.complex64)
        first_useful = config.cp_len
        matrix = MODULE.symbol_matrix(
            samples, first_useful, symbol_count=3, cfg=config
        )
        scalar = MODULE.np.vstack(
            [
                samples[start : start + config.fft_len]
                for start in (
                    first_useful + index * config.symbol_len
                    for index in range(3)
                )
            ]
        )
        MODULE.np.testing.assert_array_equal(matrix, scalar)
        self.assertFalse(matrix.flags.writeable)

    def test_fft_sync_matches_direct_reference(self):
        rng = MODULE.np.random.default_rng(20260724)
        samples = (
            rng.normal(size=4096) + 1j * rng.normal(size=4096)
        ).astype(MODULE.np.complex64)
        reference = (
            rng.normal(size=320) + 1j * rng.normal(size=320)
        ).astype(MODULE.np.complex64)
        samples[1234 : 1234 + reference.size] += 5.0 * reference
        direct_start, direct_metric = MODULE.normalized_sync_direct(
            samples, reference
        )
        fft_start, fft_metric = MODULE.normalized_sync(samples, reference)
        self.assertEqual(direct_start, 1234)
        self.assertEqual(fft_start, direct_start)
        MODULE.np.testing.assert_allclose(
            fft_metric, direct_metric, rtol=1e-5, atol=1e-7
        )

    def test_benchmark_cli_and_json(self):
        with contextlib.redirect_stdout(io.StringIO()):
            status = MODULE.main(
                [
                    "--mode",
                    "benchmark",
                    "--packets",
                    "1",
                    "--payload-symbols",
                    "4",
                    "--quiet",
                ]
            )
        self.assertEqual(status, 0)

    def test_monitor_endpoint_validation(self):
        self.assertEqual(
            MODULE.parse_monitor_endpoint("192.168.10.1:7355"),
            ("192.168.10.1", 7355),
        )
        with self.assertRaises(ValueError):
            MODULE.parse_monitor_endpoint("missing-port")

    def test_doppler_filter_taps_are_cached(self):
        MODULE._doppler_resampler_taps.cache_clear()
        first = MODULE._doppler_resampler_taps(1.0009)
        second = MODULE._doppler_resampler_taps(1.0009)
        self.assertIs(first, second)
        self.assertGreater(len(first), 0)
        self.assertEqual(
            MODULE._doppler_resampler_taps.cache_info().hits, 1
        )


if __name__ == "__main__":
    unittest.main()
