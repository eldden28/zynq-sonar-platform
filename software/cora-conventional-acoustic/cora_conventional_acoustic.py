#!/usr/bin/env python3
"""Standalone conventional acoustic BPSK, QPSK, FSK, and MFSK modem."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import struct
import subprocess
import tempfile
import time
from typing import Sequence
import wave
import zlib

import numpy as np


SAMPLE_RATE = 48_000
WIRE_VERSION = 1
MAGIC = b"CCM1"
HEADER = struct.Struct(">4sBBBBH")
HEADER_CRC = struct.Struct(">I")
PAYLOAD_CRC = struct.Struct(">I")
MAX_PAYLOAD = 4096
TRAINING_SYMBOLS = 16
MODE_IDS = {"bpsk": 1, "qpsk": 2, "fsk": 3, "mfsk": 4}


@dataclass(frozen=True)
class ConventionalConfig:
    """Waveform configuration independent of every OFDM implementation."""

    modulation: str = "qpsk"
    sample_rate: int = SAMPLE_RATE
    symbol_rate: int = 250
    carrier_frequency_hz: float = 6000.0
    mfsk_order: int = 4
    preamble_symbols: int = 31
    leading_silence_s: float = 0.05
    trailing_silence_s: float = 0.05
    output_peak: float = 0.25

    @property
    def samples_per_symbol(self) -> int:
        return self.sample_rate // self.symbol_rate

    @property
    def tone_order(self) -> int:
        if self.modulation == "fsk":
            return 2
        if self.modulation == "mfsk":
            return self.mfsk_order
        return 1

    @property
    def bits_per_symbol(self) -> int:
        if self.modulation in ("bpsk", "fsk"):
            return 1
        if self.modulation == "qpsk":
            return 2
        return int(math.log2(self.mfsk_order))

    @property
    def gross_bit_rate(self) -> int:
        return self.symbol_rate * self.bits_per_symbol

    @property
    def tone_spacing_hz(self) -> float:
        return float(self.symbol_rate)

    @property
    def tone_frequencies_hz(self) -> np.ndarray:
        order = self.tone_order
        if order == 1:
            return np.asarray([self.carrier_frequency_hz])
        offsets = np.arange(order, dtype=np.float64) - (order - 1) / 2.0
        return self.carrier_frequency_hz + offsets * self.tone_spacing_hz

    @property
    def occupied_band_hz(self) -> tuple[float, float]:
        tones = self.tone_frequencies_hz
        half_symbol_bandwidth = self.symbol_rate / 2.0
        return (
            float(tones[0] - half_symbol_bandwidth),
            float(tones[-1] + half_symbol_bandwidth),
        )


@dataclass
class EncodedPacket:
    payload: bytes
    sequence: int
    samples: np.ndarray
    data_symbols: int
    duration_s: float


@dataclass
class DecodeResult:
    valid: bool
    sequence: int
    payload: bytes
    payload_length: int
    modulation: str
    mfsk_order: int
    start_sample: int
    sync_metric: float
    data_symbols: int
    carrier_offset_hz: float | None
    error: str | None


def make_config(
    modulation: str = "qpsk",
    *,
    symbol_rate: int = 250,
    carrier_frequency_hz: float = 6000.0,
    mfsk_order: int = 4,
    output_peak: float = 0.25,
) -> ConventionalConfig:
    modulation = modulation.lower().replace("-", "")
    aliases = {"2fsk": "fsk", "bfsk": "fsk", "m-fsk": "mfsk"}
    modulation = aliases.get(modulation, modulation)
    if modulation not in MODE_IDS:
        raise ValueError("modulation must be bpsk, qpsk, fsk, or mfsk")
    if symbol_rate <= 0 or SAMPLE_RATE % symbol_rate:
        raise ValueError("symbol rate must be a positive divisor of 48000")
    if mfsk_order not in (4, 8, 16):
        raise ValueError("MFSK order must be 4, 8, or 16")
    if not math.isfinite(carrier_frequency_hz):
        raise ValueError("carrier frequency must be finite")
    if not 0.01 <= output_peak <= 0.95:
        raise ValueError("output peak must be between 0.01 and 0.95")
    cfg = ConventionalConfig(
        modulation=modulation,
        symbol_rate=symbol_rate,
        carrier_frequency_hz=carrier_frequency_hz,
        mfsk_order=mfsk_order,
        output_peak=output_peak,
    )
    low_hz, high_hz = cfg.occupied_band_hz
    if low_hz < 375.0 or high_hz >= SAMPLE_RATE / 2.0:
        raise ValueError(
            "occupied band must remain between 375 Hz and audio Nyquist"
        )
    return cfg


def bytes_to_bits(data: bytes) -> np.ndarray:
    return np.unpackbits(np.frombuffer(data, dtype=np.uint8))


def bits_to_bytes(bits: np.ndarray) -> bytes:
    values = np.asarray(bits, dtype=np.uint8).reshape(-1)
    if values.size % 8:
        values = np.pad(values, (0, 8 - values.size % 8))
    return np.packbits(values).tobytes()


def bpsk_map(bits: np.ndarray) -> np.ndarray:
    values = np.asarray(bits, dtype=np.float64).reshape(-1)
    return (1.0 - 2.0 * values).astype(np.complex128)


def bpsk_demap(symbols: np.ndarray) -> np.ndarray:
    return (np.real(symbols) < 0.0).astype(np.uint8)


def qpsk_map(bits: np.ndarray) -> np.ndarray:
    values = np.asarray(bits, dtype=np.uint8).reshape(-1, 2)
    return (
        (1.0 - 2.0 * values[:, 0])
        + 1j * (1.0 - 2.0 * values[:, 1])
    ) / math.sqrt(2.0)


def qpsk_demap(symbols: np.ndarray) -> np.ndarray:
    values = np.asarray(symbols)
    bits = np.column_stack((values.real < 0.0, values.imag < 0.0))
    return bits.astype(np.uint8).reshape(-1)


def _group_bits(bits: np.ndarray, width: int) -> np.ndarray:
    values = np.asarray(bits, dtype=np.uint8).reshape(-1)
    remainder = values.size % width
    if remainder:
        values = np.pad(values, (0, width - remainder))
    groups = values.reshape(-1, width)
    weights = 1 << np.arange(width - 1, -1, -1)
    return (groups * weights).sum(axis=1).astype(np.int64)


def _indices_to_bits(indices: np.ndarray, width: int) -> np.ndarray:
    values = np.asarray(indices, dtype=np.uint64).reshape(-1, 1)
    shifts = np.arange(width - 1, -1, -1, dtype=np.uint64)
    return ((values >> shifts) & 1).astype(np.uint8).reshape(-1)


def _pn_bits(count: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 2, count, dtype=np.uint8)


def _psk_waveform(
    symbols: np.ndarray,
    cfg: ConventionalConfig,
    *,
    symbol_offset: int = 0,
) -> np.ndarray:
    symbols = np.asarray(symbols, dtype=np.complex128).reshape(-1)
    sample_begin = symbol_offset * cfg.samples_per_symbol
    sample_indices = sample_begin + np.arange(
        symbols.size * cfg.samples_per_symbol
    )
    carrier = np.exp(
        2j
        * np.pi
        * cfg.carrier_frequency_hz
        * sample_indices
        / cfg.sample_rate
    ).reshape(symbols.size, cfg.samples_per_symbol)
    waveform = np.real(symbols[:, None] * carrier)
    return waveform.reshape(-1)


def _fsk_waveform(indices: np.ndarray, cfg: ConventionalConfig) -> np.ndarray:
    indices = np.asarray(indices, dtype=np.int64).reshape(-1)
    frequencies = cfg.tone_frequencies_hz
    if np.any(indices < 0) or np.any(indices >= frequencies.size):
        raise ValueError("FSK tone index is outside the configured alphabet")
    count = cfg.samples_per_symbol
    result = np.empty(indices.size * count, dtype=np.float64)
    phase = 0.0
    for symbol_index, tone_index in enumerate(indices):
        frequency = frequencies[tone_index]
        increment = 2.0 * np.pi * frequency / cfg.sample_rate
        begin = symbol_index * count
        result[begin : begin + count] = np.cos(
            phase + increment * np.arange(count)
        )
        phase = (phase + increment * count) % (2.0 * np.pi)
    return result


def _preamble_waveform(cfg: ConventionalConfig) -> np.ndarray:
    return _psk_waveform(
        bpsk_map(_pn_bits(cfg.preamble_symbols, 0xC04A)),
        cfg,
    )


def _training_waveform(cfg: ConventionalConfig) -> np.ndarray:
    if cfg.modulation in ("bpsk", "qpsk"):
        return _psk_waveform(
            bpsk_map(_pn_bits(TRAINING_SYMBOLS, 0x71A1)),
            cfg,
            symbol_offset=cfg.preamble_symbols,
        )
    indices = np.arange(TRAINING_SYMBOLS) % cfg.tone_order
    return _fsk_waveform(indices, cfg)


def _frame_bytes(payload: bytes, sequence: int, cfg: ConventionalConfig) -> bytes:
    header = HEADER.pack(
        MAGIC,
        WIRE_VERSION,
        MODE_IDS[cfg.modulation],
        cfg.tone_order,
        sequence & 0xFF,
        len(payload),
    )
    return (
        header
        + HEADER_CRC.pack(zlib.crc32(header) & 0xFFFFFFFF)
        + payload
        + PAYLOAD_CRC.pack(zlib.crc32(payload) & 0xFFFFFFFF)
    )


def _data_waveform(bits: np.ndarray, cfg: ConventionalConfig) -> tuple[np.ndarray, int]:
    if cfg.modulation == "bpsk":
        symbols = bpsk_map(bits)
        return _psk_waveform(
            symbols,
            cfg,
            symbol_offset=cfg.preamble_symbols + TRAINING_SYMBOLS,
        ), symbols.size
    if cfg.modulation == "qpsk":
        padded = np.pad(bits, (0, (-bits.size) % 2))
        symbols = qpsk_map(padded)
        return _psk_waveform(
            symbols,
            cfg,
            symbol_offset=cfg.preamble_symbols + TRAINING_SYMBOLS,
        ), symbols.size
    indices = _group_bits(bits, cfg.bits_per_symbol)
    return _fsk_waveform(indices, cfg), indices.size


def encode_packet(
    payload: bytes,
    *,
    sequence: int = 0,
    cfg: ConventionalConfig = ConventionalConfig(),
) -> EncodedPacket:
    payload = bytes(payload)
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"payload exceeds {MAX_PAYLOAD} bytes")
    if not 0 <= sequence <= 255:
        raise ValueError("sequence must be between 0 and 255")
    frame = _frame_bytes(payload, sequence, cfg)
    data, symbol_count = _data_waveform(bytes_to_bits(frame), cfg)
    leading = np.zeros(round(cfg.leading_silence_s * cfg.sample_rate))
    trailing = np.zeros(round(cfg.trailing_silence_s * cfg.sample_rate))
    samples = np.concatenate(
        (leading, _preamble_waveform(cfg), _training_waveform(cfg), data, trailing)
    )
    peak = float(np.max(np.abs(samples)))
    if peak:
        samples = samples * (cfg.output_peak / peak)
    samples = samples.astype(np.float32)
    return EncodedPacket(
        payload=payload,
        sequence=sequence,
        samples=samples,
        data_symbols=symbol_count,
        duration_s=samples.size / cfg.sample_rate,
    )


def _normalized_correlation(
    samples: np.ndarray, reference: np.ndarray
) -> np.ndarray:
    if samples.size < reference.size:
        raise ValueError("recording is shorter than the synchronization preamble")
    full_size = samples.size + reference.size - 1
    fft_size = 1 << (full_size - 1).bit_length()
    correlation = np.fft.irfft(
        np.fft.rfft(samples, fft_size)
        * np.fft.rfft(reference[::-1], fft_size),
        fft_size,
    )[reference.size - 1 : samples.size]
    cumulative = np.concatenate(([0.0], np.cumsum(samples * samples)))
    energy = cumulative[reference.size :] - cumulative[: -reference.size]
    denominator = np.sqrt(
        np.maximum(energy, np.finfo(float).eps) * np.sum(reference * reference)
    )
    return correlation / denominator


def _find_start(cfg: ConventionalConfig, samples: np.ndarray) -> tuple[int, float]:
    metric = np.abs(_normalized_correlation(samples, _preamble_waveform(cfg)))
    start = int(np.argmax(metric))
    value = float(metric[start])
    if value < 0.12:
        raise ValueError(f"sync not found (best normalized metric {value:.3f})")
    return start, value


def _symbol_matrix(
    samples: np.ndarray,
    offset: int,
    count: int,
    cfg: ConventionalConfig,
) -> np.ndarray:
    symbol_samples = cfg.samples_per_symbol
    end = offset + count * symbol_samples
    if offset < 0 or end > samples.size:
        raise ValueError("recording ends inside the conventional modem frame")
    return samples[offset:end].reshape(count, symbol_samples)


def _psk_observations(
    samples: np.ndarray,
    offset: int,
    count: int,
    cfg: ConventionalConfig,
) -> np.ndarray:
    symbols = _symbol_matrix(samples, offset, count, cfg)
    local_time = np.arange(cfg.samples_per_symbol) / cfg.sample_rate
    reference = np.exp(-2j * np.pi * cfg.carrier_frequency_hz * local_time)
    return (2.0 / cfg.samples_per_symbol) * (symbols @ reference)


def _estimate_psk_carrier(
    samples: np.ndarray,
    training_offset: int,
    cfg: ConventionalConfig,
) -> tuple[float, float]:
    observed = _psk_observations(
        samples, training_offset, TRAINING_SYMBOLS, cfg
    )
    expected = bpsk_map(_pn_bits(TRAINING_SYMBOLS, 0x71A1))
    residual_phase = np.unwrap(np.angle(observed * np.conjugate(expected)))
    slope, intercept = np.polyfit(
        np.arange(TRAINING_SYMBOLS, dtype=np.float64),
        residual_phase,
        1,
    )
    offset_hz = slope * cfg.symbol_rate / (2.0 * np.pi)
    return float(intercept), float(offset_hz)


def _decode_psk_bits(
    samples: np.ndarray,
    offset: int,
    bit_count: int,
    cfg: ConventionalConfig,
    phase_intercept: float,
    carrier_offset_hz: float,
) -> np.ndarray:
    symbol_count = math.ceil(bit_count / cfg.bits_per_symbol)
    observed = _psk_observations(samples, offset, symbol_count, cfg)
    body_indices = TRAINING_SYMBOLS + np.arange(symbol_count)
    phase_slope = 2.0 * np.pi * carrier_offset_hz / cfg.symbol_rate
    corrected = observed * np.exp(
        -1j * (phase_intercept + phase_slope * body_indices)
    )
    if cfg.modulation == "bpsk":
        return bpsk_demap(corrected)[:bit_count]
    return qpsk_demap(corrected)[:bit_count]


def _tone_energies(
    samples: np.ndarray,
    offset: int,
    count: int,
    cfg: ConventionalConfig,
) -> np.ndarray:
    symbols = _symbol_matrix(samples, offset, count, cfg)
    local_time = np.arange(cfg.samples_per_symbol) / cfg.sample_rate
    references = np.exp(
        -2j * np.pi * cfg.tone_frequencies_hz[:, None] * local_time[None, :]
    )
    return np.abs(symbols @ references.T) ** 2


def _fsk_gains(
    samples: np.ndarray,
    training_offset: int,
    cfg: ConventionalConfig,
) -> np.ndarray:
    energies = _tone_energies(
        samples, training_offset, TRAINING_SYMBOLS, cfg
    )
    expected = np.arange(TRAINING_SYMBOLS) % cfg.tone_order
    gains = np.ones(cfg.tone_order, dtype=np.float64)
    for tone in range(cfg.tone_order):
        values = energies[expected == tone, tone]
        if values.size:
            gains[tone] = max(float(np.mean(values)), np.finfo(float).eps)
    gains /= np.mean(gains)
    return gains


def _decode_fsk_bits(
    samples: np.ndarray,
    offset: int,
    bit_count: int,
    cfg: ConventionalConfig,
    gains: np.ndarray,
) -> np.ndarray:
    symbol_count = math.ceil(bit_count / cfg.bits_per_symbol)
    energies = _tone_energies(samples, offset, symbol_count, cfg)
    indices = np.argmax(energies / gains[None, :], axis=1)
    return _indices_to_bits(indices, cfg.bits_per_symbol)[:bit_count]


def _decode_bits(
    samples: np.ndarray,
    data_offset: int,
    bit_count: int,
    cfg: ConventionalConfig,
    carrier: tuple[float, float] | np.ndarray,
) -> np.ndarray:
    if cfg.modulation in ("bpsk", "qpsk"):
        phase, offset_hz = carrier
        return _decode_psk_bits(
            samples, data_offset, bit_count, cfg, phase, offset_hz
        )
    return _decode_fsk_bits(samples, data_offset, bit_count, cfg, carrier)


def decode_packet(
    samples: np.ndarray,
    *,
    cfg: ConventionalConfig = ConventionalConfig(),
) -> DecodeResult:
    samples = np.asarray(samples, dtype=np.float64).reshape(-1)
    start = 0
    metric = 0.0
    carrier_offset_hz: float | None = None
    try:
        start, metric = _find_start(cfg, samples)
        training_offset = start + cfg.preamble_symbols * cfg.samples_per_symbol
        data_offset = training_offset + TRAINING_SYMBOLS * cfg.samples_per_symbol
        if cfg.modulation in ("bpsk", "qpsk"):
            carrier = _estimate_psk_carrier(samples, training_offset, cfg)
            carrier_offset_hz = carrier[1]
        else:
            carrier = _fsk_gains(samples, training_offset, cfg)

        header_size = HEADER.size + HEADER_CRC.size
        header_bits = _decode_bits(
            samples, data_offset, header_size * 8, cfg, carrier
        )
        header_bytes = bits_to_bytes(header_bits)[:header_size]
        header = header_bytes[: HEADER.size]
        received_header_crc = HEADER_CRC.unpack(header_bytes[HEADER.size :])[0]
        expected_header_crc = zlib.crc32(header) & 0xFFFFFFFF
        if received_header_crc != expected_header_crc:
            raise ValueError("header CRC mismatch")
        magic, version, mode_id, tone_order, sequence, payload_length = (
            HEADER.unpack(header)
        )
        if magic != MAGIC:
            raise ValueError("conventional modem magic mismatch")
        if version != WIRE_VERSION:
            raise ValueError(f"unsupported wire version {version}")
        if mode_id != MODE_IDS[cfg.modulation] or tone_order != cfg.tone_order:
            raise ValueError("waveform configuration does not match frame header")
        if payload_length > MAX_PAYLOAD:
            raise ValueError("payload length exceeds implementation limit")

        frame_size = header_size + payload_length + PAYLOAD_CRC.size
        frame_bits = _decode_bits(
            samples, data_offset, frame_size * 8, cfg, carrier
        )
        frame = bits_to_bytes(frame_bits)[:frame_size]
        payload_begin = header_size
        payload_end = payload_begin + payload_length
        payload = frame[payload_begin:payload_end]
        received_crc = PAYLOAD_CRC.unpack(frame[payload_end:])[0]
        expected_crc = zlib.crc32(payload) & 0xFFFFFFFF
        valid = received_crc == expected_crc
        return DecodeResult(
            valid=valid,
            sequence=sequence,
            payload=payload,
            payload_length=payload_length,
            modulation=cfg.modulation,
            mfsk_order=cfg.tone_order,
            start_sample=start,
            sync_metric=metric,
            data_symbols=math.ceil(frame_size * 8 / cfg.bits_per_symbol),
            carrier_offset_hz=carrier_offset_hz,
            error=None if valid else "payload CRC mismatch",
        )
    except (IndexError, ValueError, struct.error) as error:
        return DecodeResult(
            valid=False,
            sequence=0,
            payload=b"",
            payload_length=0,
            modulation=cfg.modulation,
            mfsk_order=cfg.tone_order,
            start_sample=start,
            sync_metric=metric,
            data_symbols=0,
            carrier_offset_hz=carrier_offset_hz,
            error=str(error),
        )


def colored_acoustic_channel(
    samples: np.ndarray,
    *,
    noise_dbfs: float = -48.0,
    seed: int = 1,
) -> np.ndarray:
    """Deterministic multipath/noise channel for offline comparisons."""
    taps = np.zeros(47)
    taps[[0, 5, 13, 29, 46]] = [0.78, -0.16, 0.11, -0.07, 0.04]
    colored = np.convolve(np.asarray(samples), taps)
    colored = np.convolve(colored, np.asarray([0.18, 0.64, 0.18]))
    rng = np.random.default_rng(seed)
    noise = rng.normal(0.0, 10.0 ** (noise_dbfs / 20.0), colored.size)
    return (colored + noise).astype(np.float32)


def write_wav(path: Path, samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> None:
    pcm = np.rint(np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as sink:
        sink.setnchannels(1)
        sink.setsampwidth(2)
        sink.setframerate(sample_rate)
        sink.writeframes(pcm.tobytes())


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as source:
        channels = source.getnchannels()
        width = source.getsampwidth()
        rate = source.getframerate()
        frames = source.readframes(source.getnframes())
    if width != 2:
        raise ValueError(f"expected 16-bit PCM WAV, got {width * 8}-bit")
    values = np.frombuffer(frames, dtype="<i2").astype(np.float64)
    if channels > 1:
        values = values.reshape(-1, channels).mean(axis=1)
    return (values / 32768.0).astype(np.float32), rate


def _config_from_args(args: argparse.Namespace) -> ConventionalConfig:
    return make_config(
        args.modulation,
        symbol_rate=args.symbol_rate,
        carrier_frequency_hz=args.carrier_frequency,
        mfsk_order=args.mfsk_order,
    )


def _payload_from_args(args: argparse.Namespace) -> bytes:
    return Path(args.input).read_bytes() if args.input else args.text.encode("utf-8")


def _result_json(result: DecodeResult) -> str:
    values = asdict(result)
    values["payload"] = None
    return json.dumps(values, indent=2)


def _add_payload_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--text", default="Cora conventional acoustic modem")
    parser.add_argument("--input", help="binary payload file")
    parser.add_argument("--sequence", type=int, default=0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--modulation", choices=tuple(MODE_IDS), default="qpsk"
    )
    parser.add_argument("--mfsk-order", type=int, choices=(4, 8, 16), default=4)
    parser.add_argument("--symbol-rate", type=int, default=250)
    parser.add_argument("--carrier-frequency", type=float, default=6000.0)
    parser.add_argument("--playback-device", default="plughw:0,0")
    parser.add_argument("--capture-device", default="plughw:0,0")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("info", help="show the selected waveform parameters")
    for name in ("encode", "send", "loopback", "selftest"):
        item = commands.add_parser(name)
        _add_payload_args(item)
        if name == "encode":
            item.add_argument("--wav", required=True)
    decode = commands.add_parser("decode")
    decode.add_argument("--wav", required=True)
    decode.add_argument("--output")
    receive = commands.add_parser("receive")
    receive.add_argument("--seconds", type=int, default=8)
    receive.add_argument("--wav")
    receive.add_argument("--output")
    return parser


def _print_result(result: DecodeResult, output: str | None = None) -> int:
    print(_result_json(result))
    if not result.valid:
        return 2
    if output:
        Path(output).write_bytes(result.payload)
    else:
        try:
            print("Decoded text:", result.payload.decode("utf-8"))
        except UnicodeDecodeError:
            print("Decoded payload (hex):", result.payload.hex())
    return 0


def _play(path: Path, device: str) -> None:
    subprocess.run(["aplay", "-q", "-D", device, str(path)], check=True)


def _record_command(path: Path, device: str, seconds: int) -> list[str]:
    return [
        "arecord", "-q", "-D", device, "-f", "S16_LE", "-r",
        str(SAMPLE_RATE), "-c", "1", "-d", str(seconds), str(path),
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        cfg = _config_from_args(args)
        if args.command == "info":
            low_hz, high_hz = cfg.occupied_band_hz
            print(json.dumps({
                "application": "cora-conventional-acoustic",
                "independent_of_ofdm": True,
                "modulation": cfg.modulation,
                "mfsk_order": cfg.tone_order,
                "sample_rate": cfg.sample_rate,
                "symbol_rate": cfg.symbol_rate,
                "bits_per_symbol": cfg.bits_per_symbol,
                "gross_bit_rate": cfg.gross_bit_rate,
                "carrier_frequency_hz": cfg.carrier_frequency_hz,
                "tone_spacing_hz": cfg.tone_spacing_hz,
                "tone_frequencies_hz": cfg.tone_frequencies_hz.tolist(),
                "occupied_band_hz": [low_hz, high_hz],
            }, indent=2))
            return 0
        if args.command in ("encode", "send", "loopback", "selftest"):
            packet = encode_packet(
                _payload_from_args(args), sequence=args.sequence, cfg=cfg
            )
        if args.command == "encode":
            write_wav(Path(args.wav), packet.samples)
            print(f"Wrote {args.wav}: {packet.duration_s:.3f} s")
            return 0
        if args.command == "loopback":
            recording = np.concatenate((
                np.zeros(733), colored_acoustic_channel(packet.samples)
            ))
            result = decode_packet(recording, cfg=cfg)
            status = _print_result(result)
            if result.valid and result.payload == packet.payload:
                print(f"{cfg.modulation.upper()} colored-channel test: PASS")
                return 0
            return status or 2
        if args.command == "send":
            with tempfile.TemporaryDirectory(prefix="cora-conventional-") as folder:
                path = Path(folder) / "tx.wav"
                write_wav(path, packet.samples)
                _play(path, args.playback_device)
            return 0
        if args.command == "decode":
            samples, rate = read_wav(Path(args.wav))
            if rate != SAMPLE_RATE:
                raise ValueError(f"expected {SAMPLE_RATE} sample/s, got {rate}")
            return _print_result(decode_packet(samples, cfg=cfg), args.output)
        if args.command == "receive":
            temporary = None
            if args.wav:
                path = Path(args.wav)
            else:
                temporary = tempfile.TemporaryDirectory(
                    prefix="cora-conventional-"
                )
                path = Path(temporary.name) / "rx.wav"
            try:
                subprocess.run(
                    _record_command(path, args.capture_device, args.seconds),
                    check=True,
                )
                samples, rate = read_wav(path)
                if rate != SAMPLE_RATE:
                    raise ValueError(f"expected {SAMPLE_RATE} sample/s, got {rate}")
                return _print_result(
                    decode_packet(samples, cfg=cfg), args.output
                )
            finally:
                if temporary:
                    temporary.cleanup()
        if args.command == "selftest":
            seconds = max(2, math.ceil(packet.duration_s + 1.0))
            with tempfile.TemporaryDirectory(prefix="cora-conventional-") as folder:
                tx_path = Path(folder) / "tx.wav"
                rx_path = Path(folder) / "rx.wav"
                write_wav(tx_path, packet.samples)
                recorder = subprocess.Popen(
                    _record_command(rx_path, args.capture_device, seconds)
                )
                try:
                    time.sleep(0.25)
                    _play(tx_path, args.playback_device)
                    status = recorder.wait(timeout=seconds + 2)
                finally:
                    if recorder.poll() is None:
                        recorder.terminate()
                        recorder.wait()
                if status:
                    raise RuntimeError(f"arecord exited with status {status}")
                samples, _ = read_wav(rx_path)
                return _print_result(decode_packet(samples, cfg=cfg))
    except (OSError, RuntimeError, subprocess.CalledProcessError, ValueError) as error:
        parser.error(str(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
