#!/usr/bin/env python3
"""Download the IEEE MA-L (OUI) registry into csk/data/oui_full.csv.

Run once on any machine with internet, then copy the host/ folder to the
handheld (or run it on the handheld if it has WiFi).
"""
import csv
import io
import os
import sys
import urllib.request

URL = "https://standards-oui.ieee.org/oui/oui.csv"
OUT = os.path.join(os.path.dirname(__file__), "..", "csk", "data", "oui_full.csv")


def main() -> int:
    print(f"fetching {URL} ...")
    req = urllib.request.Request(URL, headers={"User-Agent": "csk-oui-updater"})
    raw = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
    rows = []
    for r in csv.DictReader(io.StringIO(raw)):
        prefix = (r.get("Assignment") or "").strip().upper()
        name = (r.get("Organization Name") or "").strip()
        if len(prefix) == 6 and name:
            rows.append((prefix, name))
    if len(rows) < 1000:
        print("download looks wrong, not overwriting", file=sys.stderr)
        return 1
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(sorted(rows))
    print(f"wrote {len(rows)} vendors -> {os.path.normpath(OUT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
