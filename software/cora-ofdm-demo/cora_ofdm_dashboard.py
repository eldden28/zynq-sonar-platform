#!/usr/bin/env python3
"""Progressive web dashboard for the Cora OFDM image-transfer demo."""

from __future__ import annotations

import argparse
import http.server
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
from typing import Sequence
from urllib.parse import parse_qs, urlparse


DEFAULT_WEB_ROOT = Path("/usr/share/cora-ofdm-demo/www")
DEFAULT_SOURCE = Path("/usr/share/cora-ofdm-demo/rickroll.rgb")
DEFAULT_PREVIEW = Path("/usr/share/cora-ofdm-demo/rickroll.png")
DEFAULT_OUTPUT = Path("/run/cora-ofdm-demo/received.rgb")
DEFAULT_STATE = Path("/run/cora-ofdm-demo/status.json")
DEFAULT_LOG = Path("/var/log/cora-ofdm-demo.log")
MAX_REQUEST = 4096
MAX_DATA_RESPONSE = 196608


def read_json(path: Path, default: dict) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default.copy()


class DemoController:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.lock = threading.Lock()
        self.process: subprocess.Popen | None = None
        self.log_file = None

    def status(self) -> dict:
        values = read_json(
            self.args.state,
            {
                "application": "cora-ofdm-transfer",
                "status": "idle",
                "width": self.args.width,
                "height": self.args.height,
                "total_bytes": self.args.width * self.args.height * 3,
                "bytes_received": 0,
                "frames_total": 0,
                "frames_received": 0,
                "progress_percent": 0.0,
                "payload_throughput_bps": 0.0,
                "eta_s": None,
                "retries": 0,
                "bit_errors": 0,
                "error": None,
            },
        )
        with self.lock:
            process = self.process
            if process is not None:
                return_code = process.poll()
                values["process_running"] = return_code is None
                values["process_return_code"] = return_code
                if return_code is not None:
                    self.process = None
                    if self.log_file:
                        self.log_file.close()
                        self.log_file = None
            else:
                values["process_running"] = False
                values["process_return_code"] = None
        values["dashboard_port"] = self.args.port
        return values

    def start(self, mode: str) -> None:
        if mode not in ("benchmark", "realtime"):
            raise ValueError("mode must be benchmark or realtime")
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                raise RuntimeError("an OFDM transfer is already running")
            self.args.output.parent.mkdir(parents=True, exist_ok=True)
            self.args.state.parent.mkdir(parents=True, exist_ok=True)
            self.args.log.parent.mkdir(parents=True, exist_ok=True)
            self.args.output.unlink(missing_ok=True)
            self.args.state.unlink(missing_ok=True)
            self.log_file = self.args.log.open("ab", buffering=0)
            command = [
                self.args.transfer_command,
                "--input",
                str(self.args.source),
                "--output",
                str(self.args.output),
                "--state",
                str(self.args.state),
                "--width",
                str(self.args.width),
                "--height",
                str(self.args.height),
                "--mode",
                mode,
                "--batch-size",
                str(self.args.batch_size),
                "--snr-db",
                str(self.args.snr_db),
            ]
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=self.log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

    def stop(self) -> None:
        with self.lock:
            if self.process is None or self.process.poll() is not None:
                return
            os.killpg(self.process.pid, signal.SIGINT)

    def reset(self) -> None:
        self.stop()
        with self.lock:
            if self.process is not None:
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(self.process.pid, signal.SIGTERM)
                    self.process.wait(timeout=5)
                self.process = None
            if self.log_file:
                self.log_file.close()
                self.log_file = None
        self.args.output.unlink(missing_ok=True)
        self.args.state.unlink(missing_ok=True)


class DashboardHandler(http.server.BaseHTTPRequestHandler):
    server_version = "CoraOfdmDashboard/1.0"

    @property
    def controller(self) -> DemoController:
        return self.server.controller

    def send_bytes(
        self,
        status: int,
        payload: bytes,
        content_type: str,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if extra_headers:
            for name, value in extra_headers.items():
                self.send_header(name, value)
        self.end_headers()
        self.wfile.write(payload)

    def send_json(self, status: int, values: dict) -> None:
        self.send_bytes(
            status,
            (json.dumps(values) + "\n").encode("utf-8"),
            "application/json",
        )

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            try:
                payload = (
                    self.controller.args.web_root / "index.html"
                ).read_bytes()
            except OSError:
                self.send_error(404)
                return
            self.send_bytes(200, payload, "text/html; charset=utf-8")
            return
        if parsed.path == "/source.png":
            try:
                payload = self.controller.args.preview.read_bytes()
            except OSError:
                self.send_error(404)
                return
            self.send_bytes(200, payload, "image/png")
            return
        if parsed.path == "/api/status":
            self.send_json(200, self.controller.status())
            return
        if parsed.path == "/api/data":
            self.send_data(parse_qs(parsed.query))
            return
        self.send_error(404)

    def send_data(self, query: dict[str, list[str]]) -> None:
        try:
            offset = int(query.get("offset", ["0"])[0])
        except ValueError:
            self.send_json(400, {"error": "offset must be an integer"})
            return
        status = self.controller.status()
        committed = int(status.get("bytes_received", 0))
        if offset < 0 or offset > committed:
            self.send_json(416, {"error": "offset is outside received data"})
            return
        count = min(committed - offset, MAX_DATA_RESPONSE)
        try:
            with self.controller.args.output.open("rb") as received:
                received.seek(offset)
                payload = received.read(count)
        except OSError:
            payload = b""
        self.send_bytes(
            200,
            payload,
            "application/octet-stream",
            {
                "X-Cora-Offset": str(offset),
                "X-Cora-Next-Offset": str(offset + len(payload)),
                "X-Cora-Committed-Bytes": str(committed),
            },
        )

    def do_POST(self) -> None:
        if self.headers.get("X-Cora-Request") != "1":
            self.send_json(403, {"error": "missing same-origin request header"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length < 0 or length > MAX_REQUEST:
            self.send_json(413, {"error": "request is too large"})
            return
        try:
            request = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, UnicodeError):
            self.send_json(400, {"error": "request body is not valid JSON"})
            return
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/start":
                self.controller.start(str(request.get("mode", "benchmark")))
                message = "OFDM transfer started"
            elif parsed.path == "/api/stop":
                self.controller.stop()
                message = "stop requested"
            elif parsed.path == "/api/reset":
                self.controller.reset()
                message = "demo reset"
            else:
                self.send_error(404)
                return
        except (OSError, RuntimeError, ValueError) as error:
            self.send_json(409, {"error": str(error)})
            return
        self.send_json(200, {"message": message})

    def log_message(self, format_string: str, *args) -> None:
        print(
            f"{self.address_string()} - {format_string % args}",
            flush=True,
        )


class DashboardServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, controller: DemoController):
        super().__init__(address, handler)
        self.controller = controller


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--web-root", type=Path, default=DEFAULT_WEB_ROOT)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--preview", type=Path, default=DEFAULT_PREVIEW)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--width", type=int, default=1024)
    parser.add_argument("--height", type=int, default=1024)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--snr-db", type=float, default=40.0)
    parser.add_argument(
        "--transfer-command", default="/usr/bin/cora-ofdm-transfer"
    )
    args = parser.parse_args(argv)
    if not (1 <= args.port <= 65535):
        parser.error("port must be between 1 and 65535")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    controller = DemoController(args)
    server = DashboardServer(
        (args.bind, args.port), DashboardHandler, controller
    )
    print(
        f"Cora OFDM dashboard listening on http://{args.bind}:{args.port}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        controller.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
