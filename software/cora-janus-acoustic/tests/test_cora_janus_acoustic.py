import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cora_janus_acoustic import (  # noqa: E402
    MAX_APPLICATION_DATA,
    JanusToolchain,
    make_config,
    parse_decoder_output,
    read_wav,
    simulated_acoustic_channel,
    write_wav,
)


REFERENCE_OUTPUT = f"""
State          : Parameter Set Id                             : 1
State          : Speed (m/s)                                  : 0.125
State          : RSSI                                         : 142
State          : SNR                                          : 18.5
Packet         :   Class User Identifier (8 bits)             : 16 (NATO)
Packet         :   Application Type (6 bits)                  : 3
Packet         :   Application Data (34 bits)                 : {0x2A:034b}
Packet         : CRC Validity                                 : 1
"""


def test_config_validation_and_audio_band():
    cfg = make_config(parameter_set_id=1, application_type=3, application_data=0x2A)
    assert cfg.parameter_set.occupied_band_hz == (9440.0, 13600.0)
    assert cfg.application_data == 0x2A

    with pytest.raises(ValueError, match="parameter set"):
        make_config(parameter_set_id=9)
    with pytest.raises(ValueError, match="34-bit"):
        make_config(application_data=MAX_APPLICATION_DATA + 1)
    with pytest.raises(ValueError, match="application type"):
        make_config(application_type=64)
    with pytest.raises(ValueError, match="registered codec fields"):
        make_config(class_id=16, application_type=0, application_data=1)


def test_decoder_output_parser():
    result = parse_decoder_output(REFERENCE_OUTPUT)
    assert result.valid
    assert result.parameter_set_id == 1
    assert result.class_id == 16
    assert result.application_type == 3
    assert result.application_data == 0x2A
    assert result.speed_mps == pytest.approx(0.125)
    assert result.rssi == pytest.approx(142)
    assert result.snr_db == pytest.approx(18.5)


def test_decoder_output_parser_rejects_missing_crc():
    result = parse_decoder_output("receiver reached end of file")
    assert not result.valid
    assert result.error


def test_wav_and_simulated_channel(tmp_path):
    time = np.arange(4800) / 48000.0
    samples = 0.2 * np.sin(2 * np.pi * 11520 * time)
    colored = simulated_acoustic_channel(samples)
    assert colored.size == samples.size + 72
    assert np.max(np.abs(colored)) <= 1.0
    path = tmp_path / "janus.wav"
    write_wav(path, colored)
    restored, rate = read_wav(path)
    assert rate == 48000
    np.testing.assert_allclose(restored, colored, atol=1 / 32768)


def test_toolchain_reports_missing_components(tmp_path):
    toolchain = JanusToolchain(
        transmitter=str(tmp_path / "missing-tx"),
        receiver=str(tmp_path / "missing-rx"),
        parameter_set_file=tmp_path / "missing.csv",
    )
    available, error = toolchain.availability()
    assert not available
    assert "missing JANUS" in error
