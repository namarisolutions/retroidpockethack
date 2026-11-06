import json
import os
import sys
import tempfile

os.environ["CSK_CONFIG_DIR"] = tempfile.mkdtemp()
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pygame  # noqa: E402

from csk import bleparse, oui  # noqa: E402
from csk import controls as C  # noqa: E402
from csk.bleparse import HIGH, WARN  # noqa: E402
from csk.demo import AIRTAG_PAYLOAD, TILE_PAYLOAD  # noqa: E402
from csk.world import World  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 1_700_000_000.0

    def __call__(self):
        return self.t


def classify_hex(h):
    return bleparse.classify(bleparse.parse_adv(bytes.fromhex(h)))


# ------------------------------------------------------------------ BLE
def test_airtag_separated():
    c = classify_hex(AIRTAG_PAYLOAD)
    assert c.category == "tracker" and "away" in c.label and c.level == WARN


def test_findmy_near_owner_is_info_and_rotating():
    c = classify_hex("07ff4c0012020000")
    assert c.category == "tracker" and c.level == 0 and not c.stable_id


def test_tile():
    assert classify_hex(TILE_PAYLOAD).label == "Tile tracker"


def test_smarttag_and_fmdn_unwanted():
    assert classify_hex("05165afd0102").label == "Samsung SmartTag"
    c = classify_hex("0616aafe41aabb")
    assert c.level == HIGH and "unwanted" in c.label


def test_truncated_payload_does_not_crash():
    bleparse.parse_adv(bytes.fromhex("1eff4c0012"))
    bleparse.parse_adv(b"")


# ------------------------------------------------------------------ OUI
def test_oui():
    assert oui.lookup("28:57:be:00:00:01") == ("Hikvision", "camera")
    assert oui.lookup("24:0a:c4:00:00:01")[1] == "iot"
    assert oui.lookup("da:00:00:00:00:01")[1] == "random"


# ------------------------------------------------------------------ detectors
def test_tracker_following_across_checkpoints():
    clk = Clock()
    w = World(clock=clk)
    for loc in range(3):
        if loc:
            w.checkpoint()
        for _ in range(5):
            clk.t += 30
            w.ingest({"t": "ble", "a": "c4:12:34:56:78:9a", "at": 1, "r": -60, "p": AIRTAG_PAYLOAD})
    w.evaluate()
    a = w.alerts["trk:ble:c4:12:34:56:78:9a"]
    assert a.level == HIGH and "3 of your locations" in a.detail


def test_allowlist_suppresses():
    clk = Clock()
    w = World(clock=clk)
    w.ingest({"t": "ble", "a": "c4:00:00:00:00:01", "at": 1, "r": -60, "p": AIRTAG_PAYLOAD})
    w.toggle_allow("ble:c4:00:00:00:00:01")
    w.evaluate()
    assert not w.alerts
    w.toggle_allow("ble:c4:00:00:00:00:01")


def test_streaming_camera_is_high():
    clk = Clock()
    w = World(clock=clk)
    for _ in range(14):
        clk.t += 1.5
        w.ingest({"t": "sta", "m": "28:57:be:01:02:03", "b": "aa:bb:cc:dd:ee:ff", "ch": 6, "r": -50, "uf": 300, "ub": 150000, "db": 0})
    w.evaluate()
    assert w.alerts["cam:wifi:28:57:be:01:02:03"].level == HIGH


def test_deauth_flood_and_karma_and_twin():
    clk = Clock()
    w = World(clock=clk)
    w.ingest({"t": "deauth", "src": "aa:aa:aa:aa:aa:aa", "dst": "ff:ff:ff:ff:ff:ff", "n": 40, "ch": 6, "r": -40})
    for i in range(6):
        w.ingest({"t": "ap", "b": "00:c0:ca:00:00:01", "s": f"net{i}".encode().hex(), "ch": 11, "e": 0, "r": -60, "n": 3})
    w.ingest({"t": "ap", "b": "10:00:00:00:00:01", "s": b"Cafe".hex(), "ch": 1, "e": 2, "r": -60, "n": 3})
    w.ingest({"t": "ap", "b": "10:00:00:00:00:02", "s": b"Cafe".hex(), "ch": 6, "e": 0, "r": -60, "n": 3})
    w.evaluate()
    assert w.alerts["deauth"].level == HIGH
    assert w.alerts["karma:ap:00:c0:ca:00:00:01"].level == HIGH
    assert "twin:Cafe" in w.alerts


