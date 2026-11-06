"""Connect to a chosen AP, scan what's behind it, then restore the old network.

This is a deliberate, user-initiated action with real risk: joining a hostile
AP puts the Flip on the attacker's network. The app gates it behind a warning.
Here we keep it safe-by-construction: always record the previous connection
first and always try to restore it in a finally block, even on error.
"""
from __future__ import annotations

import time

from . import netscan, oui


def scan_ap(wifi, ssid: str, *, bssid: str | None = None, password: str | None = None,
            scan_fn=netscan.scan, progress=None, clock=time.time) -> dict:
    """Returns a result dict: {ok, ssid, hosts, cameras, steps, restored, error}."""
    steps: list[str] = []

    def step(msg: str) -> None:
        steps.append(msg)
        if progress:
            progress(msg)

    result = {"ok": False, "ssid": ssid, "hosts": [], "cameras": [], "steps": steps,
              "restored": False, "error": None, "ts": clock()}

    if not wifi.available():
        result["error"] = "NetworkManager (nmcli) not available on this device"
        step(result["error"])
        return result

    prev = wifi.active_connection()
    step(f"current network: {wifi.active_ssid() or prev or 'none'}")
    try:
        step(f"connecting to '{ssid}'{f' [{bssid}]' if bssid else ''} ...")
        ok, msg = wifi.connect(ssid, password=password, bssid=bssid)
        if not ok:
            result["error"] = f"connect failed: {msg}"
            step(result["error"])
            return result
        step("connected - scanning network ...")
        scan = scan_fn()
        if scan.get("error"):
            result["error"] = scan["error"]
            step("scan error: " + scan["error"])
            return result
        hosts = scan.get("hosts", [])
        cams = []
        for h in hosts:
            vendor, cat = oui.lookup(h["mac"]) if h.get("mac") else ("", "")
            sev, reasons = netscan.classify_host(h, vendor, cat)
            h["vendor"], h["sev"], h["reasons"] = vendor, sev, reasons
            if sev in ("camera", "suspect"):
                cams.append(h)
        result["hosts"] = hosts
        result["cameras"] = cams
        result["ok"] = True
        step(f"found {len(hosts)} hosts, {len(cams)} camera/DVR-like")
        return result
    finally:
        if prev:
            step(f"restoring previous network '{prev}' ...")
            ok, _ = wifi.restore(prev)
            result["restored"] = ok
            step("restored" if ok else "WARNING: could not restore previous network")
