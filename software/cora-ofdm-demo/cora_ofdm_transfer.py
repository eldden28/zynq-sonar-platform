#!/usr/bin/env python3
"""Reliable framed file transfer through the Cora headless OFDM modem."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import time
from typing import Sequence
import zlib

import numpy as np

from cora_ofdm import (
    ModemConfig,
    acoustic_taps,
    make_packet,
    run_packet_batch,
)


MAGIC = b"CRFM"
HEADER = struct.Struct("<4sIIHHI")
DEFAULT_SOURCE = Path("/usr/share/cora-ofdm-demo/rickroll.rgb")
DEFAULT_OUTPUT = Path("/run/cora-ofdm-demo/received.rgb")
DEFAULT_STATE = Path("/run/cora-ofdm-demo/status.json")


def frame_size_bytes(cfg: ModemConfig, payload_symbols: int) -> int:
    bits = cfg.data_bins.size * 2 * payload_symbols
    if bits % 8:
        raise ValueError("OFDM payload does not contain a whole number of bytes")
    return bits // 8


def encode_frame(
    payload: bytes,
    sequence: int,
    total_frames: int,
    frame_bytes: int,
) -> np.ndarray:
    capacity = frame_bytes - HEADER.size
    if len(payload) > capacity:
        raise ValueError(f"frame payload exceeds {capacity} bytes")
    header = HEADER.pack(
        MAGIC,
        sequence,
        total_frames,
        len(payload),
        0,
        zlib.crc32(payload) & 0xFFFFFFFF,
    )
    frame = header + payload + bytes(capacity - len(payload))
    return np.unpackbits(np.frombuffer(frame, dtype=np.uint8))


def decode_frame(
    bits: np.ndarray,
    expected_sequence: int,
    expected_total: int,
) -> bytes:
    packed = np.packbits(np.asarray(bits, dtype=np.uint8)).tobytes()
    if len(packed) < HEADER.size:
        raise ValueError("received frame is shorter than its header")
    magic, sequence, total, length, _reserved, checksum = HEADER.unpack_from(
        packed
    )
    if magic != MAGIC:
        raise ValueError("received frame magic is invalid")
    if sequence != expected_sequence or total != expected_total:
        raise ValueError("received frame sequence metadata is invalid")
    capacity = len(packed) - HEADER.size
    if length > capacity:
        raise ValueError("received frame length is invalid")
    payload = packed[HEADER.size : HEADER.size + length]
    if zlib.crc32(payload) & 0xFFFFFFFF != checksum:
        raise ValueError("received frame CRC is invalid")
    return payload


def write_state(path: Path, values: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".new")
    temporary.write_text(
        json.dumps(values, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def transfer(args: argparse.Namespace) -> dict:
    cfg = ModemConfig()
    source = args.input.read_bytes()
    expected_bytes = args.width * args.height * 3
    if len(source) != expected_bytes:
        raise ValueError(
            f"RGB source has {len(source)} bytes; expected {expected_bytes}"
        )

    frame_bytes = frame_size_bytes(cfg, args.payload_symbols)
    data_bytes = frame_bytes - HEADER.size
    total_frames = math.ceil(len(source) / data_bytes)
    source_sha256 = hashlib.sha256(source).hexdigest()
    state = {
        "application": "cora-ofdm-transfer",
        "status": "warming",
        "mode": args.mode,
        "width": args.width,
        "height": args.height,
        "total_bytes": len(source),
        "bytes_received": 0,
        "frames_total": total_frames,
        "frames_received": 0,
        "frame_data_bytes": data_bytes,
        "batch_size": args.batch_size,
        "snr_db": args.snr_db,
        "doppler_ppm": args.doppler_ppm,
        "cfo_hz": args.cfo_hz,
        "retries": 0,
        "bit_errors": 0,
        "elapsed_s": 0.0,
        "payload_throughput_bps": 0.0,
        "eta_s": None,
        "progress_percent": 0.0,
        "source_sha256": source_sha256,
        "output_sha256": None,
        "error": None,
    }
    write_state(args.state, state)

    taps = acoustic_taps(cfg)
    warmup_bits = encode_frame(
        source[:data_bytes], 0, total_frames, frame_bytes
    )
    warmup = make_packet(
        cfg,
        args.payload_symbols,
        args.seed + total_frames + 1,
        payload_bits=warmup_bits,
    )
    run_packet_batch(
        cfg,
        [warmup],
        taps,
        args.snr_db,
        args.doppler_ppm,
        args.cfo_hz,
        args.seed + total_frames + 1,
        args.mode == "realtime",
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    frames_received = 0
    bytes_received = 0
    retries = 0
    bit_errors = 0

    try:
        with args.output.open("wb") as output:
            for batch_start in range(0, total_frames, args.batch_size):
                sequences = list(
                    range(
                        batch_start,
                        min(batch_start + args.batch_size, total_frames),
                    )
                )
                accepted: dict[int, bytes] = {}
                missing = sequences
                attempt = 0
                while missing and attempt <= args.max_retries:
                    packets = []
                    for sequence in missing:
                        offset = sequence * data_bytes
                        payload = source[offset : offset + data_bytes]
                        bits = encode_frame(
                            payload, sequence, total_frames, frame_bytes
                        )
                        packets.append(
                            make_packet(
                                cfg,
                                args.payload_symbols,
                                args.seed + sequence + attempt * total_frames,
                                payload_bits=bits,
                            )
                        )
                    executions = run_packet_batch(
                        cfg,
                        packets,
                        taps,
                        args.snr_db,
                        args.doppler_ppm,
                        args.cfo_hz,
                        (
                            args.seed
                            + missing[0]
                            + attempt * total_frames
                        )
                        & 0x7FFFFFFF,
                        args.mode == "realtime",
                    )
                    failed = []
                    for sequence, execution in zip(missing, executions):
                        receiver = execution.receiver
                        if receiver is not None:
                            bit_errors += receiver.bit_errors
                            try:
                                accepted[sequence] = decode_frame(
                                    receiver.decoded_bits,
                                    sequence,
                                    total_frames,
                                )
                                continue
                            except ValueError:
                                pass
                        failed.append(sequence)
                    if failed:
                        retries += len(failed)
                        attempt += 1
                    missing = failed

                if missing:
                    raise RuntimeError(
                        f"frames {missing} failed after "
                        f"{args.max_retries + 1} attempts"
                    )

                for sequence in sequences:
                    output.write(accepted[sequence])
                    bytes_received += len(accepted[sequence])
                    frames_received += 1
                output.flush()

                elapsed = time.perf_counter() - start
                rate = 8.0 * bytes_received / elapsed if elapsed else 0.0
                remaining_bits = 8.0 * (len(source) - bytes_received)
                state.update(
                    {
                        "status": "running",
                        "bytes_received": bytes_received,
                        "frames_received": frames_received,
                        "retries": retries,
                        "bit_errors": bit_errors,
                        "elapsed_s": elapsed,
                        "payload_throughput_bps": rate,
                        "eta_s": remaining_bits / rate if rate else None,
                        "progress_percent": (
                            100.0 * bytes_received / len(source)
                        ),
                    }
                )
                write_state(args.state, state)

        output_sha256 = hashlib.sha256(args.output.read_bytes()).hexdigest()
        if output_sha256 != source_sha256:
            raise RuntimeError("completed output SHA-256 does not match source")
        elapsed = time.perf_counter() - start
        state.update(
            {
                "status": "complete",
                "bytes_received": len(source),
                "frames_received": total_frames,
                "elapsed_s": elapsed,
                "payload_throughput_bps": (
                    8.0 * len(source) / elapsed if elapsed else 0.0
                ),
                "eta_s": 0.0,
                "progress_percent": 100.0,
                "output_sha256": output_sha256,
            }
        )
        write_state(args.state, state)
        return state
    except BaseException as error:
        state.update(
            {
                "status": (
                    "stopped" if isinstance(error, KeyboardInterrupt) else "failed"
                ),
                "error": str(error),
                "bytes_received": bytes_received,
                "frames_received": frames_received,
                "retries": retries,
                "bit_errors": bit_errors,
                "elapsed_s": time.perf_counter() - start,
            }
        )
        write_state(args.state, state)
        raise


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transfer a raw RGB image through the Cora OFDM modem."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--width", type=int, default=1024)
    parser.add_argument("--height", type=int, default=1024)
    parser.add_argument(
        "--mode",
        choices=("benchmark", "realtime"),
        default="benchmark",
    )
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--payload-symbols", type=int, default=40)
    parser.add_argument("--snr-db", type=float, default=40.0)
    parser.add_argument("--doppler-ppm", type=float, default=900.0)
    parser.add_argument("--cfo-hz", type=float, default=5.0)
    parser.add_argument("--seed", type=int, default=20260724)
    parser.add_argument("--max-retries", type=int, default=3)
    args = parser.parse_args(argv)
    if args.width < 1 or args.height < 1:
        parser.error("image dimensions must be positive")
    if args.batch_size < 1 or args.payload_symbols < 1:
        parser.error("batch size and payload symbols must be positive")
    if args.max_retries < 0:
        parser.error("max retries cannot be negative")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    result = transfer(args)
    print(
        f"OFDM image transfer complete: {result['bytes_received']} bytes, "
        f"{result['payload_throughput_bps']:.0f} bit/s, "
        f"SHA-256 {result['output_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
