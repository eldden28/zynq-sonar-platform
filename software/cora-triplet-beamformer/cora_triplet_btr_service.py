#!/usr/bin/env python3
"""HTTP service for the Cora triplet bearing-time record."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import urlparse

from cora_triplet_adc import AxiDmaAdcSource, AxiStreamLayout
from cora_triplet_btr import BtrConfig, BtrEngine


CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}
MAX_REQUEST_BYTES = 16_384


def resolve_web_asset(root: Path, request_path: str) -> Path | None:
    relative = request_path.lstrip("/") or "index.html"
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


class BtrServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], engine: BtrEngine, web_root: Path):
        super().__init__(address, BtrHandler)
        self.engine = engine
        self.web_root = web_root.resolve()


class BtrHandler(BaseHTTPRequestHandler):
    server: BtrServer

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send(self, status: int, payload: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, value: dict) -> None:
        self._send(
            status,
            (json.dumps(value, separators=(",", ":")) + "\n").encode("utf-8"),
            "application/json",
        )

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/status":
            self._json(200, self.server.engine.status())
            return
        if parsed.path.startswith("/api/"):
            self.send_error(404)
            return
        asset = resolve_web_asset(self.server.web_root, parsed.path)
        if asset is None:
            self.send_error(404)
            return
        self._send(200, asset.read_bytes(), CONTENT_TYPES.get(asset.suffix, "application/octet-stream"))

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/control":
            self.send_error(404)
            return
        if self.headers.get("X-Cora-Request") != "1":
            self._json(403, {"error": "missing same-origin request header"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > MAX_REQUEST_BYTES:
                raise ValueError("control request is empty or too large")
            request = json.loads(self.rfile.read(length))
            action = str(request.get("action", "")).lower()
            if action == "configure":
                values = request.get("values")
                if not isinstance(values, dict):
                    raise ValueError("configure values must be an object")
                self.server.engine.configure(values)
                message = "BTR configuration updated"
            elif action == "pause":
                self.server.engine.set_paused(True)
                message = "BTR processing paused"
            elif action == "resume":
                self.server.engine.start()
                message = "BTR processing resumed"
            elif action == "reset":
                self.server.engine.reset()
                message = "BTR history reset"
            else:
                raise ValueError("action must be configure, pause, resume, or reset")
        except (AttributeError, json.JSONDecodeError, TypeError, ValueError) as error:
            self._json(400, {"error": str(error)})
            return
        self._json(200, {"message": message, "status": self.server.engine.status()})


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--bind", default="0.0.0.0")
    result.add_argument("--port", type=int, default=8088)
    result.add_argument("--web-root", type=Path, default=Path(__file__).with_name("www"))
    result.add_argument("--carrier-hz", type=float, default=8_000.0)
    result.add_argument(
        "--source",
        choices=("synthetic", "axi"),
        default="synthetic",
        help="ADC input source (default: synthetic)",
    )
    result.add_argument(
        "--axi-device",
        type=Path,
        default=Path("/dev/cora-triplet-adc0"),
        help="character device or raw replay file for --source axi",
    )
    result.add_argument(
        "--axi-frame-samples",
        type=int,
        default=512,
        help="samples per 21-channel AXI packet (default: 512)",
    )
    result.add_argument(
        "--axi-read-timeout",
        type=float,
        default=1.0,
        help="seconds to wait for each AXI packet (default: 1.0)",
    )
    result.add_argument(
        "--axi-loop-file",
        action="store_true",
        help="rewind a regular AXI replay file at EOF",
    )
    result.add_argument(
        "--axi-no-verify-sign-extension",
        action="store_true",
        help="accept values outside the signed 24-bit range",
    )
    return result


def main() -> int:
    args = parser().parse_args()
    config = BtrConfig(carrier_hz=args.carrier_hz)
    config.validate()
    adc_source = None
    if args.source == "axi":
        adc_source = AxiDmaAdcSource(
            args.axi_device,
            block_size=config.block_size,
            layout=AxiStreamLayout(frame_samples=args.axi_frame_samples),
            read_timeout_s=args.axi_read_timeout,
            loop_file=args.axi_loop_file,
            verify_sign_extension=not args.axi_no_verify_sign_extension,
        )
    engine = BtrEngine(config, adc_source=adc_source)
    server = BtrServer((args.bind, args.port), engine, args.web_root)
    engine.start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
