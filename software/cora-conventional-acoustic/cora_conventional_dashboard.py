#!/usr/bin/env python3
"""Separate web dashboard for the conventional acoustic modem."""

from __future__ import annotations

import argparse
import http.server
import json
import math
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from typing import Sequence
from urllib.parse import urlparse

import numpy as np

from cora_conventional_acoustic import (
    SAMPLE_RATE,
    colored_acoustic_channel,
    decode_packet,
    encode_packet,
    make_config,
    read_wav,
    write_wav,
)


DEFAULT_WEB_ROOT = Path("/usr/share/cora-conventional-acoustic/www")
MAX_TEXT_BYTES = 512
MAX_REQUEST_BYTES = MAX_TEXT_BYTES * 4 + 4096


def _idle_state(air_enabled: bool, port: int) -> dict:
    cfg = make_config()
    low_hz, high_hz = cfg.occupied_band_hz
    return {
        "application": "cora-conventional-acoustic-dashboard",
        "waveform_family": "conventional-single-carrier-or-fsk",
        "independent_of_ofdm": True,
        "status": "idle",
        "modulation": cfg.modulation,
        "mfsk_order": cfg.tone_order,
        "sample_rate": cfg.sample_rate,
        "symbol_rate": cfg.symbol_rate,
        "bits_per_symbol": cfg.bits_per_symbol,
        "gross_bit_rate": cfg.gross_bit_rate,
        "carrier_frequency_hz": cfg.carrier_frequency_hz,
        "tone_spacing_hz": cfg.tone_spacing_hz,
        "tone_frequencies_hz": cfg.tone_frequencies_hz.tolist(),
        "occupied_band_hz": [low_hz, high_hz],
        "backend": None,
        "air_enabled": air_enabled,
        "dashboard_port": port,
        "max_text_bytes": MAX_TEXT_BYTES,
        "payload_bytes": 0,
        "payload_throughput_bps": 0.0,
        "waveform_duration_s": 0.0,
        "elapsed_s": 0.0,
        "sync_metric": None,
        "carrier_offset_hz": None,
        "received_text": "",
        "waveform_preview": [],
        "spectrum_frequency_hz": [],
        "spectrum_db": [],
        "error": None,
    }


def _preview(samples: np.ndarray) -> tuple[list[float], list[float], list[float]]:
    samples = np.asarray(samples, dtype=np.float64)
    stride = max(1, math.ceil(samples.size / 1000))
    waveform = np.round(samples[::stride][:1000], 4).tolist()
    fft_size = 1 << min(15, max(8, (samples.size - 1).bit_length()))
    windowed = samples[:fft_size]
    if windowed.size < fft_size:
        windowed = np.pad(windowed, (0, fft_size - windowed.size))
    windowed = windowed * np.hanning(fft_size)
    spectrum = np.abs(np.fft.rfft(windowed))
    spectrum /= max(float(np.max(spectrum)), np.finfo(float).eps)
    db = 20.0 * np.log10(np.maximum(spectrum, 1e-5))
    frequencies = np.fft.rfftfreq(fft_size, 1.0 / SAMPLE_RATE)
    spectrum_stride = max(1, math.ceil(frequencies.size / 512))
    return (
        waveform,
        np.round(frequencies[::spectrum_stride], 1).tolist(),
        np.round(db[::spectrum_stride], 1).tolist(),
    )


