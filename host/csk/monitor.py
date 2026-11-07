"""24/7 headless network defense monitor.

Runs unattended on the Flip while it's on your home network. Two watchers:

  * RF side  - the ESP32 feeds the same detectors as the app (trackers,
    cameras, rogue/fake APs, deauth). New HIGH/WARN alerts are pushed out.
  * LAN side - periodically scans the network the Flip is on (netscan), keeps
    a persistent baseline (store), and alerts when a NEW device joins or a
    camera/DVR appears.

A learning window at first start trusts everything already present, so you
only hear about what shows up afterwards. Mark devices trusted any time to
silence them for good.
"""
from __future__ import annotations

import time

from . import netscan, oui
from .bleparse import HIGH, INFO, WARN
from .store import Store


class NetMonitor:
    def __init__(self, store: Store, notifier, cfg: dict | None = None, scan_fn=netscan.scan, clock=time.time):
        self.store = store
        self.notifier = notifier
        self.cfg = (cfg or {}).get("monitor", {})
        self.scan_fn = scan_fn
        self.clock = clock
        self.learn_until = store.monitor_start() + float(self.cfg.get("learn_minutes", 10)) * 60
        self.last_scan: dict | None = None

    @property
    def learning(self) -> bool:
        return self.clock() < self.learn_until

    def tick(self) -> list[tuple[int, str, str]]:
        """Run one LAN scan, fold into the baseline, return new alerts."""
        result = self.scan_fn()
        self.last_scan = result
        alerts: list[tuple[int, str, str]] = []
        learning = self.learning
        for host in result.get("hosts", []):
            mac = host.get("mac") or ""
            if not mac:
                continue
            vendor, cat = oui.lookup(mac)
            sev, reasons = netscan.classify_host(host, vendor, cat)
            title = vendor or "unknown device"
            ips = [host["ip"]] if host.get("ip") else []
            dev, is_new = self.store.upsert(
                mac, "lan", vendor=vendor, title=title,
                category="camera" if sev == "camera" else cat,
                ips=ips, ports=[str(p) for p in host.get("ports", [])],
                extra={"last_reasons": reasons} if reasons else None)

            if learning:
                self.store.set_trusted(mac, True)  # baseline: trust what's already here
                continue
            if dev["trusted"]:
                continue

            ipd = host.get("ip", "?")
            portd = ",".join(str(p) for p in host.get("ports", [])) or "none"
            if sev == "camera":
                self._emit(alerts, HIGH, "Camera/DVR on your network",
                           f"{title} at {ipd} ({mac}) - " + "; ".join(reasons))
            elif sev == "suspect":
                self._emit(alerts, WARN, "Possible camera on your network",
                           f"{title} at {ipd} ({mac}) - " + "; ".join(reasons))
            elif is_new:
                self._emit(alerts, WARN, "New device joined your network",
                           f"{title} at {ipd} ({mac}), open ports: {portd}")
        return alerts

    def _emit(self, alerts, level, title, detail):
        alerts.append((level, title, detail))
        self.store.log_event(level, "lan", "", title, detail)
        self.notifier.send(level, title, detail)


def run(src, world, store: Store, notifier, cfg: dict, interval_rf=1.0):
    """Blocking monitor loop. src = a started RF Source (serial/demo); may be None."""
    import os

    mon_cfg = cfg.get("monitor", {})
    scan_interval = float(mon_cfg.get("scan_interval", 120))
    report_every = float(mon_cfg.get("report_minutes", 60)) * 60
    report_dir = os.path.join(os.path.expanduser(cfg.get("log_dir", "~/csk-logs")), "reports")
    netmon = NetMonitor(store, notifier, cfg)
    # Persist RF sightings under a monitor location so following spans days here too.
    if world is not None:
        world.store = store
        world.set_location(mon_cfg.get("location", "monitor"))

    dash = None
    web_port = int(mon_cfg.get("web_port", 0))
    if web_port and world is not None:
        from .dashboard import Dashboard
        dash = Dashboard(lambda: __import__("csk.dashboard", fromlist=["build_state"]).build_state(
            world, store, netmon.last_scan), host=mon_cfg.get("web_host", "0.0.0.0"), port=web_port).start()

    notifier.send(INFO, "CSK monitor started",
                  f"RF sensor {'on' if src else 'off'}, LAN scan every {int(scan_interval)}s, "
                  f"learning for {int((netmon.learn_until - store.clock()) / 60)} more min"
                  + (f", dashboard on :{web_port}" if dash else ""))

    next_scan = 0.0
    next_report = time.time() + report_every
    try:
        while True:
            now = time.time()
            if src is not None:
                for msg in src.drain():
                    world.ingest(msg)
                world.persist_sightings()
                for a in world.evaluate():
                    if a.level >= WARN:
                        store.log_event(a.level, "rf", a.dev_id, a.title, a.detail)
                        notifier.send(a.level, a.title, a.detail)
            if now >= next_scan:
                netmon.tick()
                next_scan = now + scan_interval
            if now >= next_report:
                try:
                    world.export_report(report_dir, operator=cfg.get("operator", ""), note="scheduled monitor report")
                except Exception:
                    pass
                next_report = now + report_every
            time.sleep(interval_rf)
    except KeyboardInterrupt:
        notifier.send(INFO, "CSK monitor stopped", "shutting down")
