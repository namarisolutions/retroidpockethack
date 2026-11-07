"""Turn each detected device into a named 'soul' the Demon King has claimed.

Pure flavour: a deterministic monster name derived from a MAC address, so the
same device always gets the same name. Category tints the title in the UI.
"""
from __future__ import annotations

import hashlib

_ADJ = ["Wailing", "Hollow", "Creeping", "Gilded", "Rotten", "Whispering", "Shattered",
        "Burning", "Frozen", "Vile", "Cursed", "Hungry", "Faceless", "Twitching", "Silent",
        "Ravenous", "Pale", "Writhing", "Grinning", "Forsaken", "Molten", "Sunken", "Jagged"]
_BEAST = ["Wraith", "Gremlin", "Imp", "Specter", "Revenant", "Ghoul", "Lurker", "Shade",
          "Basilisk", "Harpy", "Cerberus", "Gorgon", "Wyrm", "Phantom", "Djinn", "Golem",
          "Banshee", "Leech", "Mimic", "Hydra", "Cacodemon", "Watcher", "Crawler", "Howler"]
_TITLE = ["the Unseen", "of the Static", "of Ash", "the Twice-Bound", "of Broken Glass",
          "the Nameless", "of the Deep Net", "the Signal-Eater", "of Cold Light", "the Relentless",
          "of the Hollow Hz", "the Packet-Drinker", "of Null", "the Ever-Watching"]

# Category -> flavour rank the Demon King assigns.
RANK = {"tracker": "PARASITE", "camera": "EYE-TYRANT", "alpr": "ROAD-SPECTRE",
        "drone": "SKY-DEMON", "phone": "WANDERER", "wearable": "FAMILIAR",
        "iot": "HOMUNCULUS", "random": "PHANTOM", "": "LESSER SOUL"}


def _h(mac: str) -> int:
    return int(hashlib.sha1(mac.lower().encode()).hexdigest(), 16)


def monster_name(mac: str) -> str:
    h = _h(mac)
    a = _ADJ[h % len(_ADJ)]
    b = _BEAST[(h >> 8) % len(_BEAST)]
    name = f"{a} {b}"
    if (h >> 16) % 3 == 0:                       # some get an epithet
        name += " " + _TITLE[(h >> 20) % len(_TITLE)]
    return name


def rank(category: str) -> str:
    return RANK.get(category, RANK[""])


def soul_line(mac: str, category: str) -> str:
    return f"{monster_name(mac)}  -  {rank(category)}"


# King's rank ladder by number of souls claimed (unique devices ever seen).
_RANKS = [(0, "Restless Spirit"), (5, "Imp"), (15, "Hex-Knight"), (40, "Warlock"),
          (100, "Dread Baron"), (250, "Lich"), (600, "Demon Lord"), (1500, "THE UNKILLABLE KING")]


def king_rank(souls: int) -> tuple[str, int, int]:
    """Return (title, souls_into_rank, souls_to_next). next=0 at the top."""
    title, floor = _RANKS[0][1], 0
    nxt = 0
    for i, (need, name) in enumerate(_RANKS):
        if souls >= need:
            title, floor = name, need
            nxt = _RANKS[i + 1][0] if i + 1 < len(_RANKS) else 0
    return title, souls - floor, (nxt - souls if nxt else 0)
