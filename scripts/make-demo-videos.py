#!/usr/bin/env python3
"""Create captioned dashboard and AXI block-design walkthrough WebM videos."""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.request

from PIL import Image, ImageDraw, ImageFont


WIDTH = 1280
HEIGHT = 720
FPS = 10
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
BOLD_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def font(size: int, bold: bool = False):
    return ImageFont.truetype(BOLD_PATH if bold else FONT_PATH, size)


def wrap(draw: ImageDraw.ImageDraw, text: str, chosen_font, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textbbox((0, 0), candidate, font=chosen_font)[2] <= width:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def title_card(title: str, caption: str) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), "#06100f")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((70, 90, 1210, 630), 28, fill="#0b1d1a", outline="#4de0d0", width=3)
    draw.text((115, 155), title, font=font(44, True), fill="#eafff8")
    lines = wrap(draw, caption, font(27), 1010)
    for index, line in enumerate(lines):
        draw.text((115, 270 + index * 42), line, font=font(27), fill="#a9cfc3")
    draw.text((115, 555), "Zynq Sonar Platform · Cora Z7-10", font=font(20), fill="#4de0d0")
    return image


def screenshot(url: str, destination: Path) -> Image.Image:
    destination = destination.resolve()
    driver = subprocess.Popen(
        ["geckodriver", "--port", "4444"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    session = None

    def request(method: str, path: str, payload=None):
        body = None if payload is None else json.dumps(payload).encode()
        call = urllib.request.Request(
            f"http://127.0.0.1:4444{path}",
            data=body,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(call, timeout=20) as response:
            return json.load(response)

    try:
        for _ in range(50):
            try:
                request("GET", "/status")
                break
            except Exception:
                time.sleep(0.1)
        created = request(
            "POST",
            "/session",
            {
                "capabilities": {
                    "alwaysMatch": {
                        "browserName": "firefox",
                        "moz:firefoxOptions": {"args": ["-headless"]},
                    }
                }
            },
        )
        session = created["value"]["sessionId"]
        request(
            "POST",
            f"/session/{session}/window/rect",
            {"width": 1440, "height": 900},
        )
        request("POST", f"/session/{session}/url", {"url": url})
        time.sleep(4)
        request(
            "POST",
            f"/session/{session}/execute/sync",
            {
                "script": (
                    "const backend=document.getElementById('backend');"
                    "if(backend && backend.querySelector('[value=pl-loopback]')) {"
                    "backend.value='pl-loopback';}"
                    "return backend ? backend.value : null;"
                ),
                "args": [],
            },
        )
        time.sleep(0.2)
        encoded = request("GET", f"/session/{session}/screenshot")["value"]
        destination.write_bytes(base64.b64decode(encoded))
        return Image.open(destination).convert("RGB")
    finally:
        if session is not None:
            try:
                request("DELETE", f"/session/{session}")
            except Exception:
                pass
        driver.terminate()
        try:
            driver.wait(timeout=5)
        except subprocess.TimeoutExpired:
            driver.kill()


def dashboard_slide(source: Image.Image, title: str, caption: str, step: int) -> Image.Image:
    canvas = Image.new("RGB", (WIDTH, HEIGHT), "#030807")
    view = source.copy()
    resampling = getattr(Image, "Resampling", Image)
    view.thumbnail((WIDTH, 540), resampling.LANCZOS)
    canvas.paste(view, ((WIDTH - view.width) // 2, 72))
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.rectangle((0, 0, WIDTH, 74), fill="#06100ff2")
    draw.rectangle((0, 585, WIDTH, HEIGHT), fill="#06100ff2")
    draw.text((28, 20), f"{step:02d}  {title}", font=font(28, True), fill="#eafff8")
    lines = wrap(draw, caption, font(20), WIDTH - 60)
    for index, line in enumerate(lines[:4]):
        draw.text((30, 606 + index * 27), line, font=font(20), fill="#b8ddd2")
    return canvas


def arrow(draw, start, end, color="#5ee9ff"):
    draw.line((*start, *end), fill=color, width=8)
    x, y = end
    draw.polygon([(x, y), (x - 18, y - 12), (x - 18, y + 12)], fill=color)


def block_slide(step: int, title: str, caption: str, highlight: set[str]) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), "#05090b")
    draw = ImageDraw.Draw(image)
    draw.text((34, 24), f"{step:02d}  {title}", font=font(31, True), fill="#eafff8")
    nodes = {
        "ddr_tx": (45, 220, 175, 320, "DDR\nTX buffer"),
        "mm2s": (225, 220, 370, 320, "AXI DMA\nMM2S"),
        "router": (425, 175, 660, 365, "AXIS ACOMMS ROUTER\nloopback / accelerator\n100 MHz ÷ 400\n250 ksample/s"),
        "fft": (735, 115, 870, 200, "FFT"),
        "filter": (915, 115, 1060, 200, "Your IP /\nspectral mask"),
        "ifft": (1105, 115, 1235, 200, "IFFT"),
        "s2mm": (780, 390, 925, 490, "AXI DMA\nS2MM"),
        "ddr_rx": (1010, 390, 1190, 490, "DDR\nRX buffer"),
    }
    for name, (x1, y1, x2, y2, label) in nodes.items():
        color = "#58d7ff" if name in highlight else "#36524c"
        fill = "#123238" if name in highlight else "#0c1716"
        draw.rounded_rectangle((x1, y1, x2, y2), 14, fill=fill, outline=color, width=4)
        lines = label.split("\n")
        for index, line in enumerate(lines):
            bbox = draw.textbbox((0, 0), line, font=font(17, index == 0))
            draw.text(((x1 + x2 - bbox[2]) / 2, y1 + 17 + index * 27), line,
                      font=font(17, index == 0), fill="#eafff8")
    arrow(draw, (175, 270), (225, 270))
    arrow(draw, (370, 270), (425, 270))
    arrow(draw, (660, 215), (735, 160))
    arrow(draw, (870, 160), (915, 160))
    arrow(draw, (1060, 160), (1105, 160))
    draw.line((1235, 160, 1250, 160, 1250, 335, 660, 335), fill="#5ee9ff", width=6)
    arrow(draw, (660, 300), (780, 440))
    arrow(draw, (925, 440), (1010, 440))
    draw.arc((500, 245, 625, 345), 15, 345, fill="#ffd166", width=7)
    draw.polygon([(620, 290), (604, 277), (606, 300)], fill="#ffd166")
    lines = wrap(draw, caption, font(21), WIDTH - 80)
    draw.rounded_rectangle((30, 555, 1250, 690), 16, fill="#0b1716", outline="#29413c", width=2)
    for index, line in enumerate(lines[:4]):
        draw.text((55, 578 + index * 28), line, font=font(21), fill="#b8ddd2")
    return image


def encode_video(slides: list[Image.Image], output: Path, seconds: float = 4.0) -> None:
    with tempfile.TemporaryDirectory(prefix="cora-video-frames-") as folder:
        frame_dir = Path(folder)
        index = 0
        hold = int(seconds * FPS)
        fade = 6
        for slide_index, slide in enumerate(slides):
            for _ in range(hold):
                slide.save(frame_dir / f"frame-{index:05d}.png")
                index += 1
            if slide_index + 1 < len(slides):
                following = slides[slide_index + 1]
                for blend_index in range(1, fade + 1):
                    Image.blend(slide, following, blend_index / (fade + 1)).save(
                        frame_dir / f"frame-{index:05d}.png"
                    )
                    index += 1
        subprocess.run(
            [
                "gst-launch-1.0", "-q",
                "multifilesrc", f"location={frame_dir / 'frame-%05d.png'}",
                "caps=image/png,framerate=10/1", "!", "pngdec", "!",
                "videoconvert", "!", "vp8enc", "deadline=1", "cpu-used=4",
                "target-bitrate=1800000", "!", "webmmux", "!",
                "filesink", f"location={output}",
            ],
            check=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://192.168.10.2")
    parser.add_argument("--output-dir", type=Path, default=Path("docs/demo-videos"))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    pages = [
        (8085, "Forward sonar", "Set the array, frequency, pitch, beam count, scene, and refresh controls. Pan or zoom the square fan view to inspect targets."),
        (8082, "Acoustic OFDM text", "Enter text, select the FPGA AXIS loopback backend, choose modem and band settings, then Send. Progress, decoded text, and the receive waterfall update below."),
        (8084, "Conventional modulation", "Choose BPSK, QPSK, FSK, or MFSK. Select FPGA AXIS loopback to move the generated waveform through the 250 ksample/s PL path before decoding."),
        (8086, "JANUS interoperability", "Choose a JANUS parameter set and application fields. FPGA AXIS loopback validates the hardware stream path; USB audio remains available for later over-the-air trials."),
    ]
    dashboard = [title_card("Cora dashboard walkthrough", "A captioned tour of the live sonar and acoustic communication pages, including the new hardware AXI-stream loopback option.")]
    for step, (port, title, caption) in enumerate(pages, 1):
        target = args.output_dir / f"capture-{port}.png"
        dashboard.append(
            dashboard_slide(
                screenshot(f"{args.base_url}:{port}/", target),
                title,
                caption,
                step,
            )
        )
    dashboard.append(title_card("Choose the backend that matches the test", "Simulation exercises modem algorithms. FPGA AXIS loopback proves memory, DMA, AXIS, TLAST, and 250 ksample/s pacing. USB audio tests the physical acoustic path."))
    encode_video(dashboard, args.output_dir / "cora-dashboard-walkthrough.webm")

    block = [title_card("Acoustic AXI block-design walkthrough", "The generated Vivado design keeps a known-good loopback beside a clearly labeled accelerator slot, making custom IP insertion straightforward.")]
    block.extend([
        block_slide(1, "Host buffers and DMA", "Linux writes 512 signed 16-bit real samples. The dedicated MM2S channel packs one sample per 32-bit AXIS beat; the S2MM channel returns the processed frame.", {"ddr_tx", "mm2s", "s2mm", "ddr_rx"}),
        block_slide(2, "Known-good loopback", "Path 0 returns samples inside axis_acomms_router_0. The output register preserves AXI backpressure and TLAST while the divider enforces 250,000 samples/s.", {"router"}),
        block_slide(3, "Installed accelerator path", "Path 1 routes M_AXIS_ACCELERATOR through FFT, spectral mask, and IFFT, then returns on S_AXIS_ACCELERATOR. Software chooses the path at runtime.", {"router", "fft", "filter", "ifft"}),
        block_slide(4, "Attach your own cores", "Disconnect the shipped accelerator nets, connect your 32-bit AXIS chain between the two labeled router ports, preserve TVALID/TREADY/TLAST, and share the 100 MHz clock/reset or add clock converters.", {"router", "filter"}),
        block_slide(5, "Verify before algorithm testing", "Run cora-axis-loopback-test first. A byte-exact paced pass separates DMA/device-tree problems from custom accelerator logic before you switch to path 1.", {"ddr_tx", "mm2s", "router", "s2mm", "ddr_rx"}),
    ])
    encode_video(block, args.output_dir / "axis-acomms-block-design-walkthrough.webm")

    print(args.output_dir / "cora-dashboard-walkthrough.webm")
    print(args.output_dir / "axis-acomms-block-design-walkthrough.webm")


if __name__ == "__main__":
    main()
