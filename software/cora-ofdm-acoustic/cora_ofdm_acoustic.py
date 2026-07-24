#!/usr/bin/env python3
"""Half-duplex acoustic OFDM modem for the Cora Z7-10.

The waveform is real-valued, occupies ordinary audio frequencies, and is
designed for a 48 ksample/s USB audio adapter.  It intentionally depends only
on NumPy and the Python standard library; live audio uses ALSA's aplay and
arecord command-line tools.
"""

from __future__ import annotations

import argparse
import binascii
import json
import math
import os
import struct
import subprocess
import tempfile
import time
import wave
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class AcousticConfig:
    sample_rate: int = 48_000
    fft_len: int = 256
    cp_len: int = 64
    first_bin: int = 12
    last_bin: int = 52
    pilot_bins: tuple[int, ...] = (16, 24, 32, 40, 48)
    leading_silence_s: float = 0.10
    trailing_silence_s: float = 0.10
    output_peak: float = 0.18
    training_seed: int = 0xC0A2

    @property
    def symbol_len(self) -> int:
        return self.fft_len + self.cp_len

    @property
    def active_bins(self) -> np.ndarray:
        return np.arange(self.first_bin, self.last_bin + 1)

    @property
    def data_bins(self) -> np.ndarray:
        return np.setdiff1d(self.active_bins, np.asarray(self.pilot_bins))

    @property
    def bits_per_symbol(self) -> int:
        return self.data_bins.size * 2

    @property
    def low_frequency_hz(self) -> float:
        return self.first_bin * self.sample_rate / self.fft_len

    @property
    def high_frequency_hz(self) -> float:
        return self.last_bin * self.sample_rate / self.fft_len

    @property
    def gross_bit_rate(self) -> float:
        return self.bits_per_symbol * self.sample_rate / self.symbol_len


@dataclass
class EncodedPacket:
    sequence: int
    payload: bytes
    samples: np.ndarray
    payload_symbols: int
    duration_s: float


@dataclass
class DecodeResult:
    valid: bool
    sequence: int | None
    payload: bytes
    payload_length: int
    start_sample: int
    sync_metric: float
    payload_symbols: int
    crc_expected: int | None
    crc_received: int | None
    error: str | None = None


HEADER_MAGIC = b"C2"
HEADER_VERSION = 1
HEADER_WITHOUT_CRC = struct.Struct("<2sBBH")
HEADER = struct.Struct("<2sBBHH")
HEADER_REPETITIONS = 3
BODY_REPETITIONS = 3
TRAINING_SYMBOLS = 3
MAX_PAYLOAD = 65_535


def bytes_to_bits(data: bytes) -> np.ndarray:
    return np.unpackbits(np.frombuffer(data, dtype=np.uint8))


def bits_to_bytes(bits: np.ndarray) -> bytes:
    bits = np.asarray(bits, dtype=np.uint8).reshape(-1)
    if bits.size % 8:
        bits = np.pad(bits, (0, 8 - bits.size % 8))
    return np.packbits(bits).tobytes()


def qpsk_map(bits: np.ndarray) -> np.ndarray:
    bits = np.asarray(bits, dtype=np.uint8).reshape(-1)
    if bits.size % 2:
        bits = np.pad(bits, (0, 1))
    pairs = bits.reshape(-1, 2)
    return (
        (1.0 - 2.0 * pairs[:, 0])
        + 1j * (1.0 - 2.0 * pairs[:, 1])
    ) / math.sqrt(2.0)


def qpsk_demap(symbols: np.ndarray) -> np.ndarray:
    symbols = np.asarray(symbols)
    bits = np.empty(symbols.size * 2, dtype=np.uint8)
    bits[0::2] = np.real(symbols) < 0.0
    bits[1::2] = np.imag(symbols) < 0.0
    return bits


def _training_grids(cfg: AcousticConfig) -> tuple[np.ndarray, ...]:
    rng = np.random.default_rng(cfg.training_seed)
    grids = []
    for _ in range(TRAINING_SYMBOLS):
        grid = np.zeros(cfg.fft_len, dtype=np.complex128)
        values = 1.0 - 2.0 * rng.integers(0, 2, cfg.active_bins.size)
        grid[cfg.active_bins] = values
        grid[-cfg.active_bins] = np.conj(values)
        grids.append(grid)
    return tuple(grids)


