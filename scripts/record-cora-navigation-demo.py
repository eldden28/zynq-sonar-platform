#!/usr/bin/env python3
"""Run the standard narrated browser recorder with an ephemeral MapTiler key."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import importlib.util
import json
import sys
from pathlib import Path


SKILL = Path("/home/eldden28/.codex/skills/narrated-app-demo")
RECORDER = SKILL / "scripts" / "record_browser.py"


class BrowserProxy:
    def __init__(self, browser, key: str):
        self._browser = browser
        self._key = key

    async def new_context(self, *args, **kwargs):
        context = await self._browser.new_context(*args, **kwargs)
        value = json.dumps(self._key)
        await context.add_init_script(
            f"localStorage.setItem('cora-maptiler-key', {value});"
        )
        return context

    def __getattr__(self, name):
        return getattr(self._browser, name)


class ChromiumProxy:
    def __init__(self, chromium, key: str):
        self._chromium = chromium
        self._key = key

    async def launch(self, *args, **kwargs):
        browser = await self._chromium.launch(*args, **kwargs)
        return BrowserProxy(browser, self._key)

    def __getattr__(self, name):
        return getattr(self._chromium, name)


class PlaywrightProxy:
    def __init__(self, playwright, key: str):
        self._playwright = playwright
        self.chromium = ChromiumProxy(playwright.chromium, key)

    def __getattr__(self, name):
        return getattr(self._playwright, name)


class PlaywrightManagerProxy:
    def __init__(self, factory, key: str):
        self._manager = factory()
        self._key = key

    async def __aenter__(self):
        playwright = await self._manager.__aenter__()
        return PlaywrightProxy(playwright, self._key)

    async def __aexit__(self, *args):
        return await self._manager.__aexit__(*args)


def load_recorder():
    sys.path.insert(0, str(RECORDER.parent))
    spec = importlib.util.spec_from_file_location("narrated_record_browser", RECORDER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load recorder from {RECORDER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--timeline", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--ready")
    parser.add_argument("--title", default="Cora simulated navigation mission")
    parser.add_argument("--data-label", default="LIVE CORA Z7-10 · SYNTHETIC RIVER MISSION")
    parser.add_argument("--fps", type=int, default=20)
    parser.add_argument("--ffmpeg")
    parser.add_argument("--preview", type=float)
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    key = getpass.getpass("MapTiler browser key: ").strip()
    if not key:
        raise SystemExit("A MapTiler browser key is required")
    recorder = load_recorder()
    factory = recorder.async_playwright
    recorder.async_playwright = lambda: PlaywrightManagerProxy(factory, key)
    try:
        asyncio.run(recorder.record(args))
    finally:
        key = ""


if __name__ == "__main__":
    main()
