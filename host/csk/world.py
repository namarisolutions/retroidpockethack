"""Device registry + threat detectors.

Everything the ESP32 reports is folded into Device records. `evaluate()` runs
the detectors and maintains the alert list.

"Following" detection works like this: no GPS on the handheld, so the user
drops a *checkpoint* (X button) each time they've moved somewhere new. Any
non-allowlisted device with a stable address that has been seen at 3+
distinct checkpoints is moving with you.
"""
from __future__ import annotations

import json
import os
import time
from collections import deque
from dataclasses import dataclass, field

from . import bleparse, oui
from .bleparse import HIGH, INFO, WARN

CONFIG_DIR = os.path.expanduser(os.environ.get("CSK_CONFIG_DIR", "~/.config/csk"))
ALLOW_FILE = os.path.join(CONFIG_DIR, "allow.json")
WATCH_FILE = os.path.join(CONFIG_DIR, "watch.json")

# Well-known public hotspot names that are popular to impersonate, with the
# security each one really uses (0 = open, 2 = WPA/802.1X, None = varies).
PUBLIC_SSIDS = {
    "xfinitywifi": 0, "XFINITY": 2, "CableWiFi": 0, "SpectrumWiFi": 0, "SpectrumWiFi Plus": 2,
    "TWCWiFi": 0, "optimumwifi": 0, "attwifi": 0, "Google Starbucks": 0, "Starbucks WiFi": 0,
    "Boingo Hotspot": None, "Free Public WiFi": None,
}

CANARY_WORDS = ("NETGEAR", "Linksys", "TP-Link_", "MySpectrumWiFi", "ATT", "HOME-", "DIRECT-", "Verizon_", "WiFi-")


def canary_ssid(rng=None) -> str:
    """A believable home-network name that almost certainly doesn't exist here."""
    import random
    r = rng or random.SystemRandom()
    w = r.choice(CANARY_WORDS)
    tail = "".join(r.choice("0123456789ABCDEF") for _ in range(4))
    return f"{w}{tail}" + r.choice(["", "-2G", "-5G", ""])


def _mid(mac: str) -> str:
    """Middle 4 octets: shared by a real gateway's sibling BSSIDs (last-octet or LAA-bit variants)."""
    return mac[3:14]

# Tunables
FOLLOW_CHECKPOINTS = 3          # seen at this many checkpoints -> following
PERSIST_MIN_SPAN = 10 * 60      # tracker continuously near you this long -> alert
PERSIST_MIN_RATIO = 0.6
DEAUTH_WINDOW = 10.0
DEAUTH_THRESHOLD = 25           # deauth/disassoc frames in window
KARMA_SSIDS = 5                 # one BSSID beaconing this many SSIDs -> rogue AP
STREAM_BPS = 40_000             # sustained uplink bytes/sec that looks like video
STREAM_WINDOW = 20.0
ACTIVE_SECS = 30.0              # alert counts as "active" if refreshed within this

SSID_CAMERA_HINTS = ("ipcam", "ipc-", "ipc_", "hdwificam", "wificam", "v380", "minicam", "mini-cam",
                     "spycam", "lookcam", "cam_", "cam-", "ezviz", "hikvision", "dahua", "wyze", "yi-cam",
                     "dashcam", "p2p-cam", "ap-cam")
SSID_ALPR_HINTS = ("flock", "penguin", "pigvision", "fs ext battery")


def _decode_ssid(h: str) -> str:
    try:
        b = bytes.fromhex(h)
    except ValueError:
        return ""
    if not b or all(c == 0 for c in b):
        return ""
    return b.decode("utf-8", "replace")


