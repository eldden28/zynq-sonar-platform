#!/usr/bin/env python3
"""Web terminal for progressive Cora acoustic OFDM text transfers."""

from __future__ import annotations

import argparse
import http.server
import json
from pathlib import Path
import threading
from typing import Sequence
from urllib.parse import parse_qs, urlparse

from cora_ofdm_text_transfer import (
    AlsaAirBackend,
    ColoredRoomBackend,
    DEFAULT_BURST_PACKETS,
    DEFAULT_CHUNK_BYTES,
    MAX_TEXT_BYTES,
    TransferStopped,
    transfer_text,
)


DEFAULT_WEB_ROOT = Path("/usr/share/cora-ofdm-text-demo/www")
MAX_REQUEST_BYTES = MAX_TEXT_BYTES * 8 + 4096
MAX_DATA_RESPONSE = 4096


def idle_state(air_enabled: bool, port: int) -> dict:
    return {
        "application": "cora-ofdm-text-dashboard",
        "status": "idle",
        "backend": None,
        "air_enabled": air_enabled,
        "burst_packets": 0,
        "dashboard_port": port,
        "max_text_bytes": MAX_TEXT_BYTES,
        "total_bytes": 0,
        "bytes_received": 0,
        "packets_total": 0,
        "packets_received": 0,
        "chunk_bytes": 0,
        "retries": 0,
        "packet_errors": 0,
        "elapsed_s": 0.0,
        "payload_throughput_bps": 0.0,
        "eta_s": None,
        "progress_percent": 0.0,
        "last_sync_metric": None,
        "source_sha256": None,
        "output_sha256": None,
        "error": None,
    }


class TextDemoController:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.received = bytearray()
        self.state = idle_state(args.allow_air, args.port)
        self.state["burst_packets"] = args.burst_packets

    def status(self) -> dict:
        with self.lock:
            values = self.state.copy()
            values["process_running"] = (
                self.worker is not None and self.worker.is_alive()
            )
            return values

    def data(self, offset: int) -> tuple[bytes, int]:
        with self.lock:
            committed = len(self.received)
            if offset < 0 or offset > committed:
                raise ValueError("offset is outside received data")
            return (
                bytes(self.received[offset : offset + MAX_DATA_RESPONSE]),
                committed,
            )

    def start(self, text: str, backend_name: str) -> None:
        encoded_size = len(text.encode("utf-8"))
        if not text:
            raise ValueError("enter some text to transmit")
        if encoded_size > MAX_TEXT_BYTES:
            raise ValueError(
                f"text is {encoded_size} UTF-8 bytes; maximum is "
                f"{MAX_TEXT_BYTES}"
            )
        if backend_name not in ("loopback", "air"):
            raise ValueError("backend must be loopback or air")
        if backend_name == "air" and not self.args.allow_air:
            raise ValueError(
                "air mode is disabled until a speaker is connected and "
                "--allow-air is configured"
            )

        with self.lock:
            if self.worker is not None and self.worker.is_alive():
                raise RuntimeError("a text transfer is already running")
            self.received.clear()
            self.stop_event.clear()
            self.state = idle_state(self.args.allow_air, self.args.port)
            self.state.update(
                {
                    "status": "warming",
                    "backend": backend_name,
                    "burst_packets": self.args.burst_packets,
                    "total_bytes": encoded_size,
                }
            )
            self.worker = threading.Thread(
                target=self._run,
                args=(text, backend_name),
                daemon=True,
                name="cora-ofdm-text-transfer",
            )
            self.worker.start()

    def _run(self, text: str, backend_name: str) -> None:
        if backend_name == "air":
            backend = AlsaAirBackend(
                playback_device=self.args.playback_device,
                capture_device=self.args.capture_device,
                burst_packets=self.args.burst_packets,
            )
        else:
            backend = ColoredRoomBackend(self.args.noise_dbfs)

        def update(values: dict, chunk: bytes) -> None:
            with self.lock:
                self.received.extend(chunk)
                values["air_enabled"] = self.args.allow_air
                values["burst_packets"] = self.args.burst_packets
                values["dashboard_port"] = self.args.port
                self.state = values

        try:
            output, values = transfer_text(
                text,
                backend=backend,
                stop_event=self.stop_event,
                packet_callback=update,
                chunk_bytes=self.args.chunk_bytes,
                max_retries=self.args.max_retries,
            )
            with self.lock:
                if bytes(self.received) != output:
                    raise RuntimeError(
                        "dashboard output does not match transfer output"
                    )
                values["air_enabled"] = self.args.allow_air
                values["burst_packets"] = self.args.burst_packets
                values["dashboard_port"] = self.args.port
                self.state = values
        except BaseException as error:
            with self.lock:
                self.state.update(
                    {
                        "status": (
                            "stopped"
                            if isinstance(error, TransferStopped)
                            else "failed"
                        ),
                        "error": str(error),
                    }
                )

    def stop(self) -> None:
        self.stop_event.set()

    def reset(self) -> None:
        self.stop()
        worker = self.worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=3)
        with self.lock:
            if self.worker is not None and self.worker.is_alive():
                raise RuntimeError("transfer is still stopping")
            self.worker = None
            self.received.clear()
            self.state = idle_state(self.args.allow_air, self.args.port)
            self.state["burst_packets"] = self.args.burst_packets


