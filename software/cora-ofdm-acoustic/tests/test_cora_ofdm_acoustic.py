import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cora_ofdm_acoustic import (  # noqa: E402
    AcousticConfig,
    bytes_to_bits,
    bits_to_bytes,
    colored_room_channel,
    decode_packet,
    encode_packet,
    read_wav,
    write_wav,
)


def test_bit_conversion_round_trip():
    payload = bytes(range(251))
    assert bits_to_bytes(bytes_to_bits(payload)) == payload


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
