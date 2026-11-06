"""pygame UI built for a handheld: big text, black AMOLED background, all gamepad.

Controls (letters as printed on the Retroid; swap in the menu if needed)
  D-pad / stick   move selection          L1 / R1     switch tab (Alerts/Map/BLE/Networks/Clients/Follow)
  A               open / hunt             B           back
  X               drop location checkpoint ("I've moved")
  Y               mark device as mine (allowlist) / unmark
  START           menu                    SELECT      mute sound
  START + SELECT  quit
"""
from __future__ import annotations

import array
import json
import math
import os
import time

import pygame

from . import art
from . import controls as C
from .bleparse import HIGH, INFO, WARN
from .world import CONFIG_DIR, World

CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

BG = (0, 0, 0)
FG = (225, 230, 235)
DIM = (110, 118, 128)
FAINT = (40, 44, 50)
ACCENT = (64, 196, 255)
SEL_BG = (22, 40, 58)
LEVEL_COL = {INFO: (120, 180, 220), WARN: (255, 176, 32), HIGH: (255, 64, 64)}
CAT_COL = {"tracker": (255, 120, 80), "camera": (255, 176, 32), "alpr": (255, 176, 32), "iot": (180, 160, 255)}

TABS = ["ALERTS", "MAP", "BLE", "NETWORKS", "CLIENTS", "FOLLOW", "HISTORY"]


class MapRow:
    """One selectable line in the MAP tab: an AP header or an indented client."""
    __slots__ = ("id", "dev", "is_ap", "bssid", "clients")

    def __init__(self, dev, is_ap, bssid, clients=0):
        self.id = dev.id if dev else None
        self.dev = dev
        self.is_ap = is_ap
        self.bssid = bssid
        self.clients = clients


def load_config() -> dict:
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(cfg: dict) -> None:
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=1)
    except OSError:
        pass


def ago(sec: float) -> str:
    sec = max(0, int(sec))
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m"
    return f"{sec // 3600}h{(sec % 3600) // 60:02d}"


