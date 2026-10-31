#!/usr/bin/env python3
"""Web dashboard for JANUS interoperability experiments."""

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

from cora_acomms_pl import DEFAULT_PL_SAMPLE_RATE, roundtrip_waveform

from cora_janus_acoustic import (
    MAX_APPLICATION_DATA,
    PARAMETER_SETS,
    SAMPLE_RATE,
    JanusToolchain,
    make_config,
    read_wav,
    simulated_acoustic_channel,
    write_wav,
)


DEFAULT_WEB_ROOT = Path("/usr/share/cora-janus-acoustic/www")
MAX_REQUEST_BYTES = 8192


def _preview(samples: np.ndarray) -> tuple[list[float], list[float], list[float]]:
    samples = np.asarray(samples, dtype=np.float64)
    stride = max(1, math.ceil(samples.size / 1000))
    waveform = np.round(samples[::stride][:1000], 4).tolist()
    fft_size = 1 << min(15, max(8, (samples.size - 1).bit_length()))
    windowed = samples[:fft_size]
    if windowed.size < fft_size:
        windowed = np.pad(windowed, (0, fft_size - windowed.size))
    spectrum = np.abs(np.fft.rfft(windowed * np.hanning(fft_size)))
    spectrum /= max(float(np.max(spectrum)), np.finfo(float).eps)
    spectrum_db = 20.0 * np.log10(np.maximum(spectrum, 1e-5))
    frequency_hz = np.fft.rfftfreq(fft_size, 1.0 / SAMPLE_RATE)
    spectrum_stride = max(1, math.ceil(frequency_hz.size / 512))
    return (
        waveform,
        np.round(frequency_hz[::spectrum_stride], 1).tolist(),
        np.round(spectrum_db[::spectrum_stride], 1).tolist(),
    )


def _idle_state(
    toolchain: JanusToolchain,
    air_enabled: bool,
    port: int,
    pl_enabled: bool = False,
    pl_sample_rate: int = DEFAULT_PL_SAMPLE_RATE,
) -> dict:
    cfg = make_config()
    available, toolchain_error = toolchain.availability()
    low_hz, high_hz = cfg.parameter_set.occupied_band_hz
    return {
        "application": "cora-janus-acoustic-dashboard",
        "standard": "JANUS STANAG 4748 / ANEP-87",
        "status": "idle" if available else "unavailable",
        "process_running": False,
        "reference_toolchain_available": available,
        "reference_toolchain_error": toolchain_error,
        "air_enabled": air_enabled,
        "pl_enabled": pl_enabled,
        "pl_sample_rate": pl_sample_rate,
        "dashboard_port": port,
        "sample_rate": SAMPLE_RATE,
        "parameter_sets": [
            {
                "id": pset.identifier,
                "center_frequency_hz": pset.center_frequency_hz,
                "bandwidth_hz": pset.bandwidth_hz,
                "name": pset.name,
            }
            for pset in PARAMETER_SETS.values()
        ],
        "parameter_set_id": cfg.parameter_set_id,
        "center_frequency_hz": cfg.parameter_set.center_frequency_hz,
        "bandwidth_hz": cfg.parameter_set.bandwidth_hz,
        "occupied_band_hz": [low_hz, high_hz],
        "class_id": cfg.class_id,
        "application_type": cfg.application_type,
        "application_data": cfg.application_data,
        "application_data_hex": f"0x{cfg.application_data:X}",
        "mobility": cfg.mobility,
        "tx_rx_capable": cfg.tx_rx_capable,
        "forwarding_capable": cfg.forwarding_capable,
        "wake_up_tones": cfg.wake_up_tones,
        "backend": None,
        "waveform_duration_s": 0.0,
        "elapsed_s": 0.0,
        "decoded_valid": None,
        "decoded_class_id": None,
        "decoded_application_type": None,
        "decoded_application_data": None,
        "decoded_application_data_hex": None,
        "speed_mps": None,
        "rssi": None,
        "snr_db": None,
        "waveform_preview": [],
        "spectrum_frequency_hz": [],
        "spectrum_db": [],
        "error": toolchain_error,
    }


