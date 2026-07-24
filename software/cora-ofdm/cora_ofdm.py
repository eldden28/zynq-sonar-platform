#!/usr/bin/env python3
"""Headless CPU baseline for the Cora underwater OFDM demonstration."""

from __future__ import annotations

import argparse
import json
import math
import os
import socket
import struct
import time
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Sequence

import numpy as np
from gnuradio import blocks, channels, digital, fft, filter as gr_filter, gr
from gnuradio.fft import window


@dataclass(frozen=True)
class ModemConfig:
    sample_rate: float = 48_000.0
    fft_len: int = 256
    cp_len: int = 64
    active_low: int = 2
    active_high: int = 80
    guard_symbols: int = 4

    @property
    def symbol_len(self) -> int:
        return self.fft_len + self.cp_len

    @property
    def subcarrier_spacing(self) -> float:
        return self.sample_rate / self.fft_len

    @property
    def active_bins(self) -> np.ndarray:
        signed = np.r_[
            np.arange(-self.active_high, -self.active_low + 1),
            np.arange(self.active_low, self.active_high + 1),
        ]
        return signed % self.fft_len

    @property
    def pilot_signed_bins(self) -> np.ndarray:
        return np.array([-72, -48, -24, 24, 48, 72])

    @property
    def pilot_bins(self) -> np.ndarray:
        return self.pilot_signed_bins % self.fft_len

    @property
    def data_bins(self) -> np.ndarray:
        return np.setdiff1d(self.active_bins, self.pilot_bins)


@dataclass
class TxPacket:
    frequency_symbols: np.ndarray
    payload_bits: np.ndarray
    training: np.ndarray
    pilots: np.ndarray
    payload_symbols: int


@dataclass
class ReceiverResult:
    start_sample: int
    cfo_estimate_hz: float
    raw_bit_errors: int
    bit_errors: int
    total_bits: int
    decoded_bits: np.ndarray

    @property
    def raw_ber(self) -> float:
        return self.raw_bit_errors / self.total_bits

    @property
    def ber(self) -> float:
        return self.bit_errors / self.total_bits


@dataclass
class PacketResult:
    sequence: int
    decoded: bool
    valid: bool
    bits: int
    bit_errors: int
    raw_bit_errors: int
    cfo_estimate_hz: float | None
    tx_samples: int
    rx_samples: int
    processing_wall_s: float
    error: str | None = None


@dataclass
class BatchPacketExecution:
    packet: TxPacket
    tx_samples: np.ndarray
    rx_samples: np.ndarray
    receiver: ReceiverResult | None
    processing_wall_s: float
    error: str | None


def qpsk_map(bits: np.ndarray) -> np.ndarray:
    pairs = bits.reshape(-1, 2)
    return (
        (1.0 - 2.0 * pairs[:, 0])
        + 1j * (1.0 - 2.0 * pairs[:, 1])
    ) / math.sqrt(2.0)


def qpsk_demap(symbols: np.ndarray) -> np.ndarray:
    bits = np.empty(symbols.size * 2, dtype=np.uint8)
    bits[0::2] = np.real(symbols) < 0.0
    bits[1::2] = np.imag(symbols) < 0.0
    return bits


