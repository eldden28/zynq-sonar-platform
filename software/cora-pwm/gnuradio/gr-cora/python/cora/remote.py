# SPDX-License-Identifier: GPL-3.0-or-later
"""Network primitives for the Cora Remote I/O v1 transport.

The transport carries a contiguous stream of IEEE-754, little-endian float32
samples normalized to -1.0 through +1.0. ARMv7 Cora boards and ordinary x86
GNU Radio hosts are little-endian, so GNU Radio's stock TCP blocks interoperate
without a conversion block.
"""

from __future__ import annotations

import select
import socket
import os

try:
    import numpy
    from gnuradio import blocks, gr
except ImportError:
    numpy = None
    blocks = None
    gr = None


def _require_gnuradio() -> None:
    if gr is None or numpy is None:
        raise RuntimeError("GNU Radio and NumPy are required for TCP sample sources")


class tcp_float_source(gr.sync_block if gr is not None else object):
    """Serve one Cora Remote I/O float32 input connection at a time.

    The listener remains quiet until a client connects. After a connected
    client disconnects, ``work`` returns -1 so the containing flowgraph stops;
    that is important for PWM applications because stopping the Cora PWM sink
    also stops its DMA engine and disables the output.
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 50001):
        _require_gnuradio()
        if not 1 <= int(port) <= 65535:
            raise ValueError("TCP port must be between 1 and 65535")
        gr.sync_block.__init__(self, name="cora_tcp_float_source", in_sig=None,
                               out_sig=[numpy.float32])
        self._host = host
        self._port = int(port)
        self._listener = None
        self._client = None
        self._received_client = False
        self._pending = b""

    def start(self):
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind((self._host, self._port))
        self._listener.listen(1)
        self._listener.setblocking(False)
        self._received_client = False
        self._pending = b""
        return True

    def stop(self):
        self._close_client()
        if self._listener is not None:
            self._listener.close()
            self._listener = None
        self._pending = b""
        return True

    def _close_client(self):
        if self._client is not None:
            self._client.close()
            self._client = None

    def _accept_client(self) -> bool:
        if self._client is not None:
            return True
        readable, _, _ = select.select([self._listener], [], [], 0.05)
        if not readable:
            return False
        client, _address = self._listener.accept()
        client.setblocking(False)
        self._client = client
        self._received_client = True
        return True

    def work(self, input_items, output_items):
        if not self._accept_client():
            return 0

        try:
            received = self._client.recv(len(output_items[0]) * 4)
        except BlockingIOError:
            return 0
        except OSError:
            received = b""

        if not received:
            self._close_client()
            return -1 if self._received_client else 0

        payload = self._pending + received
        item_count = min(len(payload) // 4, len(output_items[0]))
        byte_count = item_count * 4
        if not item_count:
            self._pending = payload
            return 0
        output_items[0][:item_count] = numpy.frombuffer(payload[:byte_count], dtype="<f4")
        self._pending = payload[byte_count:]
        return item_count


class tcp_float_sink(gr.hier_block2 if gr is not None else object):
    """Connect a GNU Radio float stream to a Cora Remote I/O TCP endpoint."""

    def __init__(self, host: str, port: int):
        _require_gnuradio()
        if not 1 <= int(port) <= 65535:
            raise ValueError("TCP port must be between 1 and 65535")
        gr.hier_block2.__init__(
            self,
            "cora_tcp_float_sink",
            gr.io_signature(1, 1, gr.sizeof_float),
            gr.io_signature(0, 0, 0),
        )
        self._socket = socket.create_connection((host, int(port)), timeout=5)
        self._sink = blocks.file_descriptor_sink(gr.sizeof_float,
                                                  os.dup(self._socket.fileno()))
        self.connect(self, self._sink)
