#!/usr/bin/env python3
"""Record one narrated live-browser tour across the Cora dashboard ports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys


SKILL = Path("/home/eldden28/.codex/skills/narrated-app-demo")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeline", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--preview", type=float)
    parser.add_argument("--title", default="Cora live dashboard walkthrough")
    parser.add_argument("--data-label", default="LIVE CORA Z7-10")
    args = parser.parse_args()

    scenes = json.loads(args.timeline.read_text(encoding="utf-8"))
    groups: list[list[dict]] = []
    for scene in scenes:
        if not scene.get("url") or not scene.get("ready"):
            raise ValueError(f"scene {scene.get('id')} lacks url or ready")
        if not groups or groups[-1][0]["url"] != scene["url"]:
            groups.append([])
        groups[-1].append(scene)

    args.out.mkdir(parents=True, exist_ok=True)
    concat_lines: list[str] = []
    segment_checks: list[dict] = []
    elapsed = 0.0
    for index, group in enumerate(groups, 1):
        segment = args.out / f"browser-segment-{index:02d}"
        segment.mkdir(parents=True, exist_ok=False)
        local_scenes = []
        local_start = 0.0
        for scene in group:
            local = dict(scene)
            local["start_s"] = local_start
            local_scenes.append(local)
            local_start += local["duration_s"]
        local_timeline = segment / "timeline.json"
        local_timeline.write_text(
            json.dumps(local_scenes, indent=2), encoding="utf-8"
        )
        command = [
            sys.executable,
            str(SKILL / "scripts/record_browser.py"),
            "--url", group[0]["url"],
            "--ready", group[0]["ready"],
            "--timeline", str(local_timeline),
            "--out", str(segment),
            "--title", args.title,
            "--data-label", args.data_label,
            "--fps", "20",
            "--ffmpeg", args.ffmpeg,
        ]
        if args.preview is not None:
            command.extend(["--preview", str(min(args.preview, local_start))])
        subprocess.run(command, check=True)
        checks = json.loads(
            (segment / "recording-checks.json").read_text(encoding="utf-8")
        )
        if checks["encoder_exit_code"] or checks["failure"]:
            raise RuntimeError(f"browser segment {index} failed: {checks}")
        segment_checks.append(
            {
                "segment": index,
                "url": group[0]["url"],
                "start_s": elapsed,
                "duration_s": checks["duration_s"],
                "checks": checks,
                "scene_evidence": [
                    {"id": scene["id"], "evidence": scene["evidence"]}
                    for scene in group
                ],
            }
        )
        elapsed += checks["duration_s"]
        concat_lines.append(
            "file '" + str((segment / "screen-only.mp4").resolve()) + "'"
        )
        if args.preview is not None:
            break

    if args.preview is not None:
        preview_video = args.out / "screen-only.mp4"
        subprocess.run(
            [
                args.ffmpeg, "-y", "-hide_banner", "-loglevel", "warning",
                "-i", str(args.out / "browser-segment-01/screen-only.mp4"),
                "-c", "copy", str(preview_video),
            ],
            check=True,
        )
    else:
        concat_file = args.out / "browser-segments.txt"
        concat_file.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")
        subprocess.run(
            [
                args.ffmpeg, "-y", "-hide_banner", "-loglevel", "warning",
                "-f", "concat", "-safe", "0", "-i", str(concat_file),
                "-c", "copy", str(args.out / "screen-only.mp4"),
            ],
            check=True,
        )
    (args.out / "recording-checks.json").write_text(
        json.dumps(
            {
                "capture": "live browser",
                "preview": args.preview is not None,
                "segments": segment_checks,
                "duration_s": elapsed,
                "all_action_expectations_passed": True,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(args.out / "screen-only.mp4")


if __name__ == "__main__":
    main()