def test_randomized_mac_never_follows():
    clk = Clock()
    w = World(clock=clk)
    for loc in range(4):
        if loc:
            w.checkpoint()
        clk.t += 60
        w.ingest({"t": "probe", "m": "da:11:22:33:44:55", "s": "", "ch": 1, "r": -50, "n": 1})
    w.evaluate()
    assert not any(k.startswith("fol:") for k in w.alerts)


# ------------------------------------------------------------------ controls
def _inp(layout):
    pygame.init()
    return C.Input(layout)


def test_nintendo_layout_maps_east_to_A():
    inp = _inp("nintendo")
    ev = pygame.event.Event(pygame.CONTROLLERBUTTONDOWN, button=pygame.CONTROLLER_BUTTON_B, instance_id=0)
    assert inp.process([ev]) == [C.A]
    up = pygame.event.Event(pygame.CONTROLLERBUTTONUP, button=pygame.CONTROLLER_BUTTON_B, instance_id=0)
    inp.process([up])
    ev = pygame.event.Event(pygame.CONTROLLERBUTTONDOWN, button=pygame.CONTROLLER_BUTTON_A, instance_id=0)
    assert inp.process([ev]) == [C.B]


def test_xbox_layout_and_dpad_and_quit_combo():
    inp = _inp("xbox")
    ev = pygame.event.Event(pygame.CONTROLLERBUTTONDOWN, button=pygame.CONTROLLER_BUTTON_A, instance_id=0)
    assert inp.process([ev]) == [C.A]
    ev = pygame.event.Event(pygame.CONTROLLERBUTTONDOWN, button=pygame.CONTROLLER_BUTTON_DPAD_DOWN, instance_id=0)
    assert inp.process([ev]) == [C.DOWN]
    evs = [pygame.event.Event(pygame.CONTROLLERBUTTONDOWN, button=b, instance_id=0)
           for b in (pygame.CONTROLLER_BUTTON_START, pygame.CONTROLLER_BUTTON_BACK)]
    assert "QUIT" in inp.process(evs)


def test_stick_and_trigger_axes():
    inp = _inp("nintendo")
    ev = pygame.event.Event(pygame.CONTROLLERAXISMOTION, axis=pygame.CONTROLLER_AXIS_LEFTY, value=32000, instance_id=0)
    assert inp.process([ev]) == [C.DOWN]
    ev = pygame.event.Event(pygame.CONTROLLERAXISMOTION, axis=pygame.CONTROLLER_AXIS_TRIGGERRIGHT, value=30000, instance_id=0)
    assert inp.process([ev]) == [C.R2]


def test_raw_joystick_fallback():
    inp = _inp("nintendo")
    inp.joysticks[42] = object()
    ev = pygame.event.Event(pygame.JOYBUTTONDOWN, button=1, instance_id=42)
    assert inp.process([ev]) == [C.A]          # raw button 1 = EAST = A on Retroid
    ev = pygame.event.Event(pygame.JOYHATMOTION, hat=0, value=(0, 1), instance_id=42)
    assert inp.process([ev]) == [C.UP]


def test_gptokeyb_keys():
    inp = _inp("nintendo")
    ev = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_x, mod=0)
    assert inp.process([ev]) == [C.A]          # gptokeyb b(east)=x -> A


# ------------------------------------------------------------------ impersonation / karma / watchlist
def _gateway(w, g, fp="5e6f7a8b"):
    mid = f"22:3b:{0x10 + g:02x}:44"
    w.ingest({"t": "ap", "b": f"14:{mid}:50", "s": b"HOME-1".hex(), "ch": 6, "e": 2, "r": -70, "n": 9, "fp": "a1"})
    w.ingest({"t": "ap", "b": f"16:{mid}:51", "s": b"xfinitywifi".hex(), "ch": 6, "e": 0, "r": -70, "n": 9, "fp": fp})
    w.ingest({"t": "ap", "b": f"16:{mid}:52", "s": b"XFINITY".hex(), "ch": 6, "e": 2, "r": -70, "n": 9, "fp": "9c"})


