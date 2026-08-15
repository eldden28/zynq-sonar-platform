# SPDX-License-Identifier: GPL-3.0-or-later
"""GNU Radio interface to the Cora 512-point FFT/filter/IFFT accelerator."""

from __future__ import annotations

import fcntl
import os
import struct

import numpy
from gnuradio import gr


FFT_LENGTH = 512
_CORA_DSP_SET_FILTER = 0x40084401
_CORA_DSP_GET_INFO = 0x80204400
_CORA_DSP_GET_STATUS = 0x80304402
_CORA_DSP_CLEAR_STATS = 0x00004403


def _write_all(fd: int, data: memoryview) -> None:
    offset = 0
    while offset < len(data):
        written = os.write(fd, data[offset:])
        if written <= 0:
            raise RuntimeError("Cora DSP write made no progress")
        offset += written


def _read_exact(fd: int, count: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < count:
        data = os.read(fd, count - len(chunks))
        if not data:
            raise RuntimeError("Cora DSP returned an incomplete frame")
        chunks.extend(data)
    return bytes(chunks)


class HwFftFilterDevice:
    """Transactional signed-Q1.15 interface to ``/dev/cora-dsp0``."""

    def __init__(
        self,
        device_path: str = "/dev/cora-dsp0",
        low_bin: int = 0,
        high_bin: int = 256,
    ):
        if not 0 <= low_bin <= high_bin <= FFT_LENGTH // 2:
            raise ValueError("filter bins must satisfy 0 <= low <= high <= 256")
        self.device_path = device_path
        self.low_bin = int(low_bin)
        self.high_bin = int(high_bin)
        self._fd: int | None = None

    def open(self) -> None:
        if self._fd is not None:
            return
        self._fd = os.open(self.device_path, os.O_RDWR | os.O_CLOEXEC)
        info = bytearray(32)
        fcntl.ioctl(self._fd, _CORA_DSP_GET_INFO, info, True)
        abi, core_id, _version, fft_length = struct.unpack_from("<4I", info)
        if abi != 1 or core_id != 0x44535031 or fft_length != FFT_LENGTH:
            os.close(self._fd)
            self._fd = None
            raise RuntimeError(
                f"unexpected Cora DSP ABI/core: abi={abi}, "
                f"core=0x{core_id:08x}, fft_length={fft_length}"
            )
        self.set_filter(self.low_bin, self.high_bin)

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def set_filter(self, low_bin: int, high_bin: int) -> None:
        if not 0 <= low_bin <= high_bin <= FFT_LENGTH // 2:
            raise ValueError("filter bins must satisfy 0 <= low <= high <= 256")
        self.low_bin = int(low_bin)
        self.high_bin = int(high_bin)
        if self._fd is not None:
            fcntl.ioctl(
                self._fd,
                _CORA_DSP_SET_FILTER,
                struct.pack("<2I", self.low_bin, self.high_bin),
            )

    def clear_stats(self) -> None:
        if self._fd is None:
            raise RuntimeError("Cora DSP device is not open")
        fcntl.ioctl(self._fd, _CORA_DSP_CLEAR_STATS)

    def status(self) -> dict[str, int]:
        if self._fd is None:
            raise RuntimeError("Cora DSP device is not open")
        raw = bytearray(48)
        fcntl.ioctl(self._fd, _CORA_DSP_GET_STATUS, raw, True)
        low, high, ready, _reserved, frames, failures, elapsed, filtered = (
            struct.unpack("<4I4Q", raw)
        )
        return {
            "low_bin": low,
            "high_bin": high,
            "result_ready": ready,
            "completed_frames": frames,
            "failed_frames": failures,
            "accelerator_time_ns": elapsed,
            "filtered_bins": filtered,
        }

    def process_i16(self, samples: numpy.ndarray) -> numpy.ndarray:
        if self._fd is None:
            raise RuntimeError("Cora DSP device is not open")
        frame = numpy.asarray(samples, dtype="<i2")
        if frame.ndim != 1 or frame.size != FFT_LENGTH:
            raise ValueError("Cora DSP requires exactly 512 signed-16 samples")
        frame = numpy.ascontiguousarray(frame)
        _write_all(self._fd, memoryview(frame).cast("B"))
        result = _read_exact(self._fd, FFT_LENGTH * 2)
        return numpy.frombuffer(result, dtype="<i2").copy()


class hw_fft_filter(gr.sync_block):
    """GNU Radio float-stream wrapper around the 512-point FPGA accelerator."""

    def __init__(
        self,
        device_path: str = "/dev/cora-dsp0",
        low_bin: int = 0,
        high_bin: int = 256,
    ):
        gr.sync_block.__init__(
            self,
            name="cora_hw_fft_filter",
            in_sig=[numpy.float32],
            out_sig=[numpy.float32],
        )
        self._device = HwFftFilterDevice(device_path, low_bin, high_bin)
        self.set_output_multiple(FFT_LENGTH)

    def start(self):
        self._device.open()
        return True

    def stop(self):
        self._device.close()
        return True

    def work(self, input_items, output_items):
        available = min(len(input_items[0]), len(output_items[0]))
        count = available - (available % FFT_LENGTH)
        for offset in range(0, count, FFT_LENGTH):
            source = input_items[0][offset : offset + FFT_LENGTH]
            fixed = numpy.rint(
                numpy.clip(source, -1.0, 1.0) * 32767.0
            ).astype("<i2")
            result = self._device.process_i16(fixed)
            output_items[0][offset : offset + FFT_LENGTH] = (
                result.astype(numpy.float32) / 32768.0
            )
        return count
