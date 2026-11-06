"""Message sources: live ESP32 over serial, replay of a recorded log, or demo."""
from __future__ import annotations

import glob
import json
import queue
import threading
import time

SERIAL_GLOBS = ("/dev/ttyUSB*", "/dev/ttyACM*", "/dev/serial/by-id/*")


def find_port() -> str | None:
    for g in SERIAL_GLOBS:
        hits = sorted(glob.glob(g))
        if hits:
            return hits[0]
    return None


class Source:
    """Base: a background thread pushes parsed dicts onto self.q."""

    def __init__(self, log_path: str | None = None):
        self.q: queue.Queue = queue.Queue(maxsize=20000)
        self.status = "starting"
        self._stop = threading.Event()
        self._log = open(log_path, "a", buffering=1) if log_path else None

    def clock(self) -> float:
        return time.time()

    def _emit(self, line: str) -> None:
        line = line.strip()
        if not line.startswith("{"):
            return
        try:
            msg = json.loads(line)
        except ValueError:
            return
        if self._log:
            self._log.write(json.dumps({"ts": round(self.clock(), 3), **msg}) + "\n")
        try:
            self.q.put_nowait(msg)
        except queue.Full:
            pass

    def drain(self, limit: int = 2000):
        for _ in range(limit):
            try:
                yield self.q.get_nowait()
            except queue.Empty:
                return

    def send(self, cmd: str) -> None:
        pass

    def start(self) -> "Source":
        threading.Thread(target=self._run, daemon=True).start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        raise NotImplementedError


class SerialSource(Source):
    def __init__(self, port: str | None, baud: int = 921600, log_path: str | None = None):
        super().__init__(log_path)
        self.port_arg = port
        self.baud = baud
        self._ser = None
        self._lock = threading.Lock()

    def send(self, cmd: str) -> None:
        with self._lock:
            if self._ser:
                try:
                    self._ser.write((cmd + "\n").encode())
                except Exception:
                    pass

    def _run(self) -> None:
        try:
            import serial  # pyserial
        except ImportError:
            self.status = "pyserial missing: pip install pyserial"
            return
        while not self._stop.is_set():
            port = self.port_arg or find_port()
            if not port:
                self.status = "no ESP32 found - plug it in (USB-C OTG)"
                time.sleep(1.0)
                continue
            try:
                # dsrdtr/rtscts off and DTR/RTS low so opening the port doesn't hold the ESP32 in reset
                ser = serial.Serial()
                ser.port, ser.baudrate, ser.timeout = port, self.baud, 0.5
                ser.dtr = False
                ser.rts = False
                ser.open()
                with self._lock:
                    self._ser = ser
                self.status = f"connected {port}"
                buf = b""
                while not self._stop.is_set():
                    chunk = ser.read(4096)
                    if not chunk:
                        continue
                    buf += chunk
                    *lines, buf = buf.split(b"\n")
                    for ln in lines:
                        self._emit(ln.decode("utf-8", "replace"))
            except Exception as e:  # unplugged, permission denied, ...
                if isinstance(e, PermissionError) or "Permission denied" in str(e):
                    self.status = f"{port}: permission denied - run ./install.sh (udev rule)"
                else:
                    self.status = f"{port}: {e.__class__.__name__}: {e}"[:90]
                with self._lock:
                    self._ser = None
                time.sleep(1.0)


class ReplaySource(Source):
    """Replays a session log written with --log, preserving timing (scaled)."""

    def __init__(self, path: str, speed: float = 1.0):
        super().__init__(None)
        self.path, self.speed = path, speed
        self._t0_real = time.time()
        self._t0_log: float | None = None
        self._now = time.time()

    def clock(self) -> float:
        return self._now

    def _run(self) -> None:
        self.status = f"replay {self.path}"
        with open(self.path) as f:
            for line in f:
                if self._stop.is_set():
                    return
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                ts = msg.pop("ts", None)
                if ts is not None:
                    if self._t0_log is None:
                        self._t0_log = ts
                        self._t0_real = time.time()
                    target = self._t0_real + (ts - self._t0_log) / self.speed
                    delay = target - time.time()
                    if delay > 0:
                        time.sleep(delay)
                    self._now = ts
                self._emit(json.dumps(msg))
        self.status = "replay finished"
