#!/usr/bin/env python3
"""Userspace access to the sample-paced Cora acoustic AXI loopback."""

from __future__ import annotations

import fcntl
import os
import struct

import numpy as np


FFT_LENGTH = 512
DEFAULT_PL_SAMPLE_RATE = 250_000
_GET_INFO = 0x80204400
_CLEAR_STATS = 0x00004403
_SET_PATH = 0x40084404
_GET_STATUS = 0x80204405


def _resample(
    samples: np.ndarray,
    input_rate: int,
    output_rate: int,
    output_length: int | None = None,
) -> np.ndarray:
    values = np.asarray(samples, dtype=np.float64)
    if values.ndim != 1:
        raise ValueError("acoustic PL samples must be one-dimensional")
    if input_rate <= 0 or output_rate <= 0:
        raise ValueError("sample rates must be positive")
    if output_length is None:
        output_length = round(values.size * output_rate / input_rate)
    if output_length <= 0 or values.size == 0:
        return np.empty(0, dtype=np.float64)
    if values.size == 1:
        return np.full(output_length, values[0], dtype=np.float64)
    source_time = np.arange(values.size, dtype=np.float64) / input_rate
    target_time = np.arange(output_length, dtype=np.float64) / output_rate
    return np.interp(target_time, source_time, values, left=values[0], right=values[-1])


class AxisLoopbackDevice:
    """Transactional 16-bit samples over the Cora AXI MM2S/S2MM loop."""

    def __init__(
        self,
        device: str = "/dev/cora-dsp0",
        sample_rate: int = DEFAULT_PL_SAMPLE_RATE,
    ):
        if sample_rate <= 0:
            raise ValueError("PL sample rate must be positive")
        self.device = device
        self.sample_rate = int(sample_rate)
        self.fd: int | None = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *_exc):
        self.close()

    def open(self) -> None:
        if self.fd is not None:
            return
        self.fd = os.open(self.device, os.O_RDWR | os.O_CLOEXEC)
        try:
            info = bytearray(32)
            fcntl.ioctl(self.fd, _GET_INFO, info, True)
            abi, core_id, _version, fft_length = struct.unpack_from("<4I", info)
            if abi < 2 or core_id != 0x44535031 or fft_length != FFT_LENGTH:
                raise RuntimeError(
                    f"device lacks the acoustic AXIS router: abi={abi}, "
                    f"core=0x{core_id:08x}, frame={fft_length}"
                )
            _sample_bytes, _dma_bytes, _rate, capabilities = struct.unpack_from(
                "<4I", info, 16
            )
            if not capabilities & 1:
                raise RuntimeError("device does not advertise AXIS loopback")
            fcntl.ioctl(
                self.fd,
                _SET_PATH,
                struct.pack("<2I", 0, self.sample_rate),
            )
            self.sample_rate = self.status()["sample_rate"]
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def status(self) -> dict[str, int]:
        if self.fd is None:
            raise RuntimeError("AXIS loopback device is not open")
        raw = bytearray(32)
        fcntl.ioctl(self.fd, _GET_STATUS, raw, True)
        path, rate, clock, divider, samples, frames = struct.unpack("<4I2Q", raw)
        return {
            "path": path,
            "sample_rate": rate,
            "clock_hz": clock,
            "sample_divider": divider,
            "samples": samples,
            "frames": frames,
        }

    def clear_stats(self) -> None:
        if self.fd is None:
            raise RuntimeError("AXIS loopback device is not open")
        fcntl.ioctl(self.fd, _CLEAR_STATS)

    def process_frame(self, samples: np.ndarray) -> np.ndarray:
        if self.fd is None:
            raise RuntimeError("AXIS loopback device is not open")
        frame = np.ascontiguousarray(samples, dtype="<i2")
        if frame.ndim != 1 or frame.size != FFT_LENGTH:
            raise ValueError("AXIS DMA transactions require 512 signed-16 samples")
        view = memoryview(frame).cast("B")
        offset = 0
        while offset < len(view):
            count = os.write(self.fd, view[offset:])
            if count <= 0:
                raise RuntimeError("AXIS loopback write made no progress")
            offset += count
        output = bytearray()
        while len(output) < FFT_LENGTH * 2:
            chunk = os.read(self.fd, FFT_LENGTH * 2 - len(output))
            if not chunk:
                raise RuntimeError("AXIS loopback returned an incomplete frame")
            output.extend(chunk)
        return np.frombuffer(output, dtype="<i2").copy()

    def process(self, samples: np.ndarray) -> np.ndarray:
        values = np.ascontiguousarray(samples, dtype="<i2")
        original_length = values.size
        padded_length = ((original_length + FFT_LENGTH - 1) // FFT_LENGTH) * FFT_LENGTH
        padded = np.pad(values, (0, padded_length - original_length))
        result = np.empty(padded_length, dtype="<i2")
        for offset in range(0, padded_length, FFT_LENGTH):
            result[offset : offset + FFT_LENGTH] = self.process_frame(
                padded[offset : offset + FFT_LENGTH]
            )
        return result[:original_length]


def roundtrip_waveform(
    samples: np.ndarray,
    source_rate: int,
    *,
    device: str = "/dev/cora-dsp0",
    pl_sample_rate: int = DEFAULT_PL_SAMPLE_RATE,
) -> np.ndarray:
    """Resample a waveform into PL, round-trip it, and restore its rate."""

    source = np.asarray(samples, dtype=np.float64)
    pl_values = _resample(source, source_rate, pl_sample_rate)
    fixed = np.rint(np.clip(pl_values, -1.0, 1.0) * 32767.0).astype("<i2")
    with AxisLoopbackDevice(device, pl_sample_rate) as loopback:
        returned = loopback.process(fixed)
    returned_float = returned.astype(np.float64) / 32767.0
    return _resample(
        returned_float,
        pl_sample_rate,
        source_rate,
        output_length=source.size,
    )
