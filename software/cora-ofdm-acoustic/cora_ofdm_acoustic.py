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
import ctypes
import json
import math
import os
import struct
import subprocess
import tempfile
import time
import wave
import zlib
from dataclasses import asdict, dataclass, replace
from functools import cached_property
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class AcousticConfig:
    sample_rate: int = 48_000
    fft_len: int = 256
    cp_len: int = 64
    modem_version: int = 2
    modulation: str = "qpsk"
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

    @cached_property
    def active_bins(self) -> np.ndarray:
        bins = np.arange(self.first_bin, self.last_bin + 1)
        bins.setflags(write=False)
        return bins

    @cached_property
    def data_bins(self) -> np.ndarray:
        bins = np.setdiff1d(
            self.active_bins,
            np.asarray(self.pilot_bins),
            assume_unique=True,
        )
        bins.setflags(write=False)
        return bins

    @cached_property
    def bits_per_symbol(self) -> int:
        return int(self.data_bins.size * self.bits_per_carrier)

    @property
    def bits_per_carrier(self) -> int:
        return 3 if self.modulation == "8psk" else 2

    @property
    def low_frequency_hz(self) -> float:
        return self.first_bin * self.sample_rate / self.fft_len

    @property
    def high_frequency_hz(self) -> float:
        return self.last_bin * self.sample_rate / self.fft_len

    @property
    def gross_bit_rate(self) -> float:
        return self.bits_per_symbol * self.sample_rate / self.symbol_len

    @property
    def subcarrier_spacing_hz(self) -> float:
        return self.sample_rate / self.fft_len

    @property
    def symbol_duration_s(self) -> float:
        return self.symbol_len / self.sample_rate

    @property
    def cyclic_prefix_duration_s(self) -> float:
        return self.cp_len / self.sample_rate

    @property
    def modem_name(self) -> str:
        if self.modem_version == 0:
            return "uncoded"
        if self.modem_version == 4:
            return "v3-r2/3"
        return f"v{self.modem_version}"

    @property
    def body_code(self) -> str:
        if self.modem_version == 0:
            return "none"
        if self.modem_version == 2:
            return "triple-repetition"
        if self.modem_version == 3:
            return "convolutional-k7-r1/2"
        if self.modem_version == 4:
            return "punctured-convolutional-k7-r2/3"
        return "unknown"


@dataclass
class EncodedPacket:
    sequence: int
    payload: bytes
    samples: np.ndarray
    payload_symbols: int
    duration_s: float


@dataclass
class EncodedSuperframe:
    samples: np.ndarray
    packet_count: int
    slot_payload_bytes: int
    payload_symbols: int
    decode_blocks: tuple[tuple[int, int, int, int], ...]
    duration_s: float


@dataclass(frozen=True)
class SuperframeAcquisition:
    start_sample: int
    sync_metric: float
    channel: np.ndarray


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
UNCODED_WIRE_VERSION = 0
V2_WIRE_VERSION = 1
V3_RATE_TWO_THIRDS_WIRE_VERSION = 2
V3_WIRE_VERSION = 3
V3_RATE_TWO_THIRDS_8PSK_WIRE_VERSION = 4
V3_8PSK_WIRE_VERSION = 5
HEADER_WITHOUT_CRC = struct.Struct("<2sBBH")
HEADER = struct.Struct("<2sBBHH")
HEADER_REPETITIONS = 3
BODY_REPETITIONS = 3
TRAINING_SYMBOLS = 3
SUPERFRAME_REFRESH_PACKETS = 2
MAX_PAYLOAD = 65_535
CONVOLUTIONAL_GENERATORS = (0o171, 0o133)
CONVOLUTIONAL_STATE_BITS = 6
CONVOLUTIONAL_STATES = 1 << CONVOLUTIONAL_STATE_BITS
INTERLEAVER_DEPTH = 8
PUNCTURE_PATTERN_RATE_TWO_THIRDS = np.asarray(
    (1, 1, 1, 0),
    dtype=np.uint8,
)


