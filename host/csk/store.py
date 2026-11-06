"""Persistent device baseline for 24/7 monitoring.

A small SQLite database that survives restarts and records every device ever
seen on the network (by MAC), when it was first/last seen, how many distinct
days, whether you've trusted it, and what it looks like (vendor, IPs, open
ports, camera flag). This is what lets the monitor say "this device is new"
instead of re-alerting on your own fridge every night.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time

DEFAULT_DB = os.path.expanduser(os.environ.get("CSK_DB", "~/.config/csk/baseline.db"))


class Store:
    def __init__(self, path: str = DEFAULT_DB, clock=time.time):
        self.clock = clock
        if path != ":memory:":
            os.makedirs(os.path.dirname(path), exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS devices (
                mac TEXT PRIMARY KEY,
                kind TEXT,            -- 'lan' | 'wifi' | 'ble' | 'ap'
                vendor TEXT,
                title TEXT,
                category TEXT,        -- 'camera' | 'iot' | ...
                first_seen REAL,
                last_seen REAL,
                days TEXT,            -- JSON list of YYYY-MM-DD strings
                trusted INTEGER DEFAULT 0,
                ips TEXT,             -- JSON list
                ports TEXT,           -- JSON list of open ports
                extra TEXT            -- JSON blob
            )""")
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS events (
                ts REAL, level INTEGER, kind TEXT, mac TEXT, title TEXT, detail TEXT
            )""")
        self.db.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
        # Per (device, named-location, calendar-day) sighting history. This is
        # what lets "following" survive restarts and span days: a device seen at
        # two different places you named, on separate days, is a real tail.
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS sightings (
                mac TEXT, location TEXT, day TEXT,
                first_seen REAL, last_seen REAL, hits INTEGER, best_rssi INTEGER,
                PRIMARY KEY (mac, location, day)
            )""")
        self.db.commit()

    # ---------------------------------------------------------------- meta
    def get_meta(self, k: str, default=None):
        r = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return json.loads(r["v"]) if r else default

    def set_meta(self, k: str, v) -> None:
        self.db.execute("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                        (k, json.dumps(v)))
        self.db.commit()

    def monitor_start(self) -> float:
        t = self.get_meta("monitor_start")
        if t is None:
            t = self.clock()
            self.set_meta("monitor_start", t)
        return t

    # ------------------------------------------------------------- devices
    def get(self, mac: str) -> dict | None:
        r = self.db.execute("SELECT * FROM devices WHERE mac=?", (mac.lower(),)).fetchone()
        return self._row(r) if r else None

    @staticmethod
    def _row(r: sqlite3.Row) -> dict:
        d = dict(r)
        for k in ("days", "ips", "ports", "extra"):
            d[k] = json.loads(d[k]) if d[k] else ([] if k != "extra" else {})
        d["trusted"] = bool(d["trusted"])
        return d

    def upsert(self, mac: str, kind: str, *, vendor="", title="", category="",
               ips=None, ports=None, extra=None) -> tuple[dict, bool]:
        """Insert or update a device. Returns (device, is_new)."""
        mac = mac.lower()
        now = self.clock()
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        cur = self.get(mac)
        if cur is None:
            days = [day]
            self.db.execute(
                "INSERT INTO devices(mac,kind,vendor,title,category,first_seen,last_seen,days,trusted,ips,ports,extra)"
                " VALUES(?,?,?,?,?,?,?,?,0,?,?,?)",
                (mac, kind, vendor, title, category, now, now, json.dumps(days),
                 json.dumps(sorted(ips or [])), json.dumps(sorted(ports or [])), json.dumps(extra or {})))
            self.db.commit()
            return self.get(mac), True
        days = cur["days"]
        if day not in days:
            days = sorted(set(days) | {day})
        merged_ips = sorted(set(cur["ips"]) | set(ips or []))
        merged_ports = sorted(set(cur["ports"]) | set(ports or []))
        self.db.execute(
            "UPDATE devices SET kind=?,vendor=COALESCE(NULLIF(?,''),vendor),"
            "title=COALESCE(NULLIF(?,''),title),category=COALESCE(NULLIF(?,''),category),"
            "last_seen=?,days=?,ips=?,ports=?,extra=? WHERE mac=?",
            (kind, vendor, title, category, now, json.dumps(days),
             json.dumps(merged_ips), json.dumps(merged_ports),
             json.dumps({**cur["extra"], **(extra or {})}), mac))
        self.db.commit()
        return self.get(mac), False

    def set_trusted(self, mac: str, trusted: bool = True) -> None:
        self.db.execute("UPDATE devices SET trusted=? WHERE mac=?", (int(trusted), mac.lower()))
        self.db.commit()

    def trust_all(self) -> int:
        n = self.db.execute("UPDATE devices SET trusted=1 WHERE trusted=0").rowcount
        self.db.commit()
        return n

    def all(self) -> list[dict]:
        return [self._row(r) for r in self.db.execute("SELECT * FROM devices ORDER BY last_seen DESC")]

    def seen_since(self, since: float) -> list[dict]:
        return [self._row(r) for r in
                self.db.execute("SELECT * FROM devices WHERE last_seen>=? ORDER BY last_seen DESC", (since,))]

    # ----------------------------------------------------------- sightings
    def record_sighting(self, mac: str, location: str, *, kind="", vendor="", title="",
                        category="", rssi: int = -127) -> None:
        """Note that `mac` was seen at the named `location` right now."""
        mac = mac.lower()
        self.upsert(mac, kind or "rf", vendor=vendor, title=title, category=category)
        now = self.clock()
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        row = self.db.execute("SELECT hits,best_rssi FROM sightings WHERE mac=? AND location=? AND day=?",
                              (mac, location, day)).fetchone()
        if row:
            self.db.execute("UPDATE sightings SET last_seen=?,hits=hits+1,best_rssi=MAX(best_rssi,?) "
                            "WHERE mac=? AND location=? AND day=?", (now, rssi, mac, location, day))
        else:
            self.db.execute("INSERT INTO sightings(mac,location,day,first_seen,last_seen,hits,best_rssi) "
                            "VALUES(?,?,?,?,?,1,?)", (mac, location, day, now, now, rssi))
        self.db.commit()

    def sighting_summary(self, mac: str) -> dict:
        rows = self.db.execute("SELECT location,day,first_seen,last_seen,hits,best_rssi FROM sightings WHERE mac=?",
                               (mac.lower(),)).fetchall()
        locs = sorted({r["location"] for r in rows})
        days = sorted({r["day"] for r in rows})
        return {"locations": locs, "days": days, "n_locations": len(locs), "n_days": len(days),
                "first": min((r["first_seen"] for r in rows), default=0),
                "last": max((r["last_seen"] for r in rows), default=0),
                "hits": sum(r["hits"] for r in rows),
                "best_rssi": max((r["best_rssi"] for r in rows), default=-127)}

    def recurring(self, min_locations: int = 2, min_days: int = 2, include_trusted=False) -> list[dict]:
        """Devices that keep reappearing - across >= min_locations places OR >= min_days days."""
        macs = [r["mac"] for r in self.db.execute("SELECT DISTINCT mac FROM sightings")]
        out = []
        for mac in macs:
            dev = self.get(mac)
            if dev and dev["trusted"] and not include_trusted:
                continue
            s = self.sighting_summary(mac)
            if s["n_locations"] >= min_locations or s["n_days"] >= min_days:
                out.append({"mac": mac, "device": dev or {}, **s})
        # worst first: most locations, then most days, then most recent
        return sorted(out, key=lambda d: (-d["n_locations"], -d["n_days"], -d["last"]))

    # -------------------------------------------------------------- events
    def log_event(self, level: int, kind: str, mac: str, title: str, detail: str) -> None:
        self.db.execute("INSERT INTO events(ts,level,kind,mac,title,detail) VALUES(?,?,?,?,?,?)",
                        (self.clock(), level, kind, mac, title, detail))
        self.db.commit()

    def recent_events(self, limit: int = 200) -> list[dict]:
        return [dict(r) for r in
                self.db.execute("SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,))]

    def close(self) -> None:
        self.db.close()
