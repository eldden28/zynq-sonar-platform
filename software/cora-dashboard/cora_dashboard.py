#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Authenticated Cora hardware dashboard and PTY-backed web terminal."""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import hmac
import http.server
import json
import os
from pathlib import Path
import pty
import pwd
import re
import secrets
import select
import signal
import socket
import struct
import subprocess
import threading
import time
from urllib.parse import urlparse


VERSION = "1.1.0"
MAX_REQUEST = 4096
MAX_WS_MESSAGE = 65536
SIOCGIFADDR = 0x8915

# External channels are appended by the Xilinx XADC IIO driver in device-tree
# order.  system-user.dtsi deliberately uses this order so the otherwise
# generic Linux voltage indices have stable, board-facing names.
CORA_XADC_USER_CHANNELS = {
    9: ("A0", "VAUX1", False),
    10: ("A1", "VAUX9", False),
    11: ("A2", "VAUX6", False),
    12: ("A3", "VAUX15", False),
    13: ("A4", "VAUX5", False),
    14: ("A5", "VAUX13", False),
    15: ("A6-A7", "VAUX12", True),
    16: ("A8-A9", "VAUX0", True),
    17: ("A10-A11", "VAUX8", True),
    18: ("VP-VN", "VP/VN", True),
}


def read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="ascii", errors="replace").strip()
    except (OSError, ValueError):
        return default


def read_int(path: Path, default: int = 0) -> int:
    try:
        return int(read_text(path))
    except ValueError:
        return default


def format_cmdline(path: Path) -> str:
    try:
        return path.read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").strip()
    except OSError:
        return ""