def make_acoustic_config(
    modem_version: int | str = 2,
    low_frequency_hz: float = 2250.0,
    high_frequency_hz: float = 9750.0,
    *,
    fft_len: int = 256,
    cp_len: int = 64,
    sample_rate: int = 48_000,
    modulation: str = "qpsk",
) -> AcousticConfig:
    """Create a validated modem configuration on the OFDM-bin grid."""
    if isinstance(modem_version, str):
        value = modem_version.lower().removeprefix("v")
        if value in ("uncoded", "raw", "none"):
            modem_version = 0
        elif value in (
            "3-r2/3",
            "3-2/3",
            "r2/3",
            "2/3",
            "punctured",
        ):
            modem_version = 4
        else:
            try:
                modem_version = int(value)
            except ValueError as error:
                raise ValueError(
                    "modem version must be uncoded, v2, v3, or v3-r2/3"
                ) from error
    if modem_version not in (0, 2, 3, 4):
        raise ValueError(
            "modem version must be uncoded, v2, v3, or v3-r2/3"
        )
    modulation = modulation.lower().replace("-", "")
    if modulation not in ("qpsk", "8psk"):
        raise ValueError("modulation must be qpsk or 8psk")
    if modulation == "8psk" and modem_version not in (3, 4):
        raise ValueError("8psk currently requires v3 convolutional FEC")
    if not (
        math.isfinite(low_frequency_hz)
        and math.isfinite(high_frequency_hz)
    ):
        raise ValueError("carrier frequencies must be finite")

    if sample_rate != 48_000:
        raise ValueError("this audio modem currently requires 48000 sample/s")
    if fft_len not in (256, 512):
        raise ValueError("FFT length must be 256 or 512")
    if cp_len < 1 or cp_len >= fft_len:
        raise ValueError("cyclic prefix must be shorter than the FFT")

    bin_hz = sample_rate / fft_len
    first_bin = int(round(low_frequency_hz / bin_hz))
    last_bin = int(round(high_frequency_hz / bin_hz))
    minimum_first_bin = math.ceil(375.0 / bin_hz)
    if first_bin < minimum_first_bin:
        raise ValueError("low carrier edge must be at least 375 Hz")
    if last_bin >= fft_len // 2:
        raise ValueError(
            f"high carrier edge must be below {sample_rate / 2:g} Hz"
        )
    minimum_span_bins = math.ceil(3000.0 / bin_hz)
    if last_bin - first_bin < minimum_span_bins:
        raise ValueError("carrier band must span at least 3000 Hz")

    pilot_edge_bins = max(1, round(750.0 / bin_hz))
    pilot_bins = tuple(
        int(value)
        for value in np.rint(
            np.linspace(
                first_bin + pilot_edge_bins,
                last_bin - pilot_edge_bins,
                5,
            )
        )
    )
    if len(set(pilot_bins)) != 5:
        raise ValueError("carrier band is too narrow for five pilot bins")
    return AcousticConfig(
        sample_rate=sample_rate,
        fft_len=fft_len,
        cp_len=cp_len,
        modem_version=modem_version,
        modulation=modulation,
        first_bin=first_bin,
        last_bin=last_bin,
        pilot_bins=pilot_bins,
    )


def bytes_to_bits(data: bytes) -> np.ndarray:
    return np.unpackbits(np.frombuffer(data, dtype=np.uint8))


def bits_to_bytes(bits: np.ndarray) -> bytes:
    bits = np.asarray(bits, dtype=np.uint8).reshape(-1)
    if bits.size % 8:
        bits = np.pad(bits, (0, 8 - bits.size % 8))
    return np.packbits(bits).tobytes()


def _parity(value: int) -> int:
    return value.bit_count() & 1


def convolutional_encode(bits: np.ndarray) -> np.ndarray:
    """Encode bits with terminated K=7, rate-1/2 (171, 133 octal) FEC."""
    source = np.asarray(bits, dtype=np.uint8).reshape(-1)
    if np.any(source > 1):
        raise ValueError("convolutional input must contain only 0 or 1")
    terminated = np.concatenate(
        (source, np.zeros(CONVOLUTIONAL_STATE_BITS, dtype=np.uint8))
    )
    encoded = np.empty(terminated.size * 2, dtype=np.uint8)
    state = 0
    for index, bit in enumerate(terminated):
        register = (state << 1) | int(bit)
        encoded[2 * index] = _parity(
            register & CONVOLUTIONAL_GENERATORS[0]
        )
        encoded[2 * index + 1] = _parity(
            register & CONVOLUTIONAL_GENERATORS[1]
        )
        state = register & (CONVOLUTIONAL_STATES - 1)
    return encoded


def puncture_rate_two_thirds(encoded: np.ndarray) -> np.ndarray:
    """Puncture a rate-1/2 codeword to rate 2/3 with pattern 1110."""
    source = np.asarray(encoded, dtype=np.uint8).reshape(-1)
    if source.size % PUNCTURE_PATTERN_RATE_TWO_THIRDS.size:
        raise ValueError("rate-2/3 mother codeword must contain bit quartets")
    if np.any(source > 1):
        raise ValueError("convolutional codeword must contain only 0 or 1")
    keep = np.tile(
        PUNCTURE_PATTERN_RATE_TWO_THIRDS.astype(bool),
        source.size // PUNCTURE_PATTERN_RATE_TWO_THIRDS.size,
    )
    return source[keep]