def make_packet(
    cfg: ModemConfig,
    payload_symbols: int,
    seed: int,
    payload_bits: np.ndarray | None = None,
) -> TxPacket:
    rng = np.random.default_rng(seed)
    training = np.zeros(cfg.fft_len, dtype=np.complex64)
    training_values = 1.0 - 2.0 * rng.integers(
        0, 2, cfg.active_bins.size
    )
    training[cfg.active_bins] = training_values.astype(np.complex64)

    bits_per_symbol = cfg.data_bins.size * 2
    expected_bits = payload_symbols * bits_per_symbol
    if payload_bits is None:
        payload_bits = rng.integers(
            0, 2, expected_bits, dtype=np.uint8
        )
    else:
        payload_bits = np.asarray(payload_bits, dtype=np.uint8).reshape(-1)
        if payload_bits.size != expected_bits:
            raise ValueError(
                f"payload requires exactly {expected_bits} bits"
            )
        if np.any(payload_bits > 1):
            raise ValueError("payload bits must contain only 0 or 1")
        payload_bits = payload_bits.copy()
    pilots = np.empty((payload_symbols, cfg.pilot_bins.size), np.complex64)
    payload_grid = np.zeros(
        (payload_symbols, cfg.fft_len), dtype=np.complex64
    )
    for index in range(payload_symbols):
        bit_start = index * bits_per_symbol
        payload_grid[index, cfg.data_bins] = qpsk_map(
            payload_bits[bit_start : bit_start + bits_per_symbol]
        )
        pilots[index, :] = 1.0 if index % 2 == 0 else -1.0
        payload_grid[index, cfg.pilot_bins] = pilots[index]

    silence = np.zeros(
        (cfg.guard_symbols, cfg.fft_len), dtype=np.complex64
    )
    frequency_symbols = np.vstack(
        (silence, training[None, :], training[None, :], payload_grid, silence)
    )
    return TxPacket(
        frequency_symbols=frequency_symbols,
        payload_bits=payload_bits,
        training=training,
        pilots=pilots,
        payload_symbols=payload_symbols,
    )


def acoustic_taps(cfg: ModemConfig) -> np.ndarray:
    """Return the normalized four-path shallow-water channel model."""
    delays_ms = np.array([0.0, 0.27, 0.77, 1.27])
    delays = np.rint(delays_ms * 1e-3 * cfg.sample_rate).astype(int)
    gains = np.array(
        [
            1.00,
            0.56 * np.exp(1j * 0.35 * np.pi),
            0.35 * np.exp(-1j * 0.62 * np.pi),
            0.20 * np.exp(1j * 0.88 * np.pi),
        ],
        dtype=np.complex64,
    )
    taps = np.zeros(delays[-1] + 1, dtype=np.complex64)
    taps[delays] = gains
    taps /= np.sqrt(np.sum(np.abs(taps) ** 2))
    return taps


class UnderwaterOfdmFlowgraph(gr.top_block):
    """Finite, headless GNU Radio OFDM transmitter and channel."""

    def __init__(
        self,
        cfg: ModemConfig,
        packet: TxPacket | Sequence[TxPacket],
        taps: np.ndarray,
        snr_db: float,
        doppler_ppm: float,
        cfo_hz: float,
        noise_seed: int,
        realtime: bool,
    ):
        super().__init__("Cora headless underwater OFDM")
        packets = (
            (packet,) if isinstance(packet, TxPacket) else tuple(packet)
        )
        if not packets:
            raise ValueError("at least one packet is required")
        frequency_symbols = np.concatenate(
            [item.frequency_symbols for item in packets], axis=0
        )
        input_items = frequency_symbols.astype(np.complex64).ravel()
        ifft_gain = 1.0 / math.sqrt(cfg.active_bins.size)
        noise_voltage = 10.0 ** (-snr_db / 20.0)

        source = blocks.vector_source_c(input_items, False)
        to_vector = blocks.stream_to_vector(
            gr.sizeof_gr_complex, cfg.fft_len
        )
        inverse_fft = fft.fft_vcc(
            cfg.fft_len, False, window.rectangular(cfg.fft_len), False
        )
        normalize = blocks.multiply_const_vcc(
            (ifft_gain,) * cfg.fft_len
        )
        add_cp = digital.ofdm_cyclic_prefixer(
            cfg.fft_len, cfg.symbol_len, 0, ""
        )
        channel = channels.channel_model(
            noise_voltage=noise_voltage,
            frequency_offset=cfo_hz / cfg.sample_rate,
            epsilon=1.0 + doppler_ppm * 1e-6,
            taps=taps.tolist(),
            noise_seed=noise_seed,
            block_tags=False,
        )
        self.sink = blocks.vector_sink_c()

        self.connect(source, to_vector, inverse_fft, normalize, add_cp)
        if realtime:
            throttle = blocks.throttle(
                gr.sizeof_gr_complex, cfg.sample_rate, True
            )
            self.connect(add_cp, throttle, channel, self.sink)
            self._throttle = throttle
        else:
            self.connect(add_cp, channel, self.sink)


