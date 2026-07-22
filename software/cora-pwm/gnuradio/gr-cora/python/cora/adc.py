# SPDX-License-Identifier: GPL-3.0-or-later
"""ADC sources with a common normalized-float GNU Radio interface.

``iio_adc_source`` is for on-chip or external converters that expose the
Linux IIO sysfs ABI.  ``dma_s16_source`` is the matching high-rate transport
for a future AXI-stream converter captured by the Cora ADC DMA driver.  Both
produce floats in the range -1.0 through +1.0, so downstream flowgraphs do
not depend on the physical converter.
"""

from __future__ import annotations

import errno
import os
import time
from pathlib import Path

try:
    import numpy
    from gnuradio import gr
except ImportError:  # Allows IioAdc to be unit-tested without GNU Radio.
    numpy = None
    gr = None


class IioAdc:
    """Read and normalize one voltage channel from the Linux IIO sysfs ABI."""

    def __init__(
        self,
        device_path: str = "/sys/bus/iio/devices/iio:device0",
        channel: str = "auto",
        full_scale_volts: float = 3.3,
        scale_multiplier: float = 1.0,
    ):
        if full_scale_volts <= 0:
            raise ValueError("full_scale_volts must be positive")
        if scale_multiplier <= 0:
            raise ValueError("scale_multiplier must be positive")

        self.device_path = Path(device_path)
        self.channel = self._resolve_channel(channel)
        self.full_scale_volts = float(full_scale_volts)
        # IIO voltage scale attributes are expressed in millivolts per raw
        # count.  This optional multiplier compensates a board-level analog
        # divider (Cora A0 uses 3.3); the mandatory mV-to-V conversion is
        # applied in read_voltage().
        self.scale_multiplier = float(scale_multiplier)

    @property
    def _prefix(self) -> str:
        return f"in_{self.channel}"

    def _resolve_channel(self, channel: str) -> str:
        """Resolve ``auto`` to the first generic XADC auxiliary voltage input.

        XADC's built-in telemetry channels have descriptive names such as
        ``in_voltage0_vccint_raw``. Device-tree-selected VAUX channels use
        the generic ``in_voltageN_raw`` form, but N differs across kernel
        versions (the current Cora image exposes A0 as voltage8).
        """
        channel = channel.removeprefix("in_")
        if channel != "auto":
            return channel

        candidates = []
        for raw_path in self.device_path.glob("in_voltage*_raw"):
            stem = raw_path.name.removesuffix("_raw").removeprefix("in_")
            if stem.startswith("voltage") and stem[7:].isdigit():
                candidates.append(stem)
        if not candidates:
            raise RuntimeError(
                f"no generic IIO voltage input found below {self.device_path}; "
                "pass --channel with the desired IIO channel"
            )
        return min(candidates, key=lambda item: int(item[7:]))

    @staticmethod
    def _read_number(path: Path) -> float:
        try:
            return float(path.read_text(encoding="ascii").strip())
        except FileNotFoundError as error:
            raise RuntimeError(f"IIO attribute is missing: {path}") from error
        except ValueError as error:
            raise RuntimeError(f"IIO attribute is not numeric: {path}") from error

    def _read_optional(self, suffix: str, default: float) -> float:
        channel_path = self.device_path / f"{self._prefix}_{suffix}"
        if channel_path.exists():
            return self._read_number(channel_path)
        generic_path = self.device_path / f"in_voltage_{suffix}"
        if generic_path.exists():
            return self._read_number(generic_path)
        return default

    def read_voltage(self) -> float:
        """Return volts after the optional board-level scaling adjustment."""
        raw = self._read_number(self.device_path / f"{self._prefix}_raw")
        offset = self._read_optional("offset", 0.0)
        scale = self._read_optional("scale", 1.0)
        return (raw + offset) * scale * 0.001 * self.scale_multiplier

    def read_normalized(self) -> float:
        """Map 0..full_scale_volts into the PWM-friendly range -1..+1."""
        fraction = self.read_voltage() / self.full_scale_volts
        return max(-1.0, min(1.0, 2.0 * fraction - 1.0))


def _require_gnuradio() -> None:
    if gr is None or numpy is None:
        raise RuntimeError("GNU Radio and NumPy are required for this ADC source")


class iio_adc_source(gr.sync_block if gr is not None else object):
    """Repeat the latest IIO ADC measurement at the downstream sample rate."""

    def __init__(
        self,
        device_path: str = "/sys/bus/iio/devices/iio:device0",
        channel: str = "auto",
        full_scale_volts: float = 3.3,
        poll_period_ms: int = 100,
        scale_multiplier: float = 1.0,
    ):
        _require_gnuradio()
        if poll_period_ms <= 0:
            raise ValueError("poll_period_ms must be positive")
        gr.sync_block.__init__(self, name="cora_iio_adc_source", in_sig=None,
                               out_sig=[numpy.float32])
        self._adc = IioAdc(device_path, channel, full_scale_volts, scale_multiplier)
        self._poll_period_s = poll_period_ms / 1000.0
        self._next_poll = 0.0
        self._value = 0.0

    def start(self):
        self._value = self._adc.read_normalized()
        self._next_poll = time.monotonic() + self._poll_period_s
        return True

    def work(self, input_items, output_items):
        now = time.monotonic()
        if now >= self._next_poll:
            self._value = self._adc.read_normalized()
            self._next_poll = now + self._poll_period_s
        output_items[0].fill(self._value)
        return len(output_items[0])


class dma_s16_source(gr.sync_block if gr is not None else object):
    """Read signed-16 little-endian samples from the standard Cora ADC DMA node.

    The external-converter DMA ABI is deliberately simple: `/dev/cora-adc0`
    supplies contiguous signed 16-bit little-endian samples.  This source
    normalizes them into -1..+1 just like ``iio_adc_source``.
    """

    def __init__(self, device_path: str = "/dev/cora-adc0", scale: float = 1.0 / 32768.0):
        _require_gnuradio()
        if scale <= 0:
            raise ValueError("scale must be positive")
        gr.sync_block.__init__(self, name="cora_dma_s16_source", in_sig=None,
                               out_sig=[numpy.float32])
        self._device_path = device_path
        self._scale = float(scale)
        self._fd = None

    def start(self):
        self._fd = os.open(self._device_path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        return True

    def stop(self):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        return True

    def work(self, input_items, output_items):
        try:
            data = os.read(self._fd, len(output_items[0]) * 2)
        except BlockingIOError as error:
            if error.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                return 0
            raise
        if not data:
            return -1
        if len(data) % 2:
            raise RuntimeError("Cora ADC DMA returned an odd number of bytes")
        samples = numpy.frombuffer(data, dtype="<i2")
        output_items[0][: len(samples)] = samples * self._scale
        return len(samples)


def adc_source(backend: str = "iio", **kwargs):
    """Create an ADC source using the normalized-float Cora ADC contract."""
    if backend == "iio":
        return iio_adc_source(**kwargs)
    if backend == "dma-s16":
        return dma_s16_source(**kwargs)
    raise ValueError("backend must be 'iio' or 'dma-s16'")