def depuncture_rate_two_thirds(
    punctured: np.ndarray,
    mother_size: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Restore a punctured codeword and return its valid-bit mask."""
    source = np.asarray(punctured, dtype=np.uint8).reshape(-1)
    if mother_size % PUNCTURE_PATTERN_RATE_TWO_THIRDS.size:
        raise ValueError("rate-2/3 mother codeword must contain bit quartets")
    if np.any(source > 1):
        raise ValueError("punctured codeword must contain only 0 or 1")
    valid = np.tile(
        PUNCTURE_PATTERN_RATE_TWO_THIRDS,
        mother_size // PUNCTURE_PATTERN_RATE_TWO_THIRDS.size,
    )
    if source.size != int(np.sum(valid)):
        raise ValueError("punctured codeword has the wrong bit count")
    restored = np.zeros(mother_size, dtype=np.uint8)
    restored[valid.astype(bool)] = source
    return restored, valid


def _viterbi_tables() -> tuple[np.ndarray, np.ndarray]:
    predecessors = np.empty((CONVOLUTIONAL_STATES, 2), dtype=np.uint8)
    expected = np.empty((CONVOLUTIONAL_STATES, 2, 2), dtype=np.uint8)
    for next_state in range(CONVOLUTIONAL_STATES):
        input_bit = next_state & 1
        for choice, predecessor in enumerate(
            (
                next_state >> 1,
                (next_state >> 1) | (CONVOLUTIONAL_STATES >> 1),
            )
        ):
            predecessors[next_state, choice] = predecessor
            register = (predecessor << 1) | input_bit
            expected[next_state, choice, 0] = _parity(
                register & CONVOLUTIONAL_GENERATORS[0]
            )
            expected[next_state, choice, 1] = _parity(
                register & CONVOLUTIONAL_GENERATORS[1]
            )
    return predecessors, expected


_VITERBI_PREDECESSORS, _VITERBI_EXPECTED = _viterbi_tables()
_VITERBI_ACCELERATOR = None
_VITERBI_MASKED_ACCELERATOR = None
_VITERBI_ACCELERATOR_PROBED = False


def _viterbi_accelerator():
    """Return the optional packaged C decoder, or None on portable hosts."""
    global _VITERBI_ACCELERATOR, _VITERBI_MASKED_ACCELERATOR
    global _VITERBI_ACCELERATOR_PROBED
    if _VITERBI_ACCELERATOR_PROBED:
        return _VITERBI_ACCELERATOR

    _VITERBI_ACCELERATOR_PROBED = True
    candidates = []
    override = os.environ.get("CORA_OFDM_FEC_LIBRARY")
    if override:
        candidates.append(override)
    candidates.extend(
        (
            str(Path(__file__).with_name("libcora_ofdm_fec.so")),
            "libcora_ofdm_fec.so.1",
            "libcora_ofdm_fec.so",
        )
    )
    for candidate in candidates:
        try:
            library = ctypes.CDLL(candidate)
        except OSError:
            continue
        function = library.cora_viterbi_decode
        function.argtypes = (
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_size_t,
        )
        function.restype = ctypes.c_int
        function._cora_library = library
        _VITERBI_ACCELERATOR = function
        try:
            masked = library.cora_viterbi_decode_masked
        except AttributeError:
            masked = None
        if masked is not None:
            masked.argtypes = (
                ctypes.POINTER(ctypes.c_uint8),
                ctypes.POINTER(ctypes.c_uint8),
                ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_uint8),
                ctypes.c_size_t,
            )
            masked.restype = ctypes.c_int
            masked._cora_library = library
            _VITERBI_MASKED_ACCELERATOR = masked
        break
    return _VITERBI_ACCELERATOR


def viterbi_decode(
    encoded: np.ndarray,
    valid_mask: np.ndarray | None = None,
) -> np.ndarray:
    """Hard-decision Viterbi decode, optionally treating bits as erasures."""
    received = np.ascontiguousarray(
        np.asarray(encoded, dtype=np.uint8).reshape(-1)
    )
    if received.size % 2:
        raise ValueError("convolutional codeword must contain bit pairs")
    if np.any(received > 1):
        raise ValueError("convolutional codeword must contain only 0 or 1")
    valid = None
    if valid_mask is not None:
        valid = np.ascontiguousarray(
            np.asarray(valid_mask, dtype=np.uint8).reshape(-1)
        )
        if valid.size != received.size or np.any(valid > 1):
            raise ValueError("Viterbi valid mask must contain one flag per bit")
    steps = received.size // 2
    if steps < CONVOLUTIONAL_STATE_BITS:
        raise ValueError("convolutional codeword is too short")

    accelerator = _viterbi_accelerator()
    masked_accelerator = _VITERBI_MASKED_ACCELERATOR
    if accelerator is not None and (
        valid is None or masked_accelerator is not None
    ):
        decoded = np.empty(
            steps - CONVOLUTIONAL_STATE_BITS,
            dtype=np.uint8,
        )
        if valid is None:
            status = accelerator(
                received.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
                received.size,
                decoded.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
                decoded.size,
            )
        else:
            status = masked_accelerator(
                received.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
                valid.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
                received.size,
                decoded.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
                decoded.size,
            )
        if status:
            raise RuntimeError(
                f"accelerated Viterbi decoder failed with status {status}"
            )
        return decoded

    infinity = np.iinfo(np.int32).max // 4
    metrics = np.full(CONVOLUTIONAL_STATES, infinity, dtype=np.int32)
    metrics[0] = 0
    decisions = np.empty(
        (steps, CONVOLUTIONAL_STATES),
        dtype=np.uint8,
    )
    for step, pair in enumerate(received.reshape(-1, 2)):
        mismatches = _VITERBI_EXPECTED != pair
        if valid is not None:
            mismatches &= valid.reshape(-1, 2)[step].astype(bool)
        branch_cost = np.sum(mismatches, axis=2)
        candidates = metrics[_VITERBI_PREDECESSORS] + branch_cost
        decisions[step] = np.argmin(candidates, axis=1)
        metrics = np.min(candidates, axis=1)

    # The six zero tail bits force the encoder back to state zero.
    state = 0
    decoded = np.empty(steps, dtype=np.uint8)
    for step in range(steps - 1, -1, -1):
        decoded[step] = state & 1
        state = int(
            _VITERBI_PREDECESSORS[state, decisions[step, state]]
        )
    return decoded[:-CONVOLUTIONAL_STATE_BITS]


def interleave_bits(
    bits: np.ndarray,
    depth: int = INTERLEAVER_DEPTH,
) -> np.ndarray:
    source = np.asarray(bits, dtype=np.uint8).reshape(-1)
    if depth < 1:
        raise ValueError("interleaver depth must be positive")
    columns = math.ceil(source.size / depth)
    padded = np.pad(source, (0, depth * columns - source.size))
    return padded.reshape(depth, columns).T.reshape(-1)


def deinterleave_bits(
    bits: np.ndarray,
    original_size: int,
    depth: int = INTERLEAVER_DEPTH,
) -> np.ndarray:
    source = np.asarray(bits, dtype=np.uint8).reshape(-1)
    if original_size < 0 or depth < 1:
        raise ValueError("invalid deinterleaver dimensions")
    columns = math.ceil(original_size / depth)
    padded_size = depth * columns
    if source.size < padded_size:
        raise ValueError("interleaved codeword is too short")
    restored = source[:padded_size].reshape(columns, depth).T.reshape(-1)
    return restored[:original_size]


def _coded_body_size(cfg: AcousticConfig, required_bits: int) -> int:
    if cfg.modem_version == 0:
        return required_bits
    if cfg.modem_version == 2:
        return required_bits * BODY_REPETITIONS
    if cfg.modem_version == 3:
        convolutional_size = 2 * (
            required_bits + CONVOLUTIONAL_STATE_BITS
        )
        return (
            math.ceil(convolutional_size / INTERLEAVER_DEPTH)
            * INTERLEAVER_DEPTH
        )
    if cfg.modem_version == 4:
        mother_size = 2 * (
            required_bits + CONVOLUTIONAL_STATE_BITS
        )
        punctured_size = (
            mother_size
            * int(np.sum(PUNCTURE_PATTERN_RATE_TWO_THIRDS))
            // PUNCTURE_PATTERN_RATE_TWO_THIRDS.size
        )
        return (
            math.ceil(punctured_size / INTERLEAVER_DEPTH)
            * INTERLEAVER_DEPTH
        )
    raise ValueError(f"unsupported modem version v{cfg.modem_version}")


def _encode_body(cfg: AcousticConfig, body_bits: np.ndarray) -> np.ndarray:
    if cfg.modem_version == 0:
        return np.asarray(body_bits, dtype=np.uint8).reshape(-1)
    if cfg.modem_version == 2:
        return np.tile(body_bits, BODY_REPETITIONS)
    if cfg.modem_version == 3:
        return interleave_bits(convolutional_encode(body_bits))
    if cfg.modem_version == 4:
        return interleave_bits(
            puncture_rate_two_thirds(convolutional_encode(body_bits))
        )
    raise ValueError(f"unsupported modem version v{cfg.modem_version}")


def _decode_body(
    cfg: AcousticConfig,
    coded_bits: np.ndarray,
    required_bits: int,
) -> np.ndarray:
    if cfg.modem_version == 0:
        return np.asarray(coded_bits[:required_bits], dtype=np.uint8)
    if cfg.modem_version == 2:
        repeated = coded_bits.reshape(BODY_REPETITIONS, required_bits)
        return (
            np.sum(repeated, axis=0)
            >= math.ceil(BODY_REPETITIONS / 2)
        ).astype(np.uint8)
    if cfg.modem_version == 3:
        convolutional_size = 2 * (
            required_bits + CONVOLUTIONAL_STATE_BITS
        )
        codeword = deinterleave_bits(coded_bits, convolutional_size)
        decoded = viterbi_decode(codeword)
        if decoded.size != required_bits:
            raise ValueError("Viterbi decoder returned the wrong bit count")
        return decoded
    if cfg.modem_version == 4:
        mother_size = 2 * (
            required_bits + CONVOLUTIONAL_STATE_BITS
        )
        punctured_size = (
            mother_size
            * int(np.sum(PUNCTURE_PATTERN_RATE_TWO_THIRDS))
            // PUNCTURE_PATTERN_RATE_TWO_THIRDS.size
        )
        punctured = deinterleave_bits(coded_bits, punctured_size)
        codeword, valid = depuncture_rate_two_thirds(
            punctured,
            mother_size,
        )
        decoded = viterbi_decode(codeword, valid)
        if decoded.size != required_bits:
            raise ValueError("Viterbi decoder returned the wrong bit count")
        return decoded
    raise ValueError(f"unsupported modem version v{cfg.modem_version}")


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


_PSK8_PHASE_BY_LABEL = np.asarray((0, 1, 3, 2, 7, 6, 4, 5))


def psk8_map(bits: np.ndarray) -> np.ndarray:
    bits = np.asarray(bits, dtype=np.uint8).reshape(-1)
    if bits.size % 3:
        bits = np.pad(bits, (0, 3 - bits.size % 3))
    triples = bits.reshape(-1, 3)
    labels = (
        4 * triples[:, 0]
        + 2 * triples[:, 1]
        + triples[:, 2]
    )
    phases = _PSK8_PHASE_BY_LABEL[labels] * (math.pi / 4.0)
    return np.exp(1j * phases)


def psk8_demap(symbols: np.ndarray) -> np.ndarray:
    symbols = np.asarray(symbols).reshape(-1)
    phase_indices = np.floor(
        (np.mod(np.angle(symbols), 2.0 * math.pi) + math.pi / 8.0)
        / (math.pi / 4.0)
    ).astype(np.uint8) % 8
    labels = phase_indices ^ (phase_indices >> 1)
    bits = np.empty(labels.size * 3, dtype=np.uint8)
    bits[0::3] = (labels >> 2) & 1
    bits[1::3] = (labels >> 1) & 1
    bits[2::3] = labels & 1
    return bits


def _map_bits(cfg: AcousticConfig, bits: np.ndarray) -> np.ndarray:
    if cfg.modulation == "8psk":
        return psk8_map(bits)
    return qpsk_map(bits)


def _demap_symbols(
    cfg: AcousticConfig,
    symbols: np.ndarray,
) -> np.ndarray:
    if cfg.modulation == "8psk":
        return psk8_demap(symbols)
    return qpsk_demap(symbols)


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
    grid[cfg.data_bins] = _map_bits(cfg, padded)
    grid[np.asarray(cfg.pilot_bins)] = pilot_sign
    grid[-cfg.active_bins] = np.conj(grid[cfg.active_bins])
    return grid


def _bit_rows_to_grids(
    cfg: AcousticConfig,
    bit_rows: np.ndarray,
    pilot_signs: np.ndarray,
) -> np.ndarray:
    """Map several payload rows into Hermitian OFDM grids."""
    rows = np.asarray(bit_rows, dtype=np.uint8)
    if rows.ndim == 1:
        rows = rows.reshape(1, -1)
    if rows.ndim != 2 or rows.shape[1] > cfg.bits_per_symbol:
        raise ValueError("invalid OFDM bit-row dimensions")
    if np.any(rows > 1):
        raise ValueError("OFDM bit rows must contain only 0 or 1")

    signs = np.asarray(pilot_signs, dtype=np.float64).reshape(-1)
    if signs.size != rows.shape[0]:
        raise ValueError("one pilot sign is required per OFDM grid")

    if rows.shape[1] < cfg.bits_per_symbol:
        rows = np.pad(
            rows,
            ((0, 0), (0, cfg.bits_per_symbol - rows.shape[1])),
        )
    mapped = _map_bits(cfg, rows.reshape(-1)).reshape(
        rows.shape[0],
        cfg.data_bins.size,
    )

    grids = np.zeros(
        (rows.shape[0], cfg.fft_len),
        dtype=np.complex128,
    )
    grids[:, cfg.data_bins] = mapped
    grids[:, np.asarray(cfg.pilot_bins)] = signs[:, None]
    grids[:, -cfg.active_bins] = np.conj(grids[:, cfg.active_bins])
    return grids


def _encode_header_grids(
    cfg: AcousticConfig,
    header_bits: np.ndarray,
) -> np.ndarray:
    """Keep control headers on robust QPSK in every body mode."""
    header_cfg = (
        replace(cfg, modulation="qpsk")
        if cfg.modulation != "qpsk"
        else cfg
    )
    header_rows = np.repeat(
        np.asarray(header_bits, dtype=np.uint8).reshape(1, -1),
        HEADER_REPETITIONS,
        axis=0,
    )
    return _bit_rows_to_grids(
        header_cfg,
        header_rows,
        np.ones(HEADER_REPETITIONS),
    )


def _grids_to_symbols(
    cfg: AcousticConfig,
    grids: np.ndarray,
) -> np.ndarray:
    """Apply a batched inverse FFT and cyclic prefix."""
    useful = np.fft.ifft(
        np.asarray(grids, dtype=np.complex128),
        axis=1,
    ).real
    return np.concatenate(
        (useful[:, -cfg.cp_len :], useful),
        axis=1,
    )


def _wire_version(cfg: AcousticConfig) -> int:
    if cfg.modem_version == 0:
        return UNCODED_WIRE_VERSION
    if cfg.modem_version == 2:
        return V2_WIRE_VERSION
    if cfg.modem_version == 4:
        if cfg.modulation == "8psk":
            return V3_RATE_TWO_THIRDS_8PSK_WIRE_VERSION
        return V3_RATE_TWO_THIRDS_WIRE_VERSION
    if cfg.modem_version == 3:
        if cfg.modulation == "8psk":
            return V3_8PSK_WIRE_VERSION
        return V3_WIRE_VERSION
    raise ValueError(f"unsupported modem version v{cfg.modem_version}")


def _make_header(
    cfg: AcousticConfig,
    sequence: int,
    payload_length: int,
) -> bytes:
    prefix = HEADER_WITHOUT_CRC.pack(
        HEADER_MAGIC,
        _wire_version(cfg),
        sequence & 0xFF,
        payload_length,
    )
    crc = binascii.crc_hqx(prefix, 0xFFFF)
    return prefix + struct.pack("<H", crc)


def _parse_header(
    cfg: AcousticConfig,
    data: bytes,
) -> tuple[int, int]:
    if len(data) < HEADER.size:
        raise ValueError("short header")
    magic, version, sequence, payload_length, received_crc = HEADER.unpack(
        data[: HEADER.size]
    )
    expected_crc = binascii.crc_hqx(data[: HEADER_WITHOUT_CRC.size], 0xFFFF)
    if magic != HEADER_MAGIC:
        raise ValueError(f"bad header magic {magic!r}")
    expected_version = _wire_version(cfg)
    if version != expected_version:
        raise ValueError(
            f"received wire version {version}, expected "
            f"{expected_version} for {cfg.modem_name}"
        )
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

    header_bits = bytes_to_bits(
        _make_header(cfg, sequence, len(payload))
    )
    body = payload + struct.pack("<I", zlib.crc32(payload) & 0xFFFFFFFF)
    body_bits = bytes_to_bits(body)
    coded_body_bits = _encode_body(cfg, body_bits)
    payload_symbols = math.ceil(coded_body_bits.size / cfg.bits_per_symbol)

    header_grids = _encode_header_grids(cfg, header_bits)

    padded_body = np.pad(
        coded_body_bits,
        (0, payload_symbols * cfg.bits_per_symbol - coded_body_bits.size),
    )
    body_rows = padded_body.reshape(
        payload_symbols,
        cfg.bits_per_symbol,
    )
    pilot_signs = np.where(
        np.arange(payload_symbols) % 2 == 0,
        1.0,
        -1.0,
    )
    body_grids = _bit_rows_to_grids(cfg, body_rows, pilot_signs)
    grids = np.concatenate(
        (
            np.asarray(_training_grids(cfg)),
            header_grids,
            body_grids,
        ),
        axis=0,
    )
    symbols = _grids_to_symbols(cfg, grids).reshape(-1)

    # Keep a full silent guard symbol on each side.  The guard absorbs the
    # packet-level amplitude ramp so no training or payload samples are altered.
    guard = np.zeros(cfg.symbol_len)
    active = np.concatenate((guard, symbols, guard))
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


def encode_superframe(
    items: list[tuple[bytes, int]],
    *,
    cfg: AcousticConfig = AcousticConfig(),
) -> EncodedSuperframe:
    """Encode multiple CRC-protected packets under one acquisition preamble."""
    if not items:
        raise ValueError("superframe must contain at least one packet")
    payloads = [(bytes(payload), sequence & 0xFF) for payload, sequence in items]
    if any(len(payload) > MAX_PAYLOAD for payload, _sequence in payloads):
        raise ValueError(f"payload exceeds {MAX_PAYLOAD} bytes")

    slot_payload_bytes = max(len(payload) for payload, _sequence in payloads)
    required_bits = (slot_payload_bytes + 4) * 8
    coded_bits = _coded_body_size(cfg, required_bits)
    payload_symbols = math.ceil(coded_bits / cfg.bits_per_symbol)
    grids: list[np.ndarray] = list(_training_grids(cfg))

    for packet_index, (payload, sequence) in enumerate(payloads):
        if (
            packet_index
            and packet_index % SUPERFRAME_REFRESH_PACKETS == 0
        ):
            grids.extend(_training_grids(cfg))

        header_bits = bytes_to_bits(
            _make_header(cfg, sequence, len(payload))
        )
        grids.extend(_encode_header_grids(cfg, header_bits))

        body = (
            payload
            + struct.pack("<I", zlib.crc32(payload) & 0xFFFFFFFF)
            + bytes(slot_payload_bytes - len(payload))
        )
        encoded = _encode_body(cfg, bytes_to_bits(body))
        padded = np.pad(
            encoded,
            (0, payload_symbols * cfg.bits_per_symbol - encoded.size),
        )
        rows = padded.reshape(payload_symbols, cfg.bits_per_symbol)
        pilot_signs = np.where(
            np.arange(payload_symbols) % 2 == 0,
            1.0,
            -1.0,
        )
        grids.extend(_bit_rows_to_grids(cfg, rows, pilot_signs))

    symbols = _grids_to_symbols(cfg, np.asarray(grids)).reshape(-1)
    guard = np.zeros(cfg.symbol_len)
    active = np.concatenate((guard, symbols, guard))
    peak = float(np.max(np.abs(active)))
    if peak == 0.0:
        raise RuntimeError("generated an empty superframe")
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
    leading_samples = round(cfg.leading_silence_s * cfg.sample_rate)
    block_cursor = leading_samples + cfg.symbol_len
    decode_blocks = []
    for block_begin in range(
        0,
        len(payloads),
        SUPERFRAME_REFRESH_PACKETS,
    ):
        block_end = min(
            len(payloads),
            block_begin + SUPERFRAME_REFRESH_PACKETS,
        )
        sample_begin = block_cursor
        block_cursor += TRAINING_SYMBOLS * cfg.symbol_len
        block_cursor += (
            (block_end - block_begin)
            * (HEADER_REPETITIONS + payload_symbols)
            * cfg.symbol_len
        )
        decode_blocks.append(
            (sample_begin, block_cursor, block_begin, block_end)
        )
    return EncodedSuperframe(
        samples=samples,
        packet_count=len(payloads),
        slot_payload_bytes=slot_payload_bytes,
        payload_symbols=payload_symbols,
        decode_blocks=tuple(decode_blocks),
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


def _estimate_channel(
    cfg: AcousticConfig,
    samples: np.ndarray,
    offset: int,
) -> np.ndarray:
    estimates = []
    for index, expected in enumerate(_training_grids(cfg)):
        received = _fft_symbol(
            cfg,
            samples,
            offset + index * cfg.symbol_len,
        )
        estimates.append(
            received[cfg.active_bins] / expected[cfg.active_bins]
        )
    channel = np.ones(cfg.fft_len, dtype=np.complex128)
    channel[cfg.active_bins] = np.mean(estimates, axis=0)
    if np.any(np.abs(channel[cfg.active_bins]) < 1e-10):
        raise ValueError("training found a zero-gain subcarrier")
    return channel


def acquire_superframe(
    samples: np.ndarray,
    *,
    sync_search_samples: int | None = None,
    cfg: AcousticConfig = AcousticConfig(),
) -> SuperframeAcquisition:
    """Acquire one superframe training block and estimate its channel."""
    samples = np.asarray(samples, dtype=np.float64).reshape(-1)
    search_samples = (
        samples
        if sync_search_samples is None
        else samples[:sync_search_samples]
    )
    start, metric = _find_start(cfg, search_samples)
    channel = _estimate_channel(cfg, samples, start)
    channel.setflags(write=False)
    return SuperframeAcquisition(start, metric, channel)


def decode_superframe_slot(
    samples: np.ndarray,
    *,
    expected_sequence: int,
    slot_payload_bytes: int,
    symbol_offset: int,
    acquisition: SuperframeAcquisition,
    cfg: AcousticConfig = AcousticConfig(),
) -> DecodeResult:
    """Decode one fixed-size slot using a previously acquired channel."""
    samples = np.asarray(samples, dtype=np.float64).reshape(-1)
    required_bits = (slot_payload_bytes + 4) * 8
    coded_bits = _coded_body_size(cfg, required_bits)
    payload_symbols = math.ceil(coded_bits / cfg.bits_per_symbol)
    packet_start = acquisition.start_sample + symbol_offset
    sequence = expected_sequence & 0xFF
    payload_length = 0
    try:
        header_copies = []
        for repetition in range(HEADER_REPETITIONS):
            received = _fft_symbol(
                cfg,
                samples,
                packet_start + repetition * cfg.symbol_len,
            )
            equalized = _equalize_symbol(
                cfg,
                received,
                acquisition.channel,
                1.0,
            )
            header_copies.append(qpsk_demap(equalized[cfg.data_bins]))
        header_vote = (
            np.sum(np.stack(header_copies), axis=0)
            >= math.ceil(HEADER_REPETITIONS / 2)
        ).astype(np.uint8)
        sequence, payload_length = _parse_header(
            cfg,
            bits_to_bytes(header_vote)[: HEADER.size],
        )
        if sequence != (expected_sequence & 0xFF):
            raise ValueError(
                f"received sequence {sequence}, expected "
                f"{expected_sequence & 0xFF}"
            )
        if payload_length > slot_payload_bytes:
            raise ValueError(
                f"payload length {payload_length} exceeds superframe slot"
            )

        received_body_bits = []
        body_start = packet_start + HEADER_REPETITIONS * cfg.symbol_len
        for symbol_index in range(payload_symbols):
            received = _fft_symbol(
                cfg,
                samples,
                body_start + symbol_index * cfg.symbol_len,
            )
            pilot_sign = 1.0 if symbol_index % 2 == 0 else -1.0
            equalized = _equalize_symbol(
                cfg,
                received,
                acquisition.channel,
                pilot_sign,
            )
            received_body_bits.append(
                _demap_symbols(cfg, equalized[cfg.data_bins])
            )
        body_bits = _decode_body(
            cfg,
            np.concatenate(received_body_bits)[:coded_bits],
            required_bits,
        )
        body = bits_to_bytes(body_bits)
        payload = body[:payload_length]
        received_crc = struct.unpack(
            "<I",
            body[payload_length : payload_length + 4],
        )[0]
        expected_crc = zlib.crc32(payload) & 0xFFFFFFFF
        valid = received_crc == expected_crc
        return DecodeResult(
            valid=valid,
            sequence=sequence,
            payload=payload,
            payload_length=payload_length,
            start_sample=packet_start,
            sync_metric=acquisition.sync_metric,
            payload_symbols=payload_symbols,
            crc_expected=expected_crc,
            crc_received=received_crc,
            error=None if valid else "payload CRC mismatch",
        )
    except (ValueError, IndexError, struct.error) as error:
        return DecodeResult(
            valid=False,
            sequence=sequence,
            payload=b"",
            payload_length=payload_length,
            start_sample=packet_start,
            sync_metric=acquisition.sync_metric,
            payload_symbols=payload_symbols,
            crc_expected=None,
            crc_received=None,
            error=str(error),
        )


def decode_superframe(
    samples: np.ndarray,
    *,
    expected_sequences: list[int],
    slot_payload_bytes: int,
    sync_search_samples: int | None = None,
    cfg: AcousticConfig = AcousticConfig(),
) -> list[DecodeResult]:
    """Decode fixed-slot packets after one shared synchronization search."""
    samples = np.asarray(samples, dtype=np.float64).reshape(-1)
    if not expected_sequences:
        return []
    if slot_payload_bytes < 0 or slot_payload_bytes > MAX_PAYLOAD:
        raise ValueError("invalid superframe slot payload size")

    slot_required_bits = (slot_payload_bytes + 4) * 8
    slot_coded_bits = _coded_body_size(cfg, slot_required_bits)
    payload_symbols = math.ceil(slot_coded_bits / cfg.bits_per_symbol)
    start = 0
    metric = 0.0

    def failed(error: BaseException) -> list[DecodeResult]:
        return [
            DecodeResult(
                valid=False,
                sequence=sequence & 0xFF,
                payload=b"",
                payload_length=0,
                start_sample=start,
                sync_metric=metric,
                payload_symbols=payload_symbols,
                crc_expected=None,
                crc_received=None,
                error=str(error),
            )
            for sequence in expected_sequences
        ]

    try:
        search_samples = (
            samples
            if sync_search_samples is None
            else samples[:sync_search_samples]
        )
        start, metric = _find_start(cfg, search_samples)
        channel = _estimate_channel(cfg, samples, start)
    except (ValueError, IndexError, struct.error) as error:
        return failed(error)

    cursor = start + TRAINING_SYMBOLS * cfg.symbol_len
    results: list[DecodeResult] = []
    for packet_index, expected_sequence in enumerate(expected_sequences):
        if (
            packet_index
            and packet_index % SUPERFRAME_REFRESH_PACKETS == 0
        ):
            try:
                channel = _estimate_channel(cfg, samples, cursor)
            except (ValueError, IndexError):
                pass
            cursor += TRAINING_SYMBOLS * cfg.symbol_len

        packet_start = cursor
        sequence = expected_sequence & 0xFF
        payload_length = 0
        header_error: str | None = None
        try:
            header_copies = []
            for repetition in range(HEADER_REPETITIONS):
                received = _fft_symbol(
                    cfg,
                    samples,
                    cursor + repetition * cfg.symbol_len,
                )
                equalized = _equalize_symbol(
                    cfg,
                    received,
                    channel,
                    1.0,
                )
                header_copies.append(
                    qpsk_demap(equalized[cfg.data_bins])
                )
            header_vote = (
                np.sum(np.stack(header_copies), axis=0)
                >= math.ceil(HEADER_REPETITIONS / 2)
            ).astype(np.uint8)
            sequence, payload_length = _parse_header(
                cfg,
                bits_to_bytes(header_vote)[: HEADER.size],
            )
            if sequence != (expected_sequence & 0xFF):
                raise ValueError(
                    f"received sequence {sequence}, expected "
                    f"{expected_sequence & 0xFF}"
                )
            if payload_length > slot_payload_bytes:
                raise ValueError(
                    f"payload length {payload_length} exceeds superframe slot"
                )
        except (ValueError, IndexError, struct.error) as error:
            header_error = str(error)
        cursor += HEADER_REPETITIONS * cfg.symbol_len

        received_body_bits = []
        try:
            for symbol_index in range(payload_symbols):
                received = _fft_symbol(cfg, samples, cursor)
                pilot_sign = 1.0 if symbol_index % 2 == 0 else -1.0
                equalized = _equalize_symbol(
                    cfg,
                    received,
                    channel,
                    pilot_sign,
                )
                received_body_bits.append(
                    _demap_symbols(cfg, equalized[cfg.data_bins])
                )
                cursor += cfg.symbol_len
            if header_error is not None:
                raise ValueError(header_error)
            body_bits = _decode_body(
                cfg,
                np.concatenate(received_body_bits)[:slot_coded_bits],
                slot_required_bits,
            )
            body = bits_to_bytes(body_bits)
            payload = body[:payload_length]
            received_crc = struct.unpack(
                "<I",
                body[payload_length : payload_length + 4],
            )[0]
            expected_crc = zlib.crc32(payload) & 0xFFFFFFFF
            valid = received_crc == expected_crc
            results.append(
                DecodeResult(
                    valid=valid,
                    sequence=sequence,
                    payload=payload,
                    payload_length=payload_length,
                    start_sample=packet_start,
                    sync_metric=metric,
                    payload_symbols=payload_symbols,
                    crc_expected=expected_crc,
                    crc_received=received_crc,
                    error=None if valid else "payload CRC mismatch",
                )
            )
        except (ValueError, IndexError, struct.error) as error:
            # A fixed-size slot lets the decoder continue with later packet
            # headers even when this packet is corrupt.
            cursor = (
                packet_start
                + HEADER_REPETITIONS * cfg.symbol_len
                + payload_symbols * cfg.symbol_len
            )
            results.append(
                DecodeResult(
                    valid=False,
                    sequence=sequence,
                    payload=b"",
                    payload_length=payload_length,
                    start_sample=packet_start,
                    sync_metric=metric,
                    payload_symbols=payload_symbols,
                    crc_expected=None,
                    crc_received=None,
                    error=str(error),
                )
            )
    return results


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
            cfg,
            bits_to_bytes(header_vote)[: HEADER.size]
        )
        if payload_length > MAX_PAYLOAD:
            raise ValueError(f"invalid payload length {payload_length}")

        required_bits = (payload_length + 4) * 8
        coded_bits = _coded_body_size(cfg, required_bits)
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
            received_body_bits.append(
                _demap_symbols(cfg, equalized[cfg.data_bins])
            )
        body_bits = _decode_body(
            cfg,
            np.concatenate(received_body_bits)[:coded_bits],
            required_bits,
        )
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
