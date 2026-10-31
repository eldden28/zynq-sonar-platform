#!/usr/bin/env python3
"""Assemble the verified Cora demo clips with continuous captions and chapters."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path


CLIPS = [
    ("Series introduction", "cora-series-intro-narrated.mp4", "cora-series-intro-narrated-take-01/captions.srt", "cora-series-intro-narrated-take-01/transcript.md"),
    ("Main dashboard", "cora-dashboard-live-narrated.mp4", "cora-dashboard-live-narrated-take-03/captions.srt", "cora-dashboard-live-narrated-take-03/transcript.md"),
    ("Operations and concurrent dashboards", "cora-operations-addendum-narrated.mp4", "cora-operations-addendum-narrated-take-01/captions.srt", "cora-operations-addendum-narrated-take-01/transcript.md"),
    ("Vivado block design", "cora-vivado-block-design-live-narrated.mp4", "cora-vivado-block-design-live-narrated-take-02/captions.srt", "cora-vivado-block-design-live-narrated-take-02/transcript.md"),
    ("Modem summary and benchmarks", "cora-modem-summary-narrated.mp4", "cora-modem-summary-narrated-take-03/captions.srt", "cora-modem-summary-narrated-take-03/transcript.md"),
    ("Navigation and mission dashboard", "cora-navigation-mission-live-narrated.mp4", "cora-navigation-mission-live-narrated-take-02/captions.srt", "cora-navigation-mission-live-narrated-take-02/transcript.md"),
]

TIME = re.compile(r"(\d\d):(\d\d):(\d\d),(\d\d\d)")


def ffmpeg_duration(ffmpeg: str, video: Path) -> float:
    result = subprocess.run([ffmpeg, "-hide_banner", "-i", str(video)], capture_output=True, text=True)
    match = re.search(r"Duration: (\d\d):(\d\d):(\d\d\.\d+)", result.stderr)
    if not match:
        raise RuntimeError(f"could not determine duration for {video}")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def parse_time(value: str) -> float:
    hours, minutes, seconds, millis = TIME.fullmatch(value).groups()
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000


def format_time(value: float) -> str:
    total_ms = round(value * 1000)
    hours, total_ms = divmod(total_ms, 3_600_000)
    minutes, total_ms = divmod(total_ms, 60_000)
    seconds, millis = divmod(total_ms, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"


def shifted_srt(source: Path, offset: float, index: int) -> tuple[str, int]:
    blocks = re.split(r"\n\s*\n", source.read_text(encoding="utf-8").strip())
    output: list[str] = []
    for block in blocks:
        lines = block.splitlines()
        timing_index = next((i for i, line in enumerate(lines) if " --> " in line), None)
        if timing_index is None:
            continue
        begin, end = lines[timing_index].split(" --> ")
        lines[0:timing_index + 1] = [str(index), f"{format_time(parse_time(begin) + offset)} --> {format_time(parse_time(end) + offset)}"]
        output.append("\n".join(lines))
        index += 1
    return "\n\n".join(output), index


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--videos", type=Path, default=Path("docs/demo-videos"))
    parser.add_argument("--out", type=Path, default=Path("docs/demo-videos/cora-longform-demo-take-01"))
    parser.add_argument("--destination", type=Path, default=Path("docs/demo-videos/cora-longform-demo-narrated.mp4"))
    args = parser.parse_args()
    ffmpeg = shutil.which(args.ffmpeg) or args.ffmpeg
    if args.out.exists() or args.destination.exists():
        raise ValueError("use a fresh output directory and destination")
    args.out.mkdir(parents=True)

    manifest_lines: list[str] = []
    captions: list[str] = []
    transcript = ["# Cora long-form demo", "", "This assembly preserves the original live recordings, AI narration, and captions.", ""]
    metadata = [";FFMETADATA1", "title=Cora Z7-10 unified demo series", "comment=Assembly of verified live Cora demonstrations."]
    offset = 0.0
    subtitle_index = 1
    for order, (title, video_name, captions_name, transcript_name) in enumerate(CLIPS, 1):
        video = args.videos / video_name
        caption_source = args.videos / captions_name
        transcript_source = args.videos / transcript_name
        if not (video.is_file() and caption_source.is_file() and transcript_source.is_file()):
            raise FileNotFoundError(f"missing input for {title}")
        duration = ffmpeg_duration(ffmpeg, video)
        manifest_lines.append(f"file '{video.resolve()}'")
        section, subtitle_index = shifted_srt(caption_source, offset, subtitle_index)
        captions.append(section)
        transcript.extend([f"## {format_time(offset).replace(',', '.')} — {title}", "", transcript_source.read_text(encoding="utf-8").strip(), ""])
        start_ms = round(offset * 1000)
        end_ms = round((offset + duration) * 1000)
        metadata.extend(["[CHAPTER]", "TIMEBASE=1/1000", f"START={start_ms}", f"END={end_ms}", f"title={order}. {title}"])
        offset += duration

    manifest = args.out / "clips.ffconcat"
    captions_path = args.out / "captions.srt"
    metadata_path = args.out / "chapters.ffmeta"
    intermediate = args.out / "combined-av.mp4"
    manifest.write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")
    captions_path.write_text("\n\n".join(captions) + "\n", encoding="utf-8")
    metadata_path.write_text("\n".join(metadata) + "\n", encoding="utf-8")
    (args.out / "transcript.md").write_text("\n".join(transcript), encoding="utf-8")

    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "warning", "-f", "concat", "-safe", "0", "-i", str(manifest), "-map", "0:v:0", "-map", "0:a:0", "-c", "copy", "-movflags", "+faststart", str(intermediate)], check=True)
    subprocess.run([
        ffmpeg, "-y", "-hide_banner", "-loglevel", "warning", "-i", str(intermediate), "-i", str(captions_path), "-i", str(metadata_path),
        "-map", "0:v:0", "-map", "0:a:0", "-map", "1:0", "-map_metadata", "2", "-map_chapters", "2",
        "-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text", "-metadata:s:s:0", "language=eng", "-movflags", "+faststart", str(args.destination),
    ], check=True)
    (args.out / "assembly-checks.txt").write_text(
        f"clips={len(CLIPS)}\nexpected_duration_s={offset:.3f}\ncaption_blocks={subtitle_index - 1}\n", encoding="utf-8"
    )
    print(f"Assembled {len(CLIPS)} clips; expected duration {offset:.3f} seconds")


if __name__ == "__main__":
    main()
