"""Gamepad / keyboard input -> logical actions.

Input paths, all live at once:
  1. SDL GameController API: works whenever SDL recognises the Retroid pad
     (normal case on handheld distros, which ship a gamecontrollerdb).
  2. Raw joystick fallback: for a pad SDL doesn't know. Button indices come
     from the Linux evdev order and can be overridden in ~/.config/csk/config.json.
  3. Keyboard: desktop testing, or gptokeyb (see csk.gptk).

Face buttons are handled *by position* (SOUTH/EAST/WEST/NORTH), then turned
into logical actions using the face layout setting, so the on-screen hints
always match the letters printed on the device:

  nintendo (Retroid default): A = right, B = bottom, X = top, Y = left
  xbox:                       A = bottom, B = right, X = left, Y = top
"""
from __future__ import annotations

import time

import pygame

try:
    from pygame._sdl2 import controller as sdlc
except Exception:  # pragma: no cover
    sdlc = None

# Physical buttons
UP, DOWN, LEFT, RIGHT = "UP", "DOWN", "LEFT", "RIGHT"
SOUTH, EAST, WEST, NORTH = "SOUTH", "EAST", "WEST", "NORTH"
L1, R1, L2, R2, START, SELECT, HOME = "L1", "R1", "L2", "R2", "START", "SELECT", "HOME"

# Logical face actions (named after the printed letter)
A, B, X, Y = "A", "B", "X", "Y"

LAYOUTS = {
    "nintendo": {EAST: A, SOUTH: B, NORTH: X, WEST: Y},
    "xbox": {SOUTH: A, EAST: B, WEST: X, NORTH: Y},
}

REPEATABLE = {UP, DOWN, LEFT, RIGHT}
REPEAT_DELAY, REPEAT_RATE = 0.35, 0.07
AXIS_THRESHOLD = 0.55

KEYMAP = {
    pygame.K_UP: UP, pygame.K_DOWN: DOWN, pygame.K_LEFT: LEFT, pygame.K_RIGHT: RIGHT,
    pygame.K_w: UP, pygame.K_s: DOWN,
    # gptokeyb mapping (csk.gptk) and desktop keys
    pygame.K_z: SOUTH, pygame.K_x: EAST, pygame.K_a: WEST, pygame.K_c: NORTH,
    pygame.K_q: L1, pygame.K_e: R1, pygame.K_1: L2, pygame.K_3: R2,
    pygame.K_RETURN: START, pygame.K_BACKSPACE: SELECT, pygame.K_ESCAPE: SELECT,
    pygame.K_TAB: R1, pygame.K_h: HOME,
}

# Linux evdev order as SDL enumerates it for a typical handheld pad.
DEFAULT_RAW_BUTTONS = {
    0: SOUTH, 1: EAST, 2: NORTH, 3: WEST, 4: L1, 5: R1, 6: L2, 7: R2,
    8: SELECT, 9: START, 10: HOME, 13: UP, 14: DOWN, 15: LEFT, 16: RIGHT,
}

if sdlc is not None:
    CTRL_BUTTONS = {
        pygame.CONTROLLER_BUTTON_A: SOUTH, pygame.CONTROLLER_BUTTON_B: EAST,
        pygame.CONTROLLER_BUTTON_X: WEST, pygame.CONTROLLER_BUTTON_Y: NORTH,
        pygame.CONTROLLER_BUTTON_LEFTSHOULDER: L1, pygame.CONTROLLER_BUTTON_RIGHTSHOULDER: R1,
        pygame.CONTROLLER_BUTTON_START: START, pygame.CONTROLLER_BUTTON_BACK: SELECT,
        pygame.CONTROLLER_BUTTON_GUIDE: HOME,
        pygame.CONTROLLER_BUTTON_DPAD_UP: UP, pygame.CONTROLLER_BUTTON_DPAD_DOWN: DOWN,
        pygame.CONTROLLER_BUTTON_DPAD_LEFT: LEFT, pygame.CONTROLLER_BUTTON_DPAD_RIGHT: RIGHT,
    }
else:  # pragma: no cover
    CTRL_BUTTONS = {}


