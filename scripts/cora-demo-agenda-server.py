#!/usr/bin/env python3
"""Serve the Cora demo agenda with a live, read-only service probe."""

from __future__ import annotations

import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parent
SERVICES = (
    ("Forward-looking sonar", 8085, "Beamformed time-series scene simulation"),
    ("OFDM text modem", 8082, "CRC-protected acoustic transfer and PL loopback"),
    ("Conventional modem", 8084, "BPSK, QPSK, FSK, and MFSK"),
    ("JANUS interoperability", 8086, "Open first-contact packet implementation"),
    ("Navigation DVL", 8083, "Mission navigation and fused state"),
    ("Navigation acoustic", 8087, "USBL and acoustic mission view"),
)


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/api/services":
            values = []
            for name, port, description in SERVICES:
                try:
                    with urlopen(f"http://192.168.10.2:{port}/", timeout=2):
                        up = True
                except OSError:
                    up = False
                values.append({"name": name, "port": port, "description": description, "up": up})
            payload = json.dumps({"board": "Cora Z7-10", "services": values}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        return super().do_GET()


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 8766), Handler)
    print("http://127.0.0.1:8766/cora-demo-agenda.html")
    server.serve_forever()


if __name__ == "__main__":
    main()