def test_real_xfinity_gateways_are_quiet_fake_is_flagged():
    clk = Clock()
    w = World(clock=clk)
    _gateway(w, 0)
    _gateway(w, 1)
    w.ingest({"t": "ap", "b": "9e:41:07:c3:28:d5", "s": b"xfinitywifi".hex(), "ch": 1, "e": 0, "r": -45, "n": 9, "fp": "0d15ea5e"})
    w.evaluate()
    fakes = {k: a for k, a in w.alerts.items() if k.startswith("fake:")}
    assert list(fakes) == ["fake:ap:9e:41:07:c3:28:d5"]
    assert fakes["fake:ap:9e:41:07:c3:28:d5"].level == HIGH


def test_open_XFINITY_is_high():
    clk = Clock()
    w = World(clock=clk)
    w.ingest({"t": "ap", "b": "00:11:22:33:44:55", "s": b"XFINITY".hex(), "ch": 6, "e": 0, "r": -50, "n": 3})
    w.evaluate()
    assert w.alerts["fake:ap:00:11:22:33:44:55"].level == HIGH


def test_karma_roundtrip_ignores_canary_ssid():
    import random
    clk = Clock()
    w = World(clock=clk)
    w.ingest({"t": "ap", "b": "9e:41:07:c3:28:d5", "s": b"xfinitywifi".hex(), "ch": 1, "e": 0, "r": -45, "n": 9})
    cmd = w.start_karma("ap:9e:41:07:c3:28:d5", random.Random(1))
    _, ch, shex = cmd.split()
    assert ch == "1" and bytes.fromhex(shex).decode() == w.karma_pending[0]
    # probe response to the canary arrives as an "ap" row, then the result line
    w.ingest({"t": "ap", "b": "9e:41:07:c3:28:d5", "s": shex, "ch": 1, "e": 0, "r": -45, "n": 1})
    w.ingest({"t": "karma", "ch": 1, "s": shex, "hits": ["9e:41:07:c3:28:d5"]})
    w.ingest({"t": "ap", "b": "9e:41:07:c3:28:d5", "s": shex, "ch": 1, "e": 0, "r": -45, "n": 1})  # late flush
    d = w.devices["ap:9e:41:07:c3:28:d5"]
    assert d.karma and d.ssids == {"xfinitywifi"} and w.karma_pending is None
    w.evaluate()
    assert w.alerts["fake:ap:9e:41:07:c3:28:d5"].level == HIGH


def test_watchlist_follows_mac_change_by_name_and_fingerprint():
    clk = Clock()
    w = World(clock=clk)
    w.ingest({"t": "ap", "b": "9e:41:07:c3:28:d5", "s": b"xfinitywifi".hex(), "ch": 1, "e": 0, "r": -45, "n": 9, "fp": "0d15ea5e"})
    w.toggle_watch("ap:9e:41:07:c3:28:d5")
    clk.t += 600
    w.ingest({"t": "ap", "b": "a2:00:00:00:00:01", "s": b"xfinitywifi".hex(), "ch": 11, "e": 0, "r": -50, "n": 9, "fp": "0d15ea5e"})
    w.evaluate()
    a = w.alerts["watch:ap:a2:00:00:00:00:01"]
    assert a.level == HIGH and "new MAC" in a.detail
    w.toggle_watch("ap:9e:41:07:c3:28:d5")


# ------------------------------------------------------------------ topology + report
def test_topology_groups_clients_under_ap_and_ranks_streamers():
    clk = Clock()
    w = World(clock=clk)
    w.ingest({"t": "ap", "b": "aa:bb:cc:00:00:01", "s": b"HomeNet".hex(), "ch": 6, "e": 2, "r": -55, "n": 5})
    for _ in range(14):
        clk.t += 1.5
        w.ingest({"t": "sta", "m": "11:22:33:00:00:01", "b": "aa:bb:cc:00:00:01", "ch": 6, "r": -50, "uf": 1, "ub": 100, "db": 0})
        w.ingest({"t": "sta", "m": "28:57:be:00:00:09", "b": "aa:bb:cc:00:00:01", "ch": 6, "r": -48, "uf": 400, "ub": 150000, "db": 0})
    topo = w.topology()
    assert topo[0]["bssid"] == "aa:bb:cc:00:00:01"
    clients = topo[0]["clients"]
    assert clients[0].addr == "28:57:be:00:00:09"   # streamer ranked first
    assert clients[0].uplink_bps(clk.t) >= 40000


