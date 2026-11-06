"""Synthetic sensor feed so the UI and detectors can be exercised without hardware.

Scenario: you're walking between places. Background devices change at every
location, but one AirTag (separated from its owner) stays with you. There's
also a Hikvision camera streaming nearby, a Pineapple-style rogue AP, and a
deauth burst a little later.
"""
from __future__ import annotations

import json
import math
import random
import time

from .link import Source


def _mac(r: random.Random, prefix: str | None = None, randomized: bool = False) -> str:
    b = [r.randrange(256) for _ in range(6)]
    if randomized:
        b[0] = (b[0] | 0x02) & 0xFE
    elif prefix:
        b[:3] = bytes.fromhex(prefix)
    else:
        b[0] &= 0xFC
    return ":".join(f"{x:02x}" for x in b)


def _hex(s: str) -> str:
    return s.encode().hex()


AIRTAG_PAYLOAD = "1eff4c001219" + "10" + "ab" * 22 + "0100"
TILE_PAYLOAD = "0201060303edfe0e16edfe0102030405060708090a0b"
IPHONE_PAYLOAD = "0201061aff4c000c0e00a1b2c3d4e5f6a7b8c9d0e1f21005" + "0318a1b2c3"


class DemoSource(Source):
    def __init__(self, speed: float = 1.0, seed: int = 7, location_secs: float = 90.0, log_path=None):
        super().__init__(log_path)
        self.speed = speed
        self.r = random.Random(seed)
        self.location_secs = location_secs
        self._t0 = time.time()
        self._sim = self._t0

    def clock(self) -> float:
        return self._sim

    def send(self, cmd: str) -> None:
        parts = cmd.split()
        if parts and parts[0] == "karma" and len(parts) == 3:
            self._karma = (int(parts[1]), parts[2], time.time() + 2.5)

    def _scene(self, loc: int) -> dict:
        r = random.Random(1000 + loc)
        return {
            "aps": [(_mac(r), f"{r.choice(['NETGEAR', 'Linksys', 'MyHome', 'Starbucks', 'ATT'])}-{r.randrange(99)}",
                     r.choice([1, 6, 11]), r.choice([0, 2, 2, 2])) for _ in range(r.randrange(4, 9))],
            "phones": [_mac(r, randomized=True) for _ in range(r.randrange(3, 8))],
            "probers": [(_mac(r, randomized=True), r.choice(["", "xfinitywifi", "HomeNet"])) for _ in range(3)],
        }

    def _run(self) -> None:
        self.status = "DEMO MODE (simulated sensor)"
        self._emit(json.dumps({"t": "hello", "fw": "csk-demo", "chip": "SIM", "mac": "00:00:00:00:00:00"}))
        airtag = "c4:12:34:56:78:9a"  # random static address, stable for 24h like a real separated AirTag
        tile = "e1:22:33:44:55:66"
        cam = _mac(self.r, "2857BE")
        cam_ap = _mac(self.r, "2857BE")
        pineapple = _mac(self.r, "00C0CA")
        stranger_watch = _mac(self.r)          # stable public BLE address that also follows you
        # Two real Comcast-style gateways: xfinitywifi (open) + XFINITY (802.1X) + a home network,
        # sibling BSSIDs that differ only in the first/last octet, identical beacon layout.
        gateways = []
        for g in range(2):
            base = [0x14, 0x22, 0x3b, 0x10 + g, 0x44, 0x50]
            mac = lambda first, last: ":".join(f"{x:02x}" for x in [first] + base[1:5] + [last])
            gateways += [(mac(0x14, 0x50), f"HOME-{g}A7C", 2, "a1b2c3d4"),
                         (mac(0x16, 0x51), "xfinitywifi", 0, "5e6f7a8b"),
                         (mac(0x16, 0x52), "XFINITY", 2, "9c0d1e2f")]
        fake_xfin = "9e:41:07:c3:28:d5"   # lone made-up MAC, hostapd-style beacon
        self._karma = None
        tick = 0
        loc = 0
        scene = self._scene(0)
        next_loc = self.location_secs
        sim_elapsed = 0.0
        while not self._stop.is_set():
            time.sleep(0.25)
            sim_elapsed += 0.25 * self.speed
            self._sim = self._t0 + sim_elapsed
            tick += 1
            r = self.r
            if sim_elapsed >= next_loc:
                loc += 1
                next_loc += self.location_secs
                scene = self._scene(loc)
                self._emit(json.dumps({"t": "_cp"}))

            # BLE every tick
            rs = -55 + int(6 * math.sin(tick / 9)) + r.randrange(-3, 4)
            self._emit(json.dumps({"t": "ble", "a": airtag, "at": 1, "r": rs, "p": AIRTAG_PAYLOAD}))
            if loc % 2 == 0:
                self._emit(json.dumps({"t": "ble", "a": stranger_watch, "at": 0, "r": -70 + r.randrange(-4, 4),
                                       "p": "02010609094d6920426e6420"}))
            if loc == 0:
                self._emit(json.dumps({"t": "ble", "a": tile, "at": 1, "r": -80 + r.randrange(-4, 4), "p": TILE_PAYLOAD}))
            for p in scene["phones"]:
                if r.random() < 0.5:
                    self._emit(json.dumps({"t": "ble", "a": p, "at": 1, "r": r.randrange(-95, -60), "p": IPHONE_PAYLOAD}))

            if tick % 6:
                continue
            # WiFi summary every ~1.5 s
            for b, s, ch, enc in scene["aps"]:
                self._emit(json.dumps({"t": "ap", "b": b, "s": _hex(s), "ch": ch, "e": enc, "r": r.randrange(-90, -50), "n": 14}))
            if loc in (0, 1):
                self._emit(json.dumps({"t": "ap", "b": cam_ap, "s": _hex("IPC-HD-3F2A"), "ch": 6, "e": 2, "r": -58, "n": 15}))
                self._emit(json.dumps({"t": "sta", "m": cam, "b": scene["aps"][0][0], "ch": scene["aps"][0][2],
                                       "r": -52 + r.randrange(-3, 3), "uf": 400, "ub": 120000 + r.randrange(40000), "db": 3000}))
                self._emit(json.dumps({"t": "ap", "b": scene["aps"][1][0], "s": _hex(scene["aps"][1][1]),
                                       "ch": 11, "e": 0, "r": -61, "n": 12}))
                self._emit(json.dumps({"t": "ap", "b": _mac(random.Random(5)), "s": _hex(scene["aps"][1][1]),
                                       "ch": 1, "e": 2, "r": -70, "n": 12}))
            for s in ["FreeWiFi", "attwifi", "Starbucks WiFi", "Airport_Free", "linksys", "HOME-2G"]:
                self._emit(json.dumps({"t": "ap", "b": pineapple, "s": _hex(s), "ch": 11, "e": 0, "r": -66, "n": 5}))
            for b, s, enc, fp in gateways:
                self._emit(json.dumps({"t": "ap", "b": b, "s": _hex(s), "ch": 6, "e": enc, "r": r.randrange(-80, -65), "n": 14, "fp": fp}))
            self._emit(json.dumps({"t": "ap", "b": fake_xfin, "s": _hex("xfinitywifi"), "ch": 1, "e": 0,
                                   "r": -47 + r.randrange(-3, 3), "n": 15, "fp": "0d15ea5e"}))
            if self._karma and time.time() >= self._karma[2]:
                ch, shex, _ = self._karma
                hits = [fake_xfin] if ch == 1 else []
                self._emit(json.dumps({"t": "karma", "ch": ch, "s": shex, "hits": hits}))
                self._karma = None
            for m, s in scene["probers"]:
                self._emit(json.dumps({"t": "probe", "m": m, "s": _hex(s), "ch": 1, "r": r.randrange(-90, -60), "n": 2}))
            if 200 <= sim_elapsed % 400 <= 230:
                self._emit(json.dumps({"t": "deauth", "src": scene["aps"][0][0], "dst": "ff:ff:ff:ff:ff:ff",
                                       "k": "deauth", "rs": 7, "ch": 6, "r": -48, "n": 30}))
            self._emit(json.dumps({"t": "stat", "up": int(sim_elapsed * 1000), "ch": r.choice([1, 6, 11]), "lock": 0,
                                   "wifi": 1, "ble": 1, "drop": 0, "ovf": 0, "heap": 180000}))
