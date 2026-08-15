#!/usr/bin/env python3
"""Web terminal for progressive Cora acoustic OFDM text transfers."""

from __future__ import annotations

import argparse
from collections import deque
import http.server
import json
from pathlib import Path
import threading
from typing import Sequence
from urllib.parse import parse_qs, urlparse

import numpy as np

from cora_ofdm_acoustic import make_acoustic_config
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
DEFAULT_MODEM_VERSION = "v3-r2/3"
DEFAULT_FFT_LENGTH = 256
UNCODED_CHUNK_BYTES = 192
DEFAULT_LOW_FREQUENCY_HZ = 2000.0
DEFAULT_HIGH_FREQUENCY_HZ = 15000.0
SPECTRUM_FFT_SIZE = 1024
SPECTRUM_HOP_SIZE = 2048
SPECTRUM_HISTORY_ROWS = 180
SPECTRUM_FLOOR_DBFS = -100
SPECTRUM_CEILING_DBFS = 0


def idle_state(
    air_enabled: bool,
    port: int,
    output_channel: str = "left",
) -> dict:
    cfg = make_acoustic_config(
        DEFAULT_MODEM_VERSION,
        DEFAULT_LOW_FREQUENCY_HZ,
        DEFAULT_HIGH_FREQUENCY_HZ,
        fft_len=DEFAULT_FFT_LENGTH,
    )
    return {
        "application": "cora-ofdm-text-dashboard",
        "status": "idle",
        "backend": None,
        "framing": "continuous-superframe",
        "output_channel": output_channel,
        "modem_version": cfg.modem_name,
        "modulation": cfg.modulation,
        "bits_per_carrier": cfg.bits_per_carrier,
        "body_code": cfg.body_code,
        "low_frequency_hz": cfg.low_frequency_hz,
        "high_frequency_hz": cfg.high_frequency_hz,
        "bandwidth_hz": (
            cfg.high_frequency_hz - cfg.low_frequency_hz
        ),
        "sample_rate": cfg.sample_rate,
        "fft_len": cfg.fft_len,
        "cp_len": cfg.cp_len,
        "subcarrier_spacing_hz": cfg.subcarrier_spacing_hz,
        "symbol_duration_ms": 1000.0 * cfg.symbol_duration_s,
        "cyclic_prefix_ms": 1000.0 * cfg.cyclic_prefix_duration_s,
        "data_subcarriers": int(cfg.data_bins.size),
        "gross_bit_rate": cfg.gross_bit_rate,
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


class ReceiverSpectrumMonitor:
    """Convert captured PCM into a bounded sequence of waterfall rows."""

    def __init__(
        self,
        *,
        sample_rate: int = 48_000,
        fft_size: int = SPECTRUM_FFT_SIZE,
        hop_size: int = SPECTRUM_HOP_SIZE,
        history_rows: int = SPECTRUM_HISTORY_ROWS,
    ):
        self.sample_rate = sample_rate
        self.fft_size = fft_size
        self.hop_size = hop_size
        self.lock = threading.Lock()
        self.byte_tail = b""
        self.pending = np.empty(0, dtype=np.float32)
        self.rows: deque[dict] = deque(maxlen=history_rows)
        self.next_id = 0
        self.window = np.hanning(fft_size).astype(np.float32)
        self.amplitude_scale = max(
            float(np.sum(self.window)) / 2.0,
            1.0,
        )

    def reset(self) -> None:
        with self.lock:
            self.byte_tail = b""
            self.pending = np.empty(0, dtype=np.float32)
            self.rows.clear()
            self.next_id = 0

    def ingest_pcm(self, pcm: bytes) -> None:
        if not pcm:
            return
        with self.lock:
            pcm = self.byte_tail + pcm
            even_length = len(pcm) & ~1
            self.byte_tail = pcm[even_length:]
            if not even_length:
                return
            samples = (
                np.frombuffer(pcm[:even_length], dtype="<i2")
                .astype(np.float32)
                / 32768.0
            )
            self.pending = np.concatenate((self.pending, samples))
            while self.pending.size >= self.hop_size:
                frame = self.pending[: self.fft_size]
                self.pending = self.pending[self.hop_size :]
                spectrum = np.fft.rfft(frame * self.window)
                amplitude = np.abs(spectrum) / self.amplitude_scale
                dbfs = 20.0 * np.log10(np.maximum(amplitude, 1e-5))
                dbfs = np.clip(
                    dbfs,
                    SPECTRUM_FLOOR_DBFS,
                    SPECTRUM_CEILING_DBFS,
                )
                peak_bin = 1 + int(np.argmax(dbfs[1:]))
                self.rows.append(
                    {
                        "id": self.next_id,
                        "values": np.rint(dbfs).astype(np.int8).tolist(),
                        "peak_hz": round(
                            peak_bin * self.sample_rate / self.fft_size,
                            1,
                        ),
                        "peak_dbfs": round(float(dbfs[peak_bin]), 1),
                    }
                )
                self.next_id += 1

    def snapshot(self, after: int) -> dict:
        if after < -1:
            raise ValueError("spectrum cursor cannot be less than -1")
        with self.lock:
            latest_id = self.next_id - 1
            oldest_id = self.rows[0]["id"] if self.rows else self.next_id
            reset = (
                after > latest_id
                or (
                    after != -1
                    and self.rows
                    and after < oldest_id - 1
                )
            )
            rows = list(self.rows) if reset else [
                row for row in self.rows if row["id"] > after
            ]
        return {
            "sample_rate": self.sample_rate,
            "fft_size": self.fft_size,
            "floor_dbfs": SPECTRUM_FLOOR_DBFS,
            "ceiling_dbfs": SPECTRUM_CEILING_DBFS,
            "latest_id": latest_id,
            "reset": reset,
            "rows": rows,
        }


class TextDemoController:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.received = bytearray()
        self.spectrum = ReceiverSpectrumMonitor()
        self.output_channel = getattr(args, "output_channel", "left")
        self.state = idle_state(
            args.allow_air,
            args.port,
            self.output_channel,
        )
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

    def spectrum_data(self, after: int) -> dict:
        return self.spectrum.snapshot(after)

    def start(
        self,
        text: str,
        backend_name: str,
        modem_version: str,
        fft_len: int,
        low_frequency_hz: float,
        high_frequency_hz: float,
        output_channel: str = "left",
        modulation: str = "qpsk",
    ) -> None:
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
        if output_channel not in ("left", "right", "stereo"):
            raise ValueError(
                "output channel must be left, right, or stereo"
            )
        cfg = make_acoustic_config(
            modem_version,
            low_frequency_hz,
            high_frequency_hz,
            fft_len=fft_len,
            modulation=modulation,
        )

        with self.lock:
            if self.worker is not None and self.worker.is_alive():
                raise RuntimeError("a text transfer is already running")
            self.received.clear()
            self.spectrum.reset()
            self.stop_event.clear()
            self.state = idle_state(
                self.args.allow_air,
                self.args.port,
                self.output_channel,
            )
            self.state.update(
                {
                    "status": "warming",
                    "backend": backend_name,
                    "framing": "continuous-superframe",
                    "output_channel": output_channel,
                    "modem_version": cfg.modem_name,
                    "modulation": cfg.modulation,
                    "bits_per_carrier": cfg.bits_per_carrier,
                    "body_code": cfg.body_code,
                    "low_frequency_hz": cfg.low_frequency_hz,
                    "high_frequency_hz": cfg.high_frequency_hz,
                    "bandwidth_hz": (
                        cfg.high_frequency_hz - cfg.low_frequency_hz
                    ),
                    "sample_rate": cfg.sample_rate,
                    "fft_len": cfg.fft_len,
                    "cp_len": cfg.cp_len,
                    "subcarrier_spacing_hz": cfg.subcarrier_spacing_hz,
                    "symbol_duration_ms": (
                        1000.0 * cfg.symbol_duration_s
                    ),
                    "cyclic_prefix_ms": (
                        1000.0 * cfg.cyclic_prefix_duration_s
                    ),
                    "data_subcarriers": int(cfg.data_bins.size),
                    "gross_bit_rate": cfg.gross_bit_rate,
                    "burst_packets": self.args.burst_packets,
                    "chunk_bytes": (
                        UNCODED_CHUNK_BYTES
                        if cfg.modem_name == "uncoded"
                        else self.args.chunk_bytes
                    ),
                    "total_bytes": encoded_size,
                }
            )
            self.worker = threading.Thread(
                target=self._run,
                args=(text, backend_name, cfg, output_channel),
                daemon=True,
                name="cora-ofdm-text-transfer",
            )
            self.worker.start()

    def _run(
        self,
        text: str,
        backend_name: str,
        cfg,
        output_channel: str = "left",
    ) -> None:
        if backend_name == "air":
            backend = AlsaAirBackend(
                playback_device=self.args.playback_device,
                capture_device=self.args.capture_device,
                burst_packets=self.args.burst_packets,
                cfg=cfg,
                capture_callback=self.spectrum.ingest_pcm,
                output_channel=output_channel,
            )
        else:
            backend = ColoredRoomBackend(
                self.args.noise_dbfs,
                cfg=cfg,
                capture_callback=self.spectrum.ingest_pcm,
            )

        def update(values: dict, chunk: bytes) -> None:
            with self.lock:
                self.received.extend(chunk)
                values["air_enabled"] = self.args.allow_air
                values["burst_packets"] = self.args.burst_packets
                values["dashboard_port"] = self.args.port
                values["output_channel"] = output_channel
                self.state = values

        try:
            chunk_bytes = (
                UNCODED_CHUNK_BYTES
                if cfg.modem_name == "uncoded"
                else self.args.chunk_bytes
            )
            output, values = transfer_text(
                text,
                backend=backend,
                stop_event=self.stop_event,
                packet_callback=update,
                chunk_bytes=chunk_bytes,
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
                values["output_channel"] = output_channel
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
            self.spectrum.reset()
            self.state = idle_state(
                self.args.allow_air,
                self.args.port,
                self.output_channel,
            )
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
        if parsed.path == "/api/spectrum":
            try:
                after = int(
                    parse_qs(parsed.query).get("after", ["-1"])[0]
                )
                payload = self.controller.spectrum_data(after)
            except ValueError as error:
                self.send_json(416, {"error": str(error)})
                return
            self.send_json(200, payload)
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
                    str(
                        request.get(
                            "modem_version",
                            DEFAULT_MODEM_VERSION,
                        )
                    ),
                    int(
                        request.get(
                            "fft_len",
                            DEFAULT_FFT_LENGTH,
                        )
                    ),
                    float(
                        request.get(
                            "low_frequency_hz",
                            DEFAULT_LOW_FREQUENCY_HZ,
                        )
                    ),
                    float(
                        request.get(
                            "high_frequency_hz",
                            DEFAULT_HIGH_FREQUENCY_HZ,
                        )
                    ),
                    str(
                        request.get(
                            "output_channel",
                            self.controller.output_channel,
                        )
                    ),
                    str(request.get("modulation", "qpsk")),
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
        except (OSError, RuntimeError, TypeError, ValueError) as error:
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
    parser.add_argument("--noise-dbfs", type=float, default=-60.0)
    parser.add_argument("--playback-device", default="plughw:0,0")
    parser.add_argument("--capture-device", default="plughw:0,0")
    parser.add_argument(
        "--output-channel",
        choices=("left", "right", "stereo"),
        default="left",
        help="speaker output routing; left is the single-speaker default",
    )
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
