"""Service discovery to name and fingerprint devices on your own network.

Three standard, read-only discovery protocols, no dependencies:
  * mDNS (224.0.0.251:5353)      - Bonjour/Avahi service + instance names
  * SSDP / UPnP (239.255.255.250:1900) - device SERVER/model strings
  * ONVIF WS-Discovery (:3702)   - cameras that announce themselves explicitly

Network senders and the (pure, unit-tested) parsers are separate so the
decoding can be tested without a network. Run only on networks you own.
"""
from __future__ import annotations

import re
import socket
import struct
import time

# mDNS service types worth asking about (camera/DVR-heavy, plus generic).
MDNS_SERVICES = [
    "_rtsp._tcp.local", "_onvif._tcp.local", "_axis-video._tcp.local",
    "_dahua._tcp.local", "_http._tcp.local", "_https._tcp.local",
    "_googlecast._tcp.local", "_airplay._tcp.local", "_hap._tcp.local",
    "_nvstream._tcp.local", "_daap._tcp.local",
]

CAMERA_NAME_RE = re.compile(
    r"\b(cam|camera|ipc|ipcam|nvr|dvr|doorbell|reolink|hikvision|dahua|amcrest|wyze|"
    r"ezviz|foscam|axis|arlo|lorex|swann|ring|unifi.?protect|tapo|v380|onvif)\b", re.I)


# --------------------------------------------------------------- DNS parsing
def _read_name(buf: bytes, off: int) -> tuple[str, int]:
    """Parse a (possibly compressed) DNS name. Returns (name, next_offset)."""
    labels = []
    jumped = False
    next_off = off
    guard = 0
    while True:
        guard += 1
        if guard > 128 or off >= len(buf):
            break
        ln = buf[off]
        if ln == 0:
            off += 1
            if not jumped:
                next_off = off
            break
        if ln & 0xC0 == 0xC0:               # compression pointer
            if off + 1 >= len(buf):
                break
            ptr = ((ln & 0x3F) << 8) | buf[off + 1]
            if not jumped:
                next_off = off + 2
            off = ptr
            jumped = True
            continue
        off += 1
        labels.append(buf[off:off + ln].decode("utf-8", "replace"))
        off += ln
    return ".".join(labels), (next_off if jumped else off)


def parse_mdns(buf: bytes) -> dict:
    """Extract {instance names, service types, A-record IPs, TXT model strings}."""
    out = {"names": set(), "services": set(), "ips": set(), "txt": set()}
    try:
        qd = struct.unpack(">H", buf[4:6])[0]
        an = struct.unpack(">H", buf[6:8])[0]
        ns = struct.unpack(">H", buf[8:10])[0]
        ar = struct.unpack(">H", buf[10:12])[0]
    except struct.error:
        return out
    off = 12
    for _ in range(qd):                      # skip questions
        _, off = _read_name(buf, off)
        off += 4
    for _ in range(an + ns + ar):
        name, off = _read_name(buf, off)
        if off + 10 > len(buf):
            break
        rtype, _rclass, _ttl, rdlen = struct.unpack(">HHIH", buf[off:off + 10])
        off += 10
        rdata = buf[off:off + rdlen]
        if rtype == 12:                      # PTR -> instance name
            inst, _ = _read_name(buf, off)
            out["names"].add(inst.split(".")[0])
            if "_tcp" in name or "_udp" in name:
                out["services"].add(name.replace(".local", ""))
        elif rtype == 1 and rdlen == 4:      # A
            out["ips"].add(".".join(str(b) for b in rdata))
        elif rtype == 16:                    # TXT
            i = 0
            while i < len(rdata):
                sl = rdata[i]
                chunk = rdata[i + 1:i + 1 + sl].decode("utf-8", "replace")
                if any(k in chunk.lower() for k in ("model", "md=", "am=", "product")):
                    out["txt"].add(chunk)
                i += 1 + sl
        elif rtype == 33:                    # SRV
            if "_tcp" in name or "_udp" in name:
                out["services"].add(name.replace(".local", ""))
        off += rdlen
    return out


# ------------------------------------------------------------- SSDP / ONVIF
def parse_ssdp(text: str) -> dict:
    d = {}
    for line in text.replace("\r\n", "\n").split("\n"):
        if ":" in line:
            k, v = line.split(":", 1)
            d[k.strip().upper()] = v.strip()
    return d


