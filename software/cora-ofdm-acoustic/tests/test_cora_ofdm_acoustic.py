import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cora_ofdm_acoustic import (  # noqa: E402
    AcousticConfig,
    HEADER_REPETITIONS,
    TRAINING_SYMBOLS,
    _bit_rows_to_grids,
    _bits_to_grid,
    _grid_to_symbol,
    _grids_to_symbols,
    acquire_superframe,
    bytes_to_bits,
    bits_to_bytes,
    colored_room_channel,
    convolutional_encode,
    decode_packet,
    decode_superframe,
    decode_superframe_slot,
    depuncture_rate_two_thirds,
    deinterleave_bits,
    encode_packet,
    encode_superframe,
    interleave_bits,
    make_acoustic_config,
    puncture_rate_two_thirds,
    psk8_demap,
    psk8_map,
    read_wav,
    viterbi_decode,
    write_wav,
)


def test_cached_carrier_geometry_and_batched_waveform_are_exact():
    cfg = make_acoustic_config("v3", 1500.0, 12000.0)
    assert cfg.active_bins is cfg.active_bins
    assert cfg.data_bins is cfg.data_bins
    assert cfg.bits_per_symbol == cfg.data_bins.size * 2
    assert not cfg.active_bins.flags.writeable
    assert not cfg.data_bins.flags.writeable

    rng = np.random.default_rng(20260724)
    rows = rng.integers(
        0,
        2,
        (4, cfg.bits_per_symbol),
        dtype=np.uint8,
    )
    signs = np.array((1.0, -1.0, 1.0, -1.0))
    expected_grids = np.stack(
        [
            _bits_to_grid(cfg, row, sign)
            for row, sign in zip(rows, signs)
        ]
    )
    actual_grids = _bit_rows_to_grids(cfg, rows, signs)
    np.testing.assert_array_equal(actual_grids, expected_grids)
    np.testing.assert_array_equal(
        _grids_to_symbols(cfg, actual_grids),
        np.stack(
            [_grid_to_symbol(cfg, grid) for grid in expected_grids]
        ),
    )


def test_bit_conversion_round_trip():
    payload = bytes(range(251))
    assert bits_to_bytes(bytes_to_bits(payload)) == payload


def test_8psk_gray_constellation_and_clean_packet_round_trip():
    labels = np.arange(8, dtype=np.uint8)
    bits = np.unpackbits(labels[:, None], axis=1)[:, -3:].reshape(-1)
    symbols = psk8_map(bits)
    np.testing.assert_array_equal(psk8_demap(symbols), bits)
    np.testing.assert_allclose(np.abs(symbols), 1.0)

    cfg = make_acoustic_config(
        "v3-r2/3",
        1500.0,
        15000.0,
        modulation="8psk",
    )
    payload = bytes(range(251)) * 2
    packet = encode_packet(payload, sequence=19, cfg=cfg)
    result = decode_packet(packet.samples, cfg=cfg)
    assert result.valid, result.error
    assert result.sequence == 19
    assert result.payload == payload
    assert cfg.bits_per_carrier == 3
    assert cfg.gross_bit_rate == 30_600.0


def test_waveform_parameters_and_real_samples():
    cfg = AcousticConfig()
    packet = encode_packet(b"hello")
    assert cfg.low_frequency_hz == 2250.0
    assert cfg.high_frequency_hz == 9750.0
    assert cfg.bits_per_symbol == 72
    assert cfg.gross_bit_rate == 10_800.0
    assert np.isrealobj(packet.samples)
    assert np.max(np.abs(packet.samples)) <= cfg.output_peak + 1e-6


def test_clean_decode_with_recording_offset():
    payload = b"Cora acoustic OFDM v2 clean smoke test"
    packet = encode_packet(payload, sequence=37)
    recording = np.concatenate((np.zeros(1943), packet.samples, np.zeros(811)))
    result = decode_packet(recording)
    assert result.valid, result.error
    assert result.sequence == 37
    assert result.payload == payload


def test_colored_multipath_decode():
    payload = bytes(range(127))
    packet = encode_packet(payload, sequence=255)
    recording = np.concatenate(
        (np.zeros(731), colored_room_channel(packet.samples, noise_dbfs=-48.0))
    )
    result = decode_packet(recording)
    assert result.valid, result.error
    assert result.sequence == 255
    assert result.payload == payload


def test_v3_convolutional_fec_corrects_isolated_errors():
    rng = np.random.default_rng(20260724)
    source = rng.integers(0, 2, 512, dtype=np.uint8)
    encoded = convolutional_encode(source)
    interleaved = interleave_bits(encoded)
    interleaved[[31, 207, 419, 733, 997]] ^= 1
    restored = deinterleave_bits(interleaved, encoded.size)
    decoded = viterbi_decode(restored)
    np.testing.assert_array_equal(decoded, source)


