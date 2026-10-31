#!/usr/bin/env python3
"""Record the generated Cora block design from the actual Vivado GUI."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import time
from pathlib import Path
from textwrap import wrap

from PIL import Image, ImageDraw, ImageFont, ImageGrab


WIDTH = 1920
APP_HEIGHT = 896
HEIGHT = 1080
HEADER = 64
FPS_DEFAULT = 20


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return ImageFont.truetype(f"/usr/share/fonts/truetype/dejavu/{name}", size)


def tcl_word(value: str) -> str:
    return "{" + value.replace("}", "\\}") + "}"


def build_tcl(timeline: list[dict], project: Path, bd: Path, ready: Path, trigger: Path, log: Path) -> str:
    scheduled: list[tuple[int, str]] = []
    for scene in timeline:
        start_ms = round(float(scene["start_s"]) * 1000)
        scheduled.append((start_ms, "demo_overview"))
        for action in scene.get("actions", []):
            at_ms = start_ms + round(float(scene["duration_s"]) * float(action["at_fraction"]) * 1000)
            if action["action"] == "overview":
                scheduled.append((at_ms, "demo_overview"))
            elif action["action"] == "highlight":
                cells = " ".join(tcl_word(cell) for cell in action["cells"])
                scheduled.append((at_ms, f"demo_highlight [list {cells}]"))

    lines = [
        f"set demo_ready {tcl_word(str(ready))}",
        f"set demo_trigger {tcl_word(str(trigger))}",
        f"set demo_log {tcl_word(str(log))}",
        "proc demo_append {message} {",
        "  global demo_log",
        "  set handle [open $demo_log a]",
        "  puts $handle $message",
        "  close $handle",
        "}",
        "proc demo_clear {} {",
        "  catch {unhighlight_objects [get_bd_cells -quiet -hierarchical *]}",
        "  catch {unselect_objects [get_bd_cells -quiet -hierarchical *]}",
        "}",
        "proc demo_overview {} {",
        "  demo_clear",
        "  demo_append OVERVIEW",
        "}",
        "proc demo_highlight {names} {",
        "  demo_clear",
        "  set objects [list]",
        "  foreach name $names {",
        "    set found [get_bd_cells -quiet $name]",
        "    if {[llength $found] == 0} {demo_append [format {MISSING %s} $name]}",
        "    foreach object $found {lappend objects $object}",
        "  }",
        "  if {[llength $objects] > 0} {",
        "    catch {highlight_objects -color orange $objects}",
        "    catch {select_objects $objects}",
        "  }",
        "  demo_append [format {HIGHLIGHT %s} [join $names ,]]",
        "}",
        "proc demo_start {} {",
        "  global demo_trigger",
        "  if {![file exists $demo_trigger]} {after 100 demo_start; return}",
        "  demo_append START",
    ]
    for at_ms, command in sorted(scheduled):
        lines.append(f"  after {at_ms} {{{command}}}")
    end_ms = round((float(timeline[-1]["start_s"]) + float(timeline[-1]["duration_s"])) * 1000) + 1000
    lines.extend(
        [
            f"  after {end_ms} {{demo_append COMPLETE}}",
            "}",
            f"open_project {tcl_word(str(project))}",
            f"open_bd_design [get_files {tcl_word(str(bd))}]",
            "catch {regenerate_bd_layout}",
            "catch {validate_bd_design}",
            "set handle [open $demo_ready w]",
            "puts $handle READY",
            "close $handle",
            "demo_start",
            "demo_append GUI_READY",
            "",
        ]
    )
    return "\n".join(lines)


def resize_vivado_window(display_name: str) -> bool:
    lib = ctypes.CDLL("libX11.so.6")
    lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
    lib.XOpenDisplay.restype = ctypes.c_void_p
    lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    lib.XDefaultRootWindow.restype = ctypes.c_ulong
    lib.XQueryTree.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)), ctypes.POINTER(ctypes.c_uint)]
    lib.XQueryTree.restype = ctypes.c_int
    lib.XFetchName.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_char_p)]
    lib.XFetchName.restype = ctypes.c_int
    lib.XFree.argtypes = [ctypes.c_void_p]
    lib.XFree.restype = ctypes.c_int
    lib.XMoveResizeWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
    lib.XMoveResizeWindow.restype = ctypes.c_int
    lib.XMapRaised.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    lib.XMapRaised.restype = ctypes.c_int
    lib.XSetInputFocus.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    lib.XSetInputFocus.restype = ctypes.c_int
    lib.XFlush.argtypes = [ctypes.c_void_p]
    lib.XFlush.restype = ctypes.c_int
    lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
    lib.XCloseDisplay.restype = ctypes.c_int
    display = lib.XOpenDisplay(display_name.encode())
    if not display:
        return False
    root = lib.XDefaultRootWindow(display)

    def children(window: int) -> list[int]:
        root_return = ctypes.c_ulong()
        parent_return = ctypes.c_ulong()
        items = ctypes.POINTER(ctypes.c_ulong)()
        count = ctypes.c_uint()
        if not lib.XQueryTree(display, window, ctypes.byref(root_return), ctypes.byref(parent_return), ctypes.byref(items), ctypes.byref(count)):
            return []
        result = [items[index] for index in range(count.value)]
        if items:
            lib.XFree(items)
        return result

    candidates: list[int] = []
    stack = children(root)
    while stack:
        window = stack.pop()
        name = ctypes.c_char_p()
        if lib.XFetchName(display, window, ctypes.byref(name)) and name.value:
            if b"vivado" in name.value.lower():
                candidates.append(window)
        stack.extend(children(window))
    if not candidates:
        lib.XCloseDisplay(display)
        return False
    target = candidates[0]
    lib.XMoveResizeWindow(display, target, 0, 0, WIDTH, APP_HEIGHT)
    lib.XMapRaised(display, target)
    lib.XSetInputFocus(display, target, 1, 0)
    lib.XFlush(display)
    lib.XCloseDisplay(display)
    return True


def scene_at(timeline: list[dict], elapsed: float) -> dict:
    for scene in timeline:
        if elapsed < float(scene["start_s"]) + float(scene["duration_s"]):
            return scene
    return timeline[-1]


def compose(screen: Image.Image, scene: dict) -> Image.Image:
    frame = Image.new("RGB", (WIDTH, HEIGHT), "#061018")
    screen = screen.convert("RGB").resize((WIDTH, APP_HEIGHT), Image.Resampling.LANCZOS)
    frame.paste(screen, (0, HEADER))
    draw = ImageDraw.Draw(frame)
    draw.rectangle((0, 0, WIDTH, HEADER), fill="#07141d")
    draw.line((0, HEADER - 1, WIDTH, HEADER - 1), fill="#2d5367", width=1)
    draw.text((28, 13), scene["title"], font=font(28, True), fill="#f1fbff")
    label = "ACTUAL VIVADO GUI  •  GENERATED CORA PROJECT"
    label_font = font(15, True)
    label_box = draw.textbbox((0, 0), label, font=label_font)
    draw.text((WIDTH - (label_box[2] - label_box[0]) - 28, 22), label, font=label_font, fill="#61d9ff")
    draw.rectangle((0, HEADER + APP_HEIGHT, WIDTH, HEIGHT), fill="#07141d")
    draw.line((0, HEADER + APP_HEIGHT, WIDTH, HEADER + APP_HEIGHT), fill="#2d5367", width=1)
    caption_lines = wrap(scene.get("caption", scene["text"]), width=128)[:3]
    y = HEADER + APP_HEIGHT + 14
    caption_font = font(19)
    for line in caption_lines:
        draw.text((32, y), line, font=caption_font, fill="#e9f6fc")
        y += 28
    return frame


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeline", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--bd", required=True, type=Path)
    parser.add_argument("--ffmpeg", required=True, type=Path)
    parser.add_argument("--vivado", default="/tools/Xilinx/2025.1/Vivado/bin/vivado", type=Path)
    parser.add_argument("--fps", default=FPS_DEFAULT, type=int)
    parser.add_argument("--ready-timeout", default=180.0, type=float)
    parser.add_argument("--max-duration", type=float, help="Optional short visual-preview duration")
    args = parser.parse_args()

    if not os.environ.get("DISPLAY"):
        raise SystemExit("DISPLAY is required; launch this recorder under xvfb-run")
    timeline = json.loads(args.timeline.read_text())
    args.out.mkdir(parents=True, exist_ok=True)
    ready = (args.out / "vivado-ready").resolve()
    trigger = (args.out / "record-trigger").resolve()
    action_log = (args.out / "vivado-actions.log").resolve()
    tcl = args.out / "vivado-demo.tcl"
    reserved = [ready, trigger, action_log, tcl, args.out / "screen-only.mp4", args.out / "recording-checks.json"]
    existing = [str(path) for path in reserved if path.exists()]
    if existing:
        raise FileExistsError("Use a fresh recording directory; generated files already exist: " + ", ".join(existing))
    tcl.write_text(build_tcl(timeline, args.project.resolve(), args.bd.resolve(), ready, trigger, action_log))
    vivado_log = (args.out / "vivado.log").open("w")
    process = subprocess.Popen(
        [str(args.vivado), "-mode", "gui", "-nolog", "-nojournal", "-source", str(tcl.resolve())],
        stdout=vivado_log,
        stderr=subprocess.STDOUT,
    )
    deadline = time.monotonic() + args.ready_timeout
    while not ready.exists() and time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Vivado exited early with code {process.returncode}")
        time.sleep(0.25)
    if not ready.exists():
        process.terminate()
        raise TimeoutError("Vivado did not open the block design before the timeout")
    time.sleep(3.0)
    resized = resize_vivado_window(os.environ["DISPLAY"])
    time.sleep(1.0)

    video = args.out / "screen-only.mp4"
    command = [
        str(args.ffmpeg), "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{WIDTH}x{HEIGHT}",
        "-r", str(args.fps), "-i", "-", "-an", "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(video),
    ]
    encoder = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    trigger.write_text("START\n")
    start = time.perf_counter()
    duration = float(timeline[-1]["start_s"]) + float(timeline[-1]["duration_s"])
    if args.max_duration is not None:
        duration = min(duration, args.max_duration)
    total_frames = round(duration * args.fps)
    stills: list[str] = []
    last_scene = None
    try:
        for index in range(total_frames):
            deadline_frame = start + index / args.fps
            delay = deadline_frame - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            elapsed = min(index / args.fps, duration - 0.001)
            scene = scene_at(timeline, elapsed)
            screen = ImageGrab.grab(xdisplay=os.environ["DISPLAY"]).crop((0, 0, WIDTH, APP_HEIGHT))
            frame = compose(screen, scene)
            if scene["id"] != last_scene:
                still = args.out / f"chapter-{len(stills) + 1:02d}-{scene['id']}.png"
                frame.save(still)
                stills.append(str(still))
                last_scene = scene["id"]
            assert encoder.stdin is not None
            encoder.stdin.write(frame.tobytes())
    finally:
        if encoder.stdin:
            encoder.stdin.close()
        stderr = encoder.stderr.read().decode(errors="replace") if encoder.stderr else ""
        encoder.wait()
        (args.out / "ffmpeg-record.log").write_text(stderr)
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        vivado_log.close()
    if encoder.returncode:
        raise RuntimeError(f"ffmpeg exited with code {encoder.returncode}")
    action_text = action_log.read_text() if action_log.exists() else ""
    expected = 0
    for scene in timeline:
        for action in scene.get("actions", []):
            action_time = float(scene["start_s"]) + float(scene["duration_s"]) * float(action["at_fraction"])
            if action["action"] in {"overview", "highlight"} and action_time < duration:
                expected += 1
    checks = {
        "application": "Vivado 2025.1 GUI",
        "project": str(args.project.resolve()),
        "block_design": str(args.bd.resolve()),
        "window_resized": resized,
        "frame_count": total_frames,
        "expected_action_count": expected,
        "logged_action_count": action_text.count("HIGHLIGHT ") + action_text.count("OVERVIEW"),
        "missing_cells": [line for line in action_text.splitlines() if line.startswith("MISSING ")],
        "chapter_stills": stills,
        "vivado_exit_code": process.returncode,
        "encoder_exit_code": encoder.returncode,
    }
    checks["passed"] = bool(resized and checks["logged_action_count"] >= expected and not checks["missing_cells"] and encoder.returncode == 0)
    (args.out / "recording-checks.json").write_text(json.dumps(checks, indent=2) + "\n")
    if not checks["passed"]:
        raise RuntimeError(f"Vivado recording checks failed: {checks}")


if __name__ == "__main__":
    main()
