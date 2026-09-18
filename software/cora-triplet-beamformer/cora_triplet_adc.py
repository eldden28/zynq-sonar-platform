#!/usr/bin/env python3
"""ADC input sources for the Cora triplet bearing-time record."""

from __future__ import annotations

from dataclasses import dataclass
import errno
import os
from pathlib import Path
import select
import stat
import threading
import time

import numpy as np


AXI_CHANNEL_COUNT = 21
AXI_FRAME_SAMPLES = 512
AXI_WORD_BYTES = 4
ADC_BITS = 24


class AdcSourceStopped(RuntimeError):
    """Raised when an acquisition read is cancelled during shutdown."""


@dataclass(frozen=True)
class AxiStreamLayout:
    """Fixed sample-major layout produced by the AD4630 AXI source."""

    channel_count: int = AXI_CHANNEL_COUNT
    frame_samples: int = AXI_FRAME_SAMPLES
    word_bytes: int = AXI_WORD_BYTES
    adc_bits: int = ADC_BITS

    def validate(self, block_size: int) -> None:
        if self.channel_count != AXI_CHANNEL_COUNT:
            raise ValueError("the beamformer AXI source requires exactly 21 channels")
        if self.frame_samples <= 0:
            raise ValueError("AXI frame_samples must be positive")
        if self.word_bytes != AXI_WORD_BYTES:
            raise ValueError("the AXI source requires one 32-bit word per sample")
        if self.adc_bits != ADC_BITS:
            raise ValueError("the AXI source expects sign-extended 24-bit ADC samples")
        if block_size <= 0 or block_size % self.frame_samples:
            raise ValueError("beamformer block_size must be a multiple of AXI frame_samples")

    @property
    def frame_words(self) -> int:
        return self.channel_count * self.frame_samples

    @property
    def frame_bytes(self) -> int:
        return self.frame_words * self.word_bytes