@dataclass
class Device:
    id: str
    kind: str                  # "ble" | "wifi" (client) | "ap"
    addr: str
    first: float
    last: float
    rssi: int = -127
    ch: int = 0
    label: str = ""
    vendor: str = ""
    category: str = ""         # tracker | camera | alpr | phone | wearable | iot | random | ""
    base_level: int = INFO
    stable_id: bool = True
    ssids: set = field(default_factory=set)          # AP: advertised, client: probed
    enc: int = -1                                    # AP only
    bssid: str = ""                                  # client: associated AP
    checkpoints: set = field(default_factory=set)
    minutes: set = field(default_factory=set)
    rssi_hist: deque = field(default_factory=lambda: deque(maxlen=300))
    up_hist: deque = field(default_factory=lambda: deque(maxlen=60))   # (t, bytes)
    raw: str = ""
    fps: set = field(default_factory=set)            # AP beacon fingerprints
    karma: bool = False                              # answered a canary probe
    suspicion: list = field(default_factory=list)    # reasons (filled by evaluate)

    def seen(self, now: float, rssi: int, cp: int) -> None:
        self.last = now
        if rssi > -127:
            self.rssi = rssi
            self.rssi_hist.append((now, rssi))
        self.checkpoints.add(cp)
        self.minutes.add(int(now // 60))

    def uplink_bps(self, now: float, window: float = STREAM_WINDOW) -> float:
        total = sum(b for t, b in self.up_hist if now - t <= window)
        return total / window

    def title(self) -> str:
        if self.label:
            return self.label
        if self.kind == "ap":
            s = next(iter(self.ssids), "") if len(self.ssids) == 1 else f"{len(self.ssids)} SSIDs"
            return s or "<hidden network>"
        if self.vendor:
            return self.vendor
        return "private address" if self.category == "random" else "unknown"


@dataclass
class Alert:
    key: str
    level: int
    title: str
    detail: str
    dev_id: str
    first: float
    last: float
    seen_by_user: bool = False

    def active(self, now: float) -> bool:
        return now - self.last <= ACTIVE_SECS


class World:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.devices: dict[str, Device] = {}
        self.alerts: dict[str, Alert] = {}
        self.checkpoint_idx = 0
        self.checkpoint_times: list[float] = [clock()]
        self.deauth_events: deque = deque()   # (t, n, src, dst, rssi, ch)
        self.sensor: dict = {}
        self.sensor_hello: dict = {}
        self.last_msg = 0.0
        self.allow: dict[str, str] = self._load_json(ALLOW_FILE)
        self.watch: dict[str, dict] = self._load_json(WATCH_FILE)
        self.karma_pending: tuple[str, int, str] | None = None   # (canary, ch, target id)
        self.karma_results: list[dict] = []
        self.canaries: set[str] = set()
        self.store = None                 # optional Store for persistent cross-day memory
        self.location = "default"         # current named location (set by the UI)
        self._last_persist = 0.0
        self.new_alert_cb = None

    # -------------------------------------------------------------- allowlist
    @staticmethod
    def _load_json(path: str) -> dict:
        try:
            with open(path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_allow(self) -> None:
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(ALLOW_FILE, "w") as f:
                json.dump(self.allow, f, indent=1)
        except OSError:
            pass

    # -------------------------------------------------------------- watchlist
    def toggle_watch(self, dev_id: str) -> bool:
        """Mark a device as hostile. APs are also matched by name + fingerprint so a
        rogue AP that changes its MAC is still recognised."""
        if dev_id in self.watch:
            del self.watch[dev_id]
        else:
            d = self.devices.get(dev_id)
            self.watch[dev_id] = {"title": d.title() if d else "",
                                  "ssids": sorted(s for s in d.ssids if s) if d else [],
                                  "fps": sorted(d.fps) if d else []}
            self.allow.pop(dev_id, None)
            self._save_allow()
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(WATCH_FILE, "w") as f:
                json.dump(self.watch, f, indent=1)
        except OSError:
            pass
        return dev_id in self.watch

    def watched_match(self, d: "Device") -> str | None:
        if d.id in self.watch:
            return d.id
        if d.kind == "ap" and d.fps:
            for wid, w in self.watch.items():
                if wid.startswith("ap:") and set(w.get("fps", [])) & d.fps and set(w.get("ssids", [])) & d.ssids:
                    return wid
        return None

    # ------------------------------------------------------------- karma test
    def start_karma(self, dev_id: str, rng=None) -> str | None:
        """Returns the command to send to the ESP32, or None if not applicable."""
        d = self.devices.get(dev_id)
        if not d or d.kind != "ap" or not (1 <= d.ch <= 13):
            return None
        c = canary_ssid(rng)
        self.karma_pending = (c, d.ch, dev_id)
        self.canaries.add(c)
        return f"karma {d.ch} {c.encode().hex()}"

    def is_allowed(self, dev_id: str) -> bool:
        return dev_id in self.allow

    def toggle_allow(self, dev_id: str) -> bool:
        if dev_id in self.allow:
            del self.allow[dev_id]
        else:
            d = self.devices.get(dev_id)
            self.allow[dev_id] = d.title() if d else ""
            for k in [k for k, a in self.alerts.items() if a.dev_id == dev_id]:
                del self.alerts[k]
        self._save_allow()
        return dev_id in self.allow

    def allow_all_visible(self, within: float = 60.0) -> int:
        now = self.clock()
        n = 0
        for d in self.devices.values():
            if now - d.last <= within and d.id not in self.allow:
                self.allow[d.id] = d.title()
                n += 1
        self.alerts = {k: a for k, a in self.alerts.items() if a.dev_id not in self.allow}
        self._save_allow()
        return n

    # ------------------------------------------------------------ checkpoints
    def checkpoint(self) -> int:
        self.checkpoint_idx += 1
        self.checkpoint_times.append(self.clock())
        return self.checkpoint_idx

    # ----------------------------------------------------------------- ingest
    def _dev(self, dev_id: str, kind: str, addr: str, now: float) -> Device:
        d = self.devices.get(dev_id)
        if d is None:
            d = Device(dev_id, kind, addr, now, now)
            if kind != "ble":  # BLE vendor is resolved in ingest (only public addresses)
                d.vendor, d.category = oui.lookup(addr)
            self.devices[dev_id] = d
        return d

    def ingest(self, msg: dict) -> None:
        now = self.clock()
        self.last_msg = now
        t = msg.get("t")
        cp = self.checkpoint_idx
        if t == "ble":
            addr = msg["a"]
            d = self._dev("ble:" + addr, "ble", addr, now)
            payload = bytes.fromhex(msg.get("p", ""))
            if msg.get("p") != d.raw:
                d.raw = msg.get("p", "")
                adv = bleparse.parse_adv(payload)
                c = bleparse.classify(adv)
                if msg.get("at", 1) == 0 and not d.vendor:
                    d.vendor, ocat = oui.lookup(addr)
                    if ocat == "camera" and not c.category:
                        c = bleparse.Classification("camera", f"{d.vendor} device", WARN)
                if c.category or not d.category:
                    d.category, d.label, d.base_level, d.stable_id = c.category, c.label, c.level, c.stable_id
                if adv.name and adv.name not in d.label:
                    d.label = f"{d.label} '{adv.name}'" if d.label else adv.name
            d.seen(now, msg.get("r", -127), cp)
        elif t == "ap":
            b = msg["b"]
            ssid = _decode_ssid(msg.get("s", ""))
            if ssid in self.canaries:   # probe response to our own Karma test
                return
            d = self._dev("ap:" + b, "ap", b, now)
            d.ssids.add(ssid)
            d.ch = msg.get("ch", d.ch)
            d.enc = msg.get("e", d.enc)
            fp = msg.get("fp", "")
            if fp and fp != "00000000":
                d.fps.add(fp)
            d.seen(now, msg.get("r", -127), cp)
        elif t == "sta":
            m = msg["m"]
            d = self._dev("wifi:" + m, "wifi", m, now)
            d.bssid = msg.get("b", d.bssid)
            d.ch = msg.get("ch", d.ch)
            if msg.get("ub"):
                d.up_hist.append((now, msg["ub"]))
            d.seen(now, msg.get("r", -127), cp)
        elif t == "probe":
            m = msg["m"]
            d = self._dev("wifi:" + m, "wifi", m, now)
            s = _decode_ssid(msg.get("s", ""))
            if s:
                d.ssids.add(s)
            d.ch = msg.get("ch", d.ch)
            d.seen(now, msg.get("r", -127), cp)
        elif t == "deauth":
            self.deauth_events.append((now, msg.get("n", 1), msg.get("src", ""), msg.get("dst", ""),
                                       msg.get("r", -127), msg.get("ch", 0)))
        elif t == "karma":
            canary = _decode_ssid(msg.get("s", ""))
            hits = msg.get("hits", [])
            target = self.karma_pending[2] if self.karma_pending and self.karma_pending[0] == canary else ""
            for b in hits:
                hd = self._dev("ap:" + b, "ap", b, now)
                hd.karma = True
                hd.ssids.discard(canary)   # the canary isn't a real network name
            if target in self.devices:
                self.devices[target].ssids.discard(canary)
            self.karma_results.append({"t": now, "ch": msg.get("ch"), "canary": canary, "hits": hits, "target": target})
            self.karma_pending = None
        elif t == "stat":
            self.sensor = msg
        elif t == "hello":
            self.sensor_hello = msg
        elif t == "_cp":  # demo/replay checkpoint marker
            self.checkpoint()

    # -------------------------------------------------------------- detectors
    def _raise(self, found: dict, key: str, level: int, title: str, detail: str, dev_id: str = "") -> None:
        if dev_id and self.is_allowed(dev_id):
            return
        prev = found.get(key)
        if prev is None or prev[0] < level:
            found[key] = (level, title, detail, dev_id)

    def evaluate(self) -> list[Alert]:
        """Run detectors. Returns alerts that are brand new this call."""
        now = self.clock()
        found: dict[str, tuple] = {}
        cp = self.checkpoint_idx

        for d in self.devices.values():
            if now - d.last > 120 or self.is_allowed(d.id):
                continue
            span = d.last - d.first
            mins_span = max(1, int(d.last // 60) - int(d.first // 60) + 1)
            ratio = len(d.minutes) / mins_span
            following = d.stable_id and d.category != "random" and len(d.checkpoints) >= FOLLOW_CHECKPOINTS

            # Trackers
            if d.category == "tracker":
                lvl = d.base_level
                why = []
                if span >= PERSIST_MIN_SPAN and ratio >= PERSIST_MIN_RATIO and d.stable_id:
                    lvl = max(lvl, WARN)
                    why.append(f"near you for {int(span // 60)} min")
                if following:
                    lvl = HIGH
                    why.append(f"seen at {len(d.checkpoints)} of your locations")
                if lvl >= WARN:
                    self._raise(found, "trk:" + d.id, lvl, d.label or "Tracker",
                                ", ".join(why) or "tracker detected nearby", d.id)

            # Anything else that follows you (stable address, multiple checkpoints)
            elif following and d.kind != "ap":
                self._raise(found, "fol:" + d.id, HIGH, f"Following you: {d.title()}",
                            f"{d.kind.upper()} {d.addr} seen at {len(d.checkpoints)} locations", d.id)
            elif following and d.kind == "ap":
                self._raise(found, "fol:" + d.id, WARN, f"Mobile hotspot following: {d.title()}",
                            f"AP {d.addr} seen at {len(d.checkpoints)} locations", d.id)

            # Cameras / ALPR
            if d.category == "camera":
                bps = d.uplink_bps(now)
                lvl = HIGH if bps >= STREAM_BPS else WARN
                det = f"{d.vendor or d.label} {d.addr} ch{d.ch} {d.rssi}dBm"
                if bps >= STREAM_BPS:
                    det += f", uploading {bps / 1000:.0f} kB/s (streaming)"
                self._raise(found, "cam:" + d.id, lvl, "Camera device", det, d.id)
            elif d.category == "alpr":
                self._raise(found, "alpr:" + d.id, WARN, d.label, f"{d.addr} {d.rssi}dBm", d.id)
            elif d.kind == "wifi" and d.category in ("iot", "") and d.rssi >= -65:
                bps = d.uplink_bps(now)
                if bps >= STREAM_BPS:
                    lvl = WARN if d.category == "iot" else INFO
                    self._raise(found, "stream:" + d.id, lvl, "Close device streaming upstream",
                                f"{d.title()} {d.addr} {bps / 1000:.0f} kB/s up, {d.rssi}dBm", d.id)

            # Access point heuristics
            if d.kind == "ap":
                names = {s for s in d.ssids if s}
                low = " ".join(names).lower()
                if len(names) >= KARMA_SSIDS:
                    self._raise(found, "karma:" + d.id, HIGH, "Rogue AP (one radio, many networks)",
                                f"{d.addr} advertising {len(names)} SSIDs - Pineapple/Karma-style", d.id)
                if any(h in low for h in SSID_ALPR_HINTS):
                    self._raise(found, "alpr:" + d.id, WARN, "Possible ALPR/Flock camera", f"SSID {sorted(names)} {d.addr}", d.id)
                elif any(h in low for h in SSID_CAMERA_HINTS):
                    self._raise(found, "camssid:" + d.id, WARN, "Camera hotspot", f"SSID {sorted(names)} ch{d.ch} {d.rssi}dBm", d.id)

        # Watchlisted (user marked hostile) devices
        for d in self.devices.values():
            if now - d.last > 60:
                continue
            wid = self.watched_match(d)
            if wid:
                name = self.watch[wid].get("title") or d.title()
                how = "" if wid == d.id else f" (new MAC {d.addr}, same name + fingerprint)"
                self._raise(found, "watch:" + d.id, HIGH, f"Watchlisted: {name}",
                            f"in range now, {d.rssi} dBm, ch{d.ch}{how}", d.id)

        # Impersonated public hotspots (fake xfinitywifi etc.)
        self._impersonation(found, now)

        # Evil twin: same SSID, both open and encrypted
        by_ssid: dict[str, set] = {}
        for d in self.devices.values():
            if d.kind == "ap" and now - d.last <= 120 and d.enc >= 0:
                for s in d.ssids:
                    if s:
                        by_ssid.setdefault(s, set()).add((d.enc, d.id))
        for s, variants in by_ssid.items():
            encs = {e for e, _ in variants}
            if 0 in encs and 2 in encs:
                open_ids = [i for e, i in variants if e == 0]
                self._raise(found, "twin:" + s, WARN, f"Evil twin? '{s}'",
                            f"Same name broadcast open AND encrypted ({len(variants)} radios)", open_ids[0])

        # Deauth flood
        while self.deauth_events and now - self.deauth_events[0][0] > DEAUTH_WINDOW:
            self.deauth_events.popleft()
        total = sum(e[1] for e in self.deauth_events)
        if total >= DEAUTH_THRESHOLD:
            srcs = {e[2] for e in self.deauth_events}
            chs = sorted({e[5] for e in self.deauth_events})
            self._raise(found, "deauth", HIGH, "Deauth attack in progress",
                        f"{total} deauth/disassoc frames in {int(DEAUTH_WINDOW)}s, {len(srcs)} sources, ch {chs}")

        # Persistent cross-day / cross-location recurrence (survives restarts)
        self.recurring_alerts(found, now)

        new = []
        for key, (level, title, detail, dev_id) in found.items():
            a = self.alerts.get(key)
            if a is None:
                a = Alert(key, level, title, detail, dev_id, now, now)
                self.alerts[key] = a
                new.append(a)
            else:
                if level > a.level:
                    a.seen_by_user = False
                    new.append(a)
                a.level, a.title, a.detail, a.last = level, title, detail, now
        return new

    # ------------------------------------------------------------------ views
    def sorted_alerts(self) -> list[Alert]:
        now = self.clock()
        return sorted(self.alerts.values(), key=lambda a: (not a.active(now), -a.level, -a.last))

    def recent(self, kinds: tuple[str, ...], within: float = 120.0) -> list[Device]:
        now = self.clock()
        out = [d for d in self.devices.values() if d.kind in kinds and now - d.last <= within]
        rank = {"tracker": 0, "camera": 1, "alpr": 1}
        return sorted(out, key=lambda d: (rank.get(d.category, 5), -d.rssi))

    def _impersonation(self, found: dict, now: float) -> None:
        aps = [d for d in self.devices.values() if d.kind == "ap" and now - d.last <= 180]
        by_mid: dict[str, list] = {}
        for d in aps:
            by_mid.setdefault(_mid(d.addr), []).append(d)
        for d in aps:
            d.suspicion = []
            if d.karma:
                d.suspicion.append("answered for a network that doesn't exist (Karma test)")
        for d in aps:
            for ssid in [s for s in d.ssids if s in PUBLIC_SSIDS]:
                score, why = (5, list(d.suspicion)) if d.karma else (0, [])
                expected = PUBLIC_SSIDS[ssid]
                siblings = [o for o in by_mid.get(_mid(d.addr), []) if o is not d]
                if "hak5" in d.vendor.lower():
                    score += 3
                    why.append("hardware vendor is Hak5 (WiFi Pineapple maker)")
                if expected is not None and d.enc >= 0 and d.enc != expected:
                    score += 3 if expected == 2 else 1
                    why.append(f"'{ssid}' should be {'secured' if expected else 'open'} but this one is "
                               f"{'open' if d.enc == 0 else 'secured'}")
                named = {s for s in d.ssids if s}
                if len(named) >= KARMA_SSIDS:
                    score += 3
                    why.append(f"same radio also claims {len(named) - 1} other networks")
                if not siblings:
                    if oui.is_randomized(d.addr):
                        score += 2
                        why.append("made-up MAC with no sibling networks from the same hardware")
                    else:
                        score += 1
                        why.append("no sibling networks from the same hardware (real gateways broadcast several)")
                # fingerprint outlier: other same-name APs agree with each other but not with this one
                if d.fps:
                    others = [o for o in aps if o is not d and ssid in o.ssids and o.fps]
                    if len(others) >= 2:
                        common = set.intersection(*(o.fps for o in others))
                        if common and not (common & d.fps):
                            score += 2
                            why.append(f"beacon fingerprint differs from the other {len(others)} '{ssid}' APs")
                if score >= 2:
                    d.suspicion = why
                    lvl = HIGH if score >= 3 else WARN
                    self._raise(found, "fake:" + d.id, lvl,
                                f"{'Fake' if lvl == HIGH else 'Suspicious'} '{ssid}' hotspot",
                                f"{d.addr} ch{d.ch} {d.rssi}dBm: " + "; ".join(why), d.id)
        for d in aps:
            if d.karma and not any(s in PUBLIC_SSIDS for s in d.ssids):
                self._raise(found, "karma:" + d.id, HIGH, "Rogue AP (Karma confirmed)",
                            f"{d.addr} ch{d.ch} answered a made-up network name", d.id)

    def followers(self) -> list[Device]:
        out = [d for d in self.devices.values()
               if d.stable_id and d.category != "random" and len(d.checkpoints) >= 2 and not self.is_allowed(d.id)]
        return sorted(out, key=lambda d: (-len(d.checkpoints), -d.last))

    def set_location(self, name: str) -> None:
        self.location = name or "default"

    def persist_sightings(self, min_interval: float = 20.0) -> int:
        """Write current stable, non-ignored devices to the persistent store under
        the active location. Call periodically; rate-limited. Returns #recorded."""
        if self.store is None:
            return 0
        now = self.clock()
        if now - self._last_persist < min_interval:
            return 0
        self._last_persist = now
        n = 0
        for d in self.devices.values():
            if (now - d.last <= 60 and d.stable_id and d.category != "random"
                    and not oui.is_randomized(d.addr)
                    and d.kind != "ap" and not self.is_allowed(d.id)):
                self.store.record_sighting(d.addr, self.location, kind=d.kind, vendor=d.vendor,
                                           title=d.title(), category=d.category, rssi=d.rssi)
                n += 1
        return n

    def recurring_alerts(self, found: dict, now: float) -> None:
        """Persistent 'seen before' detector: fires on devices the store has seen
        at multiple named locations or across multiple days (survives restarts)."""
        if self.store is None:
            return
        for r in self.store.recurring(min_locations=2, min_days=2):
            mac = r["mac"]
            dev_id = None
            for d in self.devices.values():
                if d.addr == mac:
                    dev_id = d.id
                    break
            if dev_id and self.is_allowed(dev_id):
                continue
            title = (r["device"].get("title") or "").strip() or mac
            lvl = HIGH if (r["n_locations"] >= 2 and r["n_days"] >= 2) else WARN
            det = (f"seen at {r['n_locations']} of your locations ({', '.join(r['locations'][:4])}) "
                   f"on {r['n_days']} day(s)")
            self._raise(found, "recur:" + mac, lvl, f"Seen repeatedly: {title}", det, dev_id or "")

    def topology(self, within: float = 180.0) -> list[dict]:
        """Group recent WiFi clients under the AP they're associated with.

        Returns rows of {ap, clients:[...], unknown}. `ap` is None for clients
        whose AP we haven't heard a beacon from yet (grouped by BSSID). Clients
        are sorted so streamers (likely cameras) float to the top.
        """
        now = self.clock()
        aps = {d.addr: d for d in self.devices.values() if d.kind == "ap" and now - d.last <= within}
        groups: dict[str, list] = {}
        for d in self.devices.values():
            if d.kind == "wifi" and d.bssid and now - d.last <= within:
                groups.setdefault(d.bssid, []).append(d)
        rows = []
        for bssid, clients in groups.items():
            clients.sort(key=lambda c: (-c.uplink_bps(now), -c.rssi))
            rows.append({"bssid": bssid, "ap": aps.get(bssid), "clients": clients})
        # APs with no observed clients still shown (lets you see empty rogue APs)
        for addr, ap in aps.items():
            if addr not in groups:
                rows.append({"bssid": addr, "ap": ap, "clients": []})

        def rank(r):
            ap = r["ap"]
            sus = 0 if (ap and (ap.suspicion or ap.karma or self.watched_match(ap))) else 1
            streaming = 0 if any(c.uplink_bps(now) >= STREAM_BPS for c in r["clients"]) else 1
            best = max((c.rssi for c in r["clients"]), default=(ap.rssi if ap else -127))
            return (sus, streaming, -len(r["clients"]), -best)

        return sorted(rows, key=rank)

    def export_report(self, out_dir: str, operator: str = "", note: str = "") -> str:
        """Write a timestamped, integrity-signed engagement report (JSON + text).

        The signature is an HMAC over the canonical JSON using a per-install key,
        so a report can be shown to be unmodified since export. Returns the path
        of the .json file.
        """
        import hashlib
        import hmac
        import platform

        now = self.clock()
        key = self._report_key()
        self.evaluate()

        def dev_json(d: Device) -> dict:
            return {
                "id": d.id, "kind": d.kind, "addr": d.addr, "vendor": d.vendor, "category": d.category,
                "title": d.title(), "rssi": d.rssi, "best_rssi": max((r for _, r in d.rssi_hist), default=d.rssi),
                "channel": d.ch, "ssids": sorted(s for s in d.ssids if s), "enc": d.enc, "bssid": d.bssid,
                "fingerprints": sorted(d.fps), "karma": d.karma, "suspicion": list(d.suspicion),
                "checkpoints": sorted(d.checkpoints), "first_seen": round(d.first, 1), "last_seen": round(d.last, 1),
                "uplink_bps": round(d.uplink_bps(now)), "on_watchlist": self.watched_match(d) is not None,
                "marked_mine": self.is_allowed(d.id),
            }

        active = [d for d in self.devices.values() if now - d.last <= 300]
        report = {
            "schema": "csk-report/1",
            "generated_unix": round(now, 1),
            "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "operator": operator,
            "note": note,
            "sensor": self.sensor_hello,
            "host": {"platform": platform.platform(), "python": platform.python_version()},
            "checkpoints": len(self.checkpoint_times),
            "counts": {
                "devices_total": len(self.devices), "devices_active": len(active),
                "alerts": len(self.alerts),
            },
            "alerts": [
                {"level": a.level, "level_name": ["INFO", "WARN", "HIGH"][a.level], "title": a.title,
                 "detail": a.detail, "device": a.dev_id, "first": round(a.first, 1), "last": round(a.last, 1)}
                for a in self.sorted_alerts()
            ],
            "karma_tests": self.karma_results,
            "devices": [dev_json(d) for d in sorted(active, key=lambda d: -d.last)],
        }
        body = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
        sig = hmac.new(key, body, hashlib.sha256).hexdigest()
        signed = {"report": report, "integrity": {"alg": "HMAC-SHA256", "signature": sig,
                                                   "key_id": hashlib.sha256(key).hexdigest()[:16]}}

        os.makedirs(out_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(now))
        base = os.path.join(out_dir, f"csk-report-{stamp}")
        with open(base + ".json", "w") as f:
            json.dump(signed, f, indent=1)
        with open(base + ".txt", "w") as f:
            f.write(self._report_text(report, sig))
        return base + ".json"

    def _report_key(self) -> bytes:
        import secrets
        path = os.path.join(CONFIG_DIR, "report_key")
        try:
            with open(path, "rb") as f:
                return f.read().strip()
        except OSError:
            key = secrets.token_hex(32).encode()
            try:
                os.makedirs(CONFIG_DIR, exist_ok=True)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "wb") as f:
                    f.write(key)
            except OSError:
                pass
            return key

    @staticmethod
    def _report_text(report: dict, sig: str) -> str:
        L = [
            "COUNTER-SURVEILLANCE KIT - ENGAGEMENT REPORT",
            "=" * 48,
            f"Generated : {report['generated_utc']}",
            f"Operator  : {report['operator'] or '-'}",
            f"Note      : {report['note'] or '-'}",
            f"Sensor    : {report['sensor'].get('fw', '?')} / {report['sensor'].get('chip', '?')}  mac {report['sensor'].get('mac', '?')}",
            f"Devices   : {report['counts']['devices_active']} active / {report['counts']['devices_total']} total,"
            f" {report['checkpoints']} checkpoints",
            "",
            f"ALERTS ({len(report['alerts'])})",
            "-" * 48,
        ]
        for a in report["alerts"]:
            L.append(f"[{a['level_name']:4}] {a['title']}")
            L.append(f"        {a['detail']}")
        L += ["", f"DEVICES ({len(report['devices'])})", "-" * 48]
        for d in report["devices"]:
            tags = "".join(t for t, c in [(" MINE", d["marked_mine"]), (" WATCH", d["on_watchlist"]),
                                          (" KARMA", d["karma"])] if c)
            L.append(f"{d['kind']:5} {d['addr']}  {d['rssi']:>4}dBm  {d['title']}{tags}")
            if d["ssids"]:
                L.append(f"       networks: {', '.join(d['ssids'])}")
            if d["uplink_bps"] > 2000:
                L.append(f"       uplink: {d['uplink_bps'] / 1000:.0f} kB/s")
            for s in d["suspicion"]:
                L.append(f"       ! {s}")
        L += ["", "INTEGRITY", "-" * 48,
              "HMAC-SHA256 signature (verifies the JSON report is unmodified):",
              sig, "",
              "Verify: recompute HMAC-SHA256 over the canonical 'report' object",
              "with the per-install key in ~/.config/csk/report_key."]
        return "\n".join(L) + "\n"

    def sensor_ok(self) -> bool:
        return self.clock() - self.last_msg < 5.0