def build_transmitted_reference(
    cfg: ModemConfig, packet: TxPacket
) -> np.ndarray:
    occupied_fraction = cfg.active_bins.size / cfg.fft_len
    gain = math.sqrt(cfg.fft_len / occupied_fraction)
    useful = np.fft.ifft(packet.frequency_symbols, axis=1) * gain
    with_cp = np.concatenate(
        (useful[:, -cfg.cp_len :], useful), axis=1
    )
    return with_cp.ravel().astype(np.complex64)


def compensate_doppler(
    samples: np.ndarray, doppler_ppm: float
) -> np.ndarray:
    """Undo the channel sample-rate scale with GNU Radio's polyphase filter."""
    epsilon = 1.0 + doppler_ppm * 1e-6
    flowgraph = gr.top_block("Cora OFDM Doppler correction")
    source = blocks.vector_source_c(
        np.asarray(samples, dtype=np.complex64), False
    )
    resampler = gr_filter.pfb.arb_resampler_ccf(
        epsilon, taps=_doppler_resampler_taps(epsilon)
    )
    sink = blocks.vector_sink_c()
    flowgraph.connect(source, resampler, sink)
    flowgraph.run()
    return np.asarray(sink.data(), dtype=np.complex64)


@lru_cache(maxsize=8)
def _doppler_resampler_taps(rate: float) -> tuple[float, ...]:
    """Design each fixed Doppler correction filter only once per process."""
    return tuple(gr_filter.pfb.arb_resampler_ccf.create_taps(rate))


def normalized_sync_direct(
    samples: np.ndarray, reference: np.ndarray
) -> tuple[int, np.ndarray]:
    """Reference normalized correlation using NumPy's direct algorithm."""
    correlation = np.correlate(samples, reference, mode="valid")
    return normalized_correlation_metric(samples, reference, correlation)


def normalized_sync(
    samples: np.ndarray, reference: np.ndarray
) -> tuple[int, np.ndarray]:
    """Find a training waveform using FFT-based normalized correlation."""
    full_size = samples.size + reference.size - 1
    fft_size = 1 << (full_size - 1).bit_length()
    kernel = np.conjugate(reference[::-1])
    correlation = np.fft.ifft(
        np.fft.fft(samples, fft_size) * np.fft.fft(kernel, fft_size)
    )
    correlation = correlation[reference.size - 1 : samples.size]
    return normalized_correlation_metric(samples, reference, correlation)


def normalized_correlation_metric(
    samples: np.ndarray,
    reference: np.ndarray,
    correlation: np.ndarray,
) -> tuple[int, np.ndarray]:
    """Normalize correlation magnitude by each candidate window's energy."""
    power = np.abs(samples) ** 2
    cumulative = np.concatenate(
        (np.zeros(1, dtype=np.float64), np.cumsum(power, dtype=np.float64))
    )
    energy = cumulative[reference.size :] - cumulative[: -reference.size]
    energy_floor = max(float(np.max(energy)) * 1e-5, 1e-9)
    metric = np.zeros(correlation.size, dtype=np.float64)
    valid = energy > energy_floor
    reference_energy = float(np.vdot(reference, reference).real)
    metric[valid] = np.abs(correlation[valid]) / np.sqrt(
        energy[valid] * reference_energy
    )
    return int(np.argmax(metric)), metric


def reference_training_waveform(
    cfg: ModemConfig, training: np.ndarray
) -> np.ndarray:
    occupied_fraction = cfg.active_bins.size / cfg.fft_len
    gain = math.sqrt(cfg.fft_len / occupied_fraction)
    useful = np.fft.ifft(training) * gain
    return np.r_[useful[-cfg.cp_len :], useful].astype(np.complex64)