def test_rate_two_thirds_puncture_round_trip_and_erasure_decode():
    rng = np.random.default_rng(20260725)
    source = rng.integers(0, 2, 512, dtype=np.uint8)
    mother = convolutional_encode(source)
    punctured = puncture_rate_two_thirds(mother)
    assert punctured.size * 4 == mother.size * 3
    punctured[[31, 207, 419, 733, 760]] ^= 1

    restored, valid = depuncture_rate_two_thirds(
        punctured,
        mother.size,
    )
    assert int(np.sum(valid)) == punctured.size
    decoded = viterbi_decode(restored, valid)
    np.testing.assert_array_equal(decoded, source)


def test_v3_rate_two_thirds_packet_is_between_coded_and_uncoded():
    payload = bytes(range(251))
    uncoded = make_acoustic_config("uncoded", 2250.0, 9750.0)
    rate_half = make_acoustic_config("v3", 2250.0, 9750.0)
    rate_two_thirds = make_acoustic_config(
        "v3-r2/3",
        2250.0,
        9750.0,
    )
    packets = {
        "uncoded": encode_packet(payload, sequence=92, cfg=uncoded),
        "rate_half": encode_packet(payload, sequence=92, cfg=rate_half),
        "rate_two_thirds": encode_packet(
            payload,
            sequence=92,
            cfg=rate_two_thirds,
        ),
    }

    assert rate_two_thirds.modem_name == "v3-r2/3"
    assert (
        rate_two_thirds.body_code
        == "punctured-convolutional-k7-r2/3"
    )
    assert (
        packets["uncoded"].payload_symbols
        < packets["rate_two_thirds"].payload_symbols
        < packets["rate_half"].payload_symbols
    )

    recording = np.concatenate(
        (
            np.zeros(997),
            colored_room_channel(
                packets["rate_two_thirds"].samples,
                noise_dbfs=-60.0,
                seed=92,
            ),
        )
    )
    result = decode_packet(recording, cfg=rate_two_thirds)
    assert result.valid, result.error
    assert result.sequence == 92
    assert result.payload == payload

    mismatch = decode_packet(
        packets["rate_two_thirds"].samples,
        cfg=rate_half,
    )
    assert not mismatch.valid
    assert "wire version" in mismatch.error


def test_continuous_superframe_uses_one_sync_and_decodes_fixed_slots():
    cfg = make_acoustic_config("v3-r2/3", 2250.0, 9750.0)
    items = [
        (bytes([index + 1]) * (404 if index < 7 else 137), index)
        for index in range(8)
    ]
    superframe = encode_superframe(items, cfg=cfg)
    standalone_s = sum(
        encode_packet(payload, sequence=sequence, cfg=cfg).duration_s
        for payload, sequence in items
    )
    assert superframe.packet_count == 8
    assert superframe.slot_payload_bytes == 404
    assert len(superframe.decode_blocks) == 4
    assert superframe.decode_blocks[0][2:] == (0, 2)
    assert superframe.decode_blocks[-1][2:] == (6, 8)
    assert superframe.duration_s < standalone_s

    recording = np.concatenate(
        (
            np.zeros(733),
            colored_room_channel(
                superframe.samples,
                noise_dbfs=-60.0,
                seed=173,
            ),
        )
    )
    results = decode_superframe(
        recording,
        expected_sequences=list(range(8)),
        slot_payload_bytes=superframe.slot_payload_bytes,
        cfg=cfg,
    )
    assert len(results) == len(items)
    for result, (payload, sequence) in zip(results, items):
        assert result.valid, result.error
        assert result.sequence == sequence
        assert result.payload == payload

    block_results = []
    for sample_begin, sample_end, item_begin, item_end in (
        superframe.decode_blocks
    ):
        begin = max(0, 733 + sample_begin - round(0.10 * cfg.sample_rate))
        end = min(
            recording.size,
            733 + sample_end + round(0.04 * cfg.sample_rate),
        )
        block_results.extend(
            decode_superframe(
                recording[begin:end],
                expected_sequences=list(range(item_begin, item_end)),
                slot_payload_bytes=superframe.slot_payload_bytes,
                sync_search_samples=round(0.20 * cfg.sample_rate),
                cfg=cfg,
            )
        )
    assert [result.payload for result in block_results] == [
        payload for payload, _sequence in items
    ]

    slot_results = []
    slot_symbols = HEADER_REPETITIONS + superframe.payload_symbols
    for sample_begin, sample_end, item_begin, item_end in (
        superframe.decode_blocks
    ):
        begin = max(0, 733 + sample_begin - round(0.10 * cfg.sample_rate))
        end = min(
            recording.size,
            733 + sample_end + round(0.04 * cfg.sample_rate),
        )
        block = recording[begin:end]
        acquisition = acquire_superframe(
            block,
            sync_search_samples=round(0.20 * cfg.sample_rate),
            cfg=cfg,
        )
        for local_index, sequence in enumerate(range(item_begin, item_end)):
            slot_results.append(
                decode_superframe_slot(
                    block,
                    expected_sequence=sequence,
                    slot_payload_bytes=superframe.slot_payload_bytes,
                    symbol_offset=(
                        TRAINING_SYMBOLS + local_index * slot_symbols
                    )
                    * cfg.symbol_len,
                    acquisition=acquisition,
                    cfg=cfg,
                )
            )
    assert [result.payload for result in slot_results] == [
        payload for payload, _sequence in items
    ]


