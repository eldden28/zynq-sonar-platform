#!/usr/bin/env python3
"""Bounded command-line acceptance check for the Cora sonar pipeline."""

from __future__ import annotations

import argparse
import json
from typing import Sequence

from cora_sonar import SonarConfig, process_ping, scenario_targets


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Process deterministic sonar pings")
    result.add_argument("--scenario", default="point_targets")
    result.add_argument("--pings", type=int, default=3)
    result.add_argument("--max-range", type=float, default=12.0)
    result.add_argument("--noise-rms", type=float, default=0.002)
    return result


def main(arguments: Sequence[str] | None = None) -> int:
    args = parser().parse_args(arguments)
    if args.pings < 1 or args.pings > 100:
        raise SystemExit("--pings must be between 1 and 100")
    config = SonarConfig(max_range_m=args.max_range, noise_rms=args.noise_rms)
    targets = scenario_targets(args.scenario)
    totals = []
    latest = None
    for sequence in range(args.pings):
        latest = process_ping(config, targets, sequence)
        totals.append(latest.public["metrics"]["total_processing_ms"])
    assert latest is not None
    report = {
        "status": "pass",
        "scenario": args.scenario,
        "pings": args.pings,
        "mean_processing_ms": round(sum(totals) / len(totals), 2),
        "maximum_processing_ms": round(max(totals), 2),
        "beamwidth_3db_deg": latest.public["metrics"]["beamwidth_3db_deg"],
        "detections": len(latest.public["detections"]),
        "target_errors": latest.public["target_errors"],
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