def symbol_matrix(
    samples: np.ndarray,
    first_useful: int,
    symbol_count: int,
    cfg: ModemConfig,
) -> np.ndarray:
    """Return a read-only matrix of useful OFDM samples without copying."""
    final_sample = (
        first_useful
        + (symbol_count - 1) * cfg.symbol_len
        + cfg.fft_len
    )
    if first_useful < 0 or final_sample > samples.size:
        raise RuntimeError(
            f"receiver could not extract {symbol_count} OFDM symbols"
        )
    values = np.asarray(samples)
    return np.lib.stride_tricks.as_strided(
        values[first_useful:final_sample],
        shape=(symbol_count, cfg.fft_len),
        strides=(
            values.strides[0] * cfg.symbol_len,
            values.strides[0],
        ),
        writeable=False,
    )


def receive(
    cfg: ModemConfig,
    packet: TxPacket,
    received: np.ndarray,
    doppler_ppm: float,
) -> ReceiverResult:
    corrected = compensate_doppler(received, doppler_ppm)
    return receive_corrected(cfg, packet, corrected)


def receive_corrected(
    cfg: ModemConfig,
    packet: TxPacket,
    corrected: np.ndarray,
) -> ReceiverResult:
    """Decode a packet window after shared Doppler compensation."""
    corrected = np.asarray(corrected, dtype=np.complex64).copy()
    training_time = reference_training_waveform(cfg, packet.training)
    start, sync_metric = normalized_sync(corrected, training_time)
    previous_center = start - cfg.symbol_len
    if previous_center >= 0:
        search_lo = max(0, previous_center - 3)
        search_hi = min(sync_metric.size, previous_center + 4)
        previous = search_lo + int(
            np.argmax(sync_metric[search_lo:search_hi])
        )
        if sync_metric[previous] > 0.7 * sync_metric[start]:
            start = previous

    first_useful = start + cfg.cp_len
    second_useful = first_useful + cfg.symbol_len
    first = corrected[first_useful : first_useful + cfg.fft_len]
    second = corrected[second_useful : second_useful + cfg.fft_len]
    if first.size != cfg.fft_len or second.size != cfg.fft_len:
        raise RuntimeError("receiver could not extract both training symbols")

    phase_advance = np.angle(np.vdot(first, second))
    cfo_normalized = phase_advance / (2.0 * np.pi * cfg.symbol_len)
    cfo_estimate_hz = cfo_normalized * cfg.sample_rate
    sample_index = np.arange(corrected.size)
    corrected *= np.exp(
        -1j * 2.0 * np.pi * cfo_normalized * sample_index
    )

    training_symbols = symbol_matrix(corrected, first_useful, 2, cfg)
    average_training = np.mean(
        np.fft.fft(training_symbols, axis=1), axis=0
    )
    channel_estimate = np.ones(cfg.fft_len, dtype=np.complex128)
    channel_estimate[cfg.active_bins] = (
        average_training[cfg.active_bins]
        / packet.training[cfg.active_bins]
    )

    payload_start = start + 2 * cfg.symbol_len + cfg.cp_len
    payload_time = symbol_matrix(
        corrected, payload_start, packet.payload_symbols, cfg
    )
    spectra = np.fft.fft(payload_time, axis=1)
    raw_constellation = spectra[:, cfg.data_bins].reshape(-1)
    equalized = spectra / channel_estimate[None, :]
    pilot_errors = np.angle(
        np.sum(
            equalized[:, cfg.pilot_bins] * np.conjugate(packet.pilots),
            axis=1,
        )
    )
    equalized *= np.exp(-1j * pilot_errors[:, None])
    equalized_constellation = equalized[:, cfg.data_bins].reshape(-1)
    raw_constellation /= np.sqrt(np.mean(np.abs(raw_constellation) ** 2))
    raw_rotation = np.angle(np.mean(raw_constellation**4)) / 4.0
    raw_bits = qpsk_demap(
        raw_constellation * np.exp(-1j * raw_rotation)
    )
    equalized_bits = qpsk_demap(equalized_constellation)
    raw_bit_errors = int(np.count_nonzero(raw_bits != packet.payload_bits))
    bit_errors = int(
        np.count_nonzero(equalized_bits != packet.payload_bits)
    )
    return ReceiverResult(
        start_sample=start,
        cfo_estimate_hz=float(cfo_estimate_hz),
        raw_bit_errors=raw_bit_errors,
        bit_errors=bit_errors,
        total_bits=int(packet.payload_bits.size),
        decoded_bits=equalized_bits,
    )


