"""Decode raw BLE advertisement payloads and classify known tracker types.

All decoding happens here (host side) so the ESP32 firmware stays dumb and
new signatures can be added without reflashing.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# AD types
AD_FLAGS = 0x01
AD_UUID16_INCOMPLETE = 0x02
AD_UUID16_COMPLETE = 0x03
AD_NAME_SHORT = 0x08
AD_NAME_COMPLETE = 0x09
AD_SERVICE_DATA16 = 0x16
AD_MANUFACTURER = 0xFF

# Bluetooth SIG company IDs
CO_APPLE = 0x004C
CO_MICROSOFT = 0x0006
CO_SAMSUNG = 0x0075
CO_GOOGLE = 0x00E0

# 16-bit service UUIDs
UUID_TILE_A = 0xFEED
UUID_TILE_B = 0xFEEC
UUID_SMARTTAG = 0xFD5A
UUID_GOOGLE_FMDN = 0xFEAA      # Eddystone frame space; FMDN uses frame types 0x40/0x41
UUID_DULT = 0xFCB2             # IETF/Apple-Google "Detecting Unwanted Location Trackers"

# Severity levels used throughout the app
INFO, WARN, HIGH = 0, 1, 2


@dataclass
class Adv:
    name: str = ""
    uuids16: list[int] = field(default_factory=list)
    service_data: dict[int, bytes] = field(default_factory=dict)
    mfr: dict[int, bytes] = field(default_factory=dict)


@dataclass
class Classification:
    category: str          # "tracker", "phone", "wearable", "camera", "alpr", "" (unknown)
    label: str             # human readable
    level: int = INFO      # base suspicion level
    stable_id: bool = True  # False if this device type rotates its address often


def parse_adv(payload: bytes) -> Adv:
    adv = Adv()
    i = 0
    n = len(payload)
    while i < n:
        ln = payload[i]
        if ln == 0 or i + 1 + ln > n:
            break
        t = payload[i + 1]
        d = payload[i + 2:i + 1 + ln]
        if t in (AD_UUID16_INCOMPLETE, AD_UUID16_COMPLETE):
            for j in range(0, len(d) - 1, 2):
                adv.uuids16.append(d[j] | d[j + 1] << 8)
        elif t in (AD_NAME_SHORT, AD_NAME_COMPLETE):
            adv.name = d.decode("utf-8", "replace")
        elif t == AD_SERVICE_DATA16 and len(d) >= 2:
            adv.service_data[d[0] | d[1] << 8] = d[2:]
        elif t == AD_MANUFACTURER and len(d) >= 2:
            adv.mfr[d[0] | d[1] << 8] = d[2:]
        i += 1 + ln
    return adv


CAMERA_NAME_HINTS = ("cam", "ipc", "v380", "spy", "nanny", "lookcam", "hdwifi", "ezviz", "hikvision", "dahua", "wyze", "blink", "ring")
ALPR_NAME_HINTS = ("flock", "penguin", "pigvision", "fs ext battery")


def _apple(d: bytes) -> Classification:
    # Apple's continuity data is a TLV stream: [type][len][payload]...
    if len(d) >= 2 and d[0] == 0x12:
        if d[1] >= 0x19:
            return Classification("tracker", "Find My device (away from owner)", WARN)
        return Classification("tracker", "Find My device (near owner)", INFO, stable_id=False)
    kinds = {0x07: "AirPods/Beats", 0x10: "Apple device (iPhone/Mac/Watch)", 0x0F: "Apple device",
             0x0C: "Apple Handoff", 0x09: "Apple AirPlay target", 0x05: "Apple AirDrop"}
    label = kinds.get(d[0] if d else -1, "Apple device")
    return Classification("phone" if "iPhone" in label or label == "Apple device" else "wearable", label, INFO, stable_id=False)


UUID_ODID = 0xFFFA            # ASTM - Open Drone ID / FAA Remote ID
UUID_FASTPAIR = 0xFE2C        # Google Fast Pair


def pairing_kind(adv: Adv) -> str:
    """If this advert is a device-pairing/notification popup (the kind BLE-spam
    tools flood to pop endless dialogs), return its source, else ''.

    Single hits are normal (real AirPods, a Fast Pair earbud); the *flood* is
    the attack, so world.py counts distinct spammers over a short window.
    """
    # Apple proximity-pairing (AirPods/Beats popup) and nearby-action prompts
    ad = adv.mfr.get(CO_APPLE, b"")
    if ad[:1] in (b"\x07", b"\x09", b"\x0f"):     # proximity pair / airplay / nearby action
        return "Apple"
    if UUID_FASTPAIR in adv.service_data:          # Google Fast Pair model-id frame
        return "FastPair"
    ms = adv.mfr.get(CO_MICROSOFT, b"")
    if ms[:3] == b"\x03\x00\x80" or ms[:1] == b"\x03":  # Microsoft SwiftPair beacon
        return "SwiftPair"
    sm = adv.mfr.get(CO_SAMSUNG, b"")
    if sm[:2] in (b"\x01\x00", b"\x42\x09", b"\x01\x02"):  # Samsung buds/watch pairing
        return "Samsung"
    return ""


def classify(adv: Adv) -> Classification:
    sd = adv.service_data
    uu = set(adv.uuids16) | set(sd)

    if UUID_ODID in sd and sd[UUID_ODID][:1] == b"\x0d":
        return Classification("drone", "Drone (Remote ID)", HIGH)
    if UUID_DULT in uu:
        return Classification("tracker", "Tracker in unwanted-tracking mode (DULT)", HIGH)
    if UUID_GOOGLE_FMDN in sd and sd[UUID_GOOGLE_FMDN][:1] in (b"\x40", b"\x41"):
        if sd[UUID_GOOGLE_FMDN][:1] == b"\x41":
            return Classification("tracker", "Google Find My tracker (unwanted-tracking mode)", HIGH)
        return Classification("tracker", "Google Find My tracker", WARN)
    if UUID_SMARTTAG in uu:
        return Classification("tracker", "Samsung SmartTag", WARN)
    if UUID_TILE_A in uu or UUID_TILE_B in uu:
        return Classification("tracker", "Tile tracker", WARN)
    if CO_APPLE in adv.mfr:
        return _apple(adv.mfr[CO_APPLE])

    name = adv.name.lower()
    if name:
        if any(h in name for h in ALPR_NAME_HINTS):
            return Classification("alpr", f"Possible ALPR/Flock device '{adv.name}'", WARN)
        if any(h in name for h in CAMERA_NAME_HINTS):
            return Classification("camera", f"Camera-like BLE name '{adv.name}'", WARN)

    if CO_MICROSOFT in adv.mfr:
        return Classification("phone", "Windows device", INFO, stable_id=False)
    if CO_SAMSUNG in adv.mfr:
        return Classification("phone", "Samsung device", INFO, stable_id=False)
    if CO_GOOGLE in adv.mfr:
        return Classification("phone", "Google device", INFO, stable_id=False)
    return Classification("", adv.name or "", INFO)
