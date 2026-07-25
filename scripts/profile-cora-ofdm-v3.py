#!/usr/bin/env python3
"""Profile a complete acoustic transfer on the Cora Z7-10."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import threading
import time
import zlib

import numpy as np

import cora_ofdm_acoustic as acoustic
import cora_ofdm_text_transfer as transfer

try:
    import cProfile
    import pstats
except ModuleNotFoundError:
    cProfile = None
    pstats = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=("air", "loopback"), default="air")
    parser.add_argument("--bytes", type=int, default=3072)
    parser.add_argument("--chunk-bytes", type=int, default=384)
    parser.add_argument("--burst-packets", type=int, default=8)
    parser.add_argument(
        "--framing",
        choices=("continuous", "packet"),
        default="continuous",
    )
    parser.add_argument(
        "--modem-version",
        choices=("v3", "v3-r2/3", "uncoded"),
        default="v3",
    )
    parser.add_argument(
        "--fft-length",
        type=int,
        choices=(256, 512),
        default=256,
    )
    parser.add_argument("--low-frequency", type=float, default=1500.0)
    parser.add_argument("--high-frequency", type=float, default=12000.0)
    parser.add_argument("--noise-dbfs", type=float, default=-60.0)
    parser.add_argument("--output", type=Path, default=Path("/tmp/cora-v3-profile.json"))
    parser.add_argument(
        "--capture",
        type=Path,
        default=Path("/tmp/cora-v3-profile-capture.npy"),
    )
    parser.add_argument(
        "--pstats-prefix",
        type=Path,
        default=Path("/tmp/cora-v3"),
    )
    return parser.parse_args()


def summarize_intervals(
    intervals: list[dict],
    *,
    origin: float,
) -> dict:
    elapsed = [item["end"] - item["start"] for item in intervals]
    if not elapsed:
        return {
            "calls": 0,
            "sum_s": 0.0,
            "mean_s": 0.0,
            "max_s": 0.0,
            "parallel_span_s": 0.0,
            "first_start_s": None,
            "last_end_s": None,
        }
    return {
        "calls": len(intervals),
        "sum_s": sum(elapsed),
        "mean_s": sum(elapsed) / len(elapsed),
        "max_s": max(elapsed),
        "parallel_span_s": (
            max(item["end"] for item in intervals)
            - min(item["start"] for item in intervals)
        ),
        "first_start_s": min(item["start"] for item in intervals) - origin,
        "last_end_s": max(item["end"] for item in intervals) - origin,
    }


def manual_function_profile(function, targets: tuple[str, ...]) -> dict:
    """Time selected module functions without optional profiler packages."""
    records = {
        name: {"calls": 0, "sum_s": 0.0, "max_s": 0.0}
        for name in targets
    }
    originals = {name: getattr(acoustic, name) for name in targets}

    def make_wrapper(name):
        original = originals[name]

        def wrapper(*args, **kwargs):
            started = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                elapsed = time.perf_counter() - started
                records[name]["calls"] += 1
                records[name]["sum_s"] += elapsed
                records[name]["max_s"] = max(
                    records[name]["max_s"],
                    elapsed,
                )

        return wrapper

    for name in targets:
        setattr(acoustic, name, make_wrapper(name))
    started = time.perf_counter()
    try:
        result = function()
    finally:
        elapsed = time.perf_counter() - started
        for name, original in originals.items():
            setattr(acoustic, name, original)

    if isinstance(result, list):
        result_valid = all(
            bool(getattr(item, "valid", True)) for item in result
        )
    else:
        result_valid = bool(getattr(result, "valid", True))
    return {
        "elapsed_s": elapsed,
        "result_valid": result_valid,
        "functions": dict(
            sorted(
                records.items(),
                key=lambda item: item[1]["sum_s"],
                reverse=True,
            )
        ),
    }


def main() -> None:
    args = parse_args()
    if args.bytes < 1:
        raise ValueError("--bytes must be positive")

    cfg = acoustic.make_acoustic_config(
        args.modem_version,
        args.low_frequency,
        args.high_frequency,
        fft_len=args.fft_length,
    )
    if args.backend == "air":
        backend = transfer.AlsaAirBackend(
            burst_packets=args.burst_packets,
            cfg=cfg,
            continuous_framing=args.framing == "continuous",
        )
    else:
        backend = transfer.ColoredRoomBackend(args.noise_dbfs, cfg=cfg)

    lock = threading.Lock()
    encode_intervals: list[dict] = []
    decode_intervals: list[dict] = []
    encode_superframe_intervals: list[dict] = []
    decode_superframe_intervals: list[dict] = []
    acquire_superframe_intervals: list[dict] = []
    decode_slot_intervals: list[dict] = []
    process_launches: list[dict] = []
    batch_intervals: list[dict] = []
    first_capture: list[np.ndarray] = []
    encoded_durations: list[float] = []

    original_encode = transfer.encode_packet
    original_decode = transfer.decode_packet
    original_encode_superframe = transfer.encode_superframe
    original_decode_superframe = transfer.decode_superframe
    original_acquire_superframe = transfer.acquire_superframe
    original_decode_superframe_slot = transfer.decode_superframe_slot
    original_popen = transfer.subprocess.Popen

    def timed_encode(*call_args, **call_kwargs):
        started = time.perf_counter()
        result = original_encode(*call_args, **call_kwargs)
        ended = time.perf_counter()
        with lock:
            encode_intervals.append({"start": started, "end": ended})
            encoded_durations.append(result.duration_s)
        return result

    def timed_decode(samples, *call_args, **call_kwargs):
        with lock:
            if not first_capture:
                first_capture.append(np.asarray(samples).copy())
        started = time.perf_counter()
        result = original_decode(samples, *call_args, **call_kwargs)
        ended = time.perf_counter()
        with lock:
            decode_intervals.append(
                {
                    "start": started,
                    "end": ended,
                    "valid": result.valid,
                    "sequence": result.sequence,
                    "error": result.error,
                }
            )
        return result

    def timed_encode_superframe(*call_args, **call_kwargs):
        started = time.perf_counter()
        result = original_encode_superframe(*call_args, **call_kwargs)
        ended = time.perf_counter()
        with lock:
            encode_superframe_intervals.append(
                {"start": started, "end": ended}
            )
            encoded_durations.append(result.duration_s)
        return result

    def timed_decode_superframe(samples, *call_args, **call_kwargs):
        with lock:
            if not first_capture:
                first_capture.append(np.asarray(samples).copy())
        started = time.perf_counter()
        result = original_decode_superframe(
            samples,
            *call_args,
            **call_kwargs,
        )
        ended = time.perf_counter()
        with lock:
            decode_superframe_intervals.append(
                {
                    "start": started,
                    "end": ended,
                    "valid_packets": sum(item.valid for item in result),
                }
            )
        return result

    def timed_acquire_superframe(*call_args, **call_kwargs):
        started = time.perf_counter()
        result = original_acquire_superframe(*call_args, **call_kwargs)
        ended = time.perf_counter()
        with lock:
            acquire_superframe_intervals.append(
                {"start": started, "end": ended}
            )
        return result

    def timed_decode_superframe_slot(*call_args, **call_kwargs):
        started = time.perf_counter()
        result = original_decode_superframe_slot(
            *call_args,
            **call_kwargs,
        )
        ended = time.perf_counter()
        with lock:
            decode_slot_intervals.append(
                {
                    "start": started,
                    "end": ended,
                    "valid": result.valid,
                    "sequence": result.sequence,
                    "error": result.error,
                }
            )
        return result

    def timed_popen(command, *call_args, **call_kwargs):
        started = time.perf_counter()
        result = original_popen(command, *call_args, **call_kwargs)
        ended = time.perf_counter()
        executable = str(command[0]) if command else "unknown"
        with lock:
            process_launches.append(
                {
                    "process": Path(executable).name,
                    "start": started,
                    "end": ended,
                }
            )
        return result

    original_batch = getattr(backend, "transceive_batch", None)
    if callable(original_batch):
        def timed_batch(items):
            started = time.perf_counter()
            try:
                return original_batch(items)
            finally:
                ended = time.perf_counter()
                with lock:
                    batch_intervals.append(
                        {"start": started, "end": ended}
                    )

        backend.transceive_batch = timed_batch

    marker = f"OFDM-{cfg.modem_name.upper()}-"
    text = (marker * ((args.bytes + len(marker) - 1) // len(marker)))[
        : args.bytes
    ]
    origin = time.perf_counter()
    transfer.encode_packet = timed_encode
    transfer.decode_packet = timed_decode
    transfer.encode_superframe = timed_encode_superframe
    transfer.decode_superframe = timed_decode_superframe
    transfer.acquire_superframe = timed_acquire_superframe
    transfer.decode_superframe_slot = timed_decode_superframe_slot
    transfer.subprocess.Popen = timed_popen
    try:
        output, state = transfer.transfer_text(
            text,
            backend=backend,
            chunk_bytes=args.chunk_bytes,
        )
    finally:
        transfer.encode_packet = original_encode
        transfer.decode_packet = original_decode
        transfer.encode_superframe = original_encode_superframe
        transfer.decode_superframe = original_decode_superframe
        transfer.acquire_superframe = original_acquire_superframe
        transfer.decode_superframe_slot = original_decode_superframe_slot
        transfer.subprocess.Popen = original_popen
    finished = time.perf_counter()

    launches = [
        {
            **item,
            "start_s": item["start"] - origin,
            "launch_time_s": item["end"] - item["start"],
        }
        for item in process_launches
    ]
    for item in launches:
        item.pop("start")
        item.pop("end")

    result = {
        "configuration": {
            "backend": args.backend,
            "framing": getattr(backend, "framing", args.framing),
            "modem_version": cfg.modem_name,
            "body_code": cfg.body_code,
            "fft_len": cfg.fft_len,
            "cp_len": cfg.cp_len,
            "subcarrier_spacing_hz": cfg.subcarrier_spacing_hz,
            "low_frequency_hz": cfg.low_frequency_hz,
            "high_frequency_hz": cfg.high_frequency_hz,
            "data_subcarriers": int(cfg.data_bins.size),
            "payload_bytes": len(text.encode()),
            "chunk_bytes": args.chunk_bytes,
            "burst_packets": args.burst_packets,
        },
        "transfer": state,
        "instrumented_wall_s": finished - origin,
        "output_matches": output == text.encode(),
        "encode_packet": summarize_intervals(
            encode_intervals,
            origin=origin,
        ),
        "decode_packet": summarize_intervals(
            decode_intervals,
            origin=origin,
        ),
        "encode_superframe": summarize_intervals(
            encode_superframe_intervals,
            origin=origin,
        ),
        "decode_superframe": summarize_intervals(
            decode_superframe_intervals,
            origin=origin,
        ),
        "acquire_superframe": summarize_intervals(
            acquire_superframe_intervals,
            origin=origin,
        ),
        "decode_superframe_slot": summarize_intervals(
            decode_slot_intervals,
            origin=origin,
        ),
        "batch": summarize_intervals(batch_intervals, origin=origin),
        "nominal_audio_s": sum(encoded_durations),
        "nominal_packet_audio_s": sum(encoded_durations),
        "process_launches": launches,
    }
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # A standalone packet capture can be replayed for a second detailed CPU
    # profile. Streaming-slot captures are intentionally incremental, so no
    # one snapshot contains the complete superframe.
    if first_capture and args.framing == "packet":
        np.save(args.capture, first_capture[0])
        stream_cfg = replace(
            cfg,
            leading_silence_s=0.04,
            trailing_silence_s=0.04,
        )
        representative = text.encode()[: args.chunk_bytes]
        transfer_id = zlib.crc32(text.encode()) & 0xFFFFFFFF
        frame = transfer.encode_text_frame(
            representative,
            transfer_id=transfer_id,
            sequence=0,
            total_packets=state["packets_total"],
        )
        manual_encode = lambda: acoustic.encode_packet(
            frame,
            sequence=0,
            cfg=stream_cfg,
        )
        manual_decode = lambda: acoustic.decode_packet(
            first_capture[0],
            cfg=stream_cfg,
        )
        encode_label = "encode_one_packet"
        decode_label = "decode_one_captured_packet"
        result["manual_cpu_profile"] = {
            encode_label: manual_function_profile(
                manual_encode,
                (
                    "_encode_body",
                    "convolutional_encode",
                    "interleave_bits",
                    "_training_grids",
                    "_bit_rows_to_grids",
                    "_grids_to_symbols",
                ),
            ),
            decode_label: manual_function_profile(
                manual_decode,
                (
                    "_find_start",
                    "_normalized_correlation",
                    "_fft_symbol",
                    "_equalize_symbol",
                    "qpsk_demap",
                    "_decode_body",
                    "deinterleave_bits",
                    "viterbi_decode",
                ),
            ),
        }

        if cProfile is not None and pstats is not None:
            encode_profile = cProfile.Profile()
            encode_profile.runcall(manual_encode)
            encode_path = Path(f"{args.pstats_prefix}-encode.pstats")
            encode_profile.dump_stats(encode_path)

            decode_profile = cProfile.Profile()
            decode_profile.runcall(manual_decode)
            decode_path = Path(f"{args.pstats_prefix}-decode.pstats")
            decode_profile.dump_stats(decode_path)

            print("\nEncoder CPU profile (cumulative):")
            pstats.Stats(encode_profile).strip_dirs().sort_stats(
                "cumulative"
            ).print_stats(15)
            print("\nDecoder CPU profile (cumulative):")
            pstats.Stats(decode_profile).strip_dirs().sort_stats(
                "cumulative"
            ).print_stats(20)
        else:
            print(
                "\ncProfile is unavailable; used built-in function timers."
            )

        args.output.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print("\nTiming summary:")
    print(json.dumps(result, indent=2, sort_keys=True))
    print(f"\nSaved {args.output}")


if __name__ == "__main__":
    main()
