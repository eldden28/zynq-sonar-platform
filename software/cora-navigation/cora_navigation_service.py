#!/usr/bin/env python3
"""Dual web dashboard service for the Cora navigation laboratory."""

from __future__ import annotations

import argparse
import http.server
import json
import os
from pathlib import Path
import signal
import threading
import time
from typing import Sequence
from urllib.parse import parse_qs, unquote, urlparse

from cora_navigation import NavigationConfig, NavigationEngine

try:
    import resource
except ImportError:
    resource = None


MAX_REQUEST_BYTES = 64 * 1024
STATIC_CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
}


def resolve_web_asset(web_root: Path, request_path: str) -> Path | None:
    """Resolve a dashboard asset without permitting path traversal."""
    decoded = unquote(request_path)
    if "\0" in decoded or decoded.endswith("/"):
        return None
    root = web_root.resolve()
    candidate = (root / decoded.lstrip("/")).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if not candidate.is_file() or candidate.suffix not in STATIC_CONTENT_TYPES:
        return None
    return candidate


def process_usage() -> dict[str, float | int]:
    """Return metrics even on small images without python3-resource."""
    times = os.times()
    maximum_rss_kib = 0
    try:
        for line in Path("/proc/self/status").read_text(
            encoding="ascii"
        ).splitlines():
            if line.startswith("VmHWM:"):
                maximum_rss_kib = int(line.split()[1])
                break
    except (OSError, ValueError, IndexError):
        pass
    if resource is not None:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        maximum_rss_kib = int(usage.ru_maxrss)
        user_cpu_s = float(usage.ru_utime)
        system_cpu_s = float(usage.ru_stime)
    else:
        user_cpu_s = float(times.user)
        system_cpu_s = float(times.system)
    return {
        "maximum_rss_kib": maximum_rss_kib,
        "user_cpu_s": user_cpu_s,
        "system_cpu_s": system_cpu_s,
    }


class NavigationDashboardServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        engine: NavigationEngine,
        web_root: Path,
        view: str,
    ):
        super().__init__(address, NavigationHandler)
        self.engine = engine
        self.web_root = Path(web_root)
        self.view = view
        self.started_monotonic = time.monotonic()


class NavigationHandler(http.server.BaseHTTPRequestHandler):
    server_version = "CoraNavigationDashboard/1.0"

    def log_message(self, format_string: str, *args: object) -> None:
        print(
            f"{self.server.view} dashboard: "
            + format_string % args,
            flush=True,
        )

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
        self.send_header("Access-Control-Allow-Origin", "same-origin")
        if disposition:
            self.send_header("Content-Disposition", disposition)
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, values: dict) -> None:
        self._send(
            status,
            (json.dumps(values, separators=(",", ":")) + "\n").encode(),
            "application/json",
        )

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            try:
                payload = (self.server.web_root / "index.html").read_bytes()
            except OSError:
                self.send_error(404)
                return
            self._send(200, payload, "text/html; charset=utf-8")
            return
        if not parsed.path.startswith("/api/"):
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
                STATIC_CONTENT_TYPES[asset.suffix],
                cache_control="public, max-age=86400",
            )
            return
        if parsed.path == "/api/status":
            status = self.server.engine.status()
            status["service"] = {
                "view": self.server.view,
                "port": self.server.server_port,
                "uptime_s": time.monotonic()
                - self.server.started_monotonic,
                **process_usage(),
            }
            self._json(200, status)
            return
        if parsed.path == "/api/history":
            try:
                cursor = int(parse_qs(parsed.query).get("after", ["-1"])[0])
            except ValueError:
                self._json(400, {"error": "history cursor must be an integer"})
                return
            self._json(200, self.server.engine.history_after(cursor))
            return
        if parsed.path in ("/api/log/telemetry", "/api/log/raw"):
            name = (
                "telemetry.jsonl"
                if parsed.path.endswith("telemetry")
                else "raw-frames.csf"
            )
            path = self.server.engine.run_directory / name
            try:
                payload = path.read_bytes()
            except OSError:
                self._json(404, {"error": f"{name} is not available"})
                return
            self._send(
                200,
                payload,
                (
                    "application/x-ndjson"
                    if name.endswith(".jsonl")
                    else "application/octet-stream"
                ),
                disposition=f'attachment; filename="{name}"',
            )
            return
        self.send_error(404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/control":
            self.send_error(404)
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
                message = "navigation simulation started"
            elif action == "pause":
                self.server.engine.set_paused(True)
                message = "navigation simulation paused"
            elif action == "resume":
                self.server.engine.set_paused(False)
                self.server.engine.start()
                message = "navigation simulation resumed"
            elif action == "stop":
                self.server.engine.stop()
                message = "navigation simulation stopped"
            elif action == "reset":
                was_running = self.server.engine.running
                self.server.engine.stop()
                self.server.engine.reset()
                if was_running:
                    self.server.engine.start()
                message = "navigation simulation reset"
            elif action == "configure":
                values = request.get("values", {})
                if not isinstance(values, dict):
                    raise ValueError("configure values must be an object")
                self.server.engine.configure(values)
                message = "navigation configuration updated"
            else:
                raise ValueError(
                    "action must be start, pause, resume, stop, reset, or configure"
                )
        except (ValueError, TypeError) as error:
            self._json(400, {"error": str(error)})
            return
        except RuntimeError as error:
            self._json(409, {"error": str(error)})
            return
        self._json(
            200,
            {
                "message": message,
                "status": self.server.engine.status(),
            },
        )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Run the Cora DVL and LBL/iUSBL navigation dashboards"
    )
    result.add_argument("--bind", default="0.0.0.0")
    result.add_argument("--dvl-port", type=int, default=8083)
    result.add_argument("--acoustic-port", type=int, default=8084)
    result.add_argument(
        "--dvl-web-root",
        type=Path,
        default=Path("/usr/share/cora-navigation/dvl"),
    )
    result.add_argument(
        "--acoustic-web-root",
        type=Path,
        default=Path("/usr/share/cora-navigation/acoustic"),
    )
    result.add_argument(
        "--run-directory",
        type=Path,
        default=Path("/run/cora-navigation"),
    )
    result.add_argument("--raw-recording", action="store_true")
    result.add_argument("--no-autostart", action="store_true")
    return result


def main(arguments: Sequence[str] | None = None) -> None:
    args = parser().parse_args(arguments)
    config = NavigationConfig(raw_recording=args.raw_recording)
    engine = NavigationEngine(config, run_directory=args.run_directory)
    engine.reset()
    if not args.no_autostart:
        engine.start()
    servers = [
        NavigationDashboardServer(
            (args.bind, args.dvl_port),
            engine,
            args.dvl_web_root,
            "dvl",
        ),
        NavigationDashboardServer(
            (args.bind, args.acoustic_port),
            engine,
            args.acoustic_web_root,
            "acoustic",
        ),
    ]
    threads = [
        threading.Thread(
            target=server.serve_forever,
            name=f"cora-{server.view}-dashboard",
            daemon=True,
        )
        for server in servers
    ]
    for thread in threads:
        thread.start()

    stopping = threading.Event()

    def stop(_signal: int, _frame: object) -> None:
        stopping.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    print(
        "Cora navigation dashboards listening on "
        f"http://{args.bind}:{args.dvl_port} and "
        f"http://{args.bind}:{args.acoustic_port}",
        flush=True,
    )
    while not stopping.wait(0.5):
        pass
    for server in servers:
        server.shutdown()
        server.server_close()
    engine.stop()


if __name__ == "__main__":
    main()
