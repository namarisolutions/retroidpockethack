# THE UNKILLABLE DEMON KING

*it does not sleep. it does not blink. it watches.*

A counter-surveillance kit (CSK) with a crowned-demon-skull mascot that wakes
on boot and turns red-eyed and flaming when it finds a HIGH threat.

An ESP32 DevKit V1 plugged into a Retroid Pocket Flip 2 (running Linux) makes a
pocket sweeper for trackers, hidden cameras, rogue WiFi and anything that's
following you. Everything runs on the gamepad.

```
 ┌──────────────┐  USB (OTG)  ┌───────────────────────────────┐
 │ ESP32 V1     │ ──────────► │ Retroid Flip 2                │
 │ passive WiFi │  JSON lines │ csk app: detectors, alerts,   │
 │ + BLE sniff  │ ◄────────── │ RSSI hunt, follower tracking  │
 └──────────────┘  ch lock    └───────────────────────────────┘
```

**Passive by default.** The ESP32 doesn't transmit: no probe requests, no BLE
scan requests, no deauths, so nearby devices can't tell it's there. The one
exception is the **Karma test**, which you trigger by hand on a specific
network (see below).

## What it detects

| Threat | How |
|---|---|
| **AirTag / Find My** away from its owner | Apple offline-finding advert (type `0x12`, long form) |
| **Tile, Samsung SmartTag, Google Find My Device trackers** | service UUIDs `FEED/FEEC`, `FD5A`, `FEAA` (0x40/0x41) |
| Trackers in **unwanted-tracking mode** | DULT `FCB2`, Google FMDN `0x41` → HIGH |
| **Something following you** | Any device with a fixed (non-random) address that's seen at 3+ of your *checkpoints* (press **X** each time you move) |
| **Hidden / IP cameras** | MAC vendor (Hikvision, Dahua, Axis, Wyze, …), camera-style hotspot SSIDs, and **sustained upload traffic** (a streaming camera pushes ≥40 kB/s uplink) |
| **Flock / ALPR** (heuristic) | SSID / BLE name keywords |
| **WiFi Pineapple / Karma** | one BSSID beaconing many different SSIDs; confirmed with the on-demand Karma test |
| **Fake public hotspots** (fake `xfinitywifi`, `XFINITY`, `attwifi`, `CableWiFi`, …) | real carrier gateways broadcast several sibling networks from near-identical MACs with one beacon layout. A fake is usually alone, often on a made-up MAC, has a beacon fingerprint that differs from the real ones nearby, or uses the wrong security (e.g. an *open* `XFINITY`) |
| **Evil twin** | same SSID broadcast both open and encrypted |
| **Deauth attacks** (jammers, handshake capture, knocking cameras offline) | deauth/disassoc frame rate |

**What it can't do:** detect 5 GHz-only devices (the ESP32 is 2.4 GHz only),
wired or recording-only cameras (SD card, no radio), cell-site simulators
(Stingrays), or GPS trackers that report over cellular without Bluetooth.
Devices with randomized MACs can't be tracked across locations. That's by
design, and it also means they won't false-alarm. Treat alerts as leads to
check, not proof.

## 1. Flash the ESP32 (from a PC)

```sh
pip install platformio
cd firmware
pio run -t upload          # DOIT ESP32 DevKit V1 (default env)
pio device monitor         # you should see {"t":"hello",...} then JSON lines
```

If upload hangs at `Connecting....`, **hold the BOOT button** on the V1 until
it starts writing. The blue LED blinks every 1.5 s when the sensor is running.
Other boards: `-e esp32dev`, `-e esp32s3`, `-e esp32c3`, `-e esp32s2` (S2 has no BLE).

## 2. Hardware hookup

DevKit V1 (micro-USB) → **micro-USB to USB-C OTG cable** → Flip 2's USB-C port.
The Flip 2 powers the ESP32 (about 100 mA). It shows up as `/dev/ttyUSB0`
(CP2102 chip, needs the `cp210x` kernel module; `run.sh` tries to load it).

## 3. Install on the Retroid (SteamOS-based ARM image)

**The easy way - installable package (no PlatformIO on the device):**

Build the release tarball once on any machine with PlatformIO:

```sh
make package            # -> dist/csk-demon-king-<version>.tar.gz
```

Copy that one file to the Flip, then in **Desktop Mode**:

```sh
tar -xzf csk-demon-king-*.tar.gz && cd csk-demon-king-*
./install.sh            # venv + `csk` command + ESP32 udev rule + adds to Steam
./flash.sh              # flash the bundled prebuilt firmware over USB (no PlatformIO)
./run.sh --demo         # try it; or ./run.sh for live
```

The tarball carries the app, a pip-installable package (gives a `csk` command),
prebuilt ESP32 firmware (`firmware/prebuilt/devkitv1/`, merged + parts +
SHA256SUMS), the firmware source for rebuilds, the systemd monitor unit, and
the udev rule. `INSTALL.txt` inside repeats these steps.

