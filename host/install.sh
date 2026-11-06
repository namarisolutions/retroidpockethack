#!/bin/bash
# CSK installer for SteamOS-style (Arch-based, read-only root) ARM handhelds.
# Run from Desktop Mode in Konsole:   cd ~/csk && ./install.sh
#
# Does NOT unlock the read-only root filesystem. It:
#   1. creates a Python venv in this folder and installs pyserial + pygame-ce
#   2. installs a udev rule in /etc (writable on SteamOS) so the ESP32 is usable without root  [sudo]
#   3. creates a desktop entry and adds CSK to Steam as a Non-Steam Game
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "== 1/3 Python environment"
if ! python3 -c 'import venv' 2>/dev/null; then
  echo "python3 venv module missing - this image can't create virtualenvs" >&2; exit 1
fi
[ -d .venv ] || python3 -m venv .venv
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r requirements.txt
.venv/bin/python -c 'import pygame, serial; print("   pygame", pygame.version.ver, "/ pyserial", serial.__version__)'

echo "== 2/3 USB serial permissions (needs your sudo password; set one with 'passwd' if you never have)"
if [ -f /etc/udev/rules.d/99-csk-esp32.rules ] && cmp -s udev/99-csk-esp32.rules /etc/udev/rules.d/99-csk-esp32.rules; then
  echo "   udev rule already installed"
elif sudo install -m 644 udev/99-csk-esp32.rules /etc/udev/rules.d/99-csk-esp32.rules; then
  sudo udevadm control --reload-rules && sudo udevadm trigger --subsystem-match=tty || true
  getent group uucp >/dev/null && sudo usermod -aG uucp "$USER" || true
  echo "   installed. Unplug/replug the ESP32."
else
  echo "   skipped (no sudo). CSK will only work if your user can already open /dev/ttyUSB*."
fi

echo "== 3/3 Launcher"
APPS="$HOME/.local/share/applications"
mkdir -p "$APPS"
cat > "$APPS/csk.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=CSK Counter-Surveillance
Comment=ESP32 tracker / camera / rogue WiFi sweeper
Exec=$DIR/run.sh
Path=$DIR
Terminal=false
Categories=Utility;
EOF
chmod +x "$APPS/csk.desktop" "$DIR/run.sh"
if command -v steamos-add-to-steam >/dev/null 2>&1; then
  steamos-add-to-steam "$APPS/csk.desktop" && echo "   added to Steam (restart Steam / switch to Game Mode to see it)"
else
  echo "   In Steam: Games > Add a Non-Steam Game > 'CSK Counter-Surveillance'"
fi

echo
echo "Done. Try it now:  ./run.sh --demo --window 1280x720"
echo "Optional (needs internet): .venv/bin/python tools/update_oui.py"