def run_packet_batch(
    cfg: ModemConfig,
    packets: Sequence[TxPacket],
    taps: np.ndarray,
    snr_db: float,
    doppler_ppm: float,
    cfo_hz: float,
    noise_seed: int,
    realtime: bool,
) -> list[BatchPacketExecution]:
    """Run several guarded packets through shared TX, channel, and RX setup."""
    packets = tuple(packets)
    if not packets:
        return []
    packet_symbol_counts = {
        item.frequency_symbols.shape[0] for item in packets
    }
    if len(packet_symbol_counts) != 1:
        raise ValueError("all packets in a batch must have equal length")

    tx_samples = [
        build_transmitted_reference(cfg, packet) for packet in packets
    ]
    batch_start = time.perf_counter()
    flowgraph = UnderwaterOfdmFlowgraph(
        cfg=cfg,
        packet=packets,
        taps=taps,
        snr_db=snr_db,
        doppler_ppm=doppler_ppm,
        cfo_hz=cfo_hz,
        noise_seed=noise_seed,
        realtime=realtime,
    )
    flowgraph.run()
    received = np.asarray(flowgraph.sink.data(), dtype=np.complex64)
    corrected = compensate_doppler(received, doppler_ppm)
    shared_wall = time.perf_counter() - batch_start

    executions = []
    corrected_span = corrected.size / len(packets)
    received_span = received.size / len(packets)
    window_margin = cfg.symbol_len
    for index, packet in enumerate(packets):
        corrected_lo = max(
            0, math.floor(index * corrected_span) - window_margin
        )
        corrected_hi = min(
            corrected.size,
            math.ceil((index + 1) * corrected_span) + window_margin,
        )
        received_lo = round(index * received_span)
        received_hi = round((index + 1) * received_span)
        receiver_start = time.perf_counter()
        try:
            receiver = receive_corrected(
                cfg, packet, corrected[corrected_lo:corrected_hi]
            )
            error = None
        except (RuntimeError, ValueError, FloatingPointError) as error_value:
            receiver = None
            error = str(error_value)
        receiver_wall = time.perf_counter() - receiver_start
        executions.append(
            BatchPacketExecution(
                packet=packet,
                tx_samples=tx_samples[index],
                rx_samples=received[received_lo:received_hi],
                receiver=receiver,
                processing_wall_s=(
                    shared_wall / len(packets) + receiver_wall
                ),
                error=error,
            )
        )
    return executions


class UdpMonitor:
    """Emit chunked complex64 TX/RX datagrams for a PC-side visualizer."""

    MAGIC = b"COFM"
    VERSION = 1
    HEADER = struct.Struct("<4sBBHIIIf")
    TX_STREAM = 0
    RX_STREAM = 1
    CHUNK_SAMPLES = 1024

    def __init__(self, host: str, port: int, sample_rate: float):
        self.address = (host, port)
        self.sample_rate = sample_rate
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(self, stream: int, sequence: int, samples: np.ndarray) -> None:
        values = np.asarray(samples, dtype="<c8")
        for offset in range(0, values.size, self.CHUNK_SAMPLES):
            chunk = values[offset : offset + self.CHUNK_SAMPLES]
            header = self.HEADER.pack(
                self.MAGIC,
                self.VERSION,
                stream,
                0,
                sequence,
                offset,
                values.size,
                self.sample_rate,
            )
            self.socket.sendto(header + chunk.tobytes(), self.address)

    def close(self) -> None:
        self.socket.close()