**From a git checkout** (if you'd rather not build a tarball):

Copy the `host/` folder to `~/csk` (USB stick, SD card,
`scp`, or `git clone`), then in Konsole:

```sh
cd ~/csk
./install.sh                 # venv + pygame-ce/pyserial, udev rule (sudo), adds CSK to Steam
./run.sh --demo --window 1280x720    # try the UI with simulated data
.venv/bin/python tools/update_oui.py # optional: full IEEE vendor DB (needs internet)
```

`install.sh` leaves the read-only system untouched: Python packages go in
`~/csk/.venv`, and the only system change is a udev rule in `/etc` so your user
can open the ESP32 (`/dev/csk-esp32`). sudo needs a password; run `passwd`
first if you've never set one.

**Game Mode:** CSK appears in your library as a Non-Steam Game. If it isn't
there, add it via *Games → Add a Non-Steam Game*. In its controller settings,
use the **Gamepad** template (not keyboard/mouse). Steam Input handles the
pad, and Steam's *Exit game* quits CSK cleanly.

Other entry points: `./run.sh --headless` prints alerts to an SSH terminal
(type `c` + Enter to drop a checkpoint). Errors from the last launch are in
`~/csk-logs/last-run.txt`. Every live session is recorded to `~/csk-logs/`;
replay one with `.venv/bin/python -m csk --replay ~/csk-logs/session-….jsonl`.

## Controls

Letters are as printed on the Retroid (A on the right, B on the bottom). Steam
Input may already remap by position or by label, depending on its "Nintendo
layout" setting. If A and B feel backwards, flip **Menu → Face buttons**; it's
saved.

| Button | Action |
|---|---|
| D-pad / left stick | move selection (hold to scroll) |
| L1 / R1 (or D-pad ←/→) | switch tab: Alerts · **Map** · BLE · Networks · Clients · Follow |
| L2 / R2 | page up / down |
| **A** | open device → press A again to **hunt** it |
| **B** | back |
| **X** | **drop a checkpoint** ("I've moved somewhere new") |
| **Y** | mark device as **mine** (allowlist) / unmark |
| START | menu (sound, rumble, BLE/WiFi on-off, button test, …) |
| SELECT | mute |
| START + SELECT | quit |

On a device's detail page:

| Button | Action |
|---|---|
| **A** | hunt it |
| **Y** | mark as mine |
| **R1** | **watchlist** (mark hostile): HIGH alert whenever it's in range. WiFi networks are also matched by name + beacon fingerprint, so a rogue AP that changes its MAC is still caught |
| **L1** | **Karma test** (WiFi networks only): the ESP32 sends 3 probe requests for a made-up but believable network name (e.g. `Linksys897C-5G`) from a random MAC on that channel. A real AP ignores them; a Pineapple-style AP answers "that's me". It looks like an ordinary phone probing and takes about 2.5 s. It won't catch a fake that broadcasts a fixed name without answering probes; the other checks cover that case |

**Map** shows every WiFi access point with the client devices associated to it,
as a tree. Suspect/rogue APs float to the top in red; a client that's uploading
video-rate traffic is flagged **STREAMING** (a likely camera). It's the fastest
way to see "who is talking to whom" in a space.

**Join & scan an AP** (R2 on a network's detail page, or `./run.sh --scan-ap "SSID"`):
the Flip disconnects from your WiFi, joins the chosen AP, scans every device on
it for camera/DVR services (RTSP/ONVIF/Dahua/Xiongmai ports and camera-vendor
MACs), then **restores your previous network** - it always tries to restore,
even if the scan errors. Confirm screen warns first, because while joined you
are on that operator's network. Catches a camera living *behind* a rogue AP
(e.g. a fake `xfinitywifi`) that the passive RF sniff can't see into.

**Export signed report** (START menu) writes a timestamped report to
`~/csk-logs/reports/` as both `.txt` (readable) and `.json` (full data:
devices, alerts, fingerprints, signal history, Karma results). Each report
carries an **HMAC-SHA256 signature** over its canonical JSON, keyed by a
per-install secret in `~/.config/csk/report_key`, so you can later prove the
report wasn't altered after export. Set an operator ID by adding
`"operator": "your-id"` to `~/.config/csk/config.json`.

**Hunt mode** shows a large live signal reading, a warmer/colder trend, a
60-second graph and a Geiger-style click that speeds up as you get closer.
For WiFi targets it locks the ESP32 to that channel for faster updates.
New HIGH alerts sound an alarm and rumble.

### Typical sweep

1. Start at home or somewhere quiet: **Menu → Mark everything visible as mine**
   so your own phone, watch and earbuds stop showing up.
2. Go about your day and press **X** each time you've moved (another room,
   car, store, …).
3. Anything still showing up after 3 locations lands in **FOLLOW** and raises
   an alert. Open it and **hunt** it down.
4. For a room sweep (Airbnb, hotel), check **CLIENTS** for camera vendors or
   close devices with high upload, and **NETWORKS** for camera hotspots.

### If buttons don't respond / map wrong

1. **Menu → Button test** lists every raw event and how it was mapped.
2. If the pad shows as `(raw)`, SDL doesn't recognise it. Override the index
   map in `~/.config/csk/config.json`:
   ```json
   { "raw_buttons": { "0": "SOUTH", "1": "EAST", "2": "NORTH", "3": "WEST",
                      "4": "L1", "5": "R1", "6": "L2", "7": "R2",
                      "8": "SELECT", "9": "START", "13": "UP", "14": "DOWN",
                      "15": "LEFT", "16": "RIGHT" } }
   ```
3. In Game Mode, check the controller layout for CSK in Steam is **Gamepad**.
   A keyboard/mouse template sends key presses that CSK only partly understands.
4. Non-Steam launchers that grab the pad exclusively: `CSK_GPTOKEYB=1 ./run.sh`
   (uses `csk.gptk`).

## Persistent memory: "have I seen this before?"

The kit remembers devices **across restarts, days, and places** - the strongest
real signal that you're being followed. In the menu, set your **Location**
(home / work / out, editable in `~/.config/csk/config.json`). As you move and
change the location, every stable device seen is logged to
`~/.config/csk/baseline.db`.

The **HISTORY** tab then shows anything that keeps reappearing, ranked worst
first, and it persists: close the app, come back next week, and a phone or car
that has shadowed you across two places on separate days is still there, in red.
A HIGH **"Seen repeatedly"** alert fires when a device turns up at 2+ of your
locations on 2+ days. Randomized-MAC devices are never logged (they can't be
tracked reliably and would just create noise); mark anything of your own as
*mine* (Y) to drop it from history for good.

## 24/7 network defense monitor

Leave the Flip on your home network and have it watch continuously, with no
screen. It keeps a **persistent baseline** of every device that belongs there,
and alerts only when something *new* or camera-shaped appears.

```sh
./run.sh --scan                 # one-shot: list everything on your current network, flag cameras/DVRs
./run.sh --monitor              # 24/7 watch (ESP32 RF + LAN scan + alerts)
./run.sh --monitor --no-rf      # LAN only, no ESP32 plugged in
```

- **Learning window** (first 10 min by default): everything already on the
  network is trusted silently, so you only hear about later arrivals. Mark
  more devices trusted any time; trust is remembered in `~/.config/csk/baseline.db`.
- **LAN scan** is a rootless TCP sweep of your /24 that also probes for camera
  and DVR service ports (RTSP 554/8554, Dahua 37777, Xiongmai 34567, ONVIF/Hik
  8000/88). A device answering on those, or whose MAC is a camera vendor, is
  flagged. Run it only on networks you own.
- **Alerts** go to `~/csk-logs/monitor.log` and, if configured, a webhook.
  Add to `~/.config/csk/config.json`:
  ```json
  { "operator": "home",
    "notify": { "webhook": "https://ntfy.sh/your-secret-topic",
                "webhook_format": "ntfy", "min_level": 1 },
    "monitor": { "scan_interval": 120, "learn_minutes": 10, "report_minutes": 60 } }
  ```
  `webhook_format` is `ntfy`, `slack`, `discord`, or `json`. With ntfy you get
  push notifications on your phone for free. Signed reports are still written to
  `~/csk-logs/reports/` on a schedule.

**Run it as a background service** (survives logout, auto-restarts) with the
included user systemd unit — see the header of
[`systemd/csk-monitor.service`](host/systemd/csk-monitor.service).

## Layout

```
firmware/            PlatformIO project (ESP32 sniffer, JSON-lines protocol)
host/csk/            Python app
  bleparse.py        BLE advert decoding + tracker signatures
  oui.py             MAC vendor → camera/IoT category
  world.py           device registry + all detectors
  controls.py        gamepad/keyboard → actions (SDL controller, raw joystick, gptokeyb)
  ui.py              pygame handheld UI
  link.py / demo.py  serial, replay and simulated sources
  netscan.py         rootless LAN discovery + camera-port probing (your network)
  store.py           persistent device + sighting history (SQLite): cross-day following
  wifi.py / apscan.py  join a chosen AP and scan what's behind it (nmcli), then restore
  monitor.py         headless RF+LAN defense loop
  notify.py          alert sinks (local log + ntfy/Slack/Discord/JSON webhook)
host/tests/          pytest suite (python3 -m pytest host/tests)
```

Serial protocol (ESP32 → host, one JSON object per line): `hello`, `stat`,
`ap`, `sta`, `probe`, `deauth`, `ble`. Host → ESP32: `ch <n>` (0 = hop),
`ble 0|1`, `wifi 0|1`, `ping`. See the header of `firmware/src/main.cpp`.
