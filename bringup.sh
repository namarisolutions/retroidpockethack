#!/usr/bin/env bash
# THE UNKILLABLE DEMON KING - one-shot bring-up for people who don't do Linux.
#
# It walks you through: installing the app, flashing the ESP32, and checking
# that they talk. It pauses and tells you what to do at each step. Safe to
# run again if something goes wrong.
#
# Run it with ONE line in the Konsole terminal (Desktop Mode):
#   bash <(curl -sL https://raw.githubusercontent.com/namarisolutions/retroidpockethack/demon-king/bringup.sh)
set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"
# If run via curl (no repo around us), clone it first.
if [ ! -f "$ROOT/host/run.sh" ]; then
  ROOT="$HOME/csk-src"
  echo "==> Downloading the kit to $ROOT ..."
  if [ -d "$ROOT/.git" ]; then git -C "$ROOT" pull --ff-only; else git clone -b demon-king https://github.com/namarisolutions/retroidpockethack "$ROOT"; fi || {
    echo "!! Couldn't download. Is the Flip on WiFi? Is 'git' installed?"; exit 1; }
fi
cd "$ROOT"

say() { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }
pause() { printf '\n\033[1;36m%s\033[0m' "$*"; read -r _; }

cat <<'BANNER'

   ####   THE UNKILLABLE DEMON KING - setup   ####

 This sets up the kit in 3 steps. It will tell you exactly
 what to do. You can close and re-run it any time.

BANNER
pause "Press ENTER to begin..."

# ---------------------------------------------------------------- 1. the app
say "STEP 1 / 3 - installing the app (a few minutes the first time)"
if [ ! -x host/install.sh ]; then chmod +x host/install.sh host/run.sh 2>/dev/null; fi
if ! bash host/install.sh; then
  echo "!! Install hit a problem. If it asked for a password and you don't have one,"
  echo "   run 'passwd' to set one, then run this again."
fi

say "Quick look - here's the app on fake data (no hardware needed)."
echo "   A window will open. Look around, then press  SELECT + START  to quit it."
pause "Press ENTER to open the demo (or Ctrl+C to skip)..."
( cd host && ./run.sh --demo --window 1280x720 ) || true

# --------------------------------------------------------------- 2. firmware
say "STEP 2 / 3 - loading the brain onto the ESP32"
echo "   1) Plug the ESP32 into the Flip with the USB cable + OTG adapter."
echo "   2) It's the small board, NOT the handheld."
pause "Plug it in now, then press ENTER..."

if ! command -v pio >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/pio" ]; then
  say "Installing the flashing tool (PlatformIO) - this part is slow, be patient."
  python3 -m pip install --user -q platformio || pip install --user -q platformio
fi
PIO="$(command -v pio || echo "$HOME/.local/bin/pio")"

say "Flashing... if it gets stuck on 'Connecting....', HOLD the little BOOT"
echo "   button on the ESP32 until it starts writing, then let go."
if ( cd firmware && "$PIO" run -t upload ); then
  echo "   Firmware loaded. The blue light should blink about once a second."
else
  echo "!! Flashing failed. Most common fixes:"
  echo "   - Hold the BOOT button on the board and run this again."
  echo "   - Try a different USB cable (some are charge-only)."
fi

# ----------------------------------------------------------------- 3. verify
say "STEP 3 / 3 - checking the Flip and the sensor talk to each other"
( cd host && ./run.sh --selftest ) || true

cat <<'DONE'

   ####   Setup done.   ####

 To use it from now on, just launch "CSK Counter-Surveillance"
 from your Steam library (Game Mode) - like any game.

 Or from here, run:   cd ~/csk-src/host && ./run.sh

 If a check above said FAIL, copy the lines and send them to Claude.
DONE
