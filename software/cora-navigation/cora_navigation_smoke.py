#!/usr/bin/env python3
"""Run a finite real-time Cora navigation simulation and report health."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
import time

from cora_navigation import NavigationEngine


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, default=10.0)
    parser.add_argument("--mode", choices=("lbl", "iusbl"), default="lbl")
    parser.add_argument(
        "--waveform", choices=("dedicated", "ofdm"), default="dedicated"
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.duration <= 0.0:
        parser.error("--duration must be positive")
    with tempfile.TemporaryDirectory(prefix="cora-navigation-") as temporary:
        engine = NavigationEngine(run_directory=Path(temporary))
        engine.reset()
        engine.configure(
            {"acoustic_mode": args.mode, "waveform": args.waveform}
        )
        engine.start()
        try:
            time.sleep(args.duration)
            status = engine.status()
        finally:
            engine.stop()
    summary = {
        "result": "PASS",
        "duration_s": args.duration,
        "effective_rate_hz": status["stats"]["steps"] / args.duration,
        "deadline_miss_fraction": (
            status["stats"]["deadline_misses"]
            / max(status["stats"]["steps"], 1)
        ),
        "mode": args.mode,
        "waveform": args.waveform,
        "position_error_m": status["errors"]["position_norm_m"],
        "velocity_error_mps": status["errors"]["velocity_norm_mps"],
        "dvl": status["dvl"],
        "acoustic": status["acoustic"],
        "stats": status["stats"],
    }
    if (
        status["dvl"] is None
        or not status["dvl"]["valid"]
        or status["acoustic"] is None
        or not status["acoustic"]["valid"]
        or summary["effective_rate_hz"] < 90.0
        or summary["deadline_miss_fraction"] > 0.10
        or status["stats"]["dvl_queue_drops"]
        or status["stats"]["acoustic_queue_drops"]
    ):
        summary["result"] = "FAIL"
    encoded = json.dumps(summary, indent=2) + "\n"
    print(encoded, end="")
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    if summary["result"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
