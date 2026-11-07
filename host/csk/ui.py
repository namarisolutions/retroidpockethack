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

BG = (9, 10, 14)            # near-black with a hint of blue
PANEL = (16, 18, 24)        # header / footer bars
SURFACE = (22, 25, 33)      # resting card
SURFACE_SEL = (30, 38, 52)  # selected card
BORDER = (38, 42, 52)       # hairline card border
FG = (233, 237, 243)
DIM = (128, 136, 150)
FAINT = (48, 53, 64)
ACCENT = (86, 204, 255)
SEL_BG = SURFACE_SEL
LEVEL_COL = {INFO: (120, 190, 230), WARN: (255, 183, 59), HIGH: (255, 86, 92)}
LEVEL_NAME = {INFO: "INFO", WARN: "WARN", HIGH: "HIGH"}
CAT_COL = {"tracker": (255, 130, 95), "camera": (255, 183, 59), "alpr": (255, 183, 59),
           "iot": (188, 170, 255), "drone": (120, 230, 255)}
GOOD = (72, 214, 138)


def _load_font(size: int, bold: bool = False, serif: bool = False):
    """A real TTF if we can find one (big readability win over the default).

    serif=True picks an old-book serif face for the dungeon aesthetic.
    """
    import glob
    b = "-Bold" if bold else ""
    bb = "Bold" if bold else "Regular"
    if serif:
        pats = [
            "/usr/share/fonts/**/EBGaramond-%s.ttf" % ("Bold" if bold else "Regular"),
            "/usr/share/fonts/**/*Garamond*%s.ttf" % ("Bold" if bold else ""),
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif%s.ttf" % ("-Bold" if bold else ""),
            "/usr/share/fonts/**/DejaVuSerif%s.ttf" % ("-Bold" if bold else ""),
            "/usr/share/fonts/**/NotoSerif%s.ttf" % ("-Bold" if bold else "-Regular"),
            "/usr/share/fonts/**/LiberationSerif-%s.ttf" % bb,
        ]
    else:
        pats = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf" % b,
            "/usr/share/fonts/**/DejaVuSans%s.ttf" % b,
            "/usr/share/fonts/**/NotoSans%s.ttf" % ("-Bold" if bold else "-Regular"),
            "/usr/share/fonts/**/LiberationSans-%s.ttf" % bb,
            "/usr/share/fonts/**/Roboto-%s.ttf" % bb,
        ]
    for pat in pats:
        hits = sorted(glob.glob(pat, recursive=True))
        if hits:
            try:
                return pygame.font.Font(hits[0], size)
            except Exception:
                pass
    f = pygame.font.Font(None, int(size * 1.15))
    f.set_bold(bold)
    return f

TABS = ["ALERTS", "RADAR", "MAP", "BLE", "NETWORKS", "CLIENTS", "FOLLOW", "HISTORY", "SOULS"]

# Selectable colour themes (toy): overrides the base palette tokens.
THEMES = {
    "hellfire": {},   # default (defined by the module constants)
    "phosphor": dict(BG=(2, 10, 4), PANEL=(6, 18, 9), SURFACE=(8, 24, 12), SURFACE_SEL=(12, 40, 18),
                     BORDER=(18, 50, 24), FG=(120, 255, 150), DIM=(70, 150, 90), FAINT=(20, 60, 30),
                     ACCENT=(90, 255, 140), GOOD=(120, 255, 150)),
    "amber": dict(BG=(14, 8, 0), PANEL=(26, 15, 2), SURFACE=(34, 20, 4), SURFACE_SEL=(58, 34, 6),
                  BORDER=(64, 40, 10), FG=(255, 200, 90), DIM=(170, 120, 50), FAINT=(70, 45, 12),
                  ACCENT=(255, 190, 60), GOOD=(255, 210, 90)),
    "ice": dict(BG=(6, 10, 16), PANEL=(12, 18, 28), SURFACE=(16, 24, 36), SURFACE_SEL=(24, 40, 60),
                BORDER=(32, 46, 66), FG=(210, 234, 255), DIM=(120, 150, 180), FAINT=(40, 54, 74),
                ACCENT=(120, 200, 255), GOOD=(120, 230, 200)),
    # Dungeon: warm stone + torchlight + aged-parchment ink. Uses a serif face
    # and a drawn brick/vignette background.
    "dungeon": dict(BG=(18, 15, 12), PANEL=(30, 24, 18), SURFACE=(42, 34, 25), SURFACE_SEL=(64, 50, 32),
                    BORDER=(74, 58, 38), FG=(227, 208, 170), DIM=(150, 130, 100), FAINT=(58, 46, 32),
                    ACCENT=(240, 170, 60), GOOD=(150, 200, 110), SERIF=True, TEXTURE="dungeon"),
}
THEME_ORDER = ["dungeon", "hellfire", "phosphor", "amber", "ice"]
_BASE_THEME = {}   # filled on first apply


SERIF = False
TEXTURE = ""


def apply_theme(name: str) -> None:
    """Mutate the module-level palette globals so all draw code picks it up."""
    g = globals()
    keys = ("BG", "PANEL", "SURFACE", "SURFACE_SEL", "BORDER", "FG", "DIM", "FAINT", "ACCENT", "GOOD")
    if not _BASE_THEME:
        _BASE_THEME.update({k: g[k] for k in keys})
    g.update(_BASE_THEME)                         # reset to default first
    th = THEMES.get(name, {})
    g.update({k: v for k, v in th.items() if k in keys})
    g["SEL_BG"] = g["SURFACE_SEL"]
    g["SERIF"] = th.get("SERIF", False)
    g["TEXTURE"] = th.get("TEXTURE", "")


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