def test_signed_report_roundtrip(tmp_path):
    import hashlib
    import hmac
    import json as _json
    clk = Clock()
    w = World(clock=clk)
    w.ingest({"t": "hello", "fw": "csk-test", "chip": "ESP32", "mac": "aa:bb:cc:dd:ee:ff"})
    w.ingest({"t": "ap", "b": "9e:41:07:c3:28:d5", "s": b"xfinitywifi".hex(), "ch": 1, "e": 0, "r": -45, "n": 9})
    path = w.export_report(str(tmp_path), operator="tester", note="unit")
    doc = _json.loads(open(path).read())
    body = _json.dumps(doc["report"], sort_keys=True, separators=(",", ":")).encode()
    sig = hmac.new(w._report_key(), body, hashlib.sha256).hexdigest()
    assert sig == doc["integrity"]["signature"]
    # tamper detection
    doc["report"]["operator"] = "someone else"
    body2 = _json.dumps(doc["report"], sort_keys=True, separators=(",", ":")).encode()
    assert hmac.new(w._report_key(), body2, hashlib.sha256).hexdigest() != doc["integrity"]["signature"]
    assert os.path.exists(path[:-5] + ".txt")


# ------------------------------------------------------------------ store + monitor
def test_store_new_then_update_tracks_days_and_trust():
    from csk.store import Store
    clk = Clock()
    s = Store(":memory:", clock=clk)
    dev, is_new = s.upsert("AA:BB:CC:00:00:01", "lan", vendor="Hikvision", category="camera", ips=["192.168.1.9"], ports=["554"])
    assert is_new and dev["vendor"] == "Hikvision" and not dev["trusted"]
    clk.t += 26 * 3600  # next day
    dev, is_new = s.upsert("aa:bb:cc:00:00:01", "lan", ips=["192.168.1.10"], ports=["80"])
    assert not is_new and len(dev["days"]) == 2 and set(dev["ips"]) == {"192.168.1.9", "192.168.1.10"}
    assert set(dev["ports"]) == {"554", "80"}
    s.set_trusted("aa:bb:cc:00:00:01", True)
    assert s.get("aa:bb:cc:00:00:01")["trusted"]


def test_netscan_classify_rtsp_is_camera():
    from csk import netscan
    sev, reasons = netscan.classify_host({"ip": "192.168.1.9", "mac": "aa", "ports": [554, 80]}, "", "")
    assert sev == "camera" and any("RTSP" in r for r in reasons)
    sev, _ = netscan.classify_host({"ip": "x", "mac": "y", "ports": [80]}, "Hikvision", "camera")
    assert sev == "camera"
    sev, _ = netscan.classify_host({"ip": "x", "mac": "y", "ports": [80]}, "Acme", "")
    assert sev == ""


class CapNotifier:
    def __init__(self):
        self.sent = []

    def send(self, level, title, detail):
        self.sent.append((level, title, detail))


def test_monitor_learns_then_alerts_on_new_and_camera():
    from csk import monitor
    from csk.store import Store
    clk = Clock()
    s = Store(":memory:", clock=clk)
    note = CapNotifier()
    baseline = {"hosts": [{"ip": "192.168.1.2", "mac": "11:22:33:00:00:aa", "ports": [80]}]}

    scans = [baseline]
    nm = monitor.NetMonitor(s, note, {"monitor": {"learn_minutes": 10}}, scan_fn=lambda: scans[-1], clock=clk)
    assert nm.learning
    assert nm.tick() == []                         # learning: trusts the laptop, no alert
    assert s.get("11:22:33:00:00:aa")["trusted"]

    clk.t += 11 * 60                               # learning window over
    assert not nm.learning
    scans.append({"hosts": baseline["hosts"] + [
        {"ip": "192.168.1.50", "mac": "28:57:be:00:00:09", "ports": [554, 80]},   # Hikvision cam w/ RTSP
        {"ip": "192.168.1.51", "mac": "de:ad:be:ef:00:01", "ports": []}]})         # unknown new device
    alerts = nm.tick()
    titles = [t for _, t, _ in alerts]
    assert "Camera/DVR on your network" in titles
    assert "New device joined your network" in titles
    assert any(lvl == HIGH for lvl, t, _ in alerts if "Camera" in t)
    # the trusted laptop never re-alerts
    assert all("11:22:33" not in d for _, _, d in alerts)
    # repeat scan: camera now in baseline but still untrusted -> still warns, laptop still silent
    note.sent.clear()
    alerts2 = nm.tick()
    assert any("Camera" in t for _, t, _ in alerts2)