def _grid_to_symbol(cfg: AcousticConfig, grid: np.ndarray) -> np.ndarray:
    useful = np.fft.ifft(grid).real
    return np.concatenate((useful[-cfg.cp_len :], useful))


def _bits_to_grid(
    cfg: AcousticConfig, bits: np.ndarray, pilot_sign: float
) -> np.ndarray:
    bits = np.asarray(bits, dtype=np.uint8).reshape(-1)
    if bits.size > cfg.bits_per_symbol:
        raise ValueError("too many bits for one OFDM symbol")
    padded = np.pad(bits, (0, cfg.bits_per_symbol - bits.size))
    grid = np.zeros(cfg.fft_len, dtype=np.complex128)
    grid[cfg.data_bins] = qpsk_map(padded)
    grid[np.asarray(cfg.pilot_bins)] = pilot_sign
    grid[-cfg.active_bins] = np.conj(grid[cfg.active_bins])
    return grid


def _make_header(sequence: int, payload_length: int) -> bytes:
    prefix = HEADER_WITHOUT_CRC.pack(
        HEADER_MAGIC, HEADER_VERSION, sequence & 0xFF, payload_length
    )
    crc = binascii.crc_hqx(prefix, 0xFFFF)
    return prefix + struct.pack("<H", crc)


def _parse_header(data: bytes) -> tuple[int, int]:
    if len(data) < HEADER.size:
        raise ValueError("short header")
    magic, version, sequence, payload_length, received_crc = HEADER.unpack(
        data[: HEADER.size]
    )
    expected_crc = binascii.crc_hqx(data[: HEADER_WITHOUT_CRC.size], 0xFFFF)
    if magic != HEADER_MAGIC:
        raise ValueError(f"bad header magic {magic!r}")
    if version != HEADER_VERSION:
        raise ValueError(f"unsupported header version {version}")
    if received_crc != expected_crc:
        raise ValueError(
            f"header CRC mismatch: got 0x{received_crc:04x}, "
            f"expected 0x{expected_crc:04x}"
        )
    return sequence, payload_length