def memory_status_kib() -> tuple[int | None, int | None]:
    current = None
    high_water = None
    try:
        for line in Path("/proc/self/status").read_text(
            encoding="ascii"
        ).splitlines():
            if line.startswith("VmRSS:"):
                current = int(line.split()[1])
            elif line.startswith("VmHWM:"):
                high_water = int(line.split()[1])
    except (OSError, ValueError):
        pass
    return current, high_water


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the headless Cora Z7-10 CPU OFDM baseline."
    )
    parser.add_argument(
        "--mode",
        choices=("realtime", "benchmark"),
        default="realtime",
        help="48 ksample/s throttled or maximum-throughput execution",
    )
    parser.add_argument("--packets", type=int, default=10)
    parser.add_argument(
        "--warmup-packets",
        type=int,
        default=1,
        help="unmeasured packets used to warm GNU Radio and VOLK (default: 1)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="packets per shared GNU Radio execution (default: 10)",
    )
    parser.add_argument("--payload-symbols", type=int, default=40)
    parser.add_argument("--snr-db", type=float, default=18.0)
    parser.add_argument("--doppler-ppm", type=float, default=900.0)
    parser.add_argument("--cfo-hz", type=float, default=5.0)
    parser.add_argument("--seed", type=int, default=20260723)
    parser.add_argument(
        "--monitor",
        metavar="HOST:PORT",
        help="optionally send chunked TX/RX complex64 datagrams over UDP",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        help="write the complete machine-readable result to this path",
    )
    parser.add_argument(
        "--require-error-free",
        action="store_true",
        help="return status 2 when any decoded payload bit is incorrect",
    )
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args(argv)