class AxiDmaAdcSource:
    """Read fixed AXI DMA packets from a character device or replay file.

    The device contract is one complete, sample-major 512 x 21 packet per
    43,008-byte read. Each ADC value is a signed 24-bit two's-complement sample
    sign-extended into a little-endian 32-bit word. Partial reads are accepted
    and assembled, while packet truncation, timeouts, and bad sign extension
    are reported instead of silently substituting synthetic samples.
    """

    mode = "axi"
    sample_bytes = AXI_WORD_BYTES
    full_scale = float(1 << (ADC_BITS - 1))

    def __init__(
        self,
        device: str | Path,
        block_size: int = 1024,
        *,
        layout: AxiStreamLayout | None = None,
        read_timeout_s: float = 1.0,
        loop_file: bool = False,
        verify_sign_extension: bool = True,
    ):
        self.device = Path(device)
        self.block_size = int(block_size)
        self.layout = layout or AxiStreamLayout()
        self.layout.validate(self.block_size)
        if read_timeout_s <= 0.0:
            raise ValueError("AXI read timeout must be positive")
        self.read_timeout_s = float(read_timeout_s)
        self.loop_file = bool(loop_file)
        self.verify_sign_extension = bool(verify_sign_extension)
        self.frames_per_block = self.block_size // self.layout.frame_samples
        self.fd: int | None = None
        self.regular_file = False
        self.frames_received = 0
        self.bytes_received = 0
        self.short_reads = 0
        self.last_read_monotonic: float | None = None

    def open(self) -> None:
        if self.fd is not None:
            return
        flags = os.O_RDONLY | os.O_NONBLOCK
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        self.fd = os.open(self.device, flags)
        self.regular_file = stat.S_ISREG(os.fstat(self.fd).st_mode)

    @property
    def externally_paced(self) -> bool:
        return not self.regular_file

    def close(self) -> None:
        fd, self.fd = self.fd, None
        if fd is not None:
            os.close(fd)

    def reset_stats(self) -> None:
        self.frames_received = 0
        self.bytes_received = 0
        self.short_reads = 0
        self.last_read_monotonic = None

    def _wait_readable(
        self, fd: int, deadline: float, stop_event: threading.Event | None
    ) -> None:
        while True:
            if stop_event is not None and stop_event.is_set():
                raise AdcSourceStopped("AXI acquisition stopped")
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                raise TimeoutError(
                    f"timed out waiting for AXI data from {self.device}"
                )
            readable, _, _ = select.select([fd], [], [], min(remaining, 0.05))
            if readable:
                return

    def _rewind_replay_file(self, fd: int) -> bool:
        if not self.loop_file or not stat.S_ISREG(os.fstat(fd).st_mode):
            return False
        if os.lseek(fd, 0, os.SEEK_SET) != 0:
            raise OSError("failed to rewind AXI replay file")
        return True

    def _read_exact(
        self, byte_count: int, stop_event: threading.Event | None
    ) -> bytes:
        self.open()
        assert self.fd is not None
        deadline = time.monotonic() + self.read_timeout_s
        chunks: list[bytes] = []
        received = 0
        while received < byte_count:
            if stop_event is not None and stop_event.is_set():
                raise AdcSourceStopped("AXI acquisition stopped")
            try:
                chunk = os.read(self.fd, byte_count - received)
            except BlockingIOError:
                self._wait_readable(self.fd, deadline, stop_event)
                continue
            except OSError as error:
                if error.errno == errno.EINTR:
                    continue
                raise
            if not chunk:
                if self._rewind_replay_file(self.fd):
                    deadline = time.monotonic() + self.read_timeout_s
                    continue
                raise EOFError(
                    f"AXI source {self.device} ended after {received} of "
                    f"{byte_count} packet bytes"
                )
            if len(chunk) < byte_count - received:
                self.short_reads += 1
            chunks.append(chunk)
            received += len(chunk)
            self.bytes_received += len(chunk)
        return b"".join(chunks)

    def read_block(self, stop_event: threading.Event | None = None) -> np.ndarray:
        packets = [
            self._read_exact(self.layout.frame_bytes, stop_event)
            for _ in range(self.frames_per_block)
        ]
        words = np.frombuffer(b"".join(packets), dtype="<i4")
        expected_words = self.block_size * self.layout.channel_count
        if words.size != expected_words:
            raise ValueError(
                f"AXI block has {words.size} words; expected {expected_words}"
            )
        if self.verify_sign_extension:
            minimum = -(1 << (self.layout.adc_bits - 1))
            maximum = (1 << (self.layout.adc_bits - 1)) - 1
            invalid = np.flatnonzero((words < minimum) | (words > maximum))
            if invalid.size:
                index = int(invalid[0])
                raw = int(words[index]) & 0xFFFFFFFF
                raise ValueError(
                    "AXI sample is not sign-extended 24-bit data: "
                    f"word {index} is 0x{raw:08x}"
                )
        self.frames_received += self.frames_per_block
        self.last_read_monotonic = time.monotonic()
        return words.reshape(self.block_size, self.layout.channel_count).copy()

    def status(self, sample_rate_hz: int) -> dict:
        return {
            "mode": self.mode,
            "device": str(self.device),
            "sample_format": "signed-24-sign-extended-le32",
            "channel_order": "sample-major",
            "channel_count": self.layout.channel_count,
            "frame_samples": self.layout.frame_samples,
            "frame_bytes": self.layout.frame_bytes,
            "frames_per_block": self.frames_per_block,
            "word_bytes": self.layout.word_bytes,
            "payload_mb_s": round(
                self.layout.channel_count
                * sample_rate_hz
                * self.layout.word_bytes
                / 1_000_000.0,
                3,
            ),
            "verify_sign_extension": self.verify_sign_extension,
            "frames_received": self.frames_received,
            "bytes_received": self.bytes_received,
            "short_reads": self.short_reads,
        }

    def __enter__(self) -> "AxiDmaAdcSource":
        self.open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
