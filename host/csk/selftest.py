"""Self-test / bring-up diagnostic: `csk --selftest`.

Verifies the whole stack end-to-end WITHOUT hardware, then (optionally) checks
the ESP32 link if one is plugged in. Run it first on the handheld after
install, and again after flashing the firmware, before trusting a live sweep.
"""
from __future__ import annotations

import importlib
import os
import time

from .world import World

CHECK, CROSS = "[ OK ]", "[FAIL]"


class Result:
    def __init__(self):
        self.rows: list[tuple[bool, str, str]] = []
        self.ok = True

    def add(self, ok: bool, name: str, detail: str = "") -> None:
        self.rows.append((ok, name, detail))
        self.ok = self.ok and ok

    def warn(self, name: str, detail: str) -> None:
        self.rows.append((None, name, detail))   # informational, never fails


def check_imports(r: Result) -> None:
    mods = ["csk.world", "csk.bleparse", "csk.droneid", "csk.oui", "csk.netscan",
            "csk.discover", "csk.agent", "csk.monitor", "csk.store", "csk.dashboard",
            "csk.link", "csk.demo"]
    bad = []
    for m in mods:
        try:
            importlib.import_module(m)
        except Exception as e:
            bad.append(f"{m}: {e}")
    r.add(not bad, "core modules import", "; ".join(bad) if bad else f"{len(mods)} modules")
    for m, label in [("pygame", "pygame (UI)"), ("serial", "pyserial (ESP32 link)")]:
        try:
            importlib.import_module(m)
            r.add(True, label, "present")
        except Exception:
            r.add(m == "serial" and False or False, label, "MISSING - run install.sh")


def check_dirs(r: Result) -> None:
    from .world import CONFIG_DIR
    for d, label in [(CONFIG_DIR, "config dir"), (os.path.expanduser("~/csk-logs"), "log dir")]:
        try:
            os.makedirs(d, exist_ok=True)
            test = os.path.join(d, ".selftest")
            with open(test, "w") as f:
                f.write("x")
            os.remove(test)
            r.add(True, f"{label} writable", d)
        except OSError as e:
            r.add(False, f"{label} writable", f"{d}: {e}")


def check_pipeline(r: Result, seconds: float = 4.0) -> None:
    """Run the simulated sensor through the detectors; assert each fires."""
    from .demo import DemoSource
    # fast sim + short "locations" so a checkpoint (and the Remote-ID drone) occurs
    src = DemoSource(speed=10, location_secs=12).start()
    w = World(clock=src.clock)
    t0 = time.time()
    while time.time() - t0 < seconds:
        for msg in src.drain():
            w.ingest(msg)
        w.evaluate()
        time.sleep(0.02)
    src.stop()
    titles = " ".join(a.title.lower() for a in w.alerts.values())
    wants = {"trackers": "find my", "cameras": "camera", "rogue AP": "rogue",
             "fake hotspot": "fake", "drone Remote ID": "drone"}
    for label, needle in wants.items():
        r.add(needle in titles, f"detector: {label}", "fired" if needle in titles else "did NOT fire")
    r.add(len(w.devices) > 5, "device ingest", f"{len(w.devices)} devices parsed")


def check_serial(r: Result, port: str | None = None, wait: float = 4.0) -> None:
    from .link import SerialSource, find_port
    p = port or find_port()
    if not p:
        r.warn("ESP32 serial", "no port found (plug in over USB-C OTG to test the sensor)")
        return
    src = SerialSource(p)
    src.start()
    got_hello = False
    t0 = time.time()
    while time.time() - t0 < wait:
        for msg in src.drain():
            if msg.get("t") == "hello":
                got_hello = True
                r.add(True, "ESP32 firmware", f"{p}: {msg.get('fw')} / {msg.get('chip')} mac {msg.get('mac')}")
                break
        if got_hello:
            break
        time.sleep(0.1)
    if not got_hello:
        r.add(False, "ESP32 firmware", f"{p}: opened but no hello - is the firmware flashed? ({src.status})")
    src.stop()


def check_optional(r: Result) -> None:
    from . import agent
    ep = agent.llm_available()
    r.warn("local LLM", f"detected at {ep['url']}" if ep else "not running (advisor uses rules)")
    try:
        from . import netscan
        ip = netscan.local_ipv4()
        r.warn("LAN scan", f"this device is {ip}" if ip else "no LAN IP (join WiFi to scan)")
    except Exception as e:
        r.warn("LAN scan", str(e))


def run(port: str | None = None, with_serial: bool = True) -> Result:
    r = Result()
    check_imports(r)
    check_dirs(r)
    check_pipeline(r)
    if with_serial:
        check_serial(r, port)
    check_optional(r)
    return r


def main(port: str | None = None) -> int:
    print("THE UNKILLABLE DEMON KING - self test\n" + "=" * 40)
    r = run(port)
    for ok, name, detail in r.rows:
        mark = "[ -- ]" if ok is None else (CHECK if ok else CROSS)
        print(f"{mark} {name:<26} {detail}")
    print("=" * 40)
    print(("ALL CHECKS PASSED" if r.ok else "SOME CHECKS FAILED") +
          " - see FAIL rows above." * (not r.ok))
    return 0 if r.ok else 1