def encode_packet(
    payload: bytes,
    *,
    sequence: int = 0,
    cfg: AcousticConfig = AcousticConfig(),
) -> EncodedPacket:
    payload = bytes(payload)
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"payload exceeds {MAX_PAYLOAD} bytes")

    header_bits = bytes_to_bits(_make_header(sequence, len(payload)))
    body = payload + struct.pack("<I", zlib.crc32(payload) & 0xFFFFFFFF)
    body_bits = bytes_to_bits(body)
    coded_body_bits = np.tile(body_bits, BODY_REPETITIONS)
    payload_symbols = math.ceil(coded_body_bits.size / cfg.bits_per_symbol)

    symbols = [
        _grid_to_symbol(cfg, grid) for grid in _training_grids(cfg)
    ]
    for _ in range(HEADER_REPETITIONS):
        symbols.append(
            _grid_to_symbol(cfg, _bits_to_grid(cfg, header_bits, 1.0))
        )
    for index in range(payload_symbols):
        begin = index * cfg.bits_per_symbol
        symbols.append(
            _grid_to_symbol(
                cfg,
                _bits_to_grid(
                    cfg,
                    coded_body_bits[begin : begin + cfg.bits_per_symbol],
                    1.0 if index % 2 == 0 else -1.0,
                ),
            )
        )

    # Keep a full silent guard symbol on each side.  The guard absorbs the
    # packet-level amplitude ramp so no training or payload samples are altered.
    guard = np.zeros(cfg.symbol_len)
    active = np.concatenate((guard, *symbols, guard))
    peak = float(np.max(np.abs(active)))
    if peak == 0.0:
        raise RuntimeError("generated an empty waveform")
    active *= cfg.output_peak / peak

    ramp_len = min(int(0.005 * cfg.sample_rate), active.size // 2)
    if ramp_len:
        ramp = np.sin(np.linspace(0.0, math.pi / 2.0, ramp_len)) ** 2
        active[:ramp_len] *= ramp
        active[-ramp_len:] *= ramp[::-1]

    samples = np.concatenate(
        (
            np.zeros(round(cfg.leading_silence_s * cfg.sample_rate)),
            active,
            np.zeros(round(cfg.trailing_silence_s * cfg.sample_rate)),
        )
    ).astype(np.float32)
    return EncodedPacket(
        sequence=sequence & 0xFF,
        payload=payload,
        samples=samples,
        payload_symbols=payload_symbols,
        duration_s=samples.size / cfg.sample_rate,
    )


def _normalized_correlation(
    samples: np.ndarray, reference: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    samples = np.asarray(samples, dtype=np.float64)
    reference = np.asarray(reference, dtype=np.float64)
    if samples.size < reference.size:
        raise ValueError("recording is shorter than the synchronization symbol")

    # Direct correlation costs O(recording * symbol) and dominated packet
    # decoding on the Cortex-A9.  Correlation is convolution with the reference
    # reversed; zero-padding to a power of two makes this O(N log N).
    convolution_size = samples.size + reference.size - 1
    fft_size = 1 << (convolution_size - 1).bit_length()
    spectrum = np.fft.rfft(samples, fft_size)
    reference_spectrum = np.fft.rfft(reference[::-1], fft_size)
    full = np.fft.irfft(spectrum * reference_spectrum, fft_size)
    correlation = full[reference.size - 1 : samples.size]

    cumulative = np.concatenate(([0.0], np.cumsum(samples * samples)))
    energy = cumulative[reference.size :] - cumulative[: -reference.size]
    denominator = np.sqrt(
        np.maximum(energy, np.finfo(float).eps)
        * np.sum(reference * reference)
    )
    return correlation / denominator, energy


def _find_start(
    cfg: AcousticConfig, samples: np.ndarray
) -> tuple[int, float]:
    reference = _grid_to_symbol(cfg, _training_grids(cfg)[0])
    metric, energy = _normalized_correlation(samples, reference)
    absolute = np.abs(metric)
    best = int(np.argmax(absolute))
    best_value = float(absolute[best])
    if best_value < 0.08:
        raise ValueError(f"sync not found (best normalized metric {best_value:.3f})")

    # Pick the earliest nearly-equivalent peak.  Reflections can make a later
    # path slightly stronger, but the earliest path gives the useful FFT window.
    floor = best_value * 0.88
    candidates = np.flatnonzero((absolute >= floor) & (energy > 0.0))
    earliest = candidates[candidates >= max(0, best - cfg.cp_len)]
    start = int(earliest[0]) if earliest.size else best
    return start, float(absolute[start])


def _fft_symbol(
    cfg: AcousticConfig, samples: np.ndarray, offset: int
) -> np.ndarray:
    begin = offset + cfg.cp_len
    end = begin + cfg.fft_len
    if begin < 0 or end > samples.size:
        raise ValueError("recording ends inside an OFDM symbol")
    return np.fft.fft(samples[begin:end])


def _equalize_symbol(
    cfg: AcousticConfig,
    received: np.ndarray,
    channel: np.ndarray,
    pilot_sign: float,
) -> np.ndarray:
    equalized = received / channel
    pilot_bins = np.asarray(cfg.pilot_bins)
    pilot_phase = np.unwrap(np.angle(equalized[pilot_bins] / pilot_sign))
    slope, intercept = np.polyfit(pilot_bins, pilot_phase, 1)
    correction = np.exp(-1j * (slope * cfg.active_bins + intercept))
    equalized[cfg.active_bins] *= correction
    return equalized


def decode_packet(
    samples: np.ndarray,
    *,
    cfg: AcousticConfig = AcousticConfig(),
) -> DecodeResult:
    samples = np.asarray(samples, dtype=np.float64).reshape(-1)
    start = 0
    metric = 0.0
    try:
        start, metric = _find_start(cfg, samples)
        training = _training_grids(cfg)
        estimates = []
        for index, expected in enumerate(training):
            received = _fft_symbol(
                cfg, samples, start + index * cfg.symbol_len
            )
            estimates.append(
                received[cfg.active_bins] / expected[cfg.active_bins]
            )
        channel = np.ones(cfg.fft_len, dtype=np.complex128)
        channel[cfg.active_bins] = np.mean(estimates, axis=0)
        weak = np.abs(channel[cfg.active_bins]) < 1e-10
        if np.any(weak):
            raise ValueError("training found a zero-gain subcarrier")

        header_copies = []
        header_start = start + TRAINING_SYMBOLS * cfg.symbol_len
        for repetition in range(HEADER_REPETITIONS):
            received = _fft_symbol(
                cfg,
                samples,
                header_start + repetition * cfg.symbol_len,
            )
            equalized = _equalize_symbol(cfg, received, channel, 1.0)
            header_copies.append(qpsk_demap(equalized[cfg.data_bins]))
        header_vote = (
            np.sum(np.stack(header_copies), axis=0) >=
            math.ceil(HEADER_REPETITIONS / 2)
        ).astype(np.uint8)
        sequence, payload_length = _parse_header(
            bits_to_bytes(header_vote)[: HEADER.size]
        )
        if payload_length > MAX_PAYLOAD:
            raise ValueError(f"invalid payload length {payload_length}")

        required_bits = (payload_length + 4) * 8
        coded_bits = required_bits * BODY_REPETITIONS
        payload_symbols = math.ceil(coded_bits / cfg.bits_per_symbol)
        received_body_bits = []
        payload_start = header_start + HEADER_REPETITIONS * cfg.symbol_len
        for index in range(payload_symbols):
            received = _fft_symbol(
                cfg, samples, payload_start + index * cfg.symbol_len
            )
            pilot_sign = 1.0 if index % 2 == 0 else -1.0
            equalized = _equalize_symbol(
                cfg, received, channel, pilot_sign
            )
            received_body_bits.append(qpsk_demap(equalized[cfg.data_bins]))
        repeated = np.concatenate(received_body_bits)[:coded_bits].reshape(
            BODY_REPETITIONS, required_bits
        )
        body_bits = (
            np.sum(repeated, axis=0) >= math.ceil(BODY_REPETITIONS / 2)
        ).astype(np.uint8)
        body = bits_to_bytes(body_bits)
        payload = body[:payload_length]
        received_crc = struct.unpack("<I", body[payload_length : payload_length + 4])[0]
        expected_crc = zlib.crc32(payload) & 0xFFFFFFFF
        valid = received_crc == expected_crc
        return DecodeResult(
            valid=valid,
            sequence=sequence,
            payload=payload,
            payload_length=payload_length,
            start_sample=start,
            sync_metric=metric,
            payload_symbols=payload_symbols,
            crc_expected=expected_crc,
            crc_received=received_crc,
            error=None if valid else "payload CRC mismatch",
        )
    except (ValueError, IndexError, struct.error) as error:
        return DecodeResult(
            valid=False,
            sequence=None,
            payload=b"",
            payload_length=0,
            start_sample=start,
            sync_metric=metric,
            payload_symbols=0,
            crc_expected=None,
            crc_received=None,
            error=str(error),
        )


def write_wav(
    path: Path, samples: np.ndarray, sample_rate: int, channels: int = 2
) -> None:
    path = Path(path)
    clipped = np.clip(np.asarray(samples), -1.0, 1.0)
    pcm = np.rint(clipped * 32767.0).astype("<i2")
    if channels == 2:
        pcm = np.repeat(pcm[:, None], 2, axis=1).reshape(-1)
    elif channels != 1:
        raise ValueError("only mono and stereo WAV files are supported")
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm.tobytes())


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as source:
        channels = source.getnchannels()
        width = source.getsampwidth()
        rate = source.getframerate()
        frames = source.readframes(source.getnframes())
    if width != 2:
        raise ValueError(f"expected 16-bit PCM WAV, got {width * 8}-bit")
    pcm = np.frombuffer(frames, dtype="<i2").astype(np.float64)
    if channels > 1:
        pcm = pcm.reshape(-1, channels).mean(axis=1)
    return (pcm / 32768.0).astype(np.float32), rate


def colored_room_channel(
    samples: np.ndarray,
    *,
    noise_dbfs: float = -45.0,
    seed: int = 1,
) -> np.ndarray:
    """A deterministic offline channel used by tests and the loopback command."""
    taps = np.zeros(59)
    taps[[0, 7, 19, 37, 58]] = [0.72, -0.25, 0.19, -0.11, 0.07]
    colored = np.convolve(samples, taps)
    # Smooth spectral tilt standing in for inexpensive speaker/mic roll-off.
    colored = np.convolve(colored, np.array([0.20, 0.60, 0.20]))
    rng = np.random.default_rng(seed)
    noise = rng.normal(0.0, 10.0 ** (noise_dbfs / 20.0), colored.size)
    return (colored + noise).astype(np.float32)


def _payload_from_args(args: argparse.Namespace) -> bytes:
    if args.input is not None:
        return Path(args.input).read_bytes()
    return args.text.encode("utf-8")


def _print_result(result: DecodeResult, output: str | None = None) -> int:
    print(json.dumps(asdict(result) | {"payload": None}, indent=2))
    if result.valid:
        if output:
            Path(output).write_bytes(result.payload)
            print(f"Wrote {len(result.payload)} decoded bytes to {output}")
        else:
            try:
                print("Decoded text:", result.payload.decode("utf-8"))
            except UnicodeDecodeError:
                print("Decoded payload (hex):", result.payload.hex())
        return 0
    return 2


def _play(path: Path, device: str) -> None:
    subprocess.run(
        ["aplay", "-q", "-D", device, str(path)],
        check=True,
    )


def _record(path: Path, device: str, seconds: int, cfg: AcousticConfig) -> None:
    subprocess.run(
        [
            "arecord",
            "-q",
            "-D",
            device,
            "-f",
            "S16_LE",
            "-r",
            str(cfg.sample_rate),
            "-c",
            "1",
            "-d",
            str(seconds),
            str(path),
        ],
        check=True,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--playback-device", default="plughw:0,0", help="ALSA playback PCM"
    )
    parser.add_argument(
        "--capture-device", default="plughw:0,0", help="ALSA capture PCM"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    info = subparsers.add_parser("info", help="show waveform parameters")
    info.set_defaults(handler=_command_info)

    for command, help_text in (
        ("encode", "create a stereo PCM transmit WAV"),
        ("send", "play one packet through ALSA"),
        ("loopback", "run the offline colored-channel smoke test"),
        ("selftest", "record microphone while playing one packet"),
    ):
        item = subparsers.add_parser(command, help=help_text)
        item.add_argument("--text", default="Cora acoustic OFDM v2")
        item.add_argument("--input", help="binary payload file")
        item.add_argument("--sequence", type=int, default=0)
        if command == "encode":
            item.add_argument("--wav", required=True)
        item.set_defaults(handler=globals()[f"_command_{command}"])

    decode = subparsers.add_parser("decode", help="decode a recorded WAV")
    decode.add_argument("--wav", required=True)
    decode.add_argument("--output", help="write decoded binary payload")
    decode.set_defaults(handler=_command_decode)

    receive = subparsers.add_parser(
        "receive", help="record microphone and decode one packet"
    )
    receive.add_argument("--seconds", type=int, default=5)
    receive.add_argument("--wav", help="retain the recording at this path")
    receive.add_argument("--output", help="write decoded binary payload")
    receive.set_defaults(handler=_command_receive)
    return parser


def _command_info(args: argparse.Namespace) -> int:
    del args
    cfg = AcousticConfig()
    print(
        json.dumps(
            {
                "sample_rate": cfg.sample_rate,
                "fft_len": cfg.fft_len,
                "cp_len": cfg.cp_len,
                "band_hz": [cfg.low_frequency_hz, cfg.high_frequency_hz],
                "active_subcarriers": cfg.active_bins.size,
                "data_subcarriers": cfg.data_bins.size,
                "pilot_subcarriers": len(cfg.pilot_bins),
                "bits_per_symbol": cfg.bits_per_symbol,
                "gross_bit_rate": cfg.gross_bit_rate,
                "payload_bit_rate_before_framing": (
                    cfg.gross_bit_rate / BODY_REPETITIONS
                ),
                "payload_repetition_code": BODY_REPETITIONS,
                "output_peak": cfg.output_peak,
            },
            indent=2,
        )
    )
    return 0


def _command_encode(args: argparse.Namespace) -> int:
    packet = encode_packet(
        _payload_from_args(args), sequence=args.sequence
    )
    write_wav(Path(args.wav), packet.samples, AcousticConfig().sample_rate)
    print(
        f"Wrote {args.wav}: {len(packet.payload)} bytes, "
        f"{packet.payload_symbols} payload symbols, {packet.duration_s:.3f} s"
    )
    return 0


def _command_send(args: argparse.Namespace) -> int:
    packet = encode_packet(
        _payload_from_args(args), sequence=args.sequence
    )
    with tempfile.TemporaryDirectory(prefix="cora-acoustic-") as directory:
        path = Path(directory) / "tx.wav"
        write_wav(path, packet.samples, AcousticConfig().sample_rate)
        print(
            f"Playing {len(packet.payload)} bytes for "
            f"{packet.duration_s:.3f} s on {args.playback_device}"
        )
        _play(path, args.playback_device)
    return 0


def _command_decode(args: argparse.Namespace) -> int:
    samples, rate = read_wav(Path(args.wav))
    if rate != AcousticConfig().sample_rate:
        raise SystemExit(f"expected 48000 sample/s WAV, got {rate}")
    return _print_result(decode_packet(samples), args.output)


def _command_loopback(args: argparse.Namespace) -> int:
    packet = encode_packet(
        _payload_from_args(args), sequence=args.sequence
    )
    received = np.concatenate(
        (np.zeros(731), colored_room_channel(packet.samples))
    )
    result = decode_packet(received)
    status = _print_result(result)
    if result.payload != packet.payload:
        return 2
    print("Acoustic OFDM colored-channel smoke test: PASS")
    return status


def _command_receive(args: argparse.Namespace) -> int:
    cfg = AcousticConfig()
    temporary = None
    if args.wav:
        path = Path(args.wav)
    else:
        temporary = tempfile.TemporaryDirectory(prefix="cora-acoustic-")
        path = Path(temporary.name) / "rx.wav"
    try:
        print(f"Recording {args.seconds} s from {args.capture_device}")
        _record(path, args.capture_device, args.seconds, cfg)
        samples, rate = read_wav(path)
        if rate != cfg.sample_rate:
            raise RuntimeError(f"ALSA returned unexpected sample rate {rate}")
        return _print_result(decode_packet(samples), args.output)
    finally:
        if temporary:
            temporary.cleanup()


def _command_selftest(args: argparse.Namespace) -> int:
    cfg = AcousticConfig()
    packet = encode_packet(
        _payload_from_args(args), sequence=args.sequence
    )
    seconds = max(2, math.ceil(packet.duration_s + 1.0))
    with tempfile.TemporaryDirectory(prefix="cora-acoustic-") as directory:
        tx_path = Path(directory) / "tx.wav"
        rx_path = Path(directory) / "rx.wav"
        write_wav(tx_path, packet.samples, cfg.sample_rate)
        command = [
            "arecord",
            "-q",
            "-D",
            args.capture_device,
            "-f",
            "S16_LE",
            "-r",
            str(cfg.sample_rate),
            "-c",
            "1",
            "-d",
            str(seconds),
            str(rx_path),
        ]
        print(
            f"Recording {args.capture_device}; playing {len(packet.payload)} "
            f"bytes on {args.playback_device} after 0.25 s"
        )
        recorder = subprocess.Popen(command)
        try:
            time.sleep(0.25)
            _play(tx_path, args.playback_device)
            return_code = recorder.wait(timeout=seconds + 2)
        finally:
            if recorder.poll() is None:
                recorder.terminate()
                recorder.wait()
        if return_code != 0:
            raise RuntimeError(f"arecord exited with status {return_code}")
        samples, rate = read_wav(rx_path)
        if rate != cfg.sample_rate:
            raise RuntimeError(f"ALSA returned unexpected sample rate {rate}")
        result = decode_packet(samples)
        status = _print_result(result)
        if result.payload == packet.payload and result.valid:
            print("Acoustic OFDM over-air self-test: PASS")
            return 0
        return status or 2


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.handler(args)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        parser.error(str(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
