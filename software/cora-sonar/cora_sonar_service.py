#!/usr/bin/env python3
"""HTTP dashboard service for the Cora forward-sonar simulator."""

from __future__ import annotations

import argparse
import http.server
import json
from pathlib import Path
import signal
import threading
import time
from typing import Sequence
from urllib.parse import unquote, urlparse

from cora_sonar import SCENARIO_NAMES, SonarConfig, SonarEngine


MAX_REQUEST_BYTES = 64 * 1024
CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
}


def resolve_web_asset(web_root: Path, request_path: str) -> Path | None:
    decoded = unquote(request_path)
    if "\0" in decoded or decoded.endswith("/"):
        return None
    root = web_root.resolve()
    candidate = (root / decoded.lstrip("/")).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if not candidate.is_file() or candidate.suffix not in CONTENT_TYPES:
        return None
    return candidate


class SonarDashboardServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], engine: SonarEngine, web_root: Path):
        super().__init__(address, SonarHandler)
        self.engine = engine
        self.web_root = Path(web_root)
        self.started_monotonic = time.monotonic()


class SonarHandler(http.server.BaseHTTPRequestHandler):
    server_version = "CoraSonarDashboard/1.0"

    def log_message(self, format_string: str, *args: object) -> None:
        print("sonar dashboard: " + format_string % args, flush=True)

    def _send(
        self,
        status: int,
        payload: bytes,
        content_type: str,
        *,
        disposition: str | None = None,
        cache_control: str = "no-store",
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        if disposition:
            self.send_header("Content-Disposition", disposition)
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, values: dict) -> None:
        self._send(
            status,
            (json.dumps(values, separators=(",", ":")) + "\n").encode("utf-8"),
            "application/json",
        )

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            asset = self.server.web_root / "index.html"
        elif parsed.path == "/api/status":
            values = self.server.engine.status()
            values["service"] = {
                "port": self.server.server_port,
                "uptime_s": time.monotonic() - self.server.started_monotonic,
            }
            self._json(200, values)
            return
        elif parsed.path == "/api/capture":
            try:
                payload = self.server.engine.capture_npz()
            except RuntimeError as error:
                self._json(404, {"error": str(error)})
                return
            self._send(
                200,
                payload,
                "application/octet-stream",
                disposition='attachment; filename="cora-sonar-ping.npz"',
            )
            return
        elif parsed.path == "/api/scenarios":
            self._json(200, {"scenarios": list(SCENARIO_NAMES)})
            return
        elif parsed.path.startswith("/api/"):
            self.send_error(404)
            return
        else:
            asset = resolve_web_asset(self.server.web_root, parsed.path)
            if asset is None:
                self.send_error(404)
                return
        try:
            payload = asset.read_bytes()
        except OSError:
            self.send_error(404)
            return
        self._send(
            200,
            payload,
            CONTENT_TYPES.get(asset.suffix, "application/octet-stream"),
            cache_control="public, max-age=3600" if asset.name != "index.html" else "no-store",
        )

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/control":
            self.send_error(404)
            return
        if self.headers.get("X-Cora-Request") != "1":
            self._json(403, {"error": "missing same-origin request header"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json(400, {"error": "invalid Content-Length"})
            return
        if length < 1 or length > MAX_REQUEST_BYTES:
            self._json(413, {"error": "control request is empty or too large"})
            return
        try:
            request = json.loads(self.rfile.read(length))
            if not isinstance(request, dict):
                raise ValueError("request must be a JSON object")
            action = str(request.get("action", "")).lower()
            if action == "start":
                self.server.engine.start()
                message = "sonar ping processing started"
            elif action == "pause":
                self.server.engine.set_paused(True)
                message = "sonar ping processing paused"
            elif action == "resume":
                self.server.engine.start()
                message = "sonar ping processing resumed"
            elif action == "stop":
                self.server.engine.stop()
                message = "sonar ping processing stopped"
            elif action == "reset":
                self.server.engine.reset()
                message = "sonar simulation reset"
            elif action == "scenario":
                self.server.engine.set_scenario(str(request.get("name", "")))
                message = "sonar scene updated"
            elif action == "scene":
                self.server.engine.set_targets(request.get("targets"))
                message = "edited sonar scene applied"
            elif action == "configure":
                values = request.get("values")
                if not isinstance(values, dict):
                    raise ValueError("configure values must be an object")
                self.server.engine.configure(values)
                message = "sonar configuration updated"
            elif action == "ping":
                self.server.engine.process_once()
                message = "one sonar ping processed"
            else:
                raise ValueError(
                    "action must be start, pause, resume, stop, reset, scenario, scene, "
                    "configure, or ping"
                )
        except (TypeError, ValueError) as error:
            self._json(400, {"error": str(error)})
            return
        except RuntimeError as error:
            self._json(409, {"error": str(error)})
            return
        self._json(200, {"message": message, "status": self.server.engine.status()})


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Run the Cora forward-sonar dashboard")
    result.add_argument("--bind", default="0.0.0.0")
    result.add_argument("--port", type=int, default=8085)
    result.add_argument(
        "--web-root", type=Path, default=Path("/usr/share/cora-sonar/www")
    )
    result.add_argument("--max-range", type=float, default=12.0)
    result.add_argument("--ping-rate", type=float, default=1.0)
    result.add_argument("--beam-count", type=int, default=512)
    result.add_argument("--beamforming-range-bins", type=int, default=1024)
    result.add_argument("--no-autostart", action="store_true")
    return result


def main(arguments: Sequence[str] | None = None) -> None:
    args = parser().parse_args(arguments)
    config = SonarConfig(
        max_range_m=args.max_range,
        ping_rate_hz=args.ping_rate,
        beam_count=args.beam_count,
        beamforming_range_bins=args.beamforming_range_bins,
    )
    engine = SonarEngine(config)
    engine.reset()
    if not args.no_autostart:
        engine.start()
    server = SonarDashboardServer((args.bind, args.port), engine, args.web_root)
    shutdown_started = threading.Event()

    def shutdown(_signum: int, _frame: object) -> None:
        if shutdown_started.is_set():
            return
        shutdown_started.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        engine.stop()
        server.server_close()


if __name__ == "__main__":
    main()
