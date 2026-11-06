"""Active LAN discovery for the device the Flip is connected to.

Rootless and dependency-free: a TCP connect-sweep across the local /24 both
discovers hosts and probes for camera/DVR service ports. The sweep populates
the kernel ARP table, which we then read for IP->MAC (so we get vendors, and
can tell two IPs are the same physical box). Intended for *your own* network.

Camera/DVR service ports probed:
  554  RTSP            8554 RTSP-alt      88/8000 Hik/ONVIF
  37777 Dahua          34567 Xiongmai     8899 generic DVR
  80/443/8080 web UI (weak signal on its own)
"""
from __future__ import annotations

import concurrent.futures
import socket
import struct
import time

CAMERA_PORTS = {554: "RTSP", 8554: "RTSP", 37777: "Dahua", 34567: "Xiongmai-DVR",
                8000: "ONVIF/Hik", 8899: "DVR", 88: "ONVIF"}
WEB_PORTS = {80: "http", 443: "https", 8080: "http-alt"}
PROBE_PORTS = sorted(set(CAMERA_PORTS) | set(WEB_PORTS))


def local_ipv4() -> str | None:
    """Best-effort local address, without sending anything."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))  # TEST-NET-1, routes but no packet for UDP connect
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


def subnet_hosts(ip: str) -> list[str]:
    """All /24 host addresses for the given IP (simple, covers the common case)."""
    a, b, c, _ = ip.split(".")
    return [f"{a}.{b}.{c}.{h}" for h in range(1, 255)]


def read_arp() -> dict[str, str]:
    """IP -> MAC from the kernel ARP cache (Linux /proc/net/arp)."""
    out: dict[str, str] = {}
    try:
        with open("/proc/net/arp") as f:
            next(f, None)
            for line in f:
                p = line.split()
                if len(p) >= 4 and p[3] != "00:00:00:00:00:00":
                    out[p[0]] = p[3].lower()
    except OSError:
        pass
    return out


def _probe_host(ip: str, ports: list[int], timeout: float) -> list[int]:
    open_ports = []
    for port in ports:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                if s.connect_ex((ip, port)) == 0:
                    open_ports.append(port)
        except OSError:
            pass
    return open_ports


def scan(ip: str | None = None, ports: list[int] | None = None, timeout: float = 0.4,
         workers: int = 128, discover_services: bool = True) -> dict:
    """Scan the local /24. Returns {ts, cidr, hosts:[{ip,mac,ports}...]}.

    This is the default backend; the monitor accepts any callable with the same
    return shape, so it can be mocked in tests or swapped for an nmap wrapper.
    """
    ip = ip or local_ipv4()
    if not ip:
        return {"ts": time.time(), "cidr": None, "hosts": [], "error": "no local IPv4"}
    ports = ports or PROBE_PORTS
    targets = subnet_hosts(ip)
    port_map: dict[str, list[int]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_probe_host, t, ports, timeout): t for t in targets}
        for fut in concurrent.futures.as_completed(futs):
            op = fut.result()
            if op:
                port_map[futs[fut]] = op
    arp = read_arp()
    disco = {}
    if discover_services:
        try:
            from . import discover as _disc
            disco = _disc.discover()
        except Exception:
            disco = {}
    hosts = []
    for host_ip in sorted(set(port_map) | set(arp) | set(disco),
                          key=lambda s: tuple(int(x) for x in s.split("."))):
        h = {"ip": host_ip, "mac": arp.get(host_ip, ""), "ports": sorted(port_map.get(host_ip, []))}
        if host_ip in disco:
            h["disco"] = disco[host_ip]
            from . import discover as _disc
            lbl = _disc.label(disco[host_ip])
            if lbl:
                h["name"] = lbl
        hosts.append(h)
    return {"ts": time.time(), "cidr": ".".join(ip.split(".")[:3]) + ".0/24", "self_ip": ip, "hosts": hosts}


def classify_host(host: dict, vendor: str, category: str) -> tuple[str, list[str]]:
    """Return (severity_category, reasons). category: 'camera' | 'suspect' | ''."""
    reasons = []
    cam_ports = [CAMERA_PORTS[p] for p in host["ports"] if p in CAMERA_PORTS]
    if category == "camera":
        reasons.append(f"MAC vendor is a camera maker ({vendor})")
    if cam_ports:
        reasons.append("camera/DVR service ports open: " + ", ".join(sorted(set(cam_ports))))
    if 554 in host["ports"] or 8554 in host["ports"]:
        reasons.append("RTSP video stream endpoint present")
    disco = host.get("disco") or {}
    disco_cam = False
    if disco:
        from . import discover
        hints = discover.camera_hint(disco)
        reasons += hints
        disco_cam = disco.get("onvif") or any("RTSP" in h or "ONVIF" in h for h in hints)
    if reasons:
        strong = (category == "camera" or disco_cam
                  or any(p in host["ports"] for p in (554, 8554, 37777, 34567)))
        return ("camera" if strong else "suspect"), reasons
    return "", reasons
