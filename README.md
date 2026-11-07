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
| **Imposter of YOUR network** | an AP broadcasting a network name you've marked as yours, from a radio that isn't your router (targeted evil-twin / auto-join phishing) |
| **BLE spam attacks** (Flipper Zero / "AppleJuice" pairing-popup floods) | many random MACs spamming Apple/FastPair/SwiftPair/Samsung pairing prompts in a short window |
| **Drone (Remote ID)** | ASTM/FAA drone broadcast - reveals drone + operator location (see below) |

**What it can't do:** detect 5 GHz-only devices (the ESP32 is 2.4 GHz only),
wired or recording-only cameras (SD card, no radio), cell-site simulators
(Stingrays), or GPS trackers that report over cellular without Bluetooth.
Devices with randomized MACs can't be tracked across locations. That's by
design, and it also means they won't false-alarm. Treat alerts as leads to
check, not proof.

## Verify everything works (self-test)

Before trusting a live sweep, run the built-in diagnostic:

```sh
./run.sh --selftest        # or: csk --selftest
```

It checks that every module imports, the config/log dirs are writable, and the
full detector pipeline fires on the simulated sensor (trackers, cameras, rogue
AP, fake hotspot, drone Remote ID). If an ESP32 is plugged in it also confirms
the firmware is talking (`hello` + chip/MAC). Exit code is non-zero on failure,
so it's CI-friendly too. A clean run looks like:

```
[ OK ] core modules import        12 modules
[ OK ] detector: trackers         fired
[ OK ] detector: cameras          fired
[ OK ] detector: drone Remote ID  fired
[ OK ] ESP32 firmware             /dev/ttyUSB0: csk-0.1.0 / ESP32 mac ...
ALL CHECKS PASSED
```

## 1. Flash the ESP32 (from a PC)
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
| L1 / R1 (or D-pad ←/→) | switch tab: Alerts · **Radar** · Map · BLE · Networks · Clients · Follow · History · **Souls** |
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

## Drone Remote ID (who's flying overhead - and where the pilot is)

FAA / ASTM F3411 Remote ID drones broadcast their serial, live position and
**the operator's location** in the clear. Most do it over Bluetooth LE, which
the firmware already forwards, so `droneid.py` decodes it **host-side with no
reflash**. A drone in range raises a HIGH alert and its detail page shows:

- drone serial + aircraft type
- drone position, altitude, speed
- **OPERATOR (pilot) GPS location** and operator ID

WiFi-broadcast Remote ID is decoded by the same module (ingest a `odid`
message); forwarding it from the ESP32 is a future firmware add (the beacon
event buffer is too small for a full message pack today).

## Toys

- **RADAR tab:** a sweeping radar plots every device in range as a blip -
  distance from centre = signal strength, colour = category (trackers, cameras,
  drones stand out). The selected contact shows its true name (see Souls).
- **SOULS / bestiary:** the King names every device it detects - a deterministic
  monster name from the MAC (e.g. an AirTag becomes the "Writhing Revenant",
  ranked EYE-TYRANT / PARASITE / SKY-DEMON by category). Pure flavour.
- **Guard my network:** mark your own router as *mine* (Y) and the kit then
  screams if any AP broadcasts that network's name from a different radio -
  someone cloning your WiFi to phish you or make your phone auto-join. Auto-set
  when you mark an AP yours; stored in `guard.json`.
- **Auto-location (by WiFi):** instead of pressing X at every new place, the kit
  fingerprints each place by the set of strong APs around you and switches the
  location label automatically when it recognises one - so cross-day following
  works hands-free. Toggle in the menu; learns a place when you name it.
- **Channel spectrum** (Menu > Channel spectrum): a live 2.4 GHz analyzer -
  bar per channel showing AP/traffic activity, 1/6/11 marked, the sensor's
  current scan channel flagged, and a "cleanest channel" suggestion.
- **Tripwire** (L2 on any device): arm a proximity alarm - a HIGH alert fires
  when that device comes within signal range. Works on your own gear too (to
  find lost keys/tags) or on a tail you want to know has returned.
- **Dungeon music** (Menu): a slow procedural chiptune dungeon ambience loop.
- **King's rank:** the Souls tab shows your rank (Restless Spirit -> ... ->
  THE UNKILLABLE KING) and a bar to the next, earned by claiming device souls.
- **Themes** (Menu > Theme): **Dungeon** (default - serif typeface, torch-lit
  stone-brick walls, parchment tablets), Hellfire, green Phosphor CRT, Amber
  CRT, and Ice. Threat colours stay meaningful in every theme.

## The Demon King speaks
## The Demon King speaks
## The Demon King speaks (personality + advisor)

- **Voicelines + chiptune:** on each HIGH threat the Demon King barks a short
  video-game-boss line ("SOMETHING WATCHES. SO DO I.") with a synthesized
  NES-style chiptune sting - no audio files, all generated on the fly. The
  pixel skull mascot idles, blinks, and shakes with rage on the header and
  boot screen.
