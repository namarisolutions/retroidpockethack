"""CSK - counter-surveillance kit host app.

  python3 -m csk                 live, auto-detect ESP32 on /dev/ttyUSB*|ttyACM*
  python3 -m csk --demo          simulated sensor, no hardware needed
  python3 -m csk --headless      no screen; print alerts to the terminal (SSH)
  python3 -m csk --replay FILE   replay a session log
"""
from __future__ import annotations

import argparse
import os
import sys
import time

from .world import World


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="csk", description="ESP32 + handheld counter-surveillance kit")
    ap.add_argument("--port", help="serial port (default: auto)")
    ap.add_argument("--baud", type=int, default=921600)
    ap.add_argument("--demo", action="store_true", help="simulated sensor")
    ap.add_argument("--demo-speed", type=float, default=1.0, help="demo time multiplier")
    ap.add_argument("--replay", metavar="FILE", help="replay a session log")
    ap.add_argument("--replay-speed", type=float, default=1.0)
    ap.add_argument("--window", metavar="WxH", help="windowed mode, e.g. 960x540 (default fullscreen)")
    ap.add_argument("--headless", action="store_true", help="terminal output only")
    ap.add_argument("--monitor", action="store_true", help="24/7 headless defense monitor (RF + LAN scan + alerts)")
    ap.add_argument("--scan", action="store_true", help="one-shot LAN scan of the network this device is on, then exit")
    ap.add_argument("--scan-ap", metavar="SSID", help="join SSID, scan it for cameras, restore previous network, exit")
    ap.add_argument("--no-rf", action="store_true", help="monitor/scan without the ESP32 (LAN only)")
    ap.add_argument("--web", type=int, metavar="PORT", default=None,
                    help="with --monitor: serve the read-only dashboard on this port (e.g. 8099)")
    ap.add_argument("--no-log", action="store_true", help="don't record a session log")
    ap.add_argument("--log-dir", default=os.path.expanduser(os.environ.get("CSK_LOG_DIR", "~/csk-logs")))
    a = ap.parse_args(argv)

    if a.scan:
        return oneshot_scan()
    if a.scan_ap:
        return oneshot_scan_ap(a.scan_ap)

    log_path = None
    if not a.no_log and not a.replay:
        os.makedirs(a.log_dir, exist_ok=True)
        log_path = os.path.join(a.log_dir, time.strftime("session-%Y%m%d-%H%M%S.jsonl"))

    if a.demo:
        from .demo import DemoSource
        src = DemoSource(speed=a.demo_speed, log_path=None)
    elif a.replay:
        from .link import ReplaySource
        src = ReplaySource(a.replay, a.replay_speed)
    elif a.monitor and a.no_rf:
        src = None
    else:
        from .link import SerialSource
        src = SerialSource(a.port, a.baud, log_path=log_path)

    if src is not None:
        src.start()
    world = World(clock=src.clock if src else None) if src else World()

    if a.monitor:
        return monitor_mode(src, world, a.log_dir, a.web)
    if a.headless:
        return headless(src, world)

    from .ui import App
    window = tuple(int(v) for v in a.window.lower().split("x")) if a.window else None
    App(src, world, window).run()
    src.stop()
    return 0


def _load_cfg() -> dict:
    from .ui import load_config
    return load_config()


def oneshot_scan() -> int:
    from . import netscan, oui
    print("Scanning local /24 (your current network)...", flush=True)
    res = netscan.scan()
    if res.get("error"):
        print("error:", res["error"]); return 1
    print(f"{res['cidr']}  (this device {res.get('self_ip')})\n")
    hosts = res["hosts"]
    if not hosts:
        print("No hosts found. If this device has no LAN IP, join a WiFi network first.")
        return 0
    for h in hosts:
        vendor, cat = oui.lookup(h["mac"]) if h["mac"] else ("", "")
        sev, reasons = netscan.classify_host(h, vendor, cat)
        tag = {"camera": "  <== CAMERA/DVR", "suspect": "  <- possible camera"}.get(sev, "")
        ports = ",".join(str(p) for p in h["ports"]) or "-"
        nm = f"  [{h['name']}]" if h.get('name') else ""
        print(f"{h['ip']:15} {h['mac'] or '??:??':17} {vendor or cat or '':20} ports:{ports}{nm}{tag}")
        for r in reasons:
            print(f"                ! {r}")
    cams = sum(1 for h in hosts if netscan.classify_host(h, *oui.lookup(h['mac']) if h['mac'] else ('', ''))[0] == 'camera')
    print(f"\n{len(hosts)} hosts, {cams} camera/DVR-like.")
    return 0


def oneshot_scan_ap(ssid: str) -> int:
    from . import apscan
    from .wifi import Wifi
    print(f"Will join '{ssid}', scan it, then restore your network.", flush=True)
    res = apscan.scan_ap(Wifi(), ssid, progress=lambda m: print("  " + m, flush=True))
    if not res["ok"]:
        print("FAILED:", res.get("error"), flush=True)
        if not res.get("restored"):
            print("WARNING: previous network may not be restored.", flush=True)
        return 1
    print()
    for h in res["hosts"]:
        tag = {"camera": "  <== CAMERA/DVR", "suspect": "  <- possible camera"}.get(h.get("sev"), "")
        ports = ",".join(str(p) for p in h.get("ports", [])) or "-"
        print(f"{h['ip']:15} {h.get('mac', ''):17} {h.get('vendor', ''):20} ports:{ports}{tag}", flush=True)
        for r in h.get("reasons", []):
            print(f"                ! {r}", flush=True)
    print(f"\n{len(res['hosts'])} hosts, {len(res['cameras'])} camera/DVR-like."
          f"  Previous network {'restored' if res['restored'] else 'NOT restored - check WiFi'}.", flush=True)
    return 0


def monitor_mode(src, world: World, log_dir: str, web_port=None) -> int:
    import os

    from . import monitor
    from .notify import Notifier
    from .store import Store

    cfg = _load_cfg()
    cfg.setdefault("log_dir", log_dir)
    if web_port:
        cfg.setdefault("monitor", {})["web_port"] = web_port
    os.makedirs(log_dir, exist_ok=True)
    store = Store()
    notifier = Notifier.from_config(cfg, os.path.join(log_dir, "monitor.log"))
    try:
        monitor.run(src, world, store, notifier, cfg)
    finally:
        if src:
            src.stop()
        store.close()
    return 0


def headless(src, world: World) -> int:
    names = {0: "INFO", 1: "WARN", 2: "HIGH"}
    last_status = ""
    print("CSK headless - Ctrl+C to quit. Type 'c' + Enter to drop a checkpoint.", flush=True)
    import select
    try:
        while True:
            for msg in src.drain():
                world.ingest(msg)
            for al in world.evaluate():
                print(f"{time.strftime('%H:%M:%S')} [{names[al.level]}] {al.title} - {al.detail}", flush=True)
            if src.status != last_status:
                last_status = src.status
                print(f"-- {last_status}", flush=True)
            if sys.stdin.isatty() and select.select([sys.stdin], [], [], 0)[0]:
                if sys.stdin.readline().strip().lower() == "c":
                    print(f"-- checkpoint {world.checkpoint()}", flush=True)
            time.sleep(1.0)
    except KeyboardInterrupt:
        src.stop()
        return 0


if __name__ == "__main__":
    sys.exit(main())
