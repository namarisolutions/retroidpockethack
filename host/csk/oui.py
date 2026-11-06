"""MAC vendor lookup with a camera/IoT category flag.

The built-in seed table is deliberately small. Run `python3 tools/update_oui.py`
once (on any machine with internet) to pull the full IEEE registry; vendor
names are then matched against the keyword lists below, which is far more
complete than any hand-maintained prefix list.
"""
from __future__ import annotations

import csv
import os

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
FULL_DB = os.path.join(DATA_DIR, "oui_full.csv")

CAMERA_KEYWORDS = (
    "hikvision", "dahua", "axis communications", "wyze", "ezviz", "reolink", "amcrest",
    "foscam", "hanwha", "vivotek", "uniview", "arlo", "immedia", "ring llc", "lorex", "swann",
    "imilab", "yi technology", "mobotix", "avigilon", "verkada", "flock",
)
IOT_KEYWORDS = ("espressif", "tuya", "beken", "realtek", "bouffalo", "winner micro", "hi-flying", "shanghai high-flying")

# Small seed so the app is useful before the full DB is downloaded.
SEED = {
    "00408C": "Axis Communications", "ACCC8E": "Axis Communications", "B8A44F": "Axis Communications",
    "2857BE": "Hikvision", "4419B6": "Hikvision", "C056E3": "Hikvision", "BCAD28": "Hikvision",
    "4CBD8F": "Hikvision", "54C415": "Hikvision", "1868CB": "Hikvision",
    "3CEF8C": "Dahua", "9002A9": "Dahua", "4C11BF": "Dahua", "E0508B": "Dahua", "38AF29": "Dahua",
    "2CAA8E": "Wyze Labs", "D03F27": "Wyze Labs", "7C78B2": "Wyze Labs",
    "240AC4": "Espressif", "30AEA4": "Espressif", "246F28": "Espressif", "A4CF12": "Espressif",
    "84CCA8": "Espressif", "8CAAB5": "Espressif", "3C71BF": "Espressif", "7C9EBD": "Espressif",
    "10521C": "Espressif", "C82B96": "Espressif", "ECFABC": "Espressif", "483FDA": "Espressif",
    "98F4AB": "Espressif", "5CCF7F": "Espressif", "600194": "Espressif", "18FE34": "Espressif",
    "BCDDC2": "Espressif", "2CF432": "Espressif", "84F3EB": "Espressif", "CC50E3": "Espressif",
    "40F520": "Espressif", "AC67B2": "Espressif", "94B97E": "Espressif", "E8DB84": "Espressif",
    "083AF2": "Espressif", "349454": "Espressif", "3C6105": "Espressif", "58BF25": "Espressif",
    "78E36D": "Espressif", "308398": "Espressif", "C44F33": "Espressif", "2462AB": "Espressif",
    "00E04C": "Realtek",
}

_db: dict[str, str] | None = None


def _load() -> dict[str, str]:
    global _db
    if _db is None:
        _db = dict(SEED)
        if os.path.exists(FULL_DB):
            with open(FULL_DB, newline="", encoding="utf-8") as f:
                for row in csv.reader(f):
                    if len(row) >= 2:
                        _db[row[0]] = row[1]
    return _db


def is_randomized(mac: str) -> bool:
    """Locally-administered bit set -> private/randomized address, no vendor info."""
    try:
        return bool(int(mac[:2], 16) & 0x02)
    except ValueError:
        return False


def lookup(mac: str) -> tuple[str, str]:
    """Return (vendor, category) where category is 'camera', 'iot', 'random' or ''."""
    if is_randomized(mac):
        return "", "random"
    vendor = _load().get(mac.replace(":", "").upper()[:6], "")
    v = vendor.lower()
    if any(k in v for k in CAMERA_KEYWORDS):
        return vendor, "camera"
    if any(k in v for k in IOT_KEYWORDS):
        return vendor, "iot"
    return vendor, ""


def have_full_db() -> bool:
    return os.path.exists(FULL_DB)
