#!/usr/bin/env python3
"""PC-side live plotter for cora-ofdm UDP complex monitoring datagrams."""

from __future__ import annotations

import argparse
import socket
import struct
import time

import matplotlib.pyplot as plt
import numpy as np


MAGIC = b"COFM"
VERSION = 1
HEADER = struct.Struct("<4sBBHIIIf")
NAMES = {0: "TX", 1: "RX"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Display optional TX/RX complex streams from cora-ofdm."
    )
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=7355)
    parser.add_argument("--window", type=int, default=4096)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind((args.bind, args.port))
    receiver.setblocking(False)
    buffers = {
        0: np.zeros(args.window, dtype=np.complex64),
        1: np.zeros(args.window, dtype=np.complex64),
    }

    plt.ion()
    figure, axes = plt.subplots(2, 2, figsize=(12, 7))
    lines = {}
    for stream in (0, 1):
        lines[(stream, "time")], = axes[stream, 0].plot(
            np.zeros(args.window).real, linewidth=0.8
        )
        lines[(stream, "spectrum")], = axes[stream, 1].plot(
            np.zeros(args.window).real, linewidth=0.8
        )
        axes[stream, 0].set_title(f"{NAMES[stream]} real waveform")
        axes[stream, 1].set_title(f"{NAMES[stream]} spectrum")
        axes[stream, 0].set_ylim(-2.5, 2.5)
        axes[stream, 1].set_ylim(-90, 10)
        axes[stream, 0].grid(True, alpha=0.2)
        axes[stream, 1].grid(True, alpha=0.2)
    figure.tight_layout()

    print(f"Listening for cora-ofdm UDP monitoring on {args.bind}:{args.port}")
    last_draw = 0.0
    sample_rate = 48_000.0
    while plt.fignum_exists(figure.number):
        received_any = False
        while True:
            try:
                datagram, _ = receiver.recvfrom(65535)
            except BlockingIOError:
                break
            if len(datagram) < HEADER.size:
                continue
            magic, version, stream, _, _, _, _, sample_rate = HEADER.unpack_from(
                datagram
            )
            if magic != MAGIC or version != VERSION or stream not in buffers:
                continue
            samples = np.frombuffer(
                datagram, dtype="<c8", offset=HEADER.size
            ).copy()
            if samples.size:
                keep = min(samples.size, args.window)
                buffers[stream] = np.roll(buffers[stream], -keep)
                buffers[stream][-keep:] = samples[-keep:]
                received_any = True

        now = time.monotonic()
        if received_any and now - last_draw >= 0.1:
            for stream, samples in buffers.items():
                lines[(stream, "time")].set_ydata(samples.real)
                spectrum = np.fft.fftshift(np.fft.fft(samples))
                magnitude = 20.0 * np.log10(
                    np.maximum(np.abs(spectrum), 1e-9)
                )
                magnitude -= np.max(magnitude)
                lines[(stream, "spectrum")].set_ydata(magnitude)
                frequency = np.fft.fftshift(
                    np.fft.fftfreq(samples.size, 1.0 / sample_rate)
                )
                lines[(stream, "spectrum")].set_xdata(frequency / 1000.0)
                axes[stream, 1].set_xlim(
                    frequency[0] / 1000.0, frequency[-1] / 1000.0
                )
                axes[stream, 1].set_xlabel("Frequency (kHz)")
            figure.canvas.draw_idle()
            last_draw = now
        plt.pause(0.02)
    receiver.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