# Note name -> frequency (Hz), a couple of octaves of a chiptune scale.
_NOTES = {}
for _oct in range(2, 7):
    for _i, _nm in enumerate(["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]):
        _NOTES[f"{_nm}{_oct}"] = 440.0 * 2 ** ((_oct - 4) + (_i - 9) / 12)
_NOTES["-"] = 0.0   # rest


class Sound:
    """Chiptune engine: synthesizes square/triangle NES-style jingles and SFX."""

    def __init__(self):
        self.ok = False
        self.muted = False
        self.freq = 22050
        self.ch = 1
        self.cache: dict = {}
        try:
            pygame.mixer.init(22050, -16, 1, 512)
            self.freq, _, self.ch = pygame.mixer.get_init()
            # simple SFX
            self.click = self._seq([("C7", 0.012)], vol=0.5, wave="sq")
            self.blip = self._seq([("E6", 0.05)], vol=0.35, wave="sq")
            self.alarm = self._seq([("A4", 0.12), ("D#5", 0.12), ("A4", 0.12)], vol=0.5, wave="sq", duty=0.5)
            self.ok = True
        except Exception:
            pass

    def _wave(self, hz, i, wave, duty):
        if hz <= 0:
            return 0.0
        phase = (hz * i / self.freq) % 1.0
        if wave == "tri":
            return 4 * abs(phase - 0.5) - 1
        if wave == "saw":
            return 2 * phase - 1
        return 1.0 if phase < duty else -1.0        # square / pulse

    def _seq(self, notes, vol=0.5, wave="sq", duty=0.5, bass=False):
        """notes: list of (note, seconds). Returns a Sound."""
        buf = array.array("h")
        for nm, dur in notes:
            hz = _NOTES.get(nm, 0.0)
            n = max(1, int(self.freq * dur))
            for i in range(n):
                env = min(1.0, (n - i) / (n * 0.25 + 1)) * min(1.0, i / (self.freq * 0.004 + 1))
                v = self._wave(hz, i, wave, duty)
                if bass:
                    v += 0.5 * self._wave(hz / 2, i, "tri", 0.5)
                s = int(32767 * vol * env * max(-1.0, min(1.0, v)))
                for _ in range(self.ch):
                    buf.append(s)
        try:
            return pygame.mixer.Sound(buffer=buf.tobytes())
        except Exception:
            return None

    def jingle(self, name: str):
        """Build (and cache) a named chiptune voiceline sting."""
        if name in self.cache:
            return self.cache[name]
        q = 0.09
        tunes = {
            # boot: rising menacing arpeggio
            "boot": self._seq([("C3", q), ("G3", q), ("C4", q), ("D#4", q), ("G4", q * 2)], vol=0.45, wave="sq", bass=True),
            "rage": self._seq([("C5", 0.07), ("B4", 0.07), ("C5", 0.07), ("F5", 0.18)], vol=0.5, wave="sq", duty=0.25),
            "tracker": self._seq([("A4", 0.08), ("A4", 0.08), ("E5", 0.16)], vol=0.45, wave="sq"),
            "camera": self._seq([("D#5", 0.08), ("C5", 0.08), ("G4", 0.16)], vol=0.45, wave="sq", duty=0.25),
            "rogue": self._seq([("F#4", 0.07), ("F4", 0.07), ("E4", 0.07), ("D#4", 0.16)], vol=0.45, wave="saw"),
            "clear": self._seq([("C5", 0.08), ("E5", 0.08), ("G5", 0.08), ("C6", 0.18)], vol=0.4, wave="tri"),
            "found": self._seq([("G5", 0.06), ("C6", 0.14)], vol=0.4, wave="sq"),
        }
        s = tunes.get(name)
        self.cache[name] = s
        return s

    def play(self, name: str) -> None:
        if not self.ok or self.muted:
            return
        try:
            snd = getattr(self, name, None) if name in ("click", "blip", "alarm") else self.jingle(name)
            if snd:
                snd.play()
        except Exception:
            pass

    def _ambient_track(self):
        """A slow, brooding dungeon loop: a low drone bass + a sparse minor arpeggio."""
        if "ambient" in self.cache:
            return self.cache["ambient"]
        q = 0.42
        # A minor-ish wander, low and ominous
        notes = [("A2", q), ("-", q), ("C3", q), ("E3", q), ("A2", q), ("-", q), ("G2", q), ("B2", q),
                 ("A2", q), ("E3", q), ("F3", q), ("-", q), ("D3", q), ("-", q), ("E3", q), ("A2", q)]
        s = self._seq(notes, vol=0.22, wave="tri", bass=True)
        self.cache["ambient"] = s
        return s

    def ambient(self, on: bool) -> None:
        if not self.ok:
            return
        try:
            if on and not self.muted:
                trk = self._ambient_track()
                if trk:
                    trk.play(loops=-1, fade_ms=600)
            else:
                trk = self.cache.get("ambient")
                if trk:
                    trk.fadeout(400)
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
        self.inp = C.Input(self.cfg.get("layout", "nintendo"), self.cfg.get("raw_buttons"))
        self.snd = Sound()
        self.snd.muted = self.cfg.get("muted", False)
        self.rumble_on = self.cfg.get("rumble", True)
        self.music = self.cfg.get("music", False)
        self.theme = self.cfg.get("theme", "dungeon")
        apply_theme(self.theme)
        self._bg_tex = None
        self._build_fonts()
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
        # Persistent cross-day memory
        self.locations = self.cfg.get("locations", ["home", "work", "out"])
        self.loc_idx = 0
        try:
            from .store import Store
            self.w.store = Store()
            self.w.set_location(self.locations[self.loc_idx])
        except Exception:
            self.w.store = None
        self.voiceline: tuple[str, str, float] | None = None   # (text, mood, until)
        self._oracle: dict | None = None
        self._oracle_thinking = False
        self._oracle_ready = False
        self._oracle_via = ""
        self._wipe_hold = 0.0
        self.speak("boot")
        if self.music:
            self.snd.ambient(True)
        self.w.new_alert_cb = None

    # ----------------------------------------------------------- voicelines
    VOICELINES = {
        "boot": ["I AWAKEN. THE WATCH BEGINS.", "NONE SHALL WATCH YOU UNSEEN.", "I DO NOT SLEEP."],
        "rogue": ["A FALSE GATE. I SEE THROUGH IT.", "THIS NETWORK LIES.", "DECEPTION DETECTED."],
        "camera": ["AN EYE OF GLASS. I HAVE FOUND IT.", "SOMETHING WATCHES. SO DO I.", "A LENS IN THE DARK."],
        "tracker": ["A BEACON CLINGS TO YOU.", "YOU ARE BEING TAGGED.", "SHAKE OFF THIS PARASITE."],
        "deauth": ["AN ATTACK UPON THE AIR!", "SOMEONE SEVERS THE SIGNAL."],
        "follow": ["A SHADOW MOVES AS YOU MOVE.", "YOU ARE FOLLOWED. I AM CERTAIN."],
        "drone": ["A MACHINE WATCHES FROM ABOVE.", "WINGS IN THE SKY. I SEE ITS MASTER.", "EYES IN THE AIR. I HAVE ITS PILOT."],
        "blespam": ["A SWARM OF PHANTOMS! AN ATTACK.", "LIES FLOOD THE AETHER. IGNORE THEM."],
        "tripwire": ["THE SNARE IS SPRUNG!", "IT RETURNS. I FELT IT CROSS MY THRESHOLD."],
        "clear": ["THE AIR IS CLEAN... FOR NOW.", "ALL IS QUIET. I REMAIN VIGILANT."],
    }

    def speak(self, event: str, secs: float = 3.2) -> None:
        import random
        lines = self.VOICELINES.get(event)
        if not lines:
            return
        mood = {"boot": "rage", "clear": "calm"}.get(event, "rage" if event in ("rogue", "deauth") else "alert")
        self.voiceline = (random.choice(lines), mood, time.monotonic() + secs)
        self.snd.play({"boot": "boot", "rogue": "rogue", "camera": "camera", "tracker": "tracker",
                       "deauth": "rage", "follow": "found", "drone": "camera", "blespam": "rage",
                       "tripwire": "found", "clear": "clear"}.get(event, "blip"))

    def _event_for_alert(self, a) -> str:
        t = a.title.lower()
        if "deauth" in t:
            return "deauth"
        if "fake" in t or "rogue" in t or "evil twin" in t or "karma" in t:
            return "rogue"
        if "camera" in t:
            return "camera"
        if "tracker" in t or "find my" in t or "tile" in t or "tag" in t:
            return "tracker"
        if "tripwire" in t:
            return "tripwire"
        if "spam" in t:
            return "blespam"
        if "drone" in t:
            return "drone"
        if "following" in t or "seen repeatedly" in t:
            return "follow"
        return "rogue"

    def _build_fonts(self) -> None:
        u, sf = self.u, SERIF
        self.f_s = _load_font(int(u * 0.74), serif=sf)
        self.f_m = _load_font(int(u * 0.95), serif=sf)
        self.f_mb = _load_font(int(u * 0.95), bold=True, serif=sf)
        self.f_l = _load_font(int(u * 1.35), bold=True, serif=sf)
        self.f_title = _load_font(int(u * 1.8), bold=True, serif=sf)
        self.f_xl = _load_font(int(u * 5), bold=True, serif=sf)

    def _build_bg(self) -> None:
        """Pre-render the dungeon stone-brick + torch-vignette background once."""
        import random
        self._bg_tex = None
        if TEXTURE != "dungeon":
            return
        u = self.u
        surf = pygame.Surface((self.W, self.H))
        surf.fill(BG)
        rng = random.Random(1337)
        bh = int(u * 1.3)
        bw = int(u * 2.6)
        mortar = max(2, u // 10)
        stone = PANEL
        for ri, y in enumerate(range(-bh, self.H + bh, bh)):
            off = (bw // 2) if ri % 2 else 0
            for x in range(-bw + off, self.W + bw, bw):
                shade = rng.randint(-8, 10)
                c = tuple(max(0, min(255, stone[i] + shade)) for i in range(3))
                pygame.draw.rect(surf, c, (x + mortar, y + mortar, bw - mortar, bh - mortar), border_radius=3)
        # torch vignette: darken toward the edges
        vig = pygame.Surface((self.W, self.H), pygame.SRCALPHA)
        cx, cy = self.W // 2, int(self.H * 0.42)
        maxd = (cx ** 2 + cy ** 2) ** 0.5
        step = max(8, self.H // 48)
        for i in range(0, int(maxd), step):
            a = int(160 * (i / maxd) ** 2)
            pygame.draw.circle(vig, (0, 0, 0, min(210, a)), (cx, cy), int(maxd) - i, step + 1)
        surf.blit(vig, (0, 0))
        self._bg_tex = surf

    def paint_bg(self) -> None:
        if TEXTURE == "dungeon":
            if self._bg_tex is None:
                self._build_bg()
            self.screen.blit(self._bg_tex, (0, 0))
        else:
            self.screen.fill(BG)

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
                        locations=self.locations, theme=self.theme, music=self.music)
        save_config(self.cfg)

    def rssi_bar(self, x, y, w, h, rssi, col=ACCENT):
        """Segmented signal meter (5 pips) - reads like a phone's signal bars."""
        frac = max(0.0, min(1.0, (rssi + 100) / 70))
        pips = 5
        gap = max(2, w // 40)
        pw = (w - gap * (pips - 1)) / pips
        lit = round(frac * pips)
        for i in range(pips):
            px = x + i * (pw + gap)
            ph = h * (0.45 + 0.55 * (i + 1) / pips)   # rising heights
            py = y + (h - ph)
            c = col if i < lit else FAINT
            pygame.draw.rect(self.screen, c, (px, py, pw, ph), border_radius=max(1, int(pw // 4)))

    def card(self, x, y, w, h, selected=False, accent=None):
        pygame.draw.rect(self.screen, SURFACE_SEL if selected else SURFACE, (x, y, w, h), border_radius=self.u // 2)
        pygame.draw.rect(self.screen, accent or (ACCENT if selected else BORDER),
                         (x, y, w, h), width=2 if selected else 1, border_radius=self.u // 2)
        if accent:  # accent spine on the left
            pygame.draw.rect(self.screen, accent, (x, y + self.u // 3, 4, h - 2 * (self.u // 3)),
                             border_radius=2)

    def badge(self, text, color, x, y, font=None):
        font = font or self.f_s
        img = font.render(text, True, BG)
        pad = self.u // 3
        w = img.get_width() + pad * 2
        h = img.get_height() + self.u // 6
        pygame.draw.rect(self.screen, color, (x, y, w, h), border_radius=h // 2)
        self.screen.blit(img, (x + pad, y + self.u // 12))
        return x + w + self.u // 4

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
        if name == "RADAR":
            return self.w.recent(("ble", "wifi", "ap"), within=120.0)
        if name == "SOULS":
            return self.w.recent(("ble", "wifi", "ap"), within=86400.0)
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
    def _menu(self) -> list:
        """Data-driven menu: (label, value_fn|None, action_fn). No index juggling."""
        s = self.w.sensor
        on = lambda b: "ON" if b else "OFF"

        def toggle_sensor(key):
            v = not self.w.sensor.get(key, 1)
            self.src.send(f"{key} {int(v)}")
            self.w.sensor[key] = int(v)

        def mark_all():
            self.notify(f"{self.w.allow_all_visible()} devices marked as yours")
            self.view = "list"

        return [
            ("Resume", None, lambda: setattr(self, "view", "list")),
            ("Consult the Demon King", None, self.consult_oracle),
            ("Channel spectrum", None, lambda: setattr(self, "view", "spectrum")),
            ("Location", lambda: self.w.location + "  (A to change)", self.cycle_location),
            ("Face buttons", lambda: self.inp.layout.upper() + (" (A right)" if self.inp.layout == "nintendo" else " (A bottom)"),
             self.inp.toggle_layout),
            ("Sound", lambda: on(not self.snd.muted), lambda: setattr(self.snd, "muted", not self.snd.muted)),
            ("Dungeon music", lambda: on(self.music), self.toggle_music),
            ("Rumble", lambda: on(self.rumble_on), self.toggle_rumble),
            ("BLE scanning", lambda: on(s.get("ble", 1)), lambda: toggle_sensor("ble")),
            ("WiFi scanning", lambda: on(s.get("wifi", 1)), lambda: toggle_sensor("wifi")),
            ("Mark everything visible as mine", None, mark_all),
            ("Export signed report", None, lambda: (self.export_report(), setattr(self, "view", "list"))),
            ("Clear alerts", None, lambda: (self.w.alerts.clear(), self.notify("Alerts cleared"))),
            ("Button test", None, self._open_buttontest),
            ("Theme", lambda: self.theme.upper() + "  (A to change)", self.cycle_theme),
            ("PANIC WIPE (erase all data)", None, self._open_wipe),
            ("Quit", None, lambda: setattr(self, "running", False)),
        ]

    def menu_value(self, i: int) -> str:
        fn = self._menu()[i][1]
        return fn() if fn else ""

    def menu_activate(self, i: int) -> None:
        self._menu()[i][2]()
        self.save()

    def toggle_rumble(self) -> None:
        self.rumble_on = not self.rumble_on
        if self.rumble_on:
            self.inp.rumble(0.6, 200)

    def toggle_music(self) -> None:
        self.music = not self.music
        self.snd.ambient(self.music)
        self.notify("Dungeon music " + ("on" if self.music else "off"))

    def _open_buttontest(self) -> None:
        self.view = "buttontest"
        self._bt_started = time.monotonic()

    def _open_wipe(self) -> None:
        self.view = "wipeconfirm"
        self._wipe_hold = 0.0

    def cycle_theme(self) -> None:
        i = (THEME_ORDER.index(self.theme) + 1) % len(THEME_ORDER) if self.theme in THEME_ORDER else 0
        self.theme = THEME_ORDER[i]
        apply_theme(self.theme)
        self._build_fonts()
        self._build_bg()
        self.notify(f"Theme: {self.theme}")

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
        if v == "oracle":
            if act == C.A:
                self.consult_oracle()
            elif act in (C.B, C.START):
                self.view = "list"
            return
        if v == "spectrum":
            if act in (C.B, C.START, C.A):
                self.view = "list"
            return
        if v == "wipeconfirm":
            if act in (C.B, C.START):
                self.view = "menu"
            # the actual wipe is gated on a held A (see step())
            return
        if v == "buttontest":
            if act == C.START or (time.monotonic() - self._bt_started > 2 and act == "HOME"):
                self.view = "menu"
            return
        if v == "menu":
            if act == C.UP:
                self.menu_sel = (self.menu_sel - 1) % len(self._menu())
            elif act == C.DOWN:
                self.menu_sel = (self.menu_sel + 1) % len(self._menu())
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
            elif act == C.L2 and self.detail_id:
                armed = self.w.toggle_tripwire(self.detail_id, threshold=-60)
                self.notify("TRIPWIRE armed - alarms when this comes close" if armed else "Tripwire disarmed",
                            HIGH if armed else INFO)
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
        h = int(u * 1.9)
        pygame.draw.rect(self.screen, PANEL, (0, 0, self.W, h))
        pygame.draw.line(self.screen, BORDER, (0, h), (self.W, h))
        ok = self.w.sensor_ok()
        mood = self.mood()
        sc = max(1, h // 18)
        sw, sh = art.skull_size(sc)
        art.draw_skull(self.screen, u // 2, (h - sh) // 2, sc, mood, int(time.monotonic() * 3),
                       t=time.monotonic(), animate=True)
        cy = h // 2 - self.f_m.get_height() // 2
        self.text(art.SHORT, self.f_mb, LEVEL_COL[HIGH] if mood == "rage" else FG, u // 2 + sw + u // 3, cy)
        xoff = u // 2 + sw + u // 3 + self.f_mb.size(art.SHORT)[0] + int(u * 0.7)
        # sensor status: a dot + text
        pygame.draw.circle(self.screen, GOOD if ok else LEVEL_COL[HIGH], (xoff, h // 2), u // 5)
        xoff += u // 2
        status = (f"ch {s.get('ch', '?')}{' LOCK' if s.get('lock') else ''}    "
                  f"BLE {'on' if s.get('ble', 1) else 'off'}    WiFi {'on' if s.get('wifi', 1) else 'off'}"
                  if ok else self.src.status)
        self.text(status, self.f_s, DIM if ok else LEVEL_COL[WARN], xoff, h // 2 - self.f_s.get_height() // 2,
                  maxw=self.W - xoff - u * 9)
        right = f"@{self.w.location}    {len(self.w.devices)} dev    {time.strftime('%H:%M')}"
        if self.snd.muted:
            right = "MUTED    " + right
        self.text(right, self.f_s, DIM, self.W - u // 2, h // 2 - self.f_s.get_height() // 2, right=True)
        return h + u // 3

    def draw_tabs(self, y: int) -> int:
        u = self.u
        h = int(u * 1.5)
        now = self.w.clock()
        active_high = any(a.active(now) and a.level == HIGH for a in self.w.alerts.values())
        # measure each pill so widths fit the text
        labels = []
        for t in TABS:
            label = t
            if t == "ALERTS":
                cnt = sum(1 for a in self.w.alerts.values() if a.active(now))
                label = f"ALERTS  {cnt}" if cnt else t
            labels.append(label)
        gap = u // 3
        widths = [self.f_m.size(lbl)[0] + u for lbl in labels]
        total = sum(widths) + gap * (len(TABS) - 1)
        x = max(u // 2, (self.W - total) // 2)
        py = y + (h - int(u * 1.05)) // 2
        ph = int(u * 1.05)
        for i, (t, lbl, w) in enumerate(zip(TABS, labels, widths)):
            sel = i == self.tab
            if sel:
                pygame.draw.rect(self.screen, SURFACE_SEL, (x, py, w, ph), border_radius=ph // 2)
                pygame.draw.rect(self.screen, ACCENT, (x, py, w, ph), width=2, border_radius=ph // 2)
            col = LEVEL_COL[HIGH] if (t == "ALERTS" and active_high) else (FG if sel else DIM)
            self.text(lbl, self.f_m, col, x + w // 2, py + (ph - self.f_m.get_height()) // 2, center=True)
            x += w + gap
        return y + h + u // 4

    def draw_footer(self, hints: list[tuple[str, str]]) -> int:
        u = self.u
        h = int(u * 1.4)
        y = self.H - h
        pygame.draw.rect(self.screen, PANEL, (0, y, self.W, h))
        pygame.draw.line(self.screen, BORDER, (0, y), (self.W, y))
        x = u
        for btn, what in hints:
            if btn:
                r = self.f_mb.render(btn, True, BG)
                bw = r.get_width() + int(u * 0.7)
                bh = int(h * 0.56)
                by = y + (h - bh) // 2
                pygame.draw.rect(self.screen, ACCENT, (x, by, bw, bh), border_radius=bh // 2)
                self.screen.blit(r, (x + int(u * 0.35), by + (bh - r.get_height()) // 2))
                x += bw + u // 4
            x = self.text(what, self.f_s, DIM, x, y + (h - self.f_s.get_height()) // 2).right + int(u * 0.8)
        return y - u // 3

    def draw_list(self, top: int, bottom: int) -> None:
        u = self.u
        tab = TABS[self.tab]
        items = self.items()
        if tab == "SOULS":
            top = self._draw_souls_banner(top)
        gap = max(4, u // 4)
        row_h = int(u * 2.3)
        slot = row_h + gap
        rows = max(1, (bottom - top) // slot)
        if not items:
            msg = {"ALERTS": "No alerts. Scanning...", "FOLLOW": "Press X each time you've moved somewhere new.",
                   "MAP": "No WiFi clients associated yet. Listening...",
                   "HISTORY": "No history yet. Set your Location (menu) at each place you go;\ndevices that reappear across places/days are remembered here - even after a restart.",
                   "SOULS": "No souls claimed yet. Every device the King detects is bound here, named.",
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
        mx = u // 2
        self._card_x, self._card_w = mx, self.W - mx * 2 - (u // 3 if len(items) > rows else 0)
        for i, it in enumerate(items[start:start + rows]):
            idx = start + i
            y = top + i * slot + gap // 2
            accent = self._row_accent(tab, it, now)
            self.card(self._card_x, y, self._card_w, row_h, selected=idx == sel, accent=accent)
            if tab == "ALERTS":
                self._row_alert(it, y, row_h, now)
            elif tab == "MAP":
                self._row_map(it, y, row_h, now)
            elif tab == "HISTORY":
                self._row_history(it, y, row_h, now)
            elif tab == "SOULS":
                self._row_soul(it, y, row_h, now)
            else:
                self._row_device(it, y, row_h, now, tab)
        if len(items) > rows:  # scrollbar
            track = bottom - top
            sb_h = max(u, track * rows / len(items))
            sb_y = top + (track - sb_h) * start / max(1, len(items) - rows)
            pygame.draw.rect(self.screen, FAINT, (self.W - u // 3, int(sb_y), 5, int(sb_h)), border_radius=3)

    def draw_radar(self, top: int, bottom: int) -> None:
        from . import bestiary
        u = self.u
        items = self.items()
        cx, cy = self.W // 2, (top + bottom) // 2
        maxR = min(self.W, bottom - top) // 2 - u
        now = self.w.clock()
        t = time.monotonic()
        # rings + cross
        for frac in (0.33, 0.66, 1.0):
            pygame.draw.circle(self.screen, BORDER, (cx, cy), int(maxR * frac), 1)
        pygame.draw.line(self.screen, FAINT, (cx - maxR, cy), (cx + maxR, cy), 1)
        pygame.draw.line(self.screen, FAINT, (cx, cy - maxR), (cx, cy + maxR), 1)
        self.text("-30", self.f_s, FAINT, cx + 2, cy - int(maxR * 0.33) - u)
        self.text("-100 dBm", self.f_s, FAINT, cx + 2, cy - maxR - u)
        # sweeping beam
        sweep = (t * 90) % 360
        sa = math.radians(sweep)
        for i in range(26):                        # fading trail
            a = math.radians(sweep - i * 2)
            fade = max(0, 40 - i * 2)
            pygame.draw.line(self.screen, (ACCENT[0] * fade // 60, ACCENT[1] * fade // 60, ACCENT[2] * fade // 60),
                             (cx, cy), (cx + maxR * math.cos(a), cy + maxR * math.sin(a)), 2)
        if not items:
            self.text("no contacts in range", self.f_m, DIM, cx, bottom - u * 2, center=True)
        sel = min(self.sel["RADAR"], max(0, len(items) - 1))
        self.sel["RADAR"] = sel
        for i, d in enumerate(items):
            ang = (bestiary._h(d.addr) % 3600) / 10.0   # stable bearing from MAC
            frac = 1.0 - max(0.0, min(1.0, (d.rssi + 100) / 70))
            r = maxR * (0.08 + 0.92 * frac)
            bx = cx + r * math.cos(math.radians(ang))
            by = cy + r * math.sin(math.radians(ang))
            col = CAT_COL.get(d.category, ACCENT if d.kind == "ap" else FG)
            # ping brighten when the beam passes near this bearing
            dd = (sweep - ang) % 360
            near = dd < 30
            rad = u // 3 + (u // 4 if near else 0)
            if d.category in ("tracker", "camera", "drone", "alpr"):
                rad += u // 6
            pygame.draw.circle(self.screen, col, (int(bx), int(by)), rad)
            if i == sel:
                pygame.draw.circle(self.screen, FG, (int(bx), int(by)), rad + u // 3, 2)
        # selected contact label
        if items:
            d = items[sel]
            name = bestiary.monster_name(d.addr)
            self.text(name, self.f_mb, CAT_COL.get(d.category, ACCENT), cx, bottom - int(u * 2.2), center=True)
            self.text(f"{d.title()}   {d.addr}   {d.rssi} dBm", self.f_s, DIM, cx, bottom - u, center=True)
        self.text(f"{len(items)} contacts", self.f_s, DIM, self._lx(), top + u // 2)

    def _row_accent(self, tab, it, now):
        """Left-spine accent colour per row, or None."""
        if tab == "ALERTS":
            return LEVEL_COL[it.level] if it.active(now) else None
        if tab == "HISTORY":
            r = it
            if r["n_locations"] >= 2 and r["n_days"] >= 2:
                return LEVEL_COL[HIGH]
            if r["n_locations"] >= 2 or r["n_days"] >= 2:
                return LEVEL_COL[WARN]
            return None
        d = getattr(it, "dev", it)
        if d is None:
            return None
        if self.w.watched_match(d) or getattr(d, "karma", False):
            return LEVEL_COL[HIGH]
        if getattr(d, "suspicion", None):
            return LEVEL_COL[HIGH]
        if d.category in CAT_COL:
            return CAT_COL[d.category]
        return None

    def _lx(self):
        return self._card_x + self.u // 2

    def _rx(self):
        return self._card_x + self._card_w - self.u // 2

    def _row_alert(self, a, y, h, now):
        u = self.u
        lx = self._lx()
        col = LEVEL_COL[a.level] if a.active(now) else DIM
        bx = self.badge(LEVEL_NAME[a.level], col, lx, y + u // 4, self.f_s)
        if not a.seen_by_user and a.active(now):
            pygame.draw.circle(self.screen, ACCENT, (bx + u // 6, y + u // 4 + self.f_s.get_height() // 2 + u // 12), u // 7)
            bx += u // 2
        self.text(a.title, self.f_mb, col if a.level else FG, bx, y + u // 5, maxw=self._rx() - bx - u * 4)
        self.text(a.detail, self.f_s, DIM, lx, y + int(u * 1.25), maxw=self._rx() - lx - u * 4)
        self.text(("now" if a.active(now) else ago(now - a.last) + " ago"), self.f_s, DIM, self._rx(), y + u // 4, right=True)
        self.text(f"for {ago(a.last - a.first)}", self.f_s, DIM, self._rx(), y + int(u * 1.25), right=True)

    def _row_map(self, row, y, h, now):
        u = self.u
        lx, rx = self._lx(), self._rx()
        meter_x = rx - u * 4 - u * 2
        if row.is_ap:
            ap = row.dev
            if ap is None:   # clients whose AP we haven't heard a beacon from
                self.text(f"(unknown AP {row.bssid})", self.f_mb, DIM, lx, y + u // 5)
                self.text(f"{row.clients} client(s)  -  no beacon seen", self.f_s, DIM, lx, y + int(u * 1.3))
                return
            hostile = self.w.watched_match(ap) is not None
            sus = bool(ap.suspicion or ap.karma)
            col = LEVEL_COL[HIGH] if (hostile or sus) else FG
            x = lx
            if hostile:
                x = self.badge("WATCH", LEVEL_COL[HIGH], x, y + u // 4, self.f_s)
            if ap.karma:
                x = self.badge("KARMA", LEVEL_COL[HIGH], x, y + u // 4, self.f_s)
            elif sus:
                x = self.badge("SUSPECT", LEVEL_COL[HIGH], x, y + u // 4, self.f_s)
            ssid = next(iter(ap.ssids), "") if len(ap.ssids) == 1 else (f"{len(ap.ssids)} SSIDs" if ap.ssids else "<hidden>")
            self.text(ssid, self.f_mb, col, x, y + u // 5, maxw=meter_x - x - u // 2)
            sec = ["OPEN", "WEP", "WPA"][ap.enc] if 0 <= ap.enc <= 2 else "?"
            self.text(f"{ap.addr}   ch{ap.ch}   {sec}   {ap.vendor or ''}", self.f_s, DIM, lx, y + int(u * 1.3), maxw=meter_x - lx)
            self.text(f"{row.clients} client(s)", self.f_s, ACCENT, rx, y + u // 4, right=True)
            self.rssi_bar(meter_x, y + int(u * 1.3), u * 4, int(u * 0.7), ap.rssi, col if col != FG else ACCENT)
        else:
            c = row.dev
            mine = self.w.is_allowed(c.id)
            bps = c.uplink_bps(now)
            streaming = bps >= 40000
            col = LEVEL_COL[WARN] if streaming else DIM if mine else FG
            tx = lx + u
            pygame.draw.line(self.screen, FAINT, (lx + u // 2, y), (lx + u // 2, y + h), 2)
            pygame.draw.line(self.screen, FAINT, (lx + u // 2, y + h // 2), (tx, y + h // 2), 2)
            x = tx + u // 3
            if streaming:
                x = self.badge("STREAMING", LEVEL_COL[WARN], x, y + u // 4, self.f_s)
            label = c.title() + ("  [mine]" if mine else "")
            self.text(label, self.f_m, col, x, y + u // 4, maxw=meter_x - x - u // 2)
            up = f"up {bps / 1000:.0f} kB/s" if bps > 1000 else "idle"
            self.text(f"{c.addr}   {up}", self.f_s, DIM, tx + u // 3, y + int(u * 1.3), maxw=meter_x - tx)
            self.rssi_bar(meter_x, y + u // 2, u * 4, int(u * 0.7), c.rssi, col if streaming else ACCENT)
            self.text(f"{c.rssi}", self.f_m, FG, rx, y + u // 3, right=True)

    def _row_history(self, r, y, h, now):
        u = self.u
        lx, rx = self._lx(), self._rx()
        dev = r.get("device", {})
        title = (dev.get("title") or "").strip() or r["mac"]
        cat = dev.get("category", "")
        multi = r["n_locations"] >= 2 and r["n_days"] >= 2
        col = LEVEL_COL[HIGH] if multi else LEVEL_COL[WARN] if (r["n_locations"] >= 2 or r["n_days"] >= 2) else FG
        trusted = dev.get("trusted")
        if trusted:
            col = DIM
        x = lx
        if trusted:
            x = self.badge("MINE", DIM, x, y + u // 4, self.f_s)
        self.text(title, self.f_mb, col, x, y + u // 5, maxw=rx - x - u * 7)
        locs = ", ".join(r["locations"][:4]) + ("..." if r["n_locations"] > 4 else "")
        sub = f"{r['mac']}   {cat or dev.get('kind', '')}   seen at: {locs}"
        self.text(sub, self.f_s, DIM, lx, y + int(u * 1.3), maxw=rx - lx - u * 7)
        self.text(f"{r['n_locations']} place(s)", self.f_mb, col, rx, y + u // 4, right=True)
        span = ago(now - r["last"]) if r["last"] else "?"
        self.text(f"{r['n_days']} day(s) - last {span} ago", self.f_s, DIM, rx, y + int(u * 1.3), right=True)

    def _draw_souls_banner(self, top: int) -> int:
        from . import bestiary
        u = self.u
        souls = len(self.w.store.all()) if self.w.store else len(self.w.devices)
        title, into, togo = bestiary.king_rank(souls)
        self.text(f"RANK: {title}", self.f_l, ACCENT, u, top)
        sub = f"{souls} souls claimed" + (f"   ·   {togo} more to ascend" if togo else "   ·   MAXIMUM POWER")
        self.text(sub, self.f_s, DIM, self.W - u, top + u // 4, right=True)
        # progress bar to next rank
        if togo:
            span = into + togo
            frac = into / span if span else 1.0
            bw = self.W - u * 2
            by = top + int(u * 1.6)
            pygame.draw.rect(self.screen, FAINT, (u, by, bw, u // 3), border_radius=u // 6)
            pygame.draw.rect(self.screen, ACCENT, (u, by, int(bw * frac), u // 3), border_radius=u // 6)
        return top + int(u * 2.3)

    def _row_soul(self, d, y, h, now):
        from . import bestiary
        u = self.u
        lx, rx = self._lx(), self._rx()
        cat = d.category
        col = CAT_COL.get(d.category, FG)
        self.text(bestiary.monster_name(d.addr), self.f_mb, col, lx, y + u // 5, maxw=rx - lx - u * 8)
        sub = f"{bestiary.rank(cat)}   ·   {d.title()}   ·   {d.addr}"
        self.text(sub, self.f_s, DIM, lx, y + int(u * 1.3), maxw=rx - lx - u * 8)
        self.text(f"{d.rssi} dBm", self.f_m, col, rx, y + u // 4, right=True)
        self.text("claimed " + ago(now - d.first) + " ago", self.f_s, DIM, rx, y + int(u * 1.3), right=True)

    def _row_device(self, d, y, h, now, tab):
        u = self.u
        lx, rx = self._lx(), self._rx()
        mine = self.w.is_allowed(d.id)
        hostile = self.w.watched_match(d) is not None
        meter_w = u * 4
        meter_x = rx - meter_w - u * 2
        col = LEVEL_COL[HIGH] if hostile or d.karma else DIM if mine else CAT_COL.get(d.category, FG)
        x = lx
        # status badges
        for txt, bc in [("WATCH", LEVEL_COL[HIGH]) if hostile else (None, None),
                        ("KARMA", LEVEL_COL[HIGH]) if d.karma else (None, None),
                        ("MINE", DIM) if mine else (None, None),
                        (d.category.upper(), CAT_COL.get(d.category)) if d.category in CAT_COL else (None, None)]:
            if txt:
                x = self.badge(txt, bc, x, y + u // 4, self.f_s)
        title = d.title()
        self.text(title, self.f_mb, col, x, y + u // 5, maxw=meter_x - x - u // 2)
        sub = d.addr
        if d.kind == "ap":
            sub += f"   ch{d.ch}   {['OPEN', 'WEP', 'WPA'][d.enc] if 0 <= d.enc <= 2 else ''}"
        elif d.kind == "wifi":
            bps = d.uplink_bps(now)
            if bps > 1000:
                sub += f"   up {bps / 1000:.0f} kB/s"
            if d.ssids:
                sub += "   probes: " + ", ".join(sorted(d.ssids))[:60]
        if d.vendor and d.vendor not in title:
            sub += f"   {d.vendor}"
        if tab == "FOLLOW":
            sub = f"seen at {len(d.checkpoints)} of {self.w.checkpoint_idx + 1} locations   |   {d.kind.upper()}   {d.addr}"
        self.text(sub, self.f_s, DIM, lx, y + int(u * 1.3), maxw=meter_x - lx - u // 2)
        self.rssi_bar(meter_x, y + u // 2, meter_w, int(u * 0.8), d.rssi, col if d.category else ACCENT)
        self.text(f"{d.rssi}", self.f_mb, FG, rx, y + u // 3, right=True)
        self.text(ago(now - d.last), self.f_s, DIM, rx, y + int(u * 1.3), right=True)

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
        if d.drone:
            dr = d.drone
            if dr.get("serial"):
                lines.append(("Drone serial", dr["serial"]))
            if dr.get("ua_type"):
                lines.append(("Drone type", dr["ua_type"]))
            if dr.get("lat") is not None:
                alt = f"  alt {dr.get('alt_geo_m')}m" if dr.get("alt_geo_m") is not None else ""
                lines.append(("Drone position", f"{dr['lat']:.6f}, {dr['lon']:.6f}{alt}"))
            if dr.get("speed_ms") is not None:
                lines.append(("Drone speed", f"{dr['speed_ms']} m/s"))
            if dr.get("operator_lat") is not None:
                lines.append(("OPERATOR location", f"{dr['operator_lat']:.6f}, {dr['operator_lon']:.6f}"))
            if dr.get("operator_id"):
                lines.append(("Operator ID", dr["operator_id"]))
            lines.append(("Remote ID via", dr.get("via", "BLE")))
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
        entries = self._menu()
        mw, row = min(self.W - u * 2, u * 28), int(u * 1.35)
        mh = row * len(entries) + u * 3
        x, y = (self.W - mw) // 2, max(u, (self.H - mh) // 2)
        pygame.draw.rect(self.screen, PANEL, (x, y, mw, mh), border_radius=u // 2)
        pygame.draw.rect(self.screen, BORDER, (x, y, mw, mh), 1, border_radius=u // 2)
        self.text("MENU", self.f_mb, ACCENT, x + u, y + u // 2)
        y += u * 2
        for i, (label, val_fn, _) in enumerate(entries):
            if i == self.menu_sel:
                pygame.draw.rect(self.screen, SEL_BG, (x + 4, y, mw - 8, row), border_radius=u // 4)
            self.text(label, self.f_m, FG if i == self.menu_sel else DIM, x + u, y + (row - self.f_m.get_height()) // 2)
            val = val_fn() if val_fn else ""
            if val:
                self.text(val, self.f_s, ACCENT, x + mw - u, y + (row - self.f_s.get_height()) // 2, right=True)
            y += row

    def draw_buttontest(self) -> None:
        u = self.u
        self.paint_bg()
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

    def draw_voiceline(self) -> None:
        """Retro speech banner: animated skull + flashing text, bottom of screen."""
        if not self.voiceline:
            return
        text, mood, until = self.voiceline
        now = time.monotonic()
        if now > until:
            self.voiceline = None
            return
        u = self.u
        t = now
        sc = max(3, u // 5)
        sw, sh = art.skull_size(sc)
        pad = u // 2
        tw = self.f_mb.size(text)[0]
        bw = sw + pad * 3 + tw
        bh = sh + pad
        x = (self.W - bw) // 2
        y = int(self.H * 0.72)
        col = LEVEL_COL[HIGH] if mood == "rage" else LEVEL_COL[WARN] if mood == "alert" else GOOD
        # box
        pygame.draw.rect(self.screen, (8, 8, 12), (x, y, bw, bh), border_radius=u // 3)
        pygame.draw.rect(self.screen, col, (x, y, bw, bh), width=3, border_radius=u // 3)
        art.draw_skull(self.screen, x + pad, y + (bh - sh) // 2, sc, mood, int(t * 6), t=t, animate=True)
        # typewriter (types out over ~0.5s) + flash once complete
        total = 3.2
        elapsed_in = total - (until - now)          # 0 at start, grows with time
        reveal = max(1, min(len(text), int(elapsed_in / 0.5 * len(text))))
        shown = text[:reveal]
        done = reveal >= len(text)
        flash = (col if int(t * 6) % 2 else FG) if done else col
        self.text(shown, self.f_mb, flash, x + sw + pad * 2, y + (bh - self.f_mb.get_height()) // 2)

    def draw_wipeconfirm(self, top: int, bottom: int) -> None:
        u = self.u
        cx = self.W // 2
        y = top + u * 2
        self.text("PANIC WIPE", self.f_title, LEVEL_COL[HIGH], cx, y, center=True)
        y += int(u * 2.6)
        for ln in ["This erases ALL recorded data: session logs, reports,",
                   "the device baseline / following history, and your",
                   "allow & watch lists. Your settings are kept.",
                   "", "This cannot be undone."]:
            self.text(ln, self.f_m, FG if not ln.startswith("This cannot") else LEVEL_COL[WARN],
                      cx, y, center=True)
            y += int(u * 1.3)
        y += u
        # hold-to-confirm bar
        frac = min(1.0, self._wipe_hold / 2.0)
        bw = int(self.W * 0.5)
        bx = cx - bw // 2
        pygame.draw.rect(self.screen, FAINT, (bx, y, bw, u), border_radius=u // 2)
        pygame.draw.rect(self.screen, LEVEL_COL[HIGH], (bx, y, int(bw * frac), u), border_radius=u // 2)
        y += int(u * 1.8)
        self.text("HOLD  A  TO WIPE" if frac < 1 else "WIPING...", self.f_l, LEVEL_COL[HIGH], cx, y, center=True)
        self.text("B to cancel", self.f_s, DIM, cx, bottom - u, center=True)

    def draw_spectrum(self, top: int, bottom: int) -> None:
        u = self.u
        act = self.w.channel_activity()
        clean = self.w.cleanest_channel()
        cur = self.w.sensor.get("ch", 0)
        self.text("2.4 GHz CHANNEL SPECTRUM", self.f_l, ACCENT, self.W // 2, top + u // 2, center=True)
        gx0, gx1 = u * 2, self.W - u * 2
        base = bottom - int(u * 2.5)
        gh = base - (top + int(u * 2.5))
        n = 13
        bw = (gx1 - gx0) / n
        peak = max(1.0, max(v["score"] for v in act.values()))
        t = time.monotonic()
        for ch in range(1, 14):
            v = act[ch]
            frac = v["score"] / peak
            # gentle live shimmer
            frac *= 0.92 + 0.08 * math.sin(t * 3 + ch)
            bh = int(gh * min(1.0, frac))
            x = int(gx0 + (ch - 1) * bw)
            busy = frac > 0.66
            col = LEVEL_COL[HIGH] if busy else LEVEL_COL[WARN] if frac > 0.33 else GOOD
            if ch in (1, 6, 11):
                pygame.draw.rect(self.screen, FAINT, (x + 3, top + int(u * 2.5), bw - 6, gh), 1)
            pygame.draw.rect(self.screen, col, (x + 5, base - bh, bw - 10, bh), border_radius=4)
            lab_col = ACCENT if ch == cur else (GOOD if ch == clean else DIM)
            self.text(f"{ch}", self.f_m, lab_col, int(x + bw / 2), base + u // 4, center=True)
            if v["aps"]:
                self.text(f"{v['aps']}", self.f_s, DIM, int(x + bw / 2), base - bh - int(u * 0.9), center=True)
            if ch == cur:
                self.text("scan", self.f_s, ACCENT, int(x + bw / 2), base - bh - int(u * 1.7), center=True)
        self.text(f"Cleanest of 1/6/11: channel {clean}   ·   sensor on ch {cur or '?'}",
                  self.f_m, FG, self.W // 2, bottom - int(u * 1.3), center=True)

    def draw_oracle(self, top: int, bottom: int) -> None:
        u = self.u
        o = self._oracle or {}
        thinking = self._oracle_thinking
        t = time.monotonic()
        sc = max(4, u // 4)
        sw, sh = art.skull_size(sc)
        mood = "rage" if thinking else o.get("mood", "calm")
        art.draw_skull(self.screen, (self.W - sw) // 2, top + u, sc, mood, int(t * 3), t=t, animate=True)
        y = top + u + sh + u
        self.text("THE DEMON KING SPEAKS", self.f_l, LEVEL_COL[HIGH] if mood == "rage" else ACCENT,
                  self.W // 2, y, center=True)
        y += int(u * 1.8)
        if thinking:
            dots = "." * (int(t * 2) % 4)
            via = self._oracle_via or "..."
            self.text(f"the king gathers his thoughts{dots}", self.f_m, DIM, self.W // 2, y, center=True)
            self.text(f"(consulting {via})", self.f_s, DIM, self.W // 2, y + int(u * 1.4), center=True)
            return
        for line in self._wrap(o.get("speech", "..."), self.f_m, self.W - u * 4):
            self.text(line, self.f_m, FG, self.W // 2, y, center=True)
            y += int(u * 1.2)
        y += u // 2
        for act in o.get("actions", []):
            self.text(act, self.f_m, LEVEL_COL[WARN], self.W // 2, y, center=True, maxw=self.W - u * 3)
            y += int(u * 1.2)
        via = o.get("via", "rules")
        col = GOOD if via == "local LLM" else DIM
        self.text(f"[ advisor: {via} ]", self.f_s, col, self.W // 2, bottom - u, center=True)

    def _wrap(self, text, font, maxw):
        words = text.split()
        lines, cur = [], ""
        for w in words:
            trial = (cur + " " + w).strip()
            if font.size(trial)[0] > maxw and cur:
                lines.append(cur)
                cur = w
            else:
                cur = trial
        if cur:
            lines.append(cur)
        return lines[:6]

    def consult_oracle(self) -> None:
        import threading

        from . import agent
        if self._oracle_thinking:
            return
        self.view = "oracle"
        self._oracle_thinking = True
        # a tiny CPU LLM can take seconds; snapshot state and run off the UI thread
        endpoint = agent.llm_available(self.cfg)
        self._oracle_via = "local LLM" if endpoint else "rules"
        state = agent.summarize_state(self.w)

        def worker():
            try:
                out = agent.consult(self.w, self.cfg)
            except Exception as e:
                out = {"speech": "My sight falters.", "actions": [f"- advisor error: {e}"],
                       "mood": "alert", "via": "error"}
            self._oracle = out
            self._oracle_thinking = False
            self._oracle_ready = True

        threading.Thread(target=worker, daemon=True).start()

    def _poll_oracle(self) -> None:
        if self._oracle_ready:
            self._oracle_ready = False
            if self.view == "oracle" and self._oracle:
                self.speak("rogue" if self._oracle.get("mood") == "rage" else "clear", 2.2)

    def hints(self) -> list[tuple[str, str]]:
        v = self.view
        if v == "oracle":
            return [("A", "ask again"), ("B", "back")]
        if v == "spectrum":
            return [("B", "back")]
        if v == "wipeconfirm":
            return [("A", "hold to wipe"), ("B", "cancel")]
        if v == "hunt":
            return [("B", "back"), ("Y", "mine"), ("X", "checkpoint"), ("SELECT", "mute")]
        if v == "detail":
            d = self.w.devices.get(self.detail_id or "")
        if v == "detail":
            d = self.w.devices.get(self.detail_id or "")
            h = [("A", "hunt"), ("B", "back"), ("Y", "mine"), ("R1", "watch"), ("L2", "tripwire")]
            if d and d.kind == "ap":
                h += [("L1", "karma"), ("R2", "scan")]
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
            label = h.get("name") or h.get("vendor") or h.get("mac", "")
            self.text(f"{h.get('ip', '?'):<15} {label}{tag}", self.f_m, col, u, ry + u // 6,
                      maxw=self.W - u * 10)
            self.text(f"{h.get('mac', '')}   ports: {ports}", self.f_s, DIM, u, ry + int(u * 1.0), maxw=self.W - u * 10)

    def draw_boot(self) -> None:
        self.paint_bg()
        frame = int(time.monotonic() * 3)
        sc = max(5, self.H // 44)
        sw, sh = art.skull_size(sc)
        cx = self.W // 2
        art.draw_skull(self.screen, cx - sw // 2, int(self.H * 0.20), sc, "rage", frame,
                       t=time.monotonic(), animate=True)
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
        self.paint_bg()
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
            if TABS[self.tab] == "RADAR":
                self.draw_radar(y, bottom)
            else:
                self.draw_list(y, bottom)
        elif self.view == "detail":
            self.draw_detail(y, bottom)
        elif self.view == "hunt":
            self.draw_hunt(y, bottom)
        elif self.view == "scanconfirm":
            self.draw_scanconfirm(y, bottom)
        elif self.view == "scanview":
            self.draw_scanview(y, bottom)
        elif self.view == "oracle":
            self.draw_oracle(y, bottom)
        elif self.view == "spectrum":
            self.draw_spectrum(y, bottom)
        elif self.view == "wipeconfirm":
            self.draw_wipeconfirm(y, bottom)
        if self.view == "menu":
            self.draw_menu()
        else:
            self.draw_toast()
        self.draw_voiceline()
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
                    if self.rumble_on:
                        self.inp.rumble(1.0, 600)
                    self.speak(self._event_for_alert(a))   # voiceline + chiptune sting
                elif a.level == WARN:
                    self.snd.play("blip")
        self._check_karma()
        self._poll_oracle()
        events = pygame.event.get()
        for ev in events:
            if ev.type == pygame.QUIT:
                self.running = False
        for act in self.inp.process(events):
            self.handle(act)
        # Panic wipe: must HOLD A (east face) for 2s on the confirm screen.
        if self.view == "wipeconfirm":
            holding_a = any(self.inp.face(p) == C.A for p in self.inp.held)
            if holding_a:
                self._wipe_hold += 1 / 30.0
                if self._wipe_hold >= 2.0:
                    self._do_panic_wipe()
            else:
                self._wipe_hold = 0.0
        self.render()

    def _do_panic_wipe(self) -> None:
        removed = self.w.panic_wipe(os.path.expanduser(self.cfg.get("log_dir", "~/csk-logs")))
        try:
            from .store import Store
            self.w.store = Store()
            self.w.set_location(self.locations[self.loc_idx])
        except Exception:
            self.w.store = None
        self._wipe_hold = 0.0
        self.view = "list"
        self.notify(f"WIPED: {removed['files']} files, {removed['devices']} devices erased", HIGH, 5)
        self.snd.play("rage")

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
