import sys
from pathlib import Path
from types import SimpleNamespace
import time

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cora_conventional_acoustic import (  # noqa: E402
    MAX_PAYLOAD,
    bpsk_demap,
    bpsk_map,
    colored_acoustic_channel,
    decode_packet,
    encode_packet,
    make_config,
    qpsk_demap,
    qpsk_map,
    read_wav,
    write_wav,
)
from cora_conventional_dashboard import ConventionalController  # noqa: E402


def test_constellation_maps_round_trip():
    bpsk_bits = np.asarray([0, 1, 1, 0], dtype=np.uint8)
    np.testing.assert_array_equal(bpsk_demap(bpsk_map(bpsk_bits)), bpsk_bits)

    qpsk_bits = np.asarray([0, 0, 0, 1, 1, 1, 1, 0], dtype=np.uint8)
    np.testing.assert_array_equal(qpsk_demap(qpsk_map(qpsk_bits)), qpsk_bits)
    np.testing.assert_allclose(np.abs(qpsk_map(qpsk_bits)), 1.0)


@pytest.mark.parametrize(
    ("modulation", "order", "bits_per_symbol"),
    (
        ("bpsk", 4, 1),
        ("qpsk", 4, 2),
        ("fsk", 4, 1),
        ("mfsk", 4, 2),
        ("mfsk", 8, 3),
        ("mfsk", 16, 4),
    ),
)
def test_clean_and_colored_round_trip(modulation, order, bits_per_symbol):
    cfg = make_config(modulation, mfsk_order=order)
    payload = bytes(range(127))
    packet = encode_packet(payload, sequence=73, cfg=cfg)
    assert cfg.bits_per_symbol == bits_per_symbol
    assert packet.data_symbols == pytest.approx(
        ((len(payload) + 18) * 8) / bits_per_symbol,
        abs=1,
    )

    clean = decode_packet(packet.samples, cfg=cfg)
    assert clean.valid, clean.error
    assert clean.sequence == 73
    assert clean.payload == payload

    recording = np.concatenate(
        (
            np.zeros(997),
            colored_acoustic_channel(
                packet.samples,
                noise_dbfs=-48.0,
                seed=73,
            ),
        )
    )
    colored = decode_packet(recording, cfg=cfg)
    assert colored.valid, colored.error
    assert colored.payload == payload


def test_qpsk_tracks_carrier_offset_from_training():
    tx_cfg = make_config("qpsk", carrier_frequency_hz=6002.0)
    rx_cfg = make_config("qpsk", carrier_frequency_hz=6000.0)
    packet = encode_packet(b"coherent carrier recovery", cfg=tx_cfg)
    result = decode_packet(packet.samples, cfg=rx_cfg)
    assert result.valid, result.error
    assert result.payload == packet.payload
    assert result.carrier_offset_hz == pytest.approx(2.0, abs=0.1)


def test_mode_mismatch_is_rejected_by_header_crc_or_identity():
    packet = encode_packet(b"mode identity", cfg=make_config("qpsk"))
    mismatch = decode_packet(packet.samples, cfg=make_config("bpsk"))
    assert not mismatch.valid
    assert mismatch.error


def test_config_validation_and_tone_geometry():
    cfg = make_config("mfsk", mfsk_order=8, symbol_rate=250)
    assert cfg.tone_order == 8
    assert cfg.gross_bit_rate == 750
    np.testing.assert_allclose(np.diff(cfg.tone_frequencies_hz), 250.0)
    assert cfg.occupied_band_hz == (5000.0, 7000.0)

    with pytest.raises(ValueError, match="MFSK order"):
        make_config("mfsk", mfsk_order=3)
    with pytest.raises(ValueError, match="positive divisor"):
        make_config("bpsk", symbol_rate=333)
    with pytest.raises(ValueError, match="occupied band"):
        make_config("mfsk", carrier_frequency_hz=500.0, mfsk_order=16)


def test_payload_limit_and_wav_round_trip(tmp_path):
    with pytest.raises(ValueError, match="payload exceeds"):
        encode_packet(bytes(MAX_PAYLOAD + 1))

    cfg = make_config("fsk")
    packet = encode_packet(b"wav round trip", cfg=cfg)
    path = tmp_path / "packet.wav"
    write_wav(path, packet.samples)
    samples, rate = read_wav(path)
    assert rate == cfg.sample_rate
    result = decode_packet(samples, cfg=cfg)
    assert result.valid, result.error
    assert result.payload == packet.payload


def test_separate_dashboard_runs_mfsk_loopback():
    controller = ConventionalController(SimpleNamespace(
        allow_air=False,
        port=8084,
        playback_device="unused",
        capture_device="unused",
        noise_dbfs=-48.0,
    ))
    controller.start(
        text="standalone dashboard",
        backend="loopback",
        modulation="mfsk",
        mfsk_order=8,
        symbol_rate=250,
        carrier_frequency_hz=6000.0,
    )
    deadline = time.monotonic() + 5.0
    while controller.status()["process_running"]:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    state = controller.status()
    assert state["status"] == "complete", state["error"]
    assert state["received_text"] == "standalone dashboard"
    assert state["independent_of_ofdm"] is True
    assert state["modulation"] == "mfsk"
    assert state["mfsk_order"] == 8
    assert state["gross_bit_rate"] == 750
