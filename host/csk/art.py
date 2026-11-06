"""Pixel-art mascot for THE UNKILLABLE DEMON KING.

A horned demon skull drawn from a character grid, scaled up blocky like a
Flipper. It has moods that track the worst active threat so the device has a
face: calm when clear, alert on WARN, enraged with burning eyes on HIGH.
"""
from __future__ import annotations

import pygame

PALETTE = {
    ".": None,                     # transparent
    "H": (44, 26, 38),             # horn (dark)
    "h": (92, 54, 74),             # horn (light edge)
    "G": (255, 196, 40),           # crown gold
    "g": (190, 140, 18),           # crown gold shadow
    "j": (255, 70, 70),            # crown gem
    "B": (232, 230, 214),          # bone
    "S": (156, 156, 142),          # bone shadow
    "e": (10, 10, 12),             # eye socket / nasal (dark)
    "p": (255, 46, 46),            # enraged eye glow
    "y": (120, 200, 255),          # calm eye glint
    "f": (255, 136, 24),           # flame
    "F": (255, 214, 72),           # flame bright
    "_": (10, 10, 12),             # tooth gap
}

# 16 wide. Rows are equal length.
_SKULL = [
    "....H......H....",
    "...Hh.gGGg.hH...",
    "...HhGjGGjGhH...",
    "....gGGGGGGg....",
    "....BBBBBBBB....",
    "...BBBBBBBBBB...",
    "..BBBBBBBBBBBB..",
    "..BBeeeSSeeeBB..",
    "..BBeXeSSeXeBB..",
    "..BBeeeSSeeeBB..",
    "..BBBBSeeSBBBB..",
    "..SBBBeeeeBBBS..",
    "...BBBBBBBBBB...",
    "...B_B_B_B_B_...",
    "....BBBBBBBB....",
]

# Flames that sit above the crown when enraged.
_FLAMES = [
    "....f..FF..f....",
    "...fF.fFFf.Ff...",
    "....f..ff..f....",
]


def _draw_grid(surf, grid, x, y, scale, eye):
    for ry, row in enumerate(grid):
        for rx, ch in enumerate(row):
            c = ch
            if ch == "X":            # eye pixel depends on mood
                c = eye
            col = PALETTE.get(c)
            if col:
                pygame.draw.rect(surf, col, (x + rx * scale, y + ry * scale, scale, scale))


def skull_size(scale: int) -> tuple[int, int]:
    return (16 * scale, len(_SKULL) * scale)


def draw_skull(surf, x: int, y: int, scale: int = 4, mood: str = "calm", frame: int = 0) -> None:
    """mood: 'calm' | 'alert' | 'rage'. frame animates flames/eyes."""
    eye = {"calm": "y", "alert": "p", "rage": "p"}.get(mood, "e")
    if mood == "rage":
        # flicker the eyes
        eye = "p" if frame % 2 == 0 else "f"
        fl = _FLAMES if frame % 2 == 0 else [r.replace("F", "f") for r in _FLAMES]
        _draw_grid(surf, fl, x, y - len(_FLAMES) * scale, scale, eye)
    _draw_grid(surf, _SKULL, x, y, scale, eye)


NAME = "THE UNKILLABLE DEMON KING"
SHORT = "DEMON KING"
TAGLINE = "it does not sleep. it does not blink. it watches."