class Input:
    def __init__(self, layout: str = "nintendo", raw_buttons: dict | None = None):
        self.layout = layout if layout in LAYOUTS else "nintendo"
        self.raw_buttons = {int(k): v for k, v in (raw_buttons or DEFAULT_RAW_BUTTONS).items()}
        self.controllers: dict[int, object] = {}   # instance_id -> Controller
        self.joysticks: dict[int, object] = {}     # instance_id -> Joystick (non-controller pads)
        self.held: dict[str, float] = {}           # physical -> press time
        self._next_repeat: dict[str, float] = {}
        self._axis_state: dict[tuple, str | None] = {}
        self.raw_log: list[str] = []               # for the button-test screen
        if sdlc is not None:
            try:
                sdlc.init()
            except Exception:
                pass
        pygame.joystick.init()
        self.rescan()

    # ----------------------------------------------------------- devices
    def rescan(self) -> None:
        for i in range(pygame.joystick.get_count()):
            self._open(i)

    def _open(self, index: int) -> None:
        try:
            if sdlc is not None and sdlc.is_controller(index):
                c = sdlc.Controller(index)
                iid = c.as_joystick().get_instance_id()
                if iid not in self.controllers:
                    self.controllers[iid] = c
                    self._log(f"controller: {c.name}")
                return
            j = pygame.joystick.Joystick(index)
            j.init()
            iid = j.get_instance_id()
            if iid not in self.joysticks:
                self.joysticks[iid] = j
                self._log(f"joystick (raw): {j.get_name()} b={j.get_numbuttons()} a={j.get_numaxes()} h={j.get_numhats()}")
        except Exception as e:
            self._log(f"pad {index} error: {e}")

    def names(self) -> list[str]:
        out = [getattr(c, "name", "controller") for c in self.controllers.values()]
        out += [j.get_name() + " (raw)" for j in self.joysticks.values()]
        return out

    def rumble(self, strength: float = 0.8, ms: int = 400) -> None:
        for c in self.controllers.values():
            try:
                c.rumble(strength, strength, ms)
            except Exception:
                pass

    def _log(self, s: str) -> None:
        self.raw_log.append(s)
        del self.raw_log[:-12]

    # ----------------------------------------------------------- mapping
    def face(self, phys: str) -> str:
        return LAYOUTS[self.layout].get(phys, phys)

    def toggle_layout(self) -> str:
        self.layout = "xbox" if self.layout == "nintendo" else "nintendo"
        return self.layout

    # ----------------------------------------------------------- events
    def process(self, events) -> list[str]:
        """Turn pygame events into logical actions (with auto-repeat on directions)."""
        out: list[str] = []
        now = time.monotonic()

        def press(p: str, src: str) -> None:
            self._log(f"{src} -> {p} -> {self.face(p)}")
            if p in self.held:
                return
            self.held[p] = now
            self._next_repeat[p] = now + REPEAT_DELAY
            out.append(self.face(p))

        def release(p: str) -> None:
            self.held.pop(p, None)
            self._next_repeat.pop(p, None)

        def axis_dir(key: tuple, value: float, neg: str, pos: str, src: str) -> None:
            new = neg if value < -AXIS_THRESHOLD else pos if value > AXIS_THRESHOLD else None
            old = self._axis_state.get(key)
            if new != old:
                if old:
                    release(old)
                if new:
                    press(new, src)
                self._axis_state[key] = new

        for ev in events:
            t = ev.type
            if t == pygame.KEYDOWN and ev.key in KEYMAP:
                press(KEYMAP[ev.key], f"key {pygame.key.name(ev.key)}")
            elif t == pygame.KEYUP and ev.key in KEYMAP:
                release(KEYMAP[ev.key])
            elif sdlc is not None and t == pygame.CONTROLLERDEVICEADDED:
                self._open(ev.device_index)
            elif sdlc is not None and t == pygame.CONTROLLERDEVICEREMOVED:
                self.controllers.pop(ev.instance_id, None)
            elif sdlc is not None and t == pygame.CONTROLLERBUTTONDOWN and ev.button in CTRL_BUTTONS:
                press(CTRL_BUTTONS[ev.button], f"pad btn {ev.button}")
            elif sdlc is not None and t == pygame.CONTROLLERBUTTONUP and ev.button in CTRL_BUTTONS:
                release(CTRL_BUTTONS[ev.button])
            elif sdlc is not None and t == pygame.CONTROLLERAXISMOTION:
                v = ev.value / 32767.0 if abs(ev.value) > 1.5 else ev.value
                if ev.axis == pygame.CONTROLLER_AXIS_LEFTX:
                    axis_dir(("c", ev.instance_id, ev.axis), v, LEFT, RIGHT, "stick x")
                elif ev.axis == pygame.CONTROLLER_AXIS_LEFTY:
                    axis_dir(("c", ev.instance_id, ev.axis), v, UP, DOWN, "stick y")
                elif ev.axis == pygame.CONTROLLER_AXIS_TRIGGERLEFT:
                    axis_dir(("c", ev.instance_id, ev.axis), v, "_", L2, "trigger L")
                elif ev.axis == pygame.CONTROLLER_AXIS_TRIGGERRIGHT:
                    axis_dir(("c", ev.instance_id, ev.axis), v, "_", R2, "trigger R")
            elif t == pygame.JOYDEVICEADDED:
                self._open(ev.device_index)
            elif t == pygame.JOYDEVICEREMOVED:
                self.joysticks.pop(ev.instance_id, None)
            # Raw joystick events only for pads not handled by the controller API
            elif t == pygame.JOYBUTTONDOWN and ev.instance_id in self.joysticks:
                p = self.raw_buttons.get(ev.button)
                self._log(f"raw btn {ev.button} -> {p}")
                if p:
                    press(p, f"raw btn {ev.button}")
            elif t == pygame.JOYBUTTONUP and ev.instance_id in self.joysticks:
                p = self.raw_buttons.get(ev.button)
                if p:
                    release(p)
            elif t == pygame.JOYHATMOTION and ev.instance_id in self.joysticks:
                hx, hy = ev.value
                axis_dir(("h", ev.instance_id, ev.hat, 0), hx, LEFT, RIGHT, "hat")
                axis_dir(("h", ev.instance_id, ev.hat, 1), -hy, UP, DOWN, "hat")
            elif t == pygame.JOYAXISMOTION and ev.instance_id in self.joysticks and ev.axis in (0, 1):
                if ev.axis == 0:
                    axis_dir(("j", ev.instance_id, 0), ev.value, LEFT, RIGHT, "raw stick")
                else:
                    axis_dir(("j", ev.instance_id, 1), ev.value, UP, DOWN, "raw stick")

        # Auto-repeat for held directions
        for p in list(self._next_repeat):
            if p in REPEATABLE and now >= self._next_repeat[p]:
                out.append(p)
                self._next_repeat[p] = now + REPEAT_RATE
        if START in self.held and SELECT in self.held:
            out.append("QUIT")
        return out