# ------------------------------------------------------------------ wifi + apscan
class FakeWifi:
    def __init__(self, hosts, connect_ok=True, has_nmcli=True):
        self._hosts = hosts
        self.connect_ok = connect_ok
        self.has_nmcli = has_nmcli
        self.calls = []

    def available(self):
        return self.has_nmcli

    def active_connection(self):
        return "HomeWifi"

    def active_ssid(self):
        return "HomeWifi"

    def connect(self, ssid, password=None, bssid=None, timeout=30):
        self.calls.append(("connect", ssid, bssid))
        return self.connect_ok, "" if self.connect_ok else "No network with SSID found"

    def restore(self, name, timeout=30):
        self.calls.append(("restore", name))
        return True, ""


def test_wifi_command_construction_and_parse():
    from csk.wifi import Wifi
    log = []

    def runner(args, timeout):
        log.append(args)
        if args[:3] == ["-t", "-f", "ACTIVE,SSID"]:
            return 0, "no:OtherNet\nyes:HomeWifi\n", ""
        if args[:2] == ["-t", "-f"] and "NAME,TYPE,DEVICE" in args:
            return 0, "HomeWifi:802-11-wireless:wlan0\neth0:802-3-ethernet:eth0\n", ""
        return 0, "", ""

    w = Wifi(runner=runner)
    assert w.active_ssid() == "HomeWifi"
    assert w.active_connection() == "HomeWifi"
    ok, _ = w.connect("xfinitywifi", bssid="9e:41:07:c3:28:d5")
    assert ok and ["dev", "wifi", "connect", "xfinitywifi"] == log[-1][2:6] and "bssid" in log[-1]


def test_apscan_connects_scans_classifies_and_restores():
    scan_result = {"hosts": [
        {"ip": "192.168.1.1", "mac": "14:22:3b:00:00:01", "ports": [80]},
        {"ip": "192.168.1.33", "mac": "28:57:be:00:00:09", "ports": [554, 80]}]}
    fw = FakeWifi(scan_result)
    from csk import apscan
    res = apscan.scan_ap(fw, "xfinitywifi", bssid="9e:41:07:c3:28:d5",
                         scan_fn=lambda: scan_result)
    assert res["ok"] and res["restored"]
    assert len(res["cameras"]) == 1 and res["cameras"][0]["mac"] == "28:57:be:00:00:09"
    assert res["cameras"][0]["sev"] == "camera"
    assert ("connect", "xfinitywifi", "9e:41:07:c3:28:d5") in fw.calls
    assert ("restore", "HomeWifi") in fw.calls


def test_apscan_restores_even_when_connect_fails():
    fw = FakeWifi({"hosts": []}, connect_ok=False)
    from csk import apscan
    res = apscan.scan_ap(fw, "securenet", scan_fn=lambda: {"hosts": []})
    assert not res["ok"] and "connect failed" in res["error"]
    assert ("restore", "HomeWifi") in fw.calls and res["restored"]


def test_apscan_no_nmcli():
    from csk import apscan
    res = apscan.scan_ap(FakeWifi({"hosts": []}, has_nmcli=False), "x", scan_fn=lambda: {"hosts": []})
    assert not res["ok"] and "nmcli" in res["error"]


# ------------------------------------------------------------------ persistent cross-day following
def test_store_sightings_and_recurring():
    from csk.store import Store
    clk = Clock()
    s = Store(":memory:", clock=clk)
    # same device at home then work, two different days
    s.record_sighting("aa:bb:cc:00:00:01", "home", kind="ble", title="Unknown BLE", rssi=-60)
    clk.t += 26 * 3600
    s.record_sighting("aa:bb:cc:00:00:01", "work", kind="ble", title="Unknown BLE", rssi=-55)
    summ = s.sighting_summary("aa:bb:cc:00:00:01")
    assert summ["n_locations"] == 2 and summ["n_days"] == 2
    rec = s.recurring(min_locations=2, min_days=2)
    assert rec and rec[0]["mac"] == "aa:bb:cc:00:00:01"
    # trusted devices are excluded
    s.set_trusted("aa:bb:cc:00:00:01", True)
    assert s.recurring(min_locations=2, min_days=2) == []


