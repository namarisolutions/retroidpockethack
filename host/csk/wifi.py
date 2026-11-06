"""Thin NetworkManager (nmcli) wrapper so the Flip can join a chosen AP.

Used to connect to a *suspect* access point to scan what's behind it, then put
the previous connection back. All commands go through an injectable runner so
the orchestration can be unit-tested without touching real WiFi.

nmcli is what SteamOS (and most desktop Linux) uses. If it's absent, available()
returns False and the app says so instead of pretending to connect.
"""
from __future__ import annotations

import shutil
import subprocess


def _run(args: list[str], timeout: float) -> tuple[int, str, str]:
    try:
        p = subprocess.run(["nmcli", *args], capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", "nmcli not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


class Wifi:
    def __init__(self, runner=_run):
        self.run = runner

    def available(self) -> bool:
        return shutil.which("nmcli") is not None

    def active_connection(self) -> str | None:
        """Name of the currently-active WiFi connection (to restore later)."""
        rc, out, _ = self.run(["-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active"], 8)
        if rc != 0:
            return None
        for line in out.splitlines():
            parts = line.split(":")
            if len(parts) >= 2 and "wireless" in parts[1]:
                return parts[0]
        return None

    def active_ssid(self) -> str | None:
        rc, out, _ = self.run(["-t", "-f", "ACTIVE,SSID", "dev", "wifi"], 8)
        if rc != 0:
            return None
        for line in out.splitlines():
            if line.startswith("yes:"):
                return line[4:]
        return None

    def connect(self, ssid: str, password: str | None = None, bssid: str | None = None,
                timeout: float = 30) -> tuple[bool, str]:
        args = ["-w", str(int(timeout)), "dev", "wifi", "connect", ssid]
        if password:
            args += ["password", password]
        if bssid:
            args += ["bssid", bssid]
        rc, out, err = self.run(args, timeout + 5)
        return rc == 0, (err or out).strip()

    def restore(self, name: str | None, timeout: float = 30) -> tuple[bool, str]:
        if not name:
            return False, "no previous connection recorded"
        rc, out, err = self.run(["-w", str(int(timeout)), "connection", "up", name], timeout + 5)
        return rc == 0, (err or out).strip()

    def disconnect(self, name: str, timeout: float = 15) -> None:
        self.run(["connection", "down", name], timeout)