class DashboardHandler(http.server.BaseHTTPRequestHandler):
    server_version = "CoraOfdmTextDashboard/1.0"

    @property
    def controller(self) -> TextDemoController:
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
        self.send_header("Referrer-Policy", "no-referrer")
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
        if parsed.path == "/api/status":
            self.send_json(200, self.controller.status())
            return
        if parsed.path == "/api/data":
            try:
                offset = int(parse_qs(parsed.query).get("offset", ["0"])[0])
                payload, committed = self.controller.data(offset)
            except ValueError as error:
                self.send_json(416, {"error": str(error)})
                return
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
            return
        self.send_error(404)

    def do_POST(self) -> None:
        if self.headers.get("X-Cora-Request") != "1":
            self.send_json(403, {"error": "missing same-origin request header"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length < 0 or length > MAX_REQUEST_BYTES:
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
                self.controller.start(
                    str(request.get("text", "")),
                    str(request.get("backend", "loopback")),
                )
                message = "text transfer started"
            elif parsed.path == "/api/stop":
                self.controller.stop()
                message = "stop requested"
            elif parsed.path == "/api/reset":
                self.controller.reset()
                message = "terminal reset"
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

    def __init__(
        self, address, handler, controller: TextDemoController
    ) -> None:
        super().__init__(address, handler)
        self.controller = controller


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8082)
    parser.add_argument("--web-root", type=Path, default=DEFAULT_WEB_ROOT)
    parser.add_argument("--chunk-bytes", type=int, default=DEFAULT_CHUNK_BYTES)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--noise-dbfs", type=float, default=-48.0)
    parser.add_argument("--playback-device", default="plughw:0,0")
    parser.add_argument("--capture-device", default="plughw:0,0")
    parser.add_argument(
        "--burst-packets",
        type=int,
        default=DEFAULT_BURST_PACKETS,
    )
    parser.add_argument("--allow-air", action="store_true")
    args = parser.parse_args(argv)
    if not (1 <= args.port <= 65535):
        parser.error("port must be between 1 and 65535")
    if not (1 <= args.chunk_bytes <= 1024):
        parser.error("chunk bytes must be between 1 and 1024")
    if args.max_retries < 0:
        parser.error("max retries cannot be negative")
    if not (1 <= args.burst_packets <= 64):
        parser.error("burst packets must be between 1 and 64")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    controller = TextDemoController(args)
    server = DashboardServer(
        (args.bind, args.port), DashboardHandler, controller
    )
    print(
        f"Cora acoustic text dashboard listening on "
        f"http://{args.bind}:{args.port}",
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