class JanusController:
    def __init__(self, args: argparse.Namespace, toolchain: JanusToolchain | None = None):
        self.args = args
        self.pl_device = getattr(args, "pl_device", "/dev/cora-dsp0")
        self.pl_sample_rate = getattr(
            args, "pl_sample_rate", DEFAULT_PL_SAMPLE_RATE
        )
        self.toolchain = toolchain or JanusToolchain(
            transmitter=args.janus_tx,
            receiver=args.janus_rx,
            parameter_set_file=args.parameter_set_file,
        )
        self.lock = threading.Lock()
        self.worker: threading.Thread | None = None
        self.state = _idle_state(
            self.toolchain,
            args.allow_air,
            args.port,
            Path(self.pl_device).exists(),
            self.pl_sample_rate,
        )

    def status(self) -> dict:
        with self.lock:
            state = self.state.copy()
            state["process_running"] = bool(self.worker and self.worker.is_alive())
            return state

    def reset(self) -> None:
        with self.lock:
            if self.worker and self.worker.is_alive():
                raise RuntimeError("cannot reset while a transfer is running")
            self.state = _idle_state(
                self.toolchain,
                self.args.allow_air,
                self.args.port,
                Path(self.pl_device).exists(),
                self.pl_sample_rate,
            )

    def start(
        self,
        *,
        backend: str,
        parameter_set_id: int,
        class_id: int,
        application_type: int,
        application_data: int,
        mobility: bool,
        tx_rx_capable: bool,
        forwarding_capable: bool,
        wake_up_tones: bool,
    ) -> None:
        if backend not in ("loopback", "pl-loopback", "air"):
            raise ValueError("backend must be loopback, pl-loopback, or air")
        if backend == "pl-loopback" and not Path(self.pl_device).exists():
            raise ValueError("acoustic PL loopback device is unavailable")
        if backend == "air" and not self.args.allow_air:
            raise ValueError("air backend is disabled by the service")
        cfg = make_config(
            parameter_set_id=parameter_set_id,
            class_id=class_id,
            application_type=application_type,
            application_data=application_data,
            mobility=mobility,
            tx_rx_capable=tx_rx_capable,
            forwarding_capable=forwarding_capable,
            wake_up_tones=wake_up_tones,
            output_amplitude=self.args.output_amplitude,
        )
        available, error = self.toolchain.availability()
        if not available:
            raise RuntimeError(error)
        with self.lock:
            if self.worker and self.worker.is_alive():
                raise RuntimeError("a JANUS transfer is already running")
            pset = cfg.parameter_set
            self.state.update({
                "status": "warming" if backend == "air" else "running",
                "backend": backend,
                "parameter_set_id": cfg.parameter_set_id,
                "center_frequency_hz": pset.center_frequency_hz,
                "bandwidth_hz": pset.bandwidth_hz,
                "occupied_band_hz": list(pset.occupied_band_hz),
                "class_id": cfg.class_id,
                "application_type": cfg.application_type,
                "application_data": cfg.application_data,
                "application_data_hex": f"0x{cfg.application_data:X}",
                "mobility": cfg.mobility,
                "tx_rx_capable": cfg.tx_rx_capable,
                "forwarding_capable": cfg.forwarding_capable,
                "wake_up_tones": cfg.wake_up_tones,
                "waveform_duration_s": 0.0,
                "elapsed_s": 0.0,
                "decoded_valid": None,
                "decoded_class_id": None,
                "decoded_application_type": None,
                "decoded_application_data": None,
                "decoded_application_data_hex": None,
                "speed_mps": None,
                "rssi": None,
                "snr_db": None,
                "error": None,
            })
            self.worker = threading.Thread(
                target=self._run, args=(backend, cfg), daemon=True
            )
            self.worker.start()

    def _air_recording(self, tx_path: Path, duration_s: float, output_path: Path) -> None:
        seconds = max(2, math.ceil(duration_s + 1.0))
        command = [
            "arecord", "-q", "-D", self.args.capture_device,
            "-f", "S16_LE", "-r", str(SAMPLE_RATE), "-c", "1",
            "-d", str(seconds), str(output_path),
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

    def _run(self, backend: str, cfg) -> None:
        started = time.monotonic()
        try:
            with tempfile.TemporaryDirectory(prefix="cora-janus-dashboard-") as folder:
                tx_path = Path(folder) / "tx.wav"
                rx_path = Path(folder) / "rx.wav"
                packet = self.toolchain.encode(tx_path, cfg)
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
                    leading = np.zeros(round(0.1 * SAMPLE_RATE), dtype=np.float32)
                    recording = np.concatenate((
                        leading,
                        simulated_acoustic_channel(
                            packet.samples, noise_dbfs=self.args.noise_dbfs
                        ),
                    ))
                    write_wav(rx_path, recording)
                elif backend == "pl-loopback":
                    leading = np.zeros(round(0.1 * SAMPLE_RATE), dtype=np.float32)
                    recording = np.concatenate((
                        leading,
                        roundtrip_waveform(
                            packet.samples,
                            SAMPLE_RATE,
                            device=self.pl_device,
                            pl_sample_rate=self.pl_sample_rate,
                        ),
                    ))
                    write_wav(rx_path, recording)
                else:
                    self._air_recording(tx_path, packet.duration_s, rx_path)
                    _, rate = read_wav(rx_path)
                    if rate != SAMPLE_RATE:
                        raise RuntimeError(f"ALSA returned {rate} sample/s")
                result = self.toolchain.decode(rx_path, cfg)
            elapsed = time.monotonic() - started
            with self.lock:
                self.state.update({
                    "status": "complete" if result.valid else "failed",
                    "elapsed_s": elapsed,
                    "decoded_valid": result.valid,
                    "decoded_class_id": result.class_id,
                    "decoded_application_type": result.application_type,
                    "decoded_application_data": result.application_data,
                    "decoded_application_data_hex": (
                        f"0x{result.application_data:X}"
                        if result.application_data is not None else None
                    ),
                    "speed_mps": result.speed_mps,
                    "rssi": result.rssi,
                    "snr_db": result.snr_db,
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
    def controller(self) -> JanusController:
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
                data_text = str(request.get("application_data", "0x0")).strip()
                application_data = int(data_text, 0)
                if application_data > MAX_APPLICATION_DATA:
                    raise ValueError("application data must fit in 34 bits")
                self.controller.start(
                    backend=str(request.get("backend", "loopback")),
                    parameter_set_id=int(request.get("parameter_set_id", 1)),
                    class_id=int(request.get("class_id", 16)),
                    application_type=int(request.get("application_type", 0)),
                    application_data=application_data,
                    mobility=bool(request.get("mobility", False)),
                    tx_rx_capable=bool(request.get("tx_rx_capable", True)),
                    forwarding_capable=bool(request.get("forwarding_capable", False)),
                    wake_up_tones=bool(request.get("wake_up_tones", False)),
                )
                message = "JANUS packet started"
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
    parser.add_argument("--port", type=int, default=8086)
    parser.add_argument("--web-root", type=Path, default=DEFAULT_WEB_ROOT)
    parser.add_argument("--noise-dbfs", type=float, default=-60.0)
    parser.add_argument("--output-amplitude", type=float, default=0.25)
    parser.add_argument("--playback-device", default="plughw:0,0")
    parser.add_argument("--capture-device", default="plughw:0,0")
    parser.add_argument("--janus-tx", default="janus-tx")
    parser.add_argument("--janus-rx", default="janus-rx")
    parser.add_argument("--parameter-set-file", type=Path, default=Path("/usr/share/janus/etc/parameter_sets.csv"))
    parser.add_argument("--allow-air", action="store_true")
    parser.add_argument("--pl-device", default="/dev/cora-dsp0")
    parser.add_argument(
        "--pl-sample-rate", type=int, default=DEFAULT_PL_SAMPLE_RATE
    )
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if not 0.01 <= args.output_amplitude <= 1.0:
        parser.error("output amplitude must be between 0.01 and 1.0")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    controller = JanusController(args)
    server = DashboardServer((args.bind, args.port), DashboardHandler, controller)
    print(f"Cora JANUS dashboard listening on http://{args.bind}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
