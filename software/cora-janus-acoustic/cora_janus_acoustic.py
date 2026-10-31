#!/usr/bin/env python3
"""JANUS STANAG 4748 acoustic modem wrapper for the Cora audio path."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from typing import Sequence
import wave

import numpy as np


SAMPLE_RATE = 48_000
MAX_APPLICATION_DATA = (1 << 34) - 1
REFERENCE_CLASS_ID = 16
DEFAULT_PSET_FILE = Path("/usr/share/janus/etc/parameter_sets.csv")


@dataclass(frozen=True)
class JanusParameterSet:
    identifier: int
    center_frequency_hz: int
    bandwidth_hz: int
    name: str

    @property
    def occupied_band_hz(self) -> tuple[float, float]:
        return (
            self.center_frequency_hz - self.bandwidth_hz / 2.0,
            self.center_frequency_hz + self.bandwidth_hz / 2.0,
        )


PARAMETER_SETS = {
    1: JanusParameterSet(1, 11_520, 4_160, "Initial JANUS band (9-14 kHz)"),
    2: JanusParameterSet(2, 1_200, 400, "Long-range low band"),
    3: JanusParameterSet(3, 4_096, 1_365, "Long-range medium band"),
    4: JanusParameterSet(4, 8_192, 2_731, "Second medium band"),
}


@dataclass(frozen=True)
class JanusConfig:
    """A JANUS baseline packet and audio-stream configuration."""

    parameter_set_id: int = 1
    sample_rate: int = SAMPLE_RATE
    class_id: int = REFERENCE_CLASS_ID
    application_type: int = 0
    application_data: int = 0
    mobility: bool = False
    tx_rx_capable: bool = True
    forwarding_capable: bool = False
    wake_up_tones: bool = False
    output_amplitude: float = 0.25

    @property
    def parameter_set(self) -> JanusParameterSet:
        return PARAMETER_SETS[self.parameter_set_id]


@dataclass
class JanusPacket:
    config: JanusConfig
    samples: np.ndarray
    duration_s: float
    tx_output: str


@dataclass
class JanusDecodeResult:
    valid: bool
    class_id: int | None
    application_type: int | None
    application_data: int | None
    parameter_set_id: int | None
    speed_mps: float | None
    rssi: float | None
    snr_db: float | None
    raw_output: str
    error: str | None


def make_config(
    *,
    parameter_set_id: int = 1,
    class_id: int = REFERENCE_CLASS_ID,
    application_type: int = 0,
    application_data: int = 0,
    mobility: bool = False,
    tx_rx_capable: bool = True,
    forwarding_capable: bool = False,
    wake_up_tones: bool = False,
    output_amplitude: float = 0.25,
) -> JanusConfig:
    if parameter_set_id not in PARAMETER_SETS:
        raise ValueError(f"parameter set must be one of {tuple(PARAMETER_SETS)}")
    if not 0 <= class_id <= 255:
        raise ValueError("class ID must be between 0 and 255")
    if not 0 <= application_type <= 63:
        raise ValueError("application type must be between 0 and 63")
    if not 0 <= application_data <= MAX_APPLICATION_DATA:
        raise ValueError("application data must fit in the JANUS 34-bit field")
    if class_id == REFERENCE_CLASS_ID and application_type in (0, 1) and application_data:
        raise ValueError(
            "class 16 application types 0 and 1 require their registered "
            "codec fields; raw application data must be zero"
        )
    if not math.isfinite(output_amplitude) or not 0.01 <= output_amplitude <= 1.0:
        raise ValueError("output amplitude must be between 0.01 and 1.0")
    pset = PARAMETER_SETS[parameter_set_id]
    low_hz, high_hz = pset.occupied_band_hz
    if low_hz <= 0 or high_hz >= SAMPLE_RATE / 2:
        raise ValueError("parameter set does not fit the 48 ksample/s audio path")
    return JanusConfig(
        parameter_set_id=parameter_set_id,
        class_id=class_id,
        application_type=application_type,
        application_data=application_data,
        mobility=mobility,
        tx_rx_capable=tx_rx_capable,
        forwarding_capable=forwarding_capable,
        wake_up_tones=wake_up_tones,
        output_amplitude=output_amplitude,
    )


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as source:
        channels = source.getnchannels()
        width = source.getsampwidth()
        sample_rate = source.getframerate()
        frames = source.readframes(source.getnframes())
    if width != 2:
        raise ValueError(f"expected 16-bit PCM WAV, got {width * 8}-bit")
    values = np.frombuffer(frames, dtype="<i2").astype(np.float64)
    if channels > 1:
        values = values.reshape(-1, channels).mean(axis=1)
    return (values / 32768.0).astype(np.float32), sample_rate


def write_wav(path: Path, samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> None:
    pcm = np.rint(np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as sink:
        sink.setnchannels(1)
        sink.setsampwidth(2)
        sink.setframerate(sample_rate)
        sink.writeframes(pcm.tobytes())


def simulated_acoustic_channel(
    samples: np.ndarray,
    *,
    noise_dbfs: float = -60.0,
    seed: int = 4748,
) -> np.ndarray:
    """Apply deterministic mild multipath and additive noise to a JANUS packet."""
    values = np.asarray(samples, dtype=np.float64)
    taps = np.zeros(73)
    taps[[0, 11, 31, 72]] = [0.95, -0.025, 0.015, -0.008]
    result = np.convolve(values, taps)
    rng = np.random.default_rng(seed)
    result += rng.normal(0.0, 10.0 ** (noise_dbfs / 20.0), result.size)
    return np.clip(result, -1.0, 1.0).astype(np.float32)


def _number(output: str, label: str, *, integer: bool = False):
    match = re.search(
        rf"^.*{re.escape(label)}[^\r\n:]*:\s*([^\r\n]+)",
        output,
        re.MULTILINE,
    )
    if not match:
        return None
    value = match.group(1).strip().split()[0]
    try:
        if integer:
            return int(value)
        number = float(value)
        return number if math.isfinite(number) else None
    except ValueError:
        return None


def parse_decoder_output(output: str) -> JanusDecodeResult:
    crc = _number(output, "CRC Validity", integer=True)
    app_bits = re.search(
        r"^.*Application Data \(34 bits\)\s*:\s*([01]{34})",
        output,
        re.MULTILINE,
    )
    valid = crc == 1
    return JanusDecodeResult(
        valid=valid,
        class_id=_number(output, "Class User Identifier", integer=True),
        application_type=_number(output, "Application Type", integer=True),
        application_data=int(app_bits.group(1), 2) if app_bits else None,
        parameter_set_id=_number(output, "Parameter Set Id", integer=True),
        speed_mps=_number(output, "Speed (m/s)"),
        rssi=_number(output, "RSSI"),
        snr_db=_number(output, "SNR"),
        raw_output=output,
        error=None if valid else "no CRC-valid JANUS packet was decoded",
    )


class JanusToolchain:
    """Invoke the separately packaged GPL JANUS reference implementation."""

    def __init__(
        self,
        *,
        transmitter: str = "janus-tx",
        receiver: str = "janus-rx",
        parameter_set_file: Path = DEFAULT_PSET_FILE,
    ):
        self.transmitter = transmitter
        self.receiver = receiver
        self.parameter_set_file = Path(parameter_set_file)

    def availability(self) -> tuple[bool, str | None]:
        missing = []
        if not shutil.which(self.transmitter) and not Path(self.transmitter).is_file():
            missing.append(self.transmitter)
        if not shutil.which(self.receiver) and not Path(self.receiver).is_file():
            missing.append(self.receiver)
        if not self.parameter_set_file.is_file():
            missing.append(str(self.parameter_set_file))
        if missing:
            return False, "missing JANUS reference component(s): " + ", ".join(missing)
        return True, None

    def _require(self) -> None:
        available, error = self.availability()
        if not available:
            raise RuntimeError(error)

    def encode(self, output_path: Path, cfg: JanusConfig) -> JanusPacket:
        self._require()
        command = [
            self.transmitter,
            "--verbose", "1",
            "--pset-file", str(self.parameter_set_file),
            "--pset-id", str(cfg.parameter_set_id),
            "--stream-driver", "wav",
            "--stream-driver-args", str(output_path),
            "--stream-fs", str(cfg.sample_rate),
            "--stream-amp", str(cfg.output_amplitude),
            "--wut", str(int(cfg.wake_up_tones)),
            "--packet-mobility", str(int(cfg.mobility)),
            "--packet-tx-rx", str(int(cfg.tx_rx_capable)),
            "--packet-forward", str(int(cfg.forwarding_capable)),
            "--packet-class-id", str(cfg.class_id),
            "--packet-app-type", str(cfg.application_type),
            "--packet-app-data", f"{cfg.application_data:X}",
        ]
        completed = subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        samples, sample_rate = read_wav(output_path)
        if sample_rate != cfg.sample_rate:
            raise RuntimeError(f"JANUS transmitter returned {sample_rate} sample/s")
        return JanusPacket(
            config=cfg,
            samples=samples,
            duration_s=samples.size / sample_rate,
            tx_output=completed.stdout,
        )

    def decode(self, input_path: Path, cfg: JanusConfig, timeout_s: float = 4.0) -> JanusDecodeResult:
        self._require()
        command = [
            self.receiver,
            "--verbose", "2",
            "--pset-file", str(self.parameter_set_file),
            "--pset-id", str(cfg.parameter_set_id),
            "--stream-driver", "wav",
            "--stream-driver-args", str(input_path),
            "--stream-fs", str(cfg.sample_rate),
            "--doppler-correction", "1",
        ]
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        try:
            output, _ = process.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                output, _ = process.communicate(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
                output, _ = process.communicate()
        decoded = output.decode("utf-8", errors="replace")
        return parse_decoder_output(decoded)


def _toolchain_from_args(args: argparse.Namespace) -> JanusToolchain:
    return JanusToolchain(
        transmitter=args.janus_tx,
        receiver=args.janus_rx,
        parameter_set_file=args.parameter_set_file,
    )


def _config_from_args(args: argparse.Namespace) -> JanusConfig:
    value = int(str(args.application_data), 0)
    return make_config(
        parameter_set_id=args.parameter_set,
        class_id=args.class_id,
        application_type=args.application_type,
        application_data=value,
        mobility=args.mobility,
        tx_rx_capable=not args.receive_only,
        forwarding_capable=args.forwarding,
        wake_up_tones=args.wake_up_tones,
        output_amplitude=args.output_amplitude,
    )


def _record_command(path: Path, device: str, seconds: int) -> list[str]:
    return [
        "arecord", "-q", "-D", device, "-f", "S16_LE", "-r",
        str(SAMPLE_RATE), "-c", "1", "-d", str(seconds), str(path),
    ]


def _result_json(result: JanusDecodeResult) -> str:
    values = asdict(result)
    values.pop("raw_output")
    return json.dumps(values, indent=2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parameter-set", type=int, choices=tuple(PARAMETER_SETS), default=1)
    parser.add_argument("--class-id", type=int, default=REFERENCE_CLASS_ID)
    parser.add_argument("--application-type", type=int, default=0)
    parser.add_argument("--application-data", default="0x0")
    parser.add_argument("--mobility", action="store_true")
    parser.add_argument("--receive-only", action="store_true")
    parser.add_argument("--forwarding", action="store_true")
    parser.add_argument("--wake-up-tones", action="store_true")
    parser.add_argument("--output-amplitude", type=float, default=0.25)
    parser.add_argument("--janus-tx", default="janus-tx")
    parser.add_argument("--janus-rx", default="janus-rx")
    parser.add_argument("--parameter-set-file", type=Path, default=DEFAULT_PSET_FILE)
    parser.add_argument("--playback-device", default="plughw:0,0")
    parser.add_argument("--capture-device", default="plughw:0,0")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("info")
    encode = commands.add_parser("encode")
    encode.add_argument("--wav", type=Path, required=True)
    decode = commands.add_parser("decode")
    decode.add_argument("--wav", type=Path, required=True)
    commands.add_parser("loopback")
    commands.add_parser("send")
    commands.add_parser("selftest")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        cfg = _config_from_args(args)
        toolchain = _toolchain_from_args(args)
        if args.command == "info":
            available, error = toolchain.availability()
            low_hz, high_hz = cfg.parameter_set.occupied_band_hz
            print(json.dumps({
                "application": "cora-janus-acoustic",
                "standard": "JANUS STANAG 4748 / ANEP-87",
                "reference_toolchain_available": available,
                "reference_toolchain_error": error,
                "parameter_set": asdict(cfg.parameter_set),
                "occupied_band_hz": [low_hz, high_hz],
                "sample_rate": cfg.sample_rate,
                "class_id": cfg.class_id,
                "application_type": cfg.application_type,
                "application_data": cfg.application_data,
            }, indent=2))
            return 0
        if args.command == "decode":
            result = toolchain.decode(args.wav, cfg)
            print(_result_json(result))
            return 0 if result.valid else 2
        with tempfile.TemporaryDirectory(prefix="cora-janus-") as folder:
            tx_path = args.wav if args.command == "encode" else Path(folder) / "tx.wav"
            packet = toolchain.encode(tx_path, cfg)
            if args.command == "encode":
                print(f"Wrote {tx_path}: {packet.duration_s:.3f} s")
                return 0
            if args.command == "loopback":
                rx_path = Path(folder) / "rx.wav"
                leading = np.zeros(round(0.1 * SAMPLE_RATE), dtype=np.float32)
                write_wav(rx_path, np.concatenate((leading, simulated_acoustic_channel(packet.samples))))
                result = toolchain.decode(rx_path, cfg)
                print(_result_json(result))
                return 0 if result.valid and result.application_data == cfg.application_data else 2
            if args.command == "send":
                subprocess.run(["aplay", "-q", "-D", args.playback_device, str(tx_path)], check=True)
                return 0
            if args.command == "selftest":
                rx_path = Path(folder) / "rx.wav"
                seconds = max(2, math.ceil(packet.duration_s + 1.0))
                recorder = subprocess.Popen(_record_command(rx_path, args.capture_device, seconds))
                try:
                    time.sleep(0.25)
                    subprocess.run(["aplay", "-q", "-D", args.playback_device, str(tx_path)], check=True)
                    status = recorder.wait(timeout=seconds + 2)
                finally:
                    if recorder.poll() is None:
                        recorder.terminate()
                        recorder.wait()
                if status:
                    raise RuntimeError(f"arecord exited with status {status}")
                result = toolchain.decode(rx_path, cfg)
                print(_result_json(result))
                return 0 if result.valid else 2
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as error:
        parser.error(str(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