class Sound:
    def __init__(self):
        self.ok = False
        self.muted = False
        try:
            pygame.mixer.init(22050, -16, 1, 512)
            freq, _, ch = pygame.mixer.get_init()
            self.click = self._tone(freq, ch, 1800, 0.012, 0.6)
            self.alarm = self._tone(freq, ch, 880, 0.25, 0.5, wobble=True)
            self.blip = self._tone(freq, ch, 1200, 0.05, 0.35)
            self.ok = True
        except Exception:
            pass

    @staticmethod
    def _tone(freq, ch, hz, dur, vol, wobble=False):
        n = int(freq * dur)
        buf = array.array("h")
        for i in range(n):
            f = hz * (1 + 0.5 * (i // (n // 4 or 1) % 2)) if wobble else hz
            env = min(1.0, (n - i) / (n * 0.3 + 1))
            v = int(32767 * vol * env * math.sin(2 * math.pi * f * i / freq))
            for _ in range(ch):
                buf.append(v)
        return pygame.mixer.Sound(buffer=buf.tobytes())

    def play(self, name: str) -> None:
        if self.ok and not self.muted:
            try:
                getattr(self, name).play()
            except Exception:
                pass


class App:
    def __init__(self, source, world: World, window: tuple[int, int] | None = None):
        self.src, self.w = source, world
        self.cfg = load_config()
        pygame.init()
        pygame.display.set_caption(art.NAME)
        if window:
            self.screen = pygame.display.set_mode(window)
        else:
            self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
            pygame.mouse.set_visible(False)
        self.W, self.H = self.screen.get_size()
        self.u = max(12, self.H // 24)          # base text size
        self.f_s = pygame.font.Font(None, int(self.u * 0.85))
        self.f_m = pygame.font.Font(None, int(self.u * 1.1))
        self.f_l = pygame.font.Font(None, int(self.u * 1.6))
        self.f_xl = pygame.font.Font(None, int(self.u * 6))
        self.inp = C.Input(self.cfg.get("layout", "nintendo"), self.cfg.get("raw_buttons"))
        self.snd = Sound()
        self.snd.muted = self.cfg.get("muted", False)
        self.rumble_on = self.cfg.get("rumble", True)
        self.tab = 0
        self.sel = {t: 0 for t in TABS}
        self.view = "list"          # list | detail | hunt | menu | buttontest
        self.detail_id: str | None = None
        self.menu_sel = 0
        self.toast: tuple[str, int, float] | None = None
        self.running = True
        self._last_eval = 0.0
        self._next_click = 0.0
        self._hunt_locked = False
        self._scan: dict = {"running": False, "steps": [], "result": None, "sel": 0}
        self._boot_until = time.monotonic() + 3.0
        self.view = "boot"
        self.f_title = pygame.font.Font(None, int(self.u * 2.1))
        # Persistent cross-day memory
        self.locations = self.cfg.get("locations", ["home", "work", "out"])
        self.loc_idx = 0
        try:
            from .store import Store
            self.w.store = Store()
            self.w.set_location(self.locations[self.loc_idx])
        except Exception:
            self.w.store = None
        self.w.new_alert_cb = None

    # ---------------------------------------------------------------- helpers
    def text(self, s, font, col, x, y, right=False, center=False, maxw=None):
        if maxw:
            while s and font.size(s)[0] > maxw:
                s = s[:-3] + ".." if len(s) > 2 else ""
        img = font.render(s, True, col)
        r = img.get_rect()
        if right:
            r.topright = (x, y)
        elif center:
            r.midtop = (x, y)
        else:
            r.topleft = (x, y)
        self.screen.blit(img, r)
        return r

    def notify(self, msg: str, level: int = INFO, secs: float = 3.0) -> None:
        self.toast = (msg, level, time.monotonic() + secs)

    def save(self) -> None:
        self.cfg.update(layout=self.inp.layout, muted=self.snd.muted, rumble=self.rumble_on,
                        locations=self.locations)
        save_config(self.cfg)

    def rssi_bar(self, x, y, w, h, rssi, col=ACCENT):
        frac = max(0.0, min(1.0, (rssi + 100) / 70))
        pygame.draw.rect(self.screen, FAINT, (x, y, w, h), border_radius=h // 3)
        pygame.draw.rect(self.screen, col, (x, y, int(w * frac), h), border_radius=h // 3)

    # ------------------------------------------------------------------ items
    def items(self) -> list:
        name = TABS[self.tab]
        if name == "ALERTS":
            return self.w.sorted_alerts()
        if name == "MAP":
            rows = []
            for g in self.w.topology():
                ap = g["ap"]
                rows.append(MapRow(ap, True, g["bssid"], len(g["clients"])))
                for c in g["clients"]:
                    rows.append(MapRow(c, False, g["bssid"]))
            return rows
        if name == "BLE":
            return self.w.recent(("ble",))
        if name == "NETWORKS":
            return self.w.recent(("ap",))
        if name == "CLIENTS":
            return self.w.recent(("wifi",))
        if name == "HISTORY":
            return self.w.store.recurring(min_locations=1, min_days=1) if self.w.store else []
        return self.w.followers()

    def selected_device_id(self) -> str | None:
        it = self.items()
        if not it:
            return None
        i = min(self.sel[TABS[self.tab]], len(it) - 1)
        x = it[i]
        if isinstance(x, dict):   # HISTORY row: map MAC to a live device if present
            mac = x.get("mac", "")
            for d in self.w.devices.values():
                if d.addr == mac:
                    return d.id
            return None
        return getattr(x, "dev_id", None) or getattr(x, "id", None)

    # ---------------------------------------------------------------- actions
    MENU = ["Resume", "Location", "Face buttons", "Sound", "Rumble", "BLE scanning", "WiFi scanning",
            "Mark everything visible as mine", "Export signed report", "Clear alerts", "Button test", "Quit"]

    def menu_value(self, i: int) -> str:
        s = self.w.sensor
        return {1: self.w.location + "  (A to change)",
                2: self.inp.layout.upper() + (" (A right)" if self.inp.layout == "nintendo" else " (A bottom)"),
                3: "OFF" if self.snd.muted else "ON", 4: "ON" if self.rumble_on else "OFF",
                5: "ON" if s.get("ble", 1) else "OFF", 6: "ON" if s.get("wifi", 1) else "OFF"}.get(i, "")

    def menu_activate(self, i: int) -> None:
        if i == 0:
            self.view = "list"
        elif i == 1:
            self.cycle_location()
        elif i == 2:
            self.inp.toggle_layout()
        elif i == 3:
            self.snd.muted = not self.snd.muted
        elif i == 4:
            self.rumble_on = not self.rumble_on
            if self.rumble_on:
                self.inp.rumble(0.6, 200)
        elif i == 5:
            on = not self.w.sensor.get("ble", 1)
            self.src.send(f"ble {int(on)}")
            self.w.sensor["ble"] = int(on)
        elif i == 6:
            on = not self.w.sensor.get("wifi", 1)
            self.src.send(f"wifi {int(on)}")
            self.w.sensor["wifi"] = int(on)
        elif i == 7:
            n = self.w.allow_all_visible()
            self.notify(f"{n} devices marked as yours")
            self.view = "list"
        elif i == 8:
            self.export_report()
            self.view = "list"
        elif i == 9:
            self.w.alerts.clear()
            self.notify("Alerts cleared")
        elif i == 10:
            self.view = "buttontest"
            self._bt_started = time.monotonic()
        elif i == 11:
            self.running = False
        self.save()

    def cycle_location(self) -> None:
        if not self.locations:
            return
        self.loc_idx = (self.loc_idx + 1) % len(self.locations)
        self.w.set_location(self.locations[self.loc_idx])
        self.notify(f"Location set to '{self.w.location}' - sightings now logged here", INFO, 4)

    def export_report(self) -> None:
        out = os.path.join(os.path.expanduser(self.cfg.get("log_dir", "~/csk-logs")), "reports")
        try:
            path = self.w.export_report(out, operator=self.cfg.get("operator", ""))
            self.notify(f"Report saved: {os.path.basename(path)} (+.txt)", INFO, 6)
            self.snd.play("blip")
        except Exception as e:
            self.notify(f"Export failed: {e}", WARN, 6)

    def enter_hunt(self, dev_id: str) -> None:
        d = self.w.devices.get(dev_id)
        if not d:
            return
        self.detail_id = dev_id
        self.view = "hunt"
        if d.kind in ("ap", "wifi") and d.ch:
            self.src.send(f"ch {d.ch}")
            self._hunt_locked = True

    def exit_hunt(self) -> None:
        if self._hunt_locked:
            self.src.send("ch 0")
            self._hunt_locked = False
        self.view = "detail"

    def handle(self, act: str) -> None:
        if act == "QUIT":
            self.running = False
            return
        v = self.view
        if v == "boot":
            self.view = "list"
            return
        if v == "buttontest":
            if act == C.START or (time.monotonic() - self._bt_started > 2 and act == "HOME"):
                self.view = "menu"
            return
        if v == "menu":
            if act == C.UP:
                self.menu_sel = (self.menu_sel - 1) % len(self.MENU)
            elif act == C.DOWN:
                self.menu_sel = (self.menu_sel + 1) % len(self.MENU)
            elif act in (C.A, C.RIGHT, C.LEFT):
                self.menu_activate(self.menu_sel)
            elif act in (C.B, C.START):
                self.view = "list"
            return
        if act == C.START:
            self.view, self.menu_sel = "menu", 0
            return
        if act == C.SELECT:
            self.snd.muted = not self.snd.muted
            self.notify("Sound " + ("muted" if self.snd.muted else "on"))
            self.save()
            return
        if act == C.X:
            n = self.w.checkpoint()
            self.notify(f"Checkpoint {n} dropped - devices seen here are now compared")
            self.snd.play("blip")
            return
        if v == "scanconfirm":
            if act in (C.A,):
                self._start_ap_scan()
            elif act in (C.B, C.START):
                self.view = "detail"
            return
        if v == "scanview":
            res = self._scan.get("result")
            n = len(res["hosts"]) if res and res.get("hosts") else 0
            if act == C.B and not self._scan.get("running"):
                self.view = "detail"
            elif act == C.UP and n:
                self._scan["sel"] = (self._scan.get("sel", 0) - 1) % n
            elif act == C.DOWN and n:
                self._scan["sel"] = (self._scan.get("sel", 0) + 1) % n
            elif act == C.A:
                did = self.selected_device_id_scan()
                if did and did in self.w.devices:
                    self.detail_id, self.view = did, "detail"
            return
        if v == "hunt":
            if act == C.B:
                self.exit_hunt()
            elif act == C.Y:
                self._toggle_allow(self.detail_id)
            return
        if v == "detail":
            d = self.w.devices.get(self.detail_id or "")
            if act == C.B:
                self.view = "list"
            elif act == C.A and self.detail_id in self.w.devices:
                self.enter_hunt(self.detail_id)
            elif act == C.Y:
                self._toggle_allow(self.detail_id)
            elif act == C.R1 and self.detail_id:
                on = self.w.toggle_watch(self.detail_id)
                self.notify("On watchlist - alerts whenever it's in range" if on else "Removed from watchlist",
                            HIGH if on else INFO)
            elif act == C.L1:
                self.karma_test(self.detail_id)
            elif act == C.R2 and d and d.kind == "ap":
                self.view = "scanconfirm"
            return
        # list view
        tab = TABS[self.tab]
        n = len(self.items())
        if act in (C.R1, C.RIGHT):
            self.tab = (self.tab + 1) % len(TABS)
        elif act in (C.L1, C.LEFT):
            self.tab = (self.tab - 1) % len(TABS)
        elif act == C.UP and n:
            self.sel[tab] = (self.sel[tab] - 1) % n
        elif act == C.DOWN and n:
            self.sel[tab] = (self.sel[tab] + 1) % n
        elif act == C.L2 and n:
            self.sel[tab] = max(0, self.sel[tab] - 8)
        elif act == C.R2 and n:
            self.sel[tab] = min(n - 1, self.sel[tab] + 8)
        elif act == C.A:
            did = self.selected_device_id()
            if tab == "ALERTS":
                it = self.items()
                if it:
                    it[min(self.sel[tab], n - 1)].seen_by_user = True
            if did and did in self.w.devices:
                self.detail_id, self.view = did, "detail"
        elif act == C.Y:
            self._toggle_allow(self.selected_device_id())

    # ----------------------------------------------------- connect & scan an AP
    def _start_ap_scan(self) -> None:
        import threading

        from . import apscan
        from .wifi import Wifi

        d = self.w.devices.get(self.detail_id or "")
        if not d or d.kind != "ap":
            self.view = "detail"
            return
        ssid = next((s for s in d.ssids if s), "")
        if not ssid:
            self.notify("That AP has a hidden SSID - can't join it by name", WARN)
            self.view = "detail"
            return
        secured = d.enc not in (0, -1)
        self._scan = {"running": True, "steps": [], "result": None, "ssid": ssid, "sel": 0}
        self.view = "scanview"

        def worker():
            try:
                res = apscan.scan_ap(
                    Wifi(), ssid, bssid=d.addr if not secured else None,
                    password=None,
                    progress=lambda m: self._scan["steps"].append(m))
                self._scan["result"] = res
            except Exception as e:
                self._scan["result"] = {"ok": False, "error": str(e), "steps": self._scan["steps"],
                                        "hosts": [], "cameras": [], "restored": False}
            finally:
                self._scan["running"] = False

        # NOTE: connecting to an open fake AP needs no password; a secured one we
        # can't join without creds and nmcli will just fail, which we report.
        threading.Thread(target=worker, daemon=True).start()

    def selected_device_id_scan(self) -> str | None:
        res = self._scan.get("result")
        if not res or not res.get("hosts"):
            return None
        hosts = res["hosts"]
        i = min(self._scan.get("sel", 0), len(hosts) - 1)
        mac = hosts[i].get("mac")
        return "wifi:" + mac if mac else None

    def karma_test(self, dev_id: str | None) -> None:
        if self.w.karma_pending:
            self.notify("Karma test already running...")
            return
        cmd = self.w.start_karma(dev_id or "")
        if not cmd:
            self.notify("Karma test works on WiFi networks only")
            return
        self.src.send(cmd)
        self._karma_seen = len(self.w.karma_results)
        self._karma_deadline = time.monotonic() + 6
        self.notify(f"Karma test: asking for '{self.w.karma_pending[0]}' on ch {self.w.karma_pending[1]}...", INFO, 4)

    def _check_karma(self) -> None:
        res = self.w.karma_results
        if len(res) > getattr(self, "_karma_seen", len(res)):
            r = res[-1]
            self._karma_seen = len(res)
            if r["hits"]:
                self.notify(f"KARMA CONFIRMED: {len(r['hits'])} AP(s) answered for a fake network", HIGH, 8)
                self.snd.play("alarm")
                if self.rumble_on:
                    self.inp.rumble(1.0, 600)
            else:
                self.notify(f"No answer on ch {r['ch']} - not a Karma AP (could still be a static fake)", INFO, 6)
        elif self.w.karma_pending and time.monotonic() > getattr(self, "_karma_deadline", 0):
            self.w.karma_pending = None
            self.notify("Karma test timed out - is the ESP32 firmware up to date?", WARN, 5)

    def _toggle_allow(self, dev_id: str | None) -> None:
        if not dev_id:
            return
        mine = self.w.toggle_allow(dev_id)
        self.notify("Marked as yours - ignored" if mine else "Unmarked - will be watched again")

    # ----------------------------------------------------------------- render
    def mood(self) -> str:
        now = self.w.clock()
        levels = [a.level for a in self.w.alerts.values() if a.active(now)]
        if not levels:
            return "calm"
        return "rage" if max(levels) >= HIGH else "alert"

    def draw_header(self) -> int:
        u, s = self.u, self.w.sensor
        h = int(u * 1.8)
        pygame.draw.rect(self.screen, (10, 12, 16), (0, 0, self.W, h))
        ok = self.w.sensor_ok()
        sc = max(1, h // 18)
        sw, sh = art.skull_size(sc)
        art.draw_skull(self.screen, u // 2, (h - sh) // 2, sc, self.mood(), int(time.monotonic() * 3))
        self.text(art.SHORT, self.f_m, LEVEL_COL[HIGH] if self.mood() == "rage" else ACCENT,
                  u // 2 + sw + u // 3, h // 2 - self.f_m.get_height() // 2)
        xoff = u // 2 + sw + u // 3 + self.f_m.size(art.SHORT)[0] + u // 2
        status = (f"ch {s.get('ch', '?')}{' LOCK' if s.get('lock') else ''}  "
                  f"BLE {'on' if s.get('ble', 1) else 'off'}  WiFi {'on' if s.get('wifi', 1) else 'off'}"
                  if ok else self.src.status)
        self.text(status, self.f_s, FG if ok else LEVEL_COL[WARN], xoff, h // 2 - self.f_s.get_height() // 2,
                  maxw=self.W - xoff - u * 9)
        right = f"@{self.w.location}   {len(self.w.devices)} dev   {time.strftime('%H:%M')}"
        if self.snd.muted:
            right = "MUTED   " + right
        self.text(right, self.f_s, DIM, self.W - u // 2, h // 2 - self.f_s.get_height() // 2, right=True)
        return h

    def draw_tabs(self, y: int) -> int:
        u = self.u
        h = int(u * 1.4)
        n = len(TABS)
        tw = self.W / n
        now = self.w.clock()
        active_high = any(a.active(now) and a.level == HIGH for a in self.w.alerts.values())
        for i, t in enumerate(TABS):
            x = int(i * tw)
            if i == self.tab:
                pygame.draw.rect(self.screen, SEL_BG, (x, y, int(tw), h))
                pygame.draw.rect(self.screen, ACCENT, (x, y + h - 4, int(tw), 4))
            label = t
            if t == "ALERTS":
                cnt = sum(1 for a in self.w.alerts.values() if a.active(now))
                label = f"ALERTS {cnt}" if cnt else t
            col = LEVEL_COL[HIGH] if (t == "ALERTS" and active_high) else (FG if i == self.tab else DIM)
            self.text(label, self.f_s, col, int(x + tw / 2), y + (h - self.f_s.get_height()) // 2, center=True)
        return y + h

    def draw_footer(self, hints: list[tuple[str, str]]) -> int:
        u = self.u
        h = int(u * 1.3)
        y = self.H - h
        pygame.draw.rect(self.screen, (10, 12, 16), (0, y, self.W, h))
        x = u // 2
        for btn, what in hints:
            r = self.f_s.render(btn, True, BG)
            bw = r.get_width() + u // 2
            pygame.draw.rect(self.screen, DIM, (x, y + h // 6, bw, h * 2 // 3), border_radius=h // 3)
            self.screen.blit(r, (x + u // 4, y + (h - r.get_height()) // 2))
            x += bw + u // 4
            x = self.text(what, self.f_s, DIM, x, y + (h - self.f_s.get_height()) // 2).right + u // 2
        return y

    def draw_list(self, top: int, bottom: int) -> None:
        u = self.u
        tab = TABS[self.tab]
        items = self.items()
        row_h = int(u * 2.2)
        rows = max(1, (bottom - top) // row_h)
        if not items:
            msg = {"ALERTS": "No alerts. Scanning...", "FOLLOW": "Press X each time you've moved somewhere new.",
                   "MAP": "No WiFi clients associated yet. Listening...",
                   "HISTORY": "No history yet. Set your Location (menu) at each place you go;\ndevices that reappear across places/days are remembered here - even after a restart.",
                   }.get(tab, "Nothing seen yet")
            self.text(msg, self.f_m, DIM, self.W // 2, top + u * 2, center=True)
            if tab == "FOLLOW":
                self.text("Devices seen at 3+ of your locations will alert as FOLLOWING.", self.f_s, DIM,
                          self.W // 2, top + u * 4, center=True)
            return
        sel = min(self.sel[tab], len(items) - 1)
        self.sel[tab] = sel
        start = max(0, min(sel - rows // 2, len(items) - rows))
        now = self.w.clock()
        for i, it in enumerate(items[start:start + rows]):
            idx = start + i
            y = top + i * row_h
            if idx == sel:
                pygame.draw.rect(self.screen, SEL_BG, (0, y, self.W, row_h))
                pygame.draw.rect(self.screen, ACCENT, (0, y, 6, row_h))
            if tab == "ALERTS":
                self._row_alert(it, y, row_h, now)
            elif tab == "MAP":
                self._row_map(it, y, row_h, now)
            elif tab == "HISTORY":
                self._row_history(it, y, row_h, now)
            else:
                self._row_device(it, y, row_h, now, tab)
        if len(items) > rows:  # scrollbar
            sb_h = (bottom - top) * rows / len(items)
            sb_y = top + (bottom - top - sb_h) * start / max(1, len(items) - rows)
            pygame.draw.rect(self.screen, FAINT, (self.W - 6, int(sb_y), 4, int(sb_h)))

    def _row_alert(self, a, y, h, now):
        u = self.u
        col = LEVEL_COL[a.level] if a.active(now) else DIM
        pygame.draw.circle(self.screen, col, (u, y + h // 2), u // 3)
        if not a.seen_by_user and a.active(now):
            pygame.draw.circle(self.screen, FG, (u, y + h // 2), u // 3, 2)
        self.text(a.title, self.f_m, col if a.level else FG, u * 2, y + u // 5, maxw=self.W - u * 8)
        self.text(a.detail, self.f_s, DIM, u * 2, y + int(u * 1.15), maxw=self.W - u * 8)
        self.text(("now" if a.active(now) else ago(now - a.last) + " ago"), self.f_s, DIM, self.W - u, y + u // 5, right=True)
        self.text(f"for {ago(a.last - a.first)}", self.f_s, DIM, self.W - u, y + int(u * 1.15), right=True)

    def _row_map(self, row, y, h, now):
        u = self.u
        if row.is_ap:
            ap = row.dev
            if ap is None:   # clients whose AP we haven't heard a beacon from
                self.text(f"(unknown AP {row.bssid})", self.f_m, DIM, u, y + u // 5)
                self.text(f"{row.clients} client(s)  -  no beacon seen", self.f_s, DIM, u, y + int(u * 1.15))
                return
            hostile = self.w.watched_match(ap) is not None
            sus = bool(ap.suspicion or ap.karma)
            col = LEVEL_COL[HIGH] if (hostile or sus) else FG
            ssid = next(iter(ap.ssids), "") if len(ap.ssids) == 1 else (f"{len(ap.ssids)} SSIDs" if ap.ssids else "<hidden>")
            flag = ("  [WATCH]" if hostile else "") + ("  [KARMA]" if ap.karma else "") + ("  [SUSPECT]" if sus and not ap.karma else "")
            self.text(f"{ssid}{flag}", self.f_m, col, u, y + u // 5, maxw=self.W - u * 14)
            sec = ["OPEN", "WEP", "WPA"][ap.enc] if 0 <= ap.enc <= 2 else "?"
            self.text(f"{ap.addr}  ch{ap.ch}  {sec}  {ap.vendor or ''}", self.f_s, DIM, u, y + int(u * 1.15), maxw=self.W - u * 14)
            self.text(f"{row.clients} client(s)", self.f_s, ACCENT, self.W - u, y + u // 5, right=True)
            self.rssi_bar(self.W - u * 9, y + int(u * 1.3), u * 5, u // 2, ap.rssi, col if col != FG else ACCENT)
        else:
            c = row.dev
            mine = self.w.is_allowed(c.id)
            bps = c.uplink_bps(now)
            streaming = bps >= 40000
            col = LEVEL_COL[WARN] if streaming else DIM if mine else FG
            # tree connector
            pygame.draw.line(self.screen, FAINT, (u * 2, y), (u * 2, y + h), 2)
            pygame.draw.line(self.screen, FAINT, (u * 2, y + h // 2), (u * 3, y + h // 2), 2)
            label = c.title() + ("  [mine]" if mine else "")
            if streaming:
                label += "  STREAMING"
            self.text(label, self.f_s, col, int(u * 3.5), y + u // 4, maxw=self.W - u * 16)
            up = f"up {bps / 1000:.0f} kB/s" if bps > 1000 else "idle"
            self.text(f"{c.addr}   {up}", self.f_s, DIM, int(u * 3.5), y + int(u * 1.2), maxw=self.W - u * 16)
            self.rssi_bar(self.W - u * 9, y + u // 2, u * 5, u // 2, c.rssi, col if streaming else ACCENT)
            self.text(f"{c.rssi}", self.f_s, FG, self.W - u, y + u // 3, right=True)

    def _row_history(self, r, y, h, now):
        u = self.u
        dev = r.get("device", {})
        title = (dev.get("title") or "").strip() or r["mac"]
        cat = dev.get("category", "")
        multi = r["n_locations"] >= 2 and r["n_days"] >= 2
        col = LEVEL_COL[HIGH] if multi else LEVEL_COL[WARN] if (r["n_locations"] >= 2 or r["n_days"] >= 2) else FG
        trusted = dev.get("trusted")
        if trusted:
            col = DIM
        self.text(title + ("  [mine]" if trusted else ""), self.f_m, col, u, y + u // 5, maxw=self.W - u * 12)
        locs = ", ".join(r["locations"][:4]) + ("..." if r["n_locations"] > 4 else "")
        sub = f"{r['mac']}  {cat or dev.get('kind', '')}  seen at: {locs}"
        self.text(sub, self.f_s, DIM, u, y + int(u * 1.15), maxw=self.W - u * 12)
        self.text(f"{r['n_locations']} place(s)", self.f_s, col, self.W - u, y + u // 5, right=True)
        span = ago(now - r["last"]) if r["last"] else "?"
        self.text(f"{r['n_days']} day(s) - last {span} ago", self.f_s, DIM, self.W - u, y + int(u * 1.15), right=True)

    def _row_device(self, d, y, h, now, tab):
        u = self.u
        mine = self.w.is_allowed(d.id)
        hostile = self.w.watched_match(d) is not None
        col = LEVEL_COL[HIGH] if hostile or d.karma else DIM if mine else CAT_COL.get(d.category, FG)
        title = d.title() + ("  [mine]" if mine else "") + ("  [WATCH]" if hostile else "") + ("  [KARMA]" if d.karma else "")
        self.text(title, self.f_m, col, u, y + u // 5, maxw=self.W - u * 12)
        sub = d.addr
        if d.kind == "ap":
            sub += f"  ch{d.ch}  {['OPEN', 'WEP', 'WPA'][d.enc] if 0 <= d.enc <= 2 else ''}"
        elif d.kind == "wifi":
            bps = d.uplink_bps(now)
            if bps > 1000:
                sub += f"  up {bps / 1000:.0f} kB/s"
            if d.ssids:
                sub += "  probes: " + ", ".join(sorted(d.ssids))[:60]
        if d.vendor and d.vendor not in title:
            sub += f"  {d.vendor}"
        if tab == "FOLLOW":
            sub = f"seen at {len(d.checkpoints)} of {self.w.checkpoint_idx + 1} locations  |  {d.kind.upper()}  {d.addr}"
        self.text(sub, self.f_s, DIM, u, y + int(u * 1.15), maxw=self.W - u * 12)
        self.rssi_bar(self.W - u * 9, y + u // 2, u * 5, u // 2, d.rssi, col if d.category else ACCENT)
        self.text(f"{d.rssi}", self.f_s, FG, self.W - u, y + u // 3, right=True)
        self.text(ago(now - d.last), self.f_s, DIM, self.W - u, y + int(u * 1.15), right=True)

    def draw_detail(self, top: int, bottom: int) -> None:
        u = self.u
        d = self.w.devices.get(self.detail_id or "")
        if not d:
            self.text("Device gone", self.f_m, DIM, self.W // 2, top + u * 2, center=True)
            return
        now = self.w.clock()
        y = top + u // 2
        self.text(d.title(), self.f_l, CAT_COL.get(d.category, FG), u, y, maxw=self.W - u * 2)
        y += int(u * 2)
        lines = [
            ("Address", f"{d.addr}  ({d.kind.upper()})"),
            ("Vendor", d.vendor or ("private/randomized" if d.category == "random" else "unknown")),
            ("Type", d.category or "unknown"),
            ("Signal", f"{d.rssi} dBm"),
            ("First seen", f"{ago(now - d.first)} ago"),
            ("Last seen", f"{ago(now - d.last)} ago"),
            ("Locations", f"seen at {len(d.checkpoints)} of {self.w.checkpoint_idx + 1} checkpoints"),
        ]
        if d.kind == "ap":
            lines.append(("Network(s)", ", ".join(sorted(s or "<hidden>" for s in d.ssids))))
            lines.append(("Channel / sec", f"{d.ch} / {['OPEN', 'WEP', 'WPA'][d.enc] if 0 <= d.enc <= 2 else '?'}"))
        if d.kind == "wifi":
            lines.append(("Connected to", d.bssid or "-"))
            lines.append(("Uplink", f"{d.uplink_bps(now) / 1000:.1f} kB/s"))
            if d.ssids:
                lines.append(("Probing for", ", ".join(sorted(d.ssids))))
        if d.raw:
            lines.append(("Adv payload", d.raw))
        if d.fps:
            lines.append(("Fingerprint", ", ".join(sorted(d.fps))))
        if self.w.watched_match(d):
            lines.append(("Watchlist", "HOSTILE - alerts whenever in range"))
        if self.w.is_allowed(d.id):
            lines.append(("Allowlist", "marked as yours"))
        for i, why in enumerate(d.suspicion):
            lines.append(("Suspicious" if i == 0 else "", why))
        for k, v in lines:
            if y > bottom - u * 1.5:
                break
            self.text(k, self.f_s, DIM, u, y)
            self.text(v, self.f_s, FG, u * 9, y, maxw=self.W - u * 10)
            y += int(u * 1.15)

    def draw_hunt(self, top: int, bottom: int) -> None:
        u = self.u
        d = self.w.devices.get(self.detail_id or "")
        if not d:
            return
        now = self.w.clock()
        stale = now - d.last > 5
        self.text(d.title(), self.f_l, CAT_COL.get(d.category, FG), self.W // 2, top + u // 2, center=True, maxw=self.W - u * 2)
        recent = [r for t, r in d.rssi_hist if now - t <= 3]
        cur = round(sum(recent) / len(recent)) if recent else d.rssi
        col = DIM if stale else (LEVEL_COL[HIGH] if cur > -50 else LEVEL_COL[WARN] if cur > -65 else ACCENT)
        self.text(f"{cur}" if not stale else "--", self.f_xl, col, self.W // 2, top + u * 2.5, center=True)
        self.text("dBm  (closer to 0 = closer to you)", self.f_s, DIM, self.W // 2, top + u * 7.5, center=True)
        # trend: last 3 s vs the 5 s before that
        prev = [r for t, r in d.rssi_hist if 3 < now - t <= 8]
        if recent and prev and not stale:
            delta = sum(recent) / len(recent) - sum(prev) / len(prev)
            trend = "WARMER  ^" if delta > 2 else "COLDER  v" if delta < -2 else "steady"
            self.text(trend, self.f_m, col, self.W // 2, top + u * 8.7, center=True)
        bar_y = top + int(u * 10.3)
        self.rssi_bar(u * 2, bar_y, self.W - u * 4, u, cur if not stale else -100, col)
        # 60 s history graph
        gy, gh = bar_y + u * 2, bottom - bar_y - u * 3
        if gh > u * 2:
            pygame.draw.rect(self.screen, FAINT, (u * 2, gy, self.W - u * 4, gh), 1)
            pts = [(t, r) for t, r in d.rssi_hist if now - t <= 60]
            if len(pts) > 1:
                xy = [(u * 2 + (self.W - u * 4) * (1 - (now - t) / 60),
                       gy + gh * (1 - max(0, min(1, (r + 100) / 70)))) for t, r in pts]
                pygame.draw.lines(self.screen, col, False, xy, 3)
        if d.kind in ("ap", "wifi"):
            self.text(f"radio locked to ch {d.ch} for faster updates", self.f_s, DIM, self.W // 2, bottom - u * 1.2, center=True)
        # geiger click: faster when stronger
        if not stale and time.monotonic() >= self._next_click:
            self.snd.play("click")
            strength = max(0.0, min(1.0, (cur + 95) / 60))
            self._next_click = time.monotonic() + 1.2 - 1.1 * strength

    def draw_menu(self) -> None:
        u = self.u
        overlay = pygame.Surface((self.W, self.H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 210))
        self.screen.blit(overlay, (0, 0))
        mw, row = min(self.W - u * 2, u * 26), int(u * 1.5)
        mh = row * len(self.MENU) + u * 3
        x, y = (self.W - mw) // 2, (self.H - mh) // 2
        pygame.draw.rect(self.screen, (16, 20, 26), (x, y, mw, mh), border_radius=u // 2)
        self.text("MENU", self.f_m, ACCENT, x + u, y + u // 2)
        y += u * 2
        for i, item in enumerate(self.MENU):
            if i == self.menu_sel:
                pygame.draw.rect(self.screen, SEL_BG, (x + 4, y, mw - 8, row))
            self.text(item, self.f_m, FG if i == self.menu_sel else DIM, x + u, y + (row - self.f_m.get_height()) // 2)
            val = self.menu_value(i)
            if val:
                self.text(val, self.f_s, ACCENT, x + mw - u, y + (row - self.f_s.get_height()) // 2, right=True)
            y += row

    def draw_buttontest(self) -> None:
        u = self.u
        self.screen.fill(BG)
        self.text("BUTTON TEST", self.f_l, ACCENT, u, u)
        self.text("Press buttons; the last events are listed below. START returns to the menu.", self.f_s, DIM, u, u * 3)
        self.text(f"Face layout: {self.inp.layout}   Pads: {', '.join(self.inp.names()) or 'none (keyboard only)'}",
                  self.f_s, FG, u, u * 4.2, maxw=self.W - u * 2)
        held = ", ".join(f"{p}->{self.inp.face(p)}" for p in self.inp.held) or "-"
        self.text(f"Held: {held}", self.f_m, LEVEL_COL[WARN], u, u * 5.6)
        y = u * 7.5
        for line in reversed(self.inp.raw_log):
            self.text(line, self.f_s, FG, u, y)
            y += u * 1.1
        self.text("If a button maps wrong, set \"raw_buttons\" in ~/.config/csk/config.json (see README).",
                  self.f_s, DIM, u, self.H - u * 1.5)

    def draw_toast(self) -> None:
        if not self.toast:
            return
        msg, lvl, until = self.toast
        if time.monotonic() > until:
            self.toast = None
            return
        u = self.u
        img = self.f_m.render(msg, True, BG)
        w = min(self.W - u * 2, img.get_width() + u * 2)
        x, y = (self.W - w) // 2, int(self.H * 0.78)
        pygame.draw.rect(self.screen, LEVEL_COL[lvl] if lvl else ACCENT, (x, y, w, int(u * 1.8)), border_radius=u // 2)
        self.screen.blit(img, (x + u, y + (int(u * 1.8) - img.get_height()) // 2))

    def hints(self) -> list[tuple[str, str]]:
        v = self.view
        if v == "hunt":
            return [("B", "back"), ("Y", "mine"), ("X", "checkpoint"), ("SELECT", "mute")]
        if v == "detail":
            d = self.w.devices.get(self.detail_id or "")
            h = [("A", "hunt"), ("B", "back"), ("Y", "mine"), ("R1", "watchlist")]
            if d and d.kind == "ap":
                h += [("L1", "karma test"), ("R2", "join & scan")]
            return h
        if v == "scanconfirm":
            return [("A", "join & scan"), ("B", "cancel")]
        if v == "scanview":
            return [("A", "open host"), ("B", "back")] if not self._scan.get("running") else [("", "working...")]
        if v == "menu":
            return [("A", "select"), ("B", "close")]
        return [("L1/R1", "tab"), ("A", "open"), ("Y", "mine"), ("X", "I moved"), ("START", "menu")]

    def draw_scanconfirm(self, top: int, bottom: int) -> None:
        u = self.u
        d = self.w.devices.get(self.detail_id or "")
        ssid = next((s for s in d.ssids if s), "?") if d else "?"
        y = top + u
        self.text("Join this network and scan it?", self.f_l, LEVEL_COL[WARN], u, y)
        y += int(u * 2.2)
        warn = [
            f"The Flip will DISCONNECT from your current WiFi, join '{ssid}'",
            f"({d.addr if d else ''}), scan every device on it for cameras/DVRs,",
            "then put your previous network back.",
            "",
            "RISK: while joined, you are on THIS operator's network. They can",
            "see that a device connected. Only do this to investigate a network",
            "you have reason and right to inspect. An open AP needs no password;",
            "a secured one will simply fail to join.",
        ]
        for ln in warn:
            self.text(ln, self.f_m, FG if not ln.startswith("RISK") else LEVEL_COL[HIGH], u, y, maxw=self.W - u * 2)
            y += int(u * 1.3)
        self.text("Press A to proceed, B to cancel.", self.f_m, ACCENT, u, bottom - u * 1.5)

    def draw_scanview(self, top: int, bottom: int) -> None:
        u = self.u
        sc = self._scan
        self.text(f"Scanning '{sc.get('ssid', '')}'", self.f_l, ACCENT, u, top + u // 2)
        res = sc.get("result")
        y = top + int(u * 2.2)
        # progress log on the left while running / after
        for ln in sc.get("steps", [])[-7:]:
            self.text(ln, self.f_s, DIM, u, y, maxw=self.W - u * 2)
            y += int(u * 1.1)
        if sc.get("running"):
            dots = "." * (int(time.monotonic() * 2) % 4)
            self.text("working" + dots, self.f_m, LEVEL_COL[WARN], u, bottom - u * 1.5)
            return
        if not res:
            return
        if res.get("error") and not res.get("hosts"):
            self.text(res["error"], self.f_m, LEVEL_COL[HIGH], u, y + u, maxw=self.W - u * 2)
            if not res.get("restored"):
                self.text("Previous network may not be restored - check WiFi.", self.f_s, LEVEL_COL[WARN], u, y + u * 2.2)
            return
        hosts = res.get("hosts", [])
        cams = res.get("cameras", [])
        head_col = LEVEL_COL[HIGH] if cams else (60, 220, 120)
        self.text(f"{len(hosts)} devices, {len(cams)} camera/DVR-like"
                  + ("" if res.get("restored") else "   (WARNING: old network not restored)"),
                  self.f_m, head_col, u, y + u // 2)
        y += int(u * 2)
        row_h = int(u * 1.7)
        rows = max(1, (bottom - y) // row_h)
        sel = min(sc.get("sel", 0), max(0, len(hosts) - 1))
        sc["sel"] = sel
        start = max(0, min(sel - rows // 2, len(hosts) - rows))
        for i, h in enumerate(hosts[start:start + rows]):
            idx = start + i
            ry = y + i * row_h
            if idx == sel:
                pygame.draw.rect(self.screen, SEL_BG, (0, ry, self.W, row_h))
            cam = h.get("sev") in ("camera", "suspect")
            col = LEVEL_COL[HIGH] if h.get("sev") == "camera" else LEVEL_COL[WARN] if cam else FG
            ports = ",".join(str(p) for p in h.get("ports", [])) or "-"
            tag = "  <== CAMERA/DVR" if h.get("sev") == "camera" else "  <- possible camera" if cam else ""
            self.text(f"{h.get('ip', '?'):<15} {h.get('vendor') or h.get('mac', '')}{tag}", self.f_m, col, u, ry + u // 6,
                      maxw=self.W - u * 10)
            self.text(f"{h.get('mac', '')}   ports: {ports}", self.f_s, DIM, u, ry + int(u * 1.0), maxw=self.W - u * 10)

    def draw_boot(self) -> None:
        self.screen.fill(BG)
        frame = int(time.monotonic() * 3)
        sc = max(5, self.H // 44)
        sw, sh = art.skull_size(sc)
        cx = self.W // 2
        art.draw_skull(self.screen, cx - sw // 2, int(self.H * 0.20), sc, "rage", frame)
        y = int(self.H * 0.20) + sh + self.u * 2
        self.text(art.NAME, self.f_title, LEVEL_COL[HIGH], cx, y, center=True)
        y += int(self.u * 2.4)
        self.text(art.TAGLINE, self.f_m, DIM, cx, y, center=True)
        y += int(self.u * 2.2)
        self.text("counter-surveillance kit  -  passive by default", self.f_s, DIM, cx, y, center=True)
        if frame % 2 == 0:
            self.text("press any button", self.f_s, ACCENT, cx, int(self.H * 0.88), center=True)
        pygame.display.flip()

    def render(self) -> None:
        self.screen.fill(BG)
        if self.view == "boot":
            self.draw_boot()
            return
        if self.view == "buttontest":
            self.draw_buttontest()
            pygame.display.flip()
            return
        y = self.draw_header()
        if self.view in ("list", "menu"):
            y = self.draw_tabs(y)
        bottom = self.draw_footer(self.hints() if self.view != "menu" else [("A", "select"), ("B", "close")])
        if self.view in ("list", "menu"):
            self.draw_list(y, bottom)
        elif self.view == "detail":
            self.draw_detail(y, bottom)
        elif self.view == "hunt":
            self.draw_hunt(y, bottom)
        elif self.view == "scanconfirm":
            self.draw_scanconfirm(y, bottom)
        elif self.view == "scanview":
            self.draw_scanview(y, bottom)
        if self.view == "menu":
            self.draw_menu()
        else:
            self.draw_toast()
        pygame.display.flip()

    # ------------------------------------------------------------------- loop
    def step(self) -> None:
        for msg in self.src.drain():
            self.w.ingest(msg)
        if self.view == "boot" and time.monotonic() > self._boot_until:
            self.view = "list"
        if time.monotonic() - self._last_eval > 1.0:
            self._last_eval = time.monotonic()
            self.w.persist_sightings()
            for a in self.w.evaluate():
                if a.level >= WARN:
                    self.notify(a.title, a.level, 5.0)
                if a.level == HIGH:
                    self.snd.play("alarm")
                    if self.rumble_on:
                        self.inp.rumble(1.0, 600)
                elif a.level == WARN:
                    self.snd.play("blip")
        self._check_karma()
        events = pygame.event.get()
        for ev in events:
            if ev.type == pygame.QUIT:
                self.running = False
        for act in self.inp.process(events):
            self.handle(act)
        self.render()

    def run(self) -> None:
        # Steam's "Exit game" sends SIGTERM: leave the main loop cleanly so the
        # ESP32 is unlocked from any hunt channel and settings are saved.
        import signal
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "running", False))
        clock = pygame.time.Clock()
        try:
            while self.running:
                self.step()
                clock.tick(30)
        finally:
            if self._hunt_locked:
                self.src.send("ch 0")
            self.save()
            pygame.quit()