class DashboardState:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.token = self._load_or_create_token(Path(args.token_file))
        self.session_cookie = hmac.new(
            self.token.encode("utf-8"), b"cora-dashboard-session", hashlib.sha256
        ).hexdigest()
        self.metrics_lock = threading.Lock()
        self.previous_cpu = None
        self.previous_network = {}
        self.terminal_slots = threading.BoundedSemaphore(args.max_terminals)

    def _load_or_create_token(self, path: Path) -> str:
        token = read_text(path)
        if token:
            return token
        path.parent.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(24)
        temporary = path.with_suffix(".new")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="ascii") as token_file:
            token_file.write(token + "\n")
        os.replace(temporary, path)
        try:
            account = pwd.getpwnam(self.args.terminal_user)
            if os.geteuid() == 0:
                os.chown(path, 0, account.pw_gid)
                os.chmod(path, 0o640)
            else:
                os.chmod(path, 0o600)
        except (KeyError, OSError):
            os.chmod(path, 0o600)
        print(f"Cora dashboard credential created at {path}", flush=True)
        return token

    def authenticated(self, headers) -> tuple[bool, bool]:
        cookie_header = headers.get("Cookie", "")
        for item in cookie_header.split(";"):
            name, separator, value = item.strip().partition("=")
            if separator and name == "cora_session" and hmac.compare_digest(
                value, self.session_cookie
            ):
                return True, False

        authorization = headers.get("Authorization", "")
        if not authorization.startswith("Basic "):
            return False, False
        try:
            decoded = base64.b64decode(authorization[6:], validate=True).decode("utf-8")
            username, password = decoded.split(":", 1)
        except (ValueError, UnicodeError):
            return False, False
        valid = hmac.compare_digest(username, self.args.auth_user) and hmac.compare_digest(
            password, self.token
        )
        return valid, valid

    def _cpu(self) -> dict:
        fields = read_text(Path("/proc/stat")).splitlines()
        values = []
        if fields and fields[0].startswith("cpu "):
            try:
                values = [int(value) for value in fields[0].split()[1:]]
            except ValueError:
                values = []
        total = sum(values)
        idle = sum(values[3:5]) if len(values) >= 5 else 0
        percent = 0.0
        with self.metrics_lock:
            if self.previous_cpu and total > self.previous_cpu[0]:
                delta_total = total - self.previous_cpu[0]
                delta_idle = idle - self.previous_cpu[1]
                percent = 100.0 * max(0, delta_total - delta_idle) / delta_total
            self.previous_cpu = (total, idle)
        return {"percent": round(percent, 1), "cores": os.cpu_count() or 1}

    @staticmethod
    def _memory() -> dict:
        values = {}
        for line in read_text(Path("/proc/meminfo")).splitlines():
            key, separator, value = line.partition(":")
            if separator:
                try:
                    values[key] = int(value.split()[0]) * 1024
                except (ValueError, IndexError):
                    pass
        total = values.get("MemTotal", 0)
        available = values.get("MemAvailable", values.get("MemFree", 0))
        return {
            "total_bytes": total,
            "available_bytes": available,
            "used_percent": round(100.0 * (total - available) / total, 1) if total else 0,
        }

    @staticmethod
    def _ipv4(interface: str) -> str:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as control:
                request = struct.pack("256s", interface.encode("ascii")[:15])
                response = fcntl.ioctl(control.fileno(), SIOCGIFADDR, request)
                return socket.inet_ntoa(response[20:24])
        except OSError:
            return ""

    def _network(self) -> list[dict]:
        now = time.monotonic()
        result = []
        network_root = Path("/sys/class/net")
        for interface_path in sorted(network_root.glob("*")):
            name = interface_path.name
            if name == "lo":
                continue
            rx_bytes = read_int(interface_path / "statistics/rx_bytes")
            tx_bytes = read_int(interface_path / "statistics/tx_bytes")
            rx_rate = tx_rate = 0.0
            with self.metrics_lock:
                previous = self.previous_network.get(name)
                if previous and now > previous[0]:
                    elapsed = now - previous[0]
                    rx_rate = max(0, rx_bytes - previous[1]) / elapsed
                    tx_rate = max(0, tx_bytes - previous[2]) / elapsed
                self.previous_network[name] = (now, rx_bytes, tx_bytes)
            result.append(
                {
                    "name": name,
                    "address": self._ipv4(name),
                    "mac": read_text(interface_path / "address"),
                    "state": read_text(interface_path / "operstate", "unknown"),
                    "carrier": read_text(interface_path / "carrier", "0") == "1",
                    "speed_mbps": read_int(interface_path / "speed"),
                    "rx_bytes": rx_bytes,
                    "tx_bytes": tx_bytes,
                    "rx_bytes_per_second": round(rx_rate),
                    "tx_bytes_per_second": round(tx_rate),
                }
            )
        return result

    @staticmethod
    def _thermal() -> list[dict]:
        result = []
        for zone in sorted(Path("/sys/class/thermal").glob("thermal_zone*")):
            raw = read_int(zone / "temp", -1)
            if raw >= 0:
                result.append(
                    {
                        "name": read_text(zone / "type", zone.name),
                        "celsius": round(raw / 1000.0, 1),
                    }
                )
        return result

    @staticmethod
    def _fpga() -> dict:
        manager = Path("/sys/class/fpga_manager/fpga0")
        return {
            "present": manager.exists(),
            "state": read_text(manager / "state", "unavailable"),
            "name": read_text(manager / "name", "fpga0"),
        }

    @staticmethod
    def _iio() -> list[dict]:
        result = []
        for device in sorted(Path("/sys/bus/iio/devices").glob("iio:device*")):
            channels = []
            device_name = read_text(device / "name")

            def voltage_index(path: Path) -> int:
                match = re.match(r"in_voltage(\d+)", path.name)
                return int(match.group(1)) if match else 1 << 30

            for raw_path in sorted(device.glob("in_voltage*_raw"), key=voltage_index):
                prefix = raw_path.name[:-4]
                raw = read_int(raw_path)
                index = voltage_index(raw_path)
                user = CORA_XADC_USER_CHANNELS.get(index) if device_name == "xadc" else None
                channels.append(
                    {
                        "channel": prefix.removeprefix("in_"),
                        "raw": raw,
                        "raw_12bit": raw & 0xFFF,
                        "scale": read_text(device / f"{prefix}_scale"),
                        "label": user[0] if user else prefix.removeprefix("in_"),
                        "physical_channel": user[1] if user else "",
                        "user_accessible": bool(user),
                        "bipolar": user[2] if user else False,
                        "bits": 12 if device_name == "xadc" else None,
                    }
                )
            result.append(
                {"device": device.name, "name": device_name, "channels": channels}
            )
        return result

    @staticmethod
    def _pwm_path() -> Path | None:
        candidate = Path("/sys/class/misc/cora-pwm0/device")
        try:
            return candidate.resolve(strict=True)
        except OSError:
            return None

    def _pwm(self) -> dict:
        path = self._pwm_path()
        if path is None:
            return {"present": False}
        values = {"present": True, "sysfs_path": str(path)}
        for name in (
            "running",
            "opened",
            "faulted",
            "period_ticks",
            "queued_periods",
            "fifo_level",
            "active_period",
            "active_duty",
            "pwm_underruns",
            "dma_periods",
            "driver_underruns",
            "accepted_samples",
        ):
            values[name] = read_int(path / name)
        period = values.get("active_period", 0)
        duty = values.get("active_duty", 0)
        values["duty_percent"] = round(100.0 * duty / period, 2) if period else 0.0
        return values

    @staticmethod
    def _remote_pids() -> list[int]:
        pids = []
        own_pid = os.getpid()
        for process in Path("/proc").glob("[0-9]*"):
            try:
                pid = int(process.name)
            except ValueError:
                continue
            if pid == own_pid:
                continue
            command = format_cmdline(process / "cmdline")
            if "cora-remote-io" in command:
                pids.append(pid)
        return sorted(pids)

    def snapshot(self) -> dict:
        uptime_text = read_text(Path("/proc/uptime"), "0")
        try:
            uptime = float(uptime_text.split()[0])
        except (ValueError, IndexError):
            uptime = 0.0
        try:
            load = [round(value, 2) for value in os.getloadavg()]
        except OSError:
            load = [0.0, 0.0, 0.0]
        pids = self._remote_pids()
        return {
            "version": VERSION,
            "time": int(time.time()),
            "system": {
                "hostname": socket.gethostname(),
                "uptime_seconds": round(uptime),
                "load_average": load,
                "cpu": self._cpu(),
                "memory": self._memory(),
                "thermal": self._thermal(),
            },
            "network": self._network(),
            "fpga": self._fpga(),
            "iio": self._iio(),
            "pwm": self._pwm(),
            "remote_io": {"running": bool(pids), "pids": pids},
        }

    def _start_remote(self) -> str:
        if self._remote_pids():
            return "Cora Remote I/O is already running"
        log_path = Path("/var/log/cora-remote-io.log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = open(log_path, "ab", buffering=0)
        try:
            subprocess.Popen(
                ["/usr/bin/cora-remote-io"],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
        finally:
            log.close()
        return "Cora Remote I/O start requested"

    def _stop_remote(self) -> str:
        pids = self._remote_pids()
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        return f"Stop requested for {len(pids)} Cora Remote I/O process(es)"

    def _pwm_write(self, attribute: str) -> str:
        path = self._pwm_path()
        if path is None:
            raise RuntimeError("Cora PWM device is not present")
        (path / attribute).write_text("1\n", encoding="ascii")
        return f"PWM {attribute.replace('_', ' ')} requested"

    def control(self, action: str) -> str:
        actions = {
            "remote_start": self._start_remote,
            "remote_stop": self._stop_remote,
            "pwm_stop": lambda: self._pwm_write("stop"),
            "pwm_clear_stats": lambda: self._pwm_write("clear_stats"),
        }
        if action in actions:
            message = actions[action]()
            print(f"dashboard control: {action}", flush=True)
            return message
        if action in ("reboot", "poweroff"):
            command = "/sbin/reboot" if action == "reboot" else "/sbin/poweroff"
            threading.Timer(
                0.75,
                lambda: subprocess.Popen(
                    [command], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, close_fds=True
                ),
            ).start()
            print(f"dashboard control: {action}", flush=True)
            return f"System {action} requested"
        raise ValueError(f"Unknown control action: {action}")


class CoraHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, state: DashboardState):
        self.state = state
        super().__init__(address, handler)


class DashboardHandler(http.server.BaseHTTPRequestHandler):
    server_version = f"CoraDashboard/{VERSION}"
    # Browsers require an HTTP/1.1 response for a WebSocket Upgrade (101).
    protocol_version = "HTTP/1.1"

    @property
    def state(self) -> DashboardState:
        return self.server.state

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    def _security_headers(self, set_cookie: bool = False):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; connect-src 'self' ws: wss:",
        )
        if set_cookie:
            self.send_header(
                "Set-Cookie",
                f"cora_session={self.state.session_cookie}; Path=/; HttpOnly; SameSite=Strict",
            )

    def _auth(self) -> tuple[bool, bool]:
        valid, set_cookie = self.state.authenticated(self.headers)
        if valid:
            return True, set_cookie
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Cora Z7 Dashboard"')
        self._security_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False, False

    def _bytes(self, status: int, content_type: str, payload: bytes, set_cookie=False):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self._security_headers(set_cookie)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, value, set_cookie=False):
        payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self._bytes(status, "application/json; charset=utf-8", payload, set_cookie)

    def do_GET(self):
        valid, set_cookie = self._auth()
        if not valid:
            return
        path = urlparse(self.path).path
        if path == "/ws/terminal":
            self._terminal_websocket(set_cookie)
            return
        if path in ("/", "/index.html"):
            try:
                payload = (Path(self.state.args.web_root) / "index.html").read_bytes()
            except OSError as error:
                self._json(500, {"error": str(error)}, set_cookie)
                return
            self._bytes(200, "text/html; charset=utf-8", payload, set_cookie)
            return
        if path == "/api/status":
            self._json(200, self.state.snapshot(), set_cookie)
            return
        if path == "/favicon.ico":
            self._bytes(204, "image/x-icon", b"", set_cookie)
            return
        self._json(404, {"error": "Not found"}, set_cookie)

    def do_POST(self):
        valid, set_cookie = self._auth()
        if not valid:
            return
        if self.headers.get("X-Cora-Request") != "1":
            self._json(403, {"error": "Missing same-origin request header"}, set_cookie)
            return
        if urlparse(self.path).path != "/api/control":
            self._json(404, {"error": "Not found"}, set_cookie)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_REQUEST:
                raise ValueError("Invalid request size")
            request = json.loads(self.rfile.read(length))
            action = str(request["action"])
            message = self.state.control(action)
            self._json(200, {"ok": True, "message": message}, set_cookie)
        except (KeyError, ValueError, RuntimeError, OSError, json.JSONDecodeError) as error:
            self._json(400, {"ok": False, "error": str(error)}, set_cookie)

    @staticmethod
    def _ws_send(connection: socket.socket, payload: str, opcode: int = 1):
        encoded = payload.encode("utf-8")
        header = bytearray([0x80 | opcode])
        if len(encoded) < 126:
            header.append(len(encoded))
        elif len(encoded) <= 0xFFFF:
            header.append(126)
            header.extend(struct.pack("!H", len(encoded)))
        else:
            header.append(127)
            header.extend(struct.pack("!Q", len(encoded)))
        connection.sendall(header + encoded)

    @staticmethod
    def _recv_exact(connection: socket.socket, length: int) -> bytes:
        result = bytearray()
        while len(result) < length:
            chunk = connection.recv(length - len(result))
            if not chunk:
                raise ConnectionError("WebSocket closed")
            result.extend(chunk)
        return bytes(result)

    @classmethod
    def _ws_receive(cls, connection: socket.socket) -> tuple[int, bytes]:
        header = cls._recv_exact(connection, 2)
        opcode = header[0] & 0x0F
        masked = bool(header[1] & 0x80)
        length = header[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", cls._recv_exact(connection, 2))[0]
        elif length == 127:
            length = struct.unpack("!Q", cls._recv_exact(connection, 8))[0]
        if length > MAX_WS_MESSAGE:
            raise ValueError("WebSocket message is too large")
        mask = cls._recv_exact(connection, 4) if masked else b""
        payload = cls._recv_exact(connection, length)
        if masked:
            payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        return opcode, payload

    def _spawn_terminal(self) -> tuple[int, int]:
        account = pwd.getpwnam(self.state.args.terminal_user)
        pid, master = pty.fork()
        if pid:
            return pid, master
        try:
            if os.geteuid() == 0:
                os.initgroups(account.pw_name, account.pw_gid)
                os.setgid(account.pw_gid)
                os.setuid(account.pw_uid)
            elif os.geteuid() != account.pw_uid:
                raise PermissionError(
                    f"cannot switch terminal to uid {account.pw_uid} without root"
                )
            home = account.pw_dir if os.path.isdir(account.pw_dir) else "/tmp"
            os.chdir(home)
            environment = os.environ.copy()
            environment.update(
                {
                    "HOME": home,
                    "USER": account.pw_name,
                    "LOGNAME": account.pw_name,
                    "SHELL": account.pw_shell or "/bin/sh",
                    "TERM": "xterm",
                }
            )
            shell = account.pw_shell if os.path.exists(account.pw_shell) else "/bin/sh"
            os.execve(shell, [shell, "-l"], environment)
        except BaseException as error:
            os.write(2, f"terminal startup failed: {error}\r\n".encode())
            os._exit(126)

    @staticmethod
    def _resize_terminal(master: int, columns: int, rows: int):
        columns = min(240, max(20, columns))
        rows = min(100, max(5, rows))
        fcntl.ioctl(master, 0x5414, struct.pack("HHHH", rows, columns, 0, 0))

    def _terminal_websocket(self, set_cookie: bool):
        if self.state.args.no_terminal:
            self._json(403, {"error": "Web terminal is disabled"}, set_cookie)
            return
        if self.headers.get("Upgrade", "").lower() != "websocket":
            self._json(400, {"error": "WebSocket upgrade required"}, set_cookie)
            return
        origin = self.headers.get("Origin", "")
        if origin and urlparse(origin).netloc != self.headers.get("Host", ""):
            self._json(403, {"error": "Cross-origin terminal denied"}, set_cookie)
            return
        if not self.state.terminal_slots.acquire(blocking=False):
            self._json(503, {"error": "All terminal sessions are in use"}, set_cookie)
            return
        key = self.headers.get("Sec-WebSocket-Key", "")
        accept = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        ).decode("ascii")
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        if set_cookie:
            self.send_header(
                "Set-Cookie",
                f"cora_session={self.state.session_cookie}; Path=/; HttpOnly; SameSite=Strict",
            )
        self.end_headers()

        child_pid = master = None
        connection = self.connection
        try:
            child_pid, master = self._spawn_terminal()
            self._resize_terminal(master, 100, 28)
            connection.settimeout(2.0)
            while True:
                readable, _, _ = select.select([connection, master], [], [], 0.5)
                if master in readable:
                    try:
                        output = os.read(master, 8192)
                    except OSError:
                        output = b""
                    if not output:
                        break
                    self._ws_send(
                        connection,
                        json.dumps({"type": "output", "data": output.decode("utf-8", "replace")}),
                    )
                if connection in readable:
                    opcode, payload = self._ws_receive(connection)
                    if opcode == 8:
                        break
                    if opcode == 9:
                        self._ws_send(connection, payload.decode("utf-8", "replace"), opcode=10)
                        continue
                    if opcode not in (1, 2):
                        continue
                    message = json.loads(payload.decode("utf-8"))
                    if message.get("type") == "input":
                        os.write(master, str(message.get("data", "")).encode("utf-8"))
                    elif message.get("type") == "resize":
                        self._resize_terminal(
                            master, int(message.get("cols", 100)), int(message.get("rows", 28))
                        )
                exited, _ = os.waitpid(child_pid, os.WNOHANG)
                if exited:
                    child_pid = None
                    break
        except (OSError, ConnectionError, ValueError, KeyError, json.JSONDecodeError):
            pass
        finally:
            if master is not None:
                try:
                    os.close(master)
                except OSError:
                    pass
            if child_pid is not None:
                try:
                    os.kill(child_pid, signal.SIGHUP)
                except ProcessLookupError:
                    pass
                try:
                    os.waitpid(child_pid, 0)
                except ChildProcessError:
                    pass
            self.state.terminal_slots.release()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--web-root", default="/usr/share/cora-dashboard")
    parser.add_argument("--token-file", default="/var/lib/cora-dashboard/token")
    parser.add_argument("--auth-user", default="cora")
    parser.add_argument("--terminal-user", default="petalinux")
    parser.add_argument("--max-terminals", type=int, default=2)
    parser.add_argument("--no-terminal", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if not 1 <= args.max_terminals <= 8:
        parser.error("max-terminals must be between 1 and 8")
    return args


def main():
    args = parse_args()
    state = DashboardState(args)
    server = CoraHTTPServer((args.bind, args.port), DashboardHandler, state)
    print(
        f"Cora dashboard {VERSION} listening on http://{args.bind}:{args.port}; "
        f"login user {args.auth_user}, token file {args.token_file}",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
