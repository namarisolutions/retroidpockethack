"""Open Drone ID (ASTM F3411 / FAA Remote ID) decoder.

Most Remote-ID drones broadcast a standard message set over Bluetooth LE (and
WiFi). Because the firmware already forwards the full BLE advertisement, these
decode entirely host-side - no reflash needed. The payload reveals the drone's
serial, its live position, AND the operator's location, which is exactly what a
counter-surveillance tool wants when a drone is overhead.

BLE carrier: Service Data for 16-bit UUID 0xFFFA (ASTM), application code 0x0D,
a message counter, then one 25-byte message or a message pack (type 0xF).
"""
from __future__ import annotations

import struct

UUID_ODID = 0xFFFA
APP_CODE = 0x0D

MSG_BASIC, MSG_LOCATION, MSG_AUTH, MSG_SELFID, MSG_SYSTEM, MSG_OPID, MSG_PACK = 0, 1, 2, 3, 4, 5, 0xF

UA_TYPES = {0: "none", 1: "aeroplane", 2: "helicopter/multirotor", 3: "gyroplane", 4: "hybrid lift",
            5: "ornithopter", 6: "glider", 7: "kite", 8: "free balloon", 9: "captive balloon",
            10: "airship", 11: "free fall/parachute", 12: "rocket", 13: "tethered powered",
            14: "ground obstacle"}


def _i32(b: bytes) -> int:
    return struct.unpack("<i", b)[0]


def _u16(b: bytes) -> int:
    return struct.unpack("<H", b)[0]


def _alt(raw: int) -> float:
    return raw * 0.5 - 1000.0


def decode_message(msg: bytes) -> dict:
    """Decode one 25-byte Open Drone ID message into a partial dict."""
    if len(msg) < 1:
        return {}
    mtype = msg[0] >> 4
    b = msg[1:]
    out: dict = {}
    if mtype == MSG_BASIC and len(b) >= 21:
        out["id_type"] = b[0] >> 4
        out["ua_type"] = UA_TYPES.get(b[0] & 0x0F, str(b[0] & 0x0F))
        out["serial"] = b[1:21].split(b"\x00")[0].decode("ascii", "replace").strip()
    elif mtype == MSG_LOCATION and len(b) >= 16:
        out["op_status"] = b[0] >> 4
        out["track_deg"] = b[1] * 2 if (b[0] & 0x02) else b[1]
        out["speed_ms"] = round(b[2] * 0.25, 1) if b[2] < 255 else None
        out["lat"] = round(_i32(b[4:8]) / 1e7, 7)
        out["lon"] = round(_i32(b[8:12]) / 1e7, 7)
        if len(b) >= 16:
            out["alt_geo_m"] = round(_alt(_u16(b[14:16])), 1)
    elif mtype == MSG_SYSTEM and len(b) >= 9:
        out["operator_lat"] = round(_i32(b[1:5]) / 1e7, 7)
        out["operator_lon"] = round(_i32(b[5:9]) / 1e7, 7)
    elif mtype == MSG_OPID and len(b) >= 21:
        out["operator_id"] = b[1:21].split(b"\x00")[0].decode("ascii", "replace").strip()
    out["_type"] = mtype
    return out


def decode(buf: bytes) -> dict:
    """Decode a single message or a message pack into one merged dict."""
    if not buf:
        return {}
    info: dict = {}
    if buf[0] >> 4 == MSG_PACK and len(buf) >= 3:
        size = buf[1] or 25
        count = buf[2]
        off = 3
        for _ in range(count):
            if off + size > len(buf):
                break
            info.update({k: v for k, v in decode_message(buf[off:off + size]).items() if k != "_type"})
            off += size
    else:
        info.update({k: v for k, v in decode_message(buf).items() if k != "_type"})
    return info


def from_ble(payload: bytes) -> dict | None:
    """Extract Open Drone ID from a raw BLE advertisement, or None.

    Looks for Service Data (AD type 0x16) with UUID 0xFFFA and app code 0x0D.
    """
    i, n = 0, len(payload)
    while i < n:
        ln = payload[i]
        if ln == 0 or i + 1 + ln > n:
            break
        t = payload[i + 1]
        d = payload[i + 2:i + 1 + ln]
        if t == 0x16 and len(d) >= 4 and d[0] == 0xFA and d[1] == 0xFF and d[2] == APP_CODE:
            # d = [FA FF][0D app][counter][message...]
            info = decode(bytes(d[4:]))
            if info:
                info["via"] = "BLE"
                return info
        i += 1 + ln
    return None


def from_odid_hex(hexstr: str) -> dict | None:
    """Decode a raw ODID message pack (as the firmware forwards WiFi RID)."""
    try:
        info = decode(bytes.fromhex(hexstr))
    except ValueError:
        return None
    if info:
        info["via"] = "WiFi"
    return info


def _msg(mtype: int, body: bytes) -> bytes:
    return bytes([mtype << 4]) + body.ljust(24, b"\x00")[:24]


def build_pack_ble(serial="1581F5XYZ123", ua_type=2, lat=37.7749, lon=-122.4194,
                   op_lat=37.7752, op_lon=-122.4190) -> bytes:
    """Build a realistic Open Drone ID BLE advertisement (for the demo and tests)."""
    basic = _msg(MSG_BASIC, bytes([(1 << 4) | ua_type]) + serial.encode()[:20].ljust(20, b"\x00"))
    loc = _msg(MSG_LOCATION, bytes([0x10, 90, 40, 0]) + struct.pack("<ii", int(lat * 1e7), int(lon * 1e7))
               + struct.pack("<HH", 0, int((120 + 1000) / 0.5)))
    syst = _msg(MSG_SYSTEM, bytes([0x00]) + struct.pack("<ii", int(op_lat * 1e7), int(op_lon * 1e7)))
    pack = bytes([MSG_PACK << 4, 25, 3]) + basic + loc + syst
    sd = bytes([0xFA, 0xFF, APP_CODE, 0x00]) + pack        # UUID 0xFFFA + app + counter + pack
    ad = bytes([len(sd) + 1, 0x16]) + sd                    # AD: len, type 0x16 service data
    flags = bytes([0x02, 0x01, 0x06])
    return flags + ad


def summary(info: dict) -> str:
    """Short human label for a decoded drone."""
    parts = []
    if info.get("serial"):
        parts.append(info["serial"])
    if info.get("ua_type"):
        parts.append(info["ua_type"])
    return "Drone " + " ".join(parts) if parts else "Drone (Remote ID)"