- **Consult the Demon King** (menu): an on-device advisor reads the current
  threat state and gives a short situation report plus ranked actions, in
  character. Backend is **auto**: if a tiny local LLM is running it uses it
  (footer reads `advisor: local LLM`), otherwise a transparent **rules engine**
  (`advisor: rules`) - which always works offline, no model weights. The consult
  runs off the UI thread, so a slow model never freezes the app ("the king
  gathers his thoughts...").

### Give it a real brain (a very small local LLM)

Run a ~0.5B quantised model right on the handheld via llama.cpp:

```sh
./llm/setup-llm.sh      # installs llama-cpp-python[server], downloads
                        # Qwen2.5-0.5B-Instruct Q4 (~0.4 GB), writes the config
./llm/setup-llm.sh --start      # serve it at 127.0.0.1:8080 (OpenAI /v1)
# or enable the background service: systemd/csk-llm.service
```

The advisor auto-detects a server on :8080 (llama.cpp), :8000
(llama-cpp-python) or :11434 (ollama) - all OpenAI-compatible. Pick a different
model with `./llm/setup-llm.sh --model <hf-repo> <file.gguf>`. On a handheld CPU
a 0.5B model answers in a few seconds; that's why the consult is async. Force a
backend in `~/.config/csk/config.json`:
```json
{ "agent": { "backend": "auto",
             "url": "http://127.0.0.1:8080/v1/chat/completions",
             "model": "qwen2.5-0.5b-instruct-q4_k_m.gguf" } }
```
(`backend`: `auto` | `llm` | `rules`.)

## Service discovery (naming cameras on a network)

The LAN scan (`--scan`, `--scan-ap`, 24/7 monitor, and in-app Join & scan) now
also runs **mDNS, SSDP/UPnP and ONVIF WS-Discovery**. This names devices
("Reolink RLC-810", "FrontDoor") and flags cameras that *announce* themselves
via ONVIF or an RTSP mDNS service, catching ones a bare port scan would miss.
All read-only, your-network-only, no dependencies.

## Persistent memory:
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

## Operational security: panic wipe

**Menu > PANIC WIPE** (hold A for 2 s) securely erases everything the kit has
recorded about where you've been - session logs, reports, the baseline /
following database, and your allow & watch lists - for when the device might be
taken. Your settings survive. The logs are a liability precisely because they
map your movements, so this is one button away.

## 24/7 network defense monitor
## 24/7 network defense monitor

Leave the Flip on your home network and have it watch continuously, with no
screen. It keeps a **persistent baseline** of every device that belongs there,
and alerts only when something *new* or camera-shaped appears.

```sh
./run.sh --scan                 # one-shot: list everything on your current network, flag cameras/DVRs
./run.sh --monitor              # 24/7 watch (ESP32 RF + LAN scan + alerts)
./run.sh --monitor --no-rf      # LAN only, no ESP32 plugged in
./run.sh --monitor --web 8099   # also serve a read-only web dashboard
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
  bleparse.py        BLE advert decoding + tracker/drone signatures
  droneid.py         Open Drone ID / FAA Remote ID decoder (drone + operator location)
  oui.py             MAC vendor → camera/IoT category
  world.py           device registry + all detectors
  controls.py        gamepad/keyboard → actions (SDL controller, raw joystick, gptokeyb)
  ui.py              pygame handheld UI
  link.py / demo.py  serial, replay and simulated sources
  netscan.py         rootless LAN discovery + camera-port probing (your network)
  store.py           persistent device + sighting history (SQLite): cross-day following
  wifi.py / apscan.py  join a chosen AP and scan what's behind it (nmcli), then restore
  monitor.py         headless RF+LAN defense loop
  dashboard.py       read-only web dashboard for the monitor (view from your phone)
  selftest.py        bring-up diagnostic (csk --selftest)
  notify.py          alert sinks (local log + ntfy/Slack/Discord/JSON webhook)
  discover.py        mDNS / SSDP / ONVIF service discovery (names + camera detection)
  agent.py           on-device advisor (auto: local LLM if running, else rules)
  llm/setup-llm.sh   install + run a tiny local LLM (llama.cpp) on the device
  art.py             pixel-art Demon King mascot + idle/blink/rage animations
  bestiary.py        turns each device into a named monster "soul" (radar + souls tab)
host/tests/          pytest suite (python3 -m pytest host/tests)
```

Serial protocol (ESP32 → host, one JSON object per line): `hello`, `stat`,
`ap`, `sta`, `probe`, `deauth`, `ble`. Host → ESP32: `ch <n>` (0 = hop),
`ble 0|1`, `wifi 0|1`, `ping`. See the header of `firmware/src/main.cpp`.