def test_v3_wide_band_colored_multipath_decode():
    cfg = make_acoustic_config("v3", 1500.0, 15000.0)
    payload = bytes(range(251))
    packet = encode_packet(payload, sequence=83, cfg=cfg)
    recording = np.concatenate(
        (
            np.zeros(997),
            colored_room_channel(
                packet.samples,
                noise_dbfs=-60.0,
                seed=83,
            ),
        )
    )
    result = decode_packet(recording, cfg=cfg)
    assert result.valid, result.error
    assert result.sequence == 83
    assert result.payload == payload
    assert cfg.low_frequency_hz == 1500.0
    assert cfg.high_frequency_hz == 15000.0
    assert len(cfg.pilot_bins) == 5


def test_v3_512_point_mode_decodes_and_preserves_physical_pilots():
    cfg = make_acoustic_config(
        "v3",
        1500.0,
        12000.0,
        fft_len=512,
    )
    baseline = make_acoustic_config("v3", 1500.0, 12000.0)
    assert cfg.fft_len == 512
    assert cfg.cp_len == 64
    assert cfg.subcarrier_spacing_hz == 93.75
    assert cfg.cyclic_prefix_duration_s == baseline.cyclic_prefix_duration_s
    assert cfg.data_bins.size == 108
    assert cfg.bits_per_symbol == 216
    assert cfg.gross_bit_rate == 18_000.0
    np.testing.assert_array_equal(
        np.asarray(cfg.pilot_bins) * cfg.subcarrier_spacing_hz,
        np.asarray(baseline.pilot_bins) * baseline.subcarrier_spacing_hz,
    )

    payload = bytes(range(251))
    packet = encode_packet(payload, sequence=84, cfg=cfg)
    recording = np.concatenate(
        (
            np.zeros(997),
            colored_room_channel(
                packet.samples,
                noise_dbfs=-60.0,
                seed=84,
            ),
        )
    )
    result = decode_packet(recording, cfg=cfg)
    assert result.valid, result.error
    assert result.sequence == 84
    assert result.payload == payload


def test_v3_uses_fewer_body_symbols_and_rejects_v2_decoder():
    payload = bytes(range(251))
    v2 = make_acoustic_config("v2", 2250.0, 9750.0)
    v3 = make_acoustic_config("v3", 2250.0, 9750.0)
    v2_packet = encode_packet(payload, cfg=v2)
    v3_packet = encode_packet(payload, cfg=v3)
    assert v3_packet.payload_symbols < v2_packet.payload_symbols
    mismatch = decode_packet(v3_packet.samples, cfg=v2)
    assert not mismatch.valid
    assert "wire version" in mismatch.error


def test_uncoded_body_round_trip_is_shorter_and_wire_distinct():
    payload = bytes(range(251))
    uncoded = make_acoustic_config("uncoded", 2250.0, 9750.0)
    v3 = make_acoustic_config("v3", 2250.0, 9750.0)
    uncoded_packet = encode_packet(payload, sequence=91, cfg=uncoded)
    v3_packet = encode_packet(payload, sequence=91, cfg=v3)

    assert uncoded.modem_name == "uncoded"
    assert uncoded.body_code == "none"
    assert uncoded_packet.payload_symbols < v3_packet.payload_symbols

    result = decode_packet(uncoded_packet.samples, cfg=uncoded)
    assert result.valid, result.error
    assert result.sequence == 91
    assert result.payload == payload

    mismatch = decode_packet(uncoded_packet.samples, cfg=v3)
    assert not mismatch.valid
    assert "wire version" in mismatch.error


def test_wav_round_trip(tmp_path):
    payload = b"WAV transport"
    packet = encode_packet(payload)
    wav_path = tmp_path / "packet.wav"
    write_wav(wav_path, packet.samples, AcousticConfig().sample_rate)
    samples, rate = read_wav(wav_path)
    result = decode_packet(samples)
    assert rate == 48_000
    assert result.valid, result.error
    assert result.payload == payload