class ConventionalController:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.lock = threading.Lock()
        self.worker: threading.Thread | None = None
        self.state = _idle_state(args.allow_air, args.port)

    def status(self) -> dict:
        with self.lock:
            state = self.state.copy()
            state["process_running"] = bool(
                self.worker is not None and self.worker.is_alive()
            )
            return state

    def reset(self) -> None:
        with self.lock:
            if self.worker is not None and self.worker.is_alive():
                raise RuntimeError("cannot reset while a transfer is running")
            self.state = _idle_state(self.args.allow_air, self.args.port)

    def start(
        self,
        *,
        text: str,
        backend: str,
        modulation: str,
        mfsk_order: int,
        symbol_rate: int,
        carrier_frequency_hz: float,
    ) -> None:
        payload = text.encode("utf-8")
        if not payload:
            raise ValueError("text cannot be empty")
        if len(payload) > MAX_TEXT_BYTES:
            raise ValueError(f"text exceeds {MAX_TEXT_BYTES} UTF-8 bytes")
        if backend not in ("loopback", "air"):
            raise ValueError("backend must be loopback or air")
        if backend == "air" and not self.args.allow_air:
            raise ValueError("air backend is disabled by the service")
        cfg = make_config(
            modulation,
            mfsk_order=mfsk_order,
            symbol_rate=symbol_rate,
            carrier_frequency_hz=carrier_frequency_hz,
        )
        with self.lock:
            if self.worker is not None and self.worker.is_alive():
                raise RuntimeError("a transfer is already running")
            low_hz, high_hz = cfg.occupied_band_hz
            self.state.update({
                "status": "warming" if backend == "air" else "running",
                "backend": backend,
                "modulation": cfg.modulation,
                "mfsk_order": cfg.tone_order,
                "symbol_rate": cfg.symbol_rate,
                "bits_per_symbol": cfg.bits_per_symbol,
                "gross_bit_rate": cfg.gross_bit_rate,
                "carrier_frequency_hz": cfg.carrier_frequency_hz,
                "tone_spacing_hz": cfg.tone_spacing_hz,
                "tone_frequencies_hz": cfg.tone_frequencies_hz.tolist(),
                "occupied_band_hz": [low_hz, high_hz],
                "payload_bytes": len(payload),
                "payload_throughput_bps": 0.0,
                "waveform_duration_s": 0.0,
                "elapsed_s": 0.0,
                "sync_metric": None,
                "carrier_offset_hz": None,
                "received_text": "",
                "error": None,
            })
            self.worker = threading.Thread(
                target=self._run,
                args=(payload, backend, cfg),
                daemon=True,
            )
            self.worker.start()

    def _air_recording(self, packet, cfg) -> np.ndarray:
        seconds = max(2, math.ceil(packet.duration_s + 1.0))
        with tempfile.TemporaryDirectory(prefix="cora-conventional-") as folder:
            tx_path = Path(folder) / "tx.wav"
            rx_path = Path(folder) / "rx.wav"
            write_wav(tx_path, packet.samples, cfg.sample_rate)
            command = [
                "arecord", "-q", "-D", self.args.capture_device,
                "-f", "S16_LE", "-r", str(cfg.sample_rate), "-c", "1",
                "-d", str(seconds), str(rx_path),
            ]
            recorder = subprocess.Popen(command)
            try:
                time.sleep(0.25)
                subprocess.run(
                    ["aplay", "-q", "-D", self.args.playback_device, str(tx_path)],
                    check=True,
                )
                status = recorder.wait(timeout=seconds + 2)
            finally:
                if recorder.poll() is None:
                    recorder.terminate()
                    recorder.wait()
            if status:
                raise RuntimeError(f"arecord exited with status {status}")
            samples, rate = read_wav(rx_path)
            if rate != cfg.sample_rate:
                raise RuntimeError(f"ALSA returned {rate} sample/s")
            return samples

    def _run(self, payload: bytes, backend: str, cfg) -> None:
        started = time.monotonic()
        try:
            packet = encode_packet(payload, cfg=cfg)
            waveform, frequencies, spectrum = _preview(packet.samples)
            with self.lock:
                self.state.update({
                    "status": "running",
                    "waveform_duration_s": packet.duration_s,
                    "waveform_preview": waveform,
                    "spectrum_frequency_hz": frequencies,
                    "spectrum_db": spectrum,
                })
            if backend == "loopback":
                recording = np.concatenate((
                    np.zeros(997),
                    colored_acoustic_channel(
                        packet.samples,
                        noise_dbfs=self.args.noise_dbfs,
                    ),
                ))
            else:
                recording = self._air_recording(packet, cfg)
            result = decode_packet(recording, cfg=cfg)
            elapsed = time.monotonic() - started
            received_text = (
                result.payload.decode("utf-8", errors="replace")
                if result.valid else ""
            )
            with self.lock:
                self.state.update({
                    "status": "complete" if result.valid else "failed",
                    "elapsed_s": elapsed,
                    "payload_throughput_bps": (
                        len(result.payload) * 8.0 / elapsed
                        if result.valid and elapsed else 0.0
                    ),
                    "sync_metric": result.sync_metric,
                    "carrier_offset_hz": result.carrier_offset_hz,
                    "received_text": received_text,
                    "error": result.error,
                })
        except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as error:
            with self.lock:
                self.state.update({
                    "status": "failed",
                    "elapsed_s": time.monotonic() - started,
                    "error": str(error),
                })


class DashboardHandler(http.server.BaseHTTPRequestHandler):
    @property
    def controller(self) -> ConventionalController:
        return self.server.controller

    def _send(self, status: int, payload: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, values: dict) -> None:
        self._send(status, (json.dumps(values) + "\n").encode(), "application/json")

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            try:
                payload = (self.controller.args.web_root / "index.html").read_bytes()
            except OSError:
                self.send_error(404)
                return
            self._send(200, payload, "text/html; charset=utf-8")
            return
        if path == "/api/status":
            self._json(200, self.controller.status())
            return
        self.send_error(404)

    def do_POST(self) -> None:
        if self.headers.get("X-Cora-Request") != "1":
            self._json(403, {"error": "missing same-origin request header"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > MAX_REQUEST_BYTES:
                raise ValueError("request is too large")
            request = json.loads(self.rfile.read(length) or b"{}")
            path = urlparse(self.path).path
            if path == "/api/start":
                self.controller.start(
                    text=str(request.get("text", "")),
                    backend=str(request.get("backend", "loopback")),
                    modulation=str(request.get("modulation", "qpsk")),
                    mfsk_order=int(request.get("mfsk_order", 4)),
                    symbol_rate=int(request.get("symbol_rate", 250)),
                    carrier_frequency_hz=float(
                        request.get("carrier_frequency_hz", 6000.0)
                    ),
                )
                message = "conventional modem transfer started"
            elif path == "/api/reset":
                self.controller.reset()
                message = "dashboard reset"
            else:
                self.send_error(404)
                return
        except (TypeError, ValueError, RuntimeError) as error:
            self._json(409, {"error": str(error)})
            return
        self._json(200, {"message": message})

    def log_message(self, format_string: str, *args) -> None:
        print(f"{self.address_string()} - {format_string % args}", flush=True)


class DashboardServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, controller):
        super().__init__(address, handler)
        self.controller = controller


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8084)
    parser.add_argument("--web-root", type=Path, default=DEFAULT_WEB_ROOT)
    parser.add_argument("--noise-dbfs", type=float, default=-48.0)
    parser.add_argument("--playback-device", default="plughw:0,0")
    parser.add_argument("--capture-device", default="plughw:0,0")
    parser.add_argument("--allow-air", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    controller = ConventionalController(args)
    server = DashboardServer((args.bind, args.port), DashboardHandler, controller)
    print(
        f"Cora conventional modem dashboard listening on http://{args.bind}:{args.port}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