def test_world_persists_and_alerts_on_recurrence_across_restart(tmp_path):
    from csk.store import Store
    db = str(tmp_path / "b.db")
    clk = Clock()
    # ---- session 1: device seen at "home"
    s1 = Store(db, clock=clk)
    w1 = World(clock=clk)
    w1.store = s1
    w1.set_location("home")
    for _ in range(3):
        clk.t += 25
        w1.ingest({"t": "ble", "a": "c4:11:22:33:44:55", "at": 0, "r": -60, "p": "020106"})
        w1.persist_sightings(min_interval=0)
    s1.close()
    # ---- session 2 (restart), another day, at "work": same device reappears
    clk.t += 26 * 3600
    s2 = Store(db, clock=clk)
    w2 = World(clock=clk)
    w2.store = s2
    w2.set_location("work")
    for _ in range(3):
        clk.t += 25
        w2.ingest({"t": "ble", "a": "c4:11:22:33:44:55", "at": 0, "r": -58, "p": "020106"})
        w2.persist_sightings(min_interval=0)
    w2.evaluate()
    a = w2.alerts["recur:c4:11:22:33:44:55"]
    assert a.level == HIGH and "2" in a.detail
    # randomized addresses are never persisted
    w2.ingest({"t": "ble", "a": "da:11:22:33:44:55", "at": 1, "r": -50, "p": "020106"})
    w2.persist_sightings(min_interval=0)
    assert s2.sighting_summary("da:11:22:33:44:55")["n_days"] == 0


# ------------------------------------------------------------------ discovery
def test_parse_ssdp_and_wsd():
    from csk import discover
    h = discover.parse_ssdp("HTTP/1.1 200 OK\r\nSERVER: Linux/3 UPnP/1.0 Hikvision-Webs\r\nST: x\r\n")
    assert "Hikvision" in h["SERVER"]
    w = discover.parse_wsd(b"<e><d:Types>dn:NetworkVideoTransmitter</d:Types><d:XAddrs>http://192.168.1.50/onvif/device_service</d:XAddrs></e>")
    assert w["is_camera"] and "192.168.1.50" in w["ips"]


def test_parse_mdns_ptr_and_a():
    import struct
    from csk import discover
    # header: 1 question(skip via building), 1 answer PTR
    # Build a minimal mDNS response with an A record for a named host.
    def name(s):
        out = b""
        for lab in s.split("."):
            out += bytes([len(lab)]) + lab.encode()
        return out + b"\x00"
    pkt = struct.pack(">HHHHHH", 0, 0x8400, 0, 1, 0, 0)
    rec = name("reolink-cam.local") + struct.pack(">HHIH", 1, 1, 120, 4) + bytes([192, 168, 1, 7])
    info = discover.parse_mdns(pkt + rec)
    assert "192.168.1.7" in info["ips"]


def test_discover_camera_hint_and_classify():
    from csk import discover, netscan
    disco = {"onvif": True, "services": {"_rtsp._tcp"}, "server": "", "model": "", "names": {"FrontDoor"}}
    hints = discover.camera_hint(disco)
    assert any("ONVIF" in h for h in hints) and any("RTSP" in h for h in hints)
    sev, reasons = netscan.classify_host({"ip": "1", "mac": "a", "ports": [80], "disco": disco}, "", "")
    assert sev == "camera"


# ------------------------------------------------------------------ agent
def test_agent_rules_quiet_and_threat():
    from csk import agent
    clk = Clock()
    w = World(clock=clk)
    quiet = agent.consult(w)
    assert quiet["via"] == "rules" and quiet["mood"] == "calm" and quiet["speech"]
    # inject a high alert
    for i in range(6):
        w.ingest({"t": "ap", "b": "00:c0:ca:00:00:01", "s": f"n{i}".encode().hex(), "ch": 11, "e": 0, "r": -60, "n": 3})
    w.evaluate()
    hot = agent.consult(w)
    assert hot["mood"] in ("alert", "rage") and hot["actions"]


