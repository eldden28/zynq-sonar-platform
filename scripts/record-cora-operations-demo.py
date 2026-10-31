#!/usr/bin/env python3
"""Record authenticated Cora operations and concurrent-dashboard evidence."""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import math
import re
import subprocess
import textwrap
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from playwright.async_api import async_playwright, expect


WIDTH, HEIGHT, HEADER, APP_HEIGHT, FPS = 1920, 1080, 64, 896, 20


def font(size: int):
    return ImageFont.truetype("DejaVuSans.ttf", size)


def ffmpeg_path(path: str) -> str:
    return path


def dashboard_password() -> str:
    """Read the documented development credential without storing it in output."""
    readme = Path("software/cora-dashboard/README.md").read_text(encoding="utf-8")
    match = re.search(r"password\s+`([^`]+)`", readme)
    if not match:
        raise RuntimeError("documented development dashboard credential not found")
    return match.group(1)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeline", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--preview", type=float, help="Record only the opening seconds for visual review")
    parser.add_argument("--fast", action="store_true", help="Render frames without wall-clock pacing")
    args = parser.parse_args()
    timeline = json.loads(args.timeline.read_text(encoding="utf-8"))
    total = sum(scene["duration_s"] for scene in timeline)
    if args.preview is not None:
        total = min(total, max(0.1, args.preview))
    if args.out.exists():
        raise ValueError("use a fresh output directory")
    args.out.mkdir(parents=True)
    output = args.out / "screen-only.mp4"
    checks: list[dict] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        main_context = await browser.new_context(
            viewport={"width": WIDTH, "height": APP_HEIGHT},
            http_credentials={"username": "cora", "password": dashboard_password()},
        )
        plain_context = await browser.new_context(viewport={"width": WIDTH, "height": APP_HEIGHT})
        main_page = await main_context.new_page()
        image_page = await plain_context.new_page()
        text_page = await plain_context.new_page()
        await main_page.goto("http://192.168.10.2:8080/", wait_until="domcontentloaded")
        await main_page.locator("#cpu").wait_for()
        await image_page.goto("http://192.168.10.2:8081/", wait_until="domcontentloaded")
        await image_page.locator("[data-start='benchmark']").wait_for()
        await text_page.goto("http://192.168.10.2:8082/", wait_until="domcontentloaded")
        await text_page.locator("#send").wait_for()
        await asyncio.sleep(1.1)
        await expect(main_page.locator("#iio-system")).to_contain_text("voltage0_vccint")
        checks.append({"check": "authenticated dashboard", "result": "CPU and XADC visible"})

        encoder = subprocess.Popen(
            [ffmpeg_path(args.ffmpeg), "-y", "-hide_banner", "-loglevel", "warning", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{WIDTH}x{HEIGHT}", "-r", str(FPS), "-i", "-", "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-threads", "3", "-pix_fmt", "yuv420p", str(output)],
            stdin=subprocess.PIPE,
        )
        started = time.perf_counter()
        active = None
        current_app = None
        frame = 0
        try:
            while frame < round(total * FPS):
                elapsed = frame / FPS
                offset = 0.0
                scene = timeline[-1]
                for candidate in timeline:
                    if elapsed < offset + candidate["duration_s"]:
                        scene = candidate
                        break
                    offset += candidate["duration_s"]
                if active != scene["id"]:
                    active = scene["id"]
                    if active == "web-terminal":
                        await main_page.locator("#terminal").scroll_into_view_if_needed()
                        await main_page.locator("#term-open").click()
                        await main_page.wait_for_timeout(700)
                        await main_page.locator("#terminal").click()
                        await main_page.keyboard.type("uname -n; uptime")
                        await main_page.keyboard.press("Enter")
                        await main_page.wait_for_timeout(900)
                        await expect(main_page.locator("#terminal")).to_contain_text("cora-z7-10-baseline")
                        checks.append({"check": "PTY web terminal", "result": "hostname and uptime returned"})
                    elif active == "main-dashboard":
                        await main_page.evaluate("window.scrollTo(0,0)")
                    elif active == "ofdm-image":
                        await image_page.evaluate("window.scrollTo(0,0)")
                    elif active == "simultaneous":
                        await main_page.evaluate("window.scrollTo(0,0)")
                        await text_page.evaluate("window.scrollTo(0,0)")
                        checks.append({"check": "concurrent pages", "result": "8080 and 8082 captured together"})
                    (args.out / f"chapter-{len(checks):02}.png").write_bytes(
                        await (main_page if active in ("main-dashboard", "web-terminal") else image_page).screenshot(type="png")
                    )
                    if active in ("main-dashboard", "web-terminal"):
                        current_app = Image.open(io.BytesIO(await main_page.screenshot(type="png"))).convert("RGB")
                    elif active == "ofdm-image":
                        current_app = Image.open(io.BytesIO(await image_page.screenshot(type="png"))).convert("RGB")
                    else:
                        left = Image.open(io.BytesIO(await main_page.screenshot(type="png"))).convert("RGB")
                        right = Image.open(io.BytesIO(await text_page.screenshot(type="png"))).convert("RGB")
                        current_app = Image.new("RGB", (WIDTH, APP_HEIGHT), "#07121a")
                        current_app.paste(left.resize((960, APP_HEIGHT)), (0, 0))
                        current_app.paste(right.resize((960, APP_HEIGHT)), (960, 0))
                        draw_app = ImageDraw.Draw(current_app)
                        draw_app.rectangle((952, 0, 968, APP_HEIGHT), fill="#36d8ca")
                assert current_app is not None
                app = current_app
                canvas = Image.new("RGB", (WIDTH, HEIGHT), "#0b131c")
                canvas.paste(app, (0, HEADER))
                draw = ImageDraw.Draw(canvas)
                draw.text((28, 8), "Cora operations addendum", fill="#36d8ca", font=font(22))
                draw.text((28, 36), "LIVE CORA BOARD · AUTHENTICATED 8080 SESSION · AI NARRATION", fill="#95a8ba", font=font(16))
                draw.text((WIDTH - 28, 20), scene["title"], anchor="ra", fill="#e5edf4", font=font(22))
                lines = textwrap.wrap(scene["text"], width=135)
                for number, line in enumerate(lines):
                    draw.text((WIDTH // 2, HEADER + APP_HEIGHT + 12 + number * 30), line, anchor="ma", fill="#e5edf4", font=font(24))
                draw.rectangle((0, HEIGHT - 4, WIDTH * elapsed / total, HEIGHT), fill="#36d8ca")
                assert encoder.stdin is not None
                encoder.stdin.write(canvas.tobytes())
                frame += 1
                if not args.fast:
                    await asyncio.sleep(max(0, frame / FPS - (time.perf_counter() - started)))
        finally:
            assert encoder.stdin is not None
            encoder.stdin.close()
            code = encoder.wait(timeout=30)
            (args.out / "recording-checks.json").write_text(json.dumps({"checks": checks, "frames": frame, "duration_s": frame / FPS, "encoder_exit_code": code}, indent=2), encoding="utf-8")
            await main_context.close()
            await plain_context.close()
            await browser.close()
            if code:
                raise RuntimeError("ffmpeg failed")


if __name__ == "__main__":
    asyncio.run(main())