def parse_wsd(xml: bytes) -> dict:
    s = xml.decode("utf-8", "replace")
    xaddrs = re.findall(r"<[^>]*XAddrs>([^<]+)<", s)
    types = " ".join(re.findall(r"<[^>]*Types>([^<]+)<", s))
    addrs = " ".join(xaddrs)
    ips = set(re.findall(r"https?://(\d+\.\d+\.\d+\.\d+)", addrs))
    return {"xaddrs": addrs.strip(), "types": types.strip(), "ips": ips,
            "is_camera": "NetworkVideoTransmitter" in types or "onvif" in addrs.lower()}


# --------------------------------------------------------------- live probes
def _mcast_sock(timeout: float, ttl: int = 2) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)
    s.settimeout(timeout)
    return s


def _mdns_query_packet(service: str) -> bytes:
    pkt = struct.pack(">HHHHHH", 0, 0, 1, 0, 0, 0)
    for label in service.split("."):
        pkt += bytes([len(label)]) + label.encode()
    pkt += b"\x00" + struct.pack(">HH", 12, 1)   # QTYPE PTR, QCLASS IN
    return pkt


def discover(timeout: float = 2.5) -> dict:
    """Run all three protocols. Returns {ip: {names,services,server,model,onvif}}."""
    by_ip: dict[str, dict] = {}

    def slot(ip):
        return by_ip.setdefault(ip, {"names": set(), "services": set(), "server": "",
                                     "model": "", "onvif": False})

    # mDNS
    try:
        s = _mcast_sock(timeout)
        for svc in MDNS_SERVICES:
            try:
                s.sendto(_mdns_query_packet(svc), ("224.0.0.251", 5353))
            except OSError:
                pass
        end = time.time() + timeout
        while time.time() < end:
            try:
                data, addr = s.recvfrom(65535)
            except socket.timeout:
                break
            except OSError:
                break
            info = parse_mdns(data)
            d = slot(addr[0])
            d["names"] |= info["names"]
            d["services"] |= info["services"]
            if info["txt"]:
                d["model"] = d["model"] or next(iter(info["txt"]))
        s.close()
    except OSError:
        pass

    # SSDP / UPnP
    try:
        s = _mcast_sock(timeout)
        msearch = ("M-SEARCH * HTTP/1.1\r\nHOST:239.255.255.250:1900\r\n"
                   'MAN:"ssdp:discover"\r\nMX:1\r\nST:ssdp:all\r\n\r\n').encode()
        s.sendto(msearch, ("239.255.255.250", 1900))
        end = time.time() + timeout
        while time.time() < end:
            try:
                data, addr = s.recvfrom(65535)
            except (socket.timeout, OSError):
                break
            h = parse_ssdp(data.decode("utf-8", "replace"))
            d = slot(addr[0])
            d["server"] = d["server"] or h.get("SERVER", "")
        s.close()
    except OSError:
        pass

    # ONVIF WS-Discovery
    try:
        s = _mcast_sock(timeout)
        probe = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope" '
            'xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing" '
            'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery">'
            "<e:Header><w:MessageID>uuid:csk-probe</w:MessageID>"
            "<w:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>"
            "<w:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>"
            "</e:Header><e:Body><d:Probe/></e:Body></e:Envelope>").encode()
        s.sendto(probe, ("239.255.255.250", 3702))
        end = time.time() + timeout
        while time.time() < end:
            try:
                data, addr = s.recvfrom(65535)
            except (socket.timeout, OSError):
                break
            w = parse_wsd(data)
            for ip in (w["ips"] or {addr[0]}):
                d = slot(ip)
                if w["is_camera"]:
                    d["onvif"] = True
        s.close()
    except OSError:
        pass

    return by_ip


def camera_hint(info: dict) -> list[str]:
    """Reasons this discovery info looks like a camera."""
    reasons = []
    if info.get("onvif"):
        reasons.append("announces ONVIF video service (WS-Discovery)")
    svc = " ".join(info.get("services", []))
    if "_rtsp" in svc:
        reasons.append("advertises RTSP video service (mDNS)")
    if any(s in svc for s in ("_axis-video", "_dahua")):
        reasons.append("advertises a camera-vendor mDNS service")
    blob = " ".join([info.get("server", ""), info.get("model", "")] + list(info.get("names", [])))
    if CAMERA_NAME_RE.search(blob):
        reasons.append(f"camera-like name/model: {blob.strip()[:60]}")
    return reasons


def label(info: dict) -> str:
    """Short human label for a discovered device, if any."""
    name = next(iter(info.get("names", [])), "")
    model = info.get("model", "")
    server = info.get("server", "").split(" ")[0] if info.get("server") else ""
    return (name or model or server or "").strip()