def parse_monitor_endpoint(value: str) -> tuple[str, int]:
    try:
        host, port_text = value.rsplit(":", 1)
        port = int(port_text)
    except (ValueError, TypeError) as error:
        raise ValueError("monitor endpoint must be HOST:PORT") from error
    if not host or not (1 <= port <= 65535):
        raise ValueError("monitor endpoint must contain a valid host and port")
    return host, port


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.packets < 1:
        raise SystemExit("--packets must be at least 1")
    if args.warmup_packets < 0:
        raise SystemExit("--warmup-packets cannot be negative")
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be at least 1")
    if args.payload_symbols < 1:
        raise SystemExit("--payload-symbols must be at least 1")

    cfg = ModemConfig()
    taps = acoustic_taps(cfg)
    monitor = None
    if args.monitor:
        try:
            monitor_host, monitor_port = parse_monitor_endpoint(args.monitor)
        except ValueError as error:
            raise SystemExit(str(error)) from error
        monitor = UdpMonitor(monitor_host, monitor_port, cfg.sample_rate)

    print("Cora Z7-10 headless underwater OFDM CPU baseline")
    print(f"  mode                    : {args.mode}")
    print(f"  GNU Radio               : {gr.version()}")
    print(f"  sample rate             : {cfg.sample_rate:.0f} samples/s")
    print(f"  FFT / cyclic prefix     : {cfg.fft_len} / {cfg.cp_len}")
    print(f"  modulation / pilots     : QPSK / {cfg.pilot_bins.size} BPSK")
    print(f"  packet batch size       : {args.batch_size}")
    print(
        f"  channel                 : {np.count_nonzero(taps)} paths, "
        f"{args.snr_db:.1f} dB SNR, {args.cfo_hz:.1f} Hz CFO, "
        f"{args.doppler_ppm:.0f} ppm Doppler"
    )
    if monitor:
        print(f"  UDP monitor             : {args.monitor} (TX and RX)")

    packet_results: list[PacketResult] = []
    total_processing_wall = 0.0

    def packet_seed(sequence: int) -> int:
        return (
            args.seed + sequence
            if sequence >= 0
            else args.seed + args.packets - sequence
        )

    def execute_sequences(
        sequences: Sequence[int],
    ) -> list[BatchPacketExecution]:
        packets = [
            make_packet(
                cfg, args.payload_symbols, packet_seed(sequence)
            )
            for sequence in sequences
        ]
        return run_packet_batch(
            cfg=cfg,
            packets=packets,
            taps=taps,
            snr_db=args.snr_db,
            doppler_ppm=args.doppler_ppm,
            cfo_hz=args.cfo_hz,
            noise_seed=packet_seed(sequences[0]) & 0x7FFFFFFF,
            realtime=args.mode == "realtime",
        )

    try:
        warmup_sequences = list(range(-args.warmup_packets, 0))
        for offset in range(0, len(warmup_sequences), args.batch_size):
            sequences = warmup_sequences[offset : offset + args.batch_size]
            executions = execute_sequences(sequences)
            for sequence, execution in zip(sequences, executions):
                decoded = execution.receiver is not None
                if not args.quiet:
                    print(
                        f"  warm-up {sequence + args.warmup_packets + 1:2d}/"
                        f"{args.warmup_packets}: "
                        f"{'decoded' if decoded else 'failed'}, "
                        f"wall={execution.processing_wall_s:.3f} s"
                    )

        usage_start = os.times()
        wall_start = time.perf_counter()
        measured_sequences = list(range(args.packets))
        for offset in range(0, args.packets, args.batch_size):
            sequences = measured_sequences[offset : offset + args.batch_size]
            executions = execute_sequences(sequences)
            for sequence, execution in zip(sequences, executions):
                receiver = execution.receiver
                decoded = receiver is not None
                valid = decoded and receiver.bit_errors == 0
                bits = int(execution.packet.payload_bits.size)
                bit_errors = receiver.bit_errors if receiver else bits
                raw_bit_errors = (
                    receiver.raw_bit_errors if receiver else bits
                )
                cfo_estimate_hz = (
                    receiver.cfo_estimate_hz if receiver else None
                )
                total_processing_wall += execution.processing_wall_s
                item = PacketResult(
                    sequence=sequence,
                    decoded=decoded,
                    valid=valid,
                    bits=bits,
                    bit_errors=bit_errors,
                    raw_bit_errors=raw_bit_errors,
                    cfo_estimate_hz=cfo_estimate_hz,
                    tx_samples=int(execution.tx_samples.size),
                    rx_samples=int(execution.rx_samples.size),
                    processing_wall_s=execution.processing_wall_s,
                    error=execution.error,
                )
                packet_results.append(item)
                if monitor:
                    monitor.send(
                        UdpMonitor.TX_STREAM,
                        sequence,
                        execution.tx_samples,
                    )
                    monitor.send(
                        UdpMonitor.RX_STREAM,
                        sequence,
                        execution.rx_samples,
                    )
                if not args.quiet:
                    cfo_text = (
                        f"{cfo_estimate_hz:.3f} Hz"
                        if cfo_estimate_hz is not None
                        else "unavailable"
                    )
                    print(
                        f"  packet {sequence + 1:4d}/{args.packets}: "
                        f"{'PASS' if valid else 'FAIL'}, "
                        f"errors={bit_errors}/{item.bits}, "
                        f"CFO={cfo_text}, "
                        f"wall={execution.processing_wall_s:.3f} s"
                    )
    finally:
        if monitor:
            monitor.close()

    assert wall_start is not None
    assert usage_start is not None
    wall_s = time.perf_counter() - wall_start
    usage_end = os.times()
    cpu_s = (
        usage_end.user
        + usage_end.system
        - usage_start.user
        - usage_start.system
    )
    cpu_percent = 100.0 * cpu_s / wall_s if wall_s else 0.0
    current_rss_kib, high_water_rss_kib = memory_status_kib()

    total_bits = sum(item.bits for item in packet_results)
    bit_errors = sum(item.bit_errors for item in packet_results)
    raw_bit_errors = sum(item.raw_bit_errors for item in packet_results)
    decoded_packets = sum(item.decoded for item in packet_results)
    valid_packets = sum(item.valid for item in packet_results)
    packet_errors = args.packets - valid_packets
    tx_samples = sum(item.tx_samples for item in packet_results)
    sample_throughput = (
        tx_samples / total_processing_wall if total_processing_wall else 0.0
    )
    payload_throughput = (
        total_bits / total_processing_wall if total_processing_wall else 0.0
    )
    summary = {
        "application": "cora-ofdm",
        "mode": args.mode,
        "validation": "PASS" if packet_errors == 0 else "FAIL",
        "receiver_status": (
            "PASS" if decoded_packets == args.packets else "FAIL"
        ),
        "configuration": {
            "sample_rate": cfg.sample_rate,
            "fft_len": cfg.fft_len,
            "cp_len": cfg.cp_len,
            "payload_symbols": args.payload_symbols,
            "snr_db": args.snr_db,
            "doppler_ppm": args.doppler_ppm,
            "cfo_hz": args.cfo_hz,
            "seed": args.seed,
            "warmup_packets": args.warmup_packets,
            "batch_size": args.batch_size,
        },
        "wall_time_s": wall_s,
        "processing_wall_time_s": total_processing_wall,
        "cpu_time_s": cpu_s,
        "cpu_utilization_percent": cpu_percent,
        "logical_cpus": os.cpu_count(),
        "current_rss_kib": current_rss_kib,
        "peak_rss_kib": high_water_rss_kib,
        "packets_requested": args.packets,
        "decoded_packets": decoded_packets,
        "valid_packets": valid_packets,
        "packet_errors": packet_errors,
        "total_bits": total_bits,
        "bit_errors": bit_errors,
        "raw_bit_errors": raw_bit_errors,
        "ber": bit_errors / total_bits if total_bits else None,
        "tx_samples": tx_samples,
        "sample_throughput_sps": sample_throughput,
        "realtime_factor": sample_throughput / cfg.sample_rate,
        "payload_throughput_bps": payload_throughput,
        "monitor_enabled": bool(monitor),
    }
    result_document = {
        "summary": summary,
        "packets": [asdict(item) for item in packet_results],
    }

    print("Summary")
    print(
        f"  receiver status         : {summary['receiver_status']} "
        f"({decoded_packets}/{args.packets} packets decoded)"
    )
    print(
        f"  validation              : {summary['validation']} "
        f"({valid_packets}/{args.packets} packets error-free)"
    )
    print(
        f"  decoded / packet errors : {decoded_packets} / {packet_errors}"
    )
    print(f"  bit errors / BER        : {bit_errors}/{total_bits} / {summary['ber']:.6g}")
    print(f"  wall / CPU time         : {wall_s:.3f} / {cpu_s:.3f} s")
    print(f"  process CPU utilization : {cpu_percent:.1f}%")
    print(
        f"  current / peak RSS      : "
        f"{current_rss_kib if current_rss_kib is not None else 'unknown'} / "
        f"{high_water_rss_kib if high_water_rss_kib is not None else 'unknown'} KiB"
    )
    print(
        f"  sample throughput       : {sample_throughput:.0f} samples/s "
        f"({summary['realtime_factor']:.2f}x real time)"
    )
    print(f"  payload throughput      : {payload_throughput:.0f} bit/s")

    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(result_document, indent=2) + "\n", encoding="utf-8"
        )
        print(f"  JSON result             : {args.json_output}")
    receiver_failed = decoded_packets != args.packets
    validation_required_and_failed = (
        args.require_error_free and packet_errors != 0
    )
    return 2 if receiver_failed or validation_required_and_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