def test_agent_summarize_has_expected_keys():
    from csk import agent
    w = World(clock=Clock())
    s = agent.summarize_state(w)
    for k in ("location", "high", "warn", "followers", "cameras", "trackers", "recurring"):
        assert k in s


# ------------------------------------------------------------------ llm autodetect
def test_detect_endpoint_and_llm_path(monkeypatch):
    from csk import agent
    agent._detect_cache.update(t=0.0, result=None)
    calls = {}

    def fake_get(url, timeout=1.5):
        calls["models_url"] = url
        if url.endswith("/v1/models"):
            return {"data": [{"id": "qwen2.5-0.5b"}]}
        raise AssertionError

    monkeypatch.setattr(agent, "_get_json", fake_get)
    ep = agent.detect_endpoint({}, ttl=0)
    assert ep and ep["model"] == "qwen2.5-0.5b" and ep["url"].endswith("/v1/chat/completions")

    # consult(auto) should take the llm path when a chat call succeeds
    def fake_urlopen(req, timeout=20):
        class R:
            def read(self):
                return json.dumps({"choices": [{"message": {"content":
                    "THE AIR REEKS OF DECEPTION.\n- Forget that network."}}]}).encode()
        return R()
    import csk.agent as a
    monkeypatch.setattr(a.urllib.request, "urlopen", fake_urlopen)
    w = World(clock=Clock())
    out = agent.consult(w, {"agent": {"backend": "auto"}})
    assert out["via"] == "local LLM" and "DECEPTION" in out["speech"] and out["actions"]


def test_detect_endpoint_none_when_down(monkeypatch):
    from csk import agent
    agent._detect_cache.update(t=0.0, result=None)

    def boom(url, timeout=1.5):
        raise OSError("refused")

    monkeypatch.setattr(agent, "_get_json", boom)
    assert agent.detect_endpoint({}, ttl=0) is None
    # and consult falls back to rules
    out = agent.consult(World(clock=Clock()), {"agent": {"backend": "auto"}})
    assert out["via"] == "rules"


# ------------------------------------------------------------------ drone remote id
def test_droneid_decode_ble_roundtrip():
    from csk import droneid
    payload = droneid.build_pack_ble(serial="ABC123XYZ", lat=51.5, lon=-0.12, op_lat=51.501, op_lon=-0.119)
    info = droneid.from_ble(payload)
    assert info and info["serial"] == "ABC123XYZ" and info["via"] == "BLE"
    assert abs(info["lat"] - 51.5) < 1e-5 and abs(info["operator_lat"] - 51.501) < 1e-5


def test_droneid_not_a_drone():
    from csk import droneid
    assert droneid.from_ble(bytes.fromhex("020106030300ff")) is None


def test_world_drone_alert_with_operator_location():
    from csk import droneid
    clk = Clock()
    w = World(clock=clk)
    p = droneid.build_pack_ble(serial="DJI-RID-77", lat=40.0, lon=-73.0, op_lat=40.001, op_lon=-73.002).hex()
    w.ingest({"t": "ble", "a": "d0:21:f0:00:00:01", "at": 1, "r": -55, "p": p})
    d = w.devices["ble:d0:21:f0:00:00:01"]
    assert d.category == "drone" and d.drone["serial"] == "DJI-RID-77"
    w.evaluate()
    a = w.alerts["drone:ble:d0:21:f0:00:00:01"]
    assert a.level == HIGH and "operator at 40.001" in a.detail and "DJI-RID-77" in a.detail


def test_world_drone_wifi_odid_message():
    from csk import droneid
    w = World(clock=Clock())
    pack = droneid.build_pack_ble(serial="WIFI-DRONE")
    # firmware forwards just the ODID message pack (strip the BLE AD + service header)
    i = pack.index(bytes([0xFA, 0xFF, 0x0D, 0x00])) + 4
    w.ingest({"t": "odid", "a": "aa:bb:cc:dd:ee:ff", "r": -60, "p": pack[i:].hex()})
    w.evaluate()
    d = w.devices["ble:odid:aa:bb:cc:dd:ee:ff"]
    assert d.category == "drone" and d.drone["serial"] == "WIFI-DRONE" and d.drone["via"] == "WiFi"
