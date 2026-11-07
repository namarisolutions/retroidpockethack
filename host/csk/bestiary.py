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
