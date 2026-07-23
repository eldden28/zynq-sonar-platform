#!/usr/bin/python3
"""Minimal authenticated WebSocket/PTY integration probe for a running dashboard."""

import argparse
import base64
import json
import os
import socket
import struct
import time


def masked_frame(payload: str) -> bytes:
    encoded = payload.encode("utf-8")
    mask = os.urandom(4)
    header = bytearray([0x81])
    if len(encoded) < 126:
        header.append(0x80 | len(encoded))
    else:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", len(encoded)))
    return bytes(header) + mask + bytes(
        value ^ mask[index % 4] for index, value in enumerate(encoded)
    )


def receive_frame(connection: socket.socket) -> str:
    header = connection.recv(2)
    if len(header) != 2:
        raise RuntimeError("short WebSocket header")
    length = header[1] & 0x7F
    if length == 126:
        length = struct.unpack("!H", connection.recv(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", connection.recv(8))[0]
    payload = bytearray()
    while len(payload) < length:
        payload.extend(connection.recv(length - len(payload)))
    return bytes(payload).decode("utf-8", "replace")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--user", default="cora")
    parser.add_argument("--token-file", required=True)
    args = parser.parse_args()

    token = open(args.token_file, encoding="ascii").read().strip()
    authorization = base64.b64encode(f"{args.user}:{token}".encode()).decode()
    websocket_key = base64.b64encode(os.urandom(16)).decode()
    request = (
        "GET /ws/terminal HTTP/1.1\r\n"
        f"Host: {args.host}:{args.port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {websocket_key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        f"Authorization: Basic {authorization}\r\n\r\n"
    )
    marker = "CORA_WEB_TERMINAL_PASS"
    with socket.create_connection((args.host, args.port), timeout=3) as connection:
        connection.sendall(request.encode("ascii"))
        response = connection.recv(4096)
        if not response.startswith(b"HTTP/1.1 101"):
            raise RuntimeError(response.decode("utf-8", "replace"))
        connection.sendall(
            masked_frame(json.dumps({"type": "input", "data": f"printf '{marker}\\n'\r"}))
        )
        deadline = time.monotonic() + 5
        received = ""
        while marker not in received and time.monotonic() < deadline:
            message = json.loads(receive_frame(connection))
            received += message.get("data", "")
        if marker not in received:
            raise RuntimeError(f"terminal marker not received: {received!r}")
    print("WebSocket PTY terminal: PASS")


if __name__ == "__main__":
    main()
