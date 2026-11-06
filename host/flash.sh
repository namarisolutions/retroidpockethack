#!/usr/bin/env bash
# Flash the prebuilt Demon King firmware to an ESP32 over USB - no PlatformIO needed.
#
#   ./flash.sh                 auto-detect port, flash the DevKit V1 build
#   ./flash.sh /dev/ttyUSB0    specify the port
#   ./flash.sh --erase         full chip erase first (use if it misbehaves)
#
# Needs esptool. If it's missing this installs it into a local venv.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
FW="$DIR/firmware/prebuilt/devkitv1"
[ -f "$FW/firmware.bin" ] || { echo "No prebuilt firmware at $FW. Build with: cd firmware && pio run" >&2; exit 1; }

ERASE=0
PORT=""
for a in "$@"; do
  case "$a" in
    --erase) ERASE=1 ;;
    /dev/*)  PORT="$a" ;;
  esac
done

# esptool: prefer system, else a throwaway venv next to this script.
if command -v esptool.py >/dev/null 2>&1; then
  ESPTOOL="esptool.py"
elif python3 -c "import esptool" 2>/dev/null; then
  ESPTOOL="python3 -m esptool"
else
  echo "esptool not found - installing into $DIR/.flashvenv ..."
  python3 -m venv "$DIR/.flashvenv"
  "$DIR/.flashvenv/bin/pip" -q install esptool
  ESPTOOL="$DIR/.flashvenv/bin/python -m esptool"
fi

if [ -z "$PORT" ]; then
  PORT="$(ls /dev/ttyUSB* /dev/ttyACM* 2>/dev/null | head -n1 || true)"
  [ -n "$PORT" ] || { echo "No ESP32 serial port found. Plug it in (USB-C OTG) or pass /dev/ttyUSBx." >&2; exit 1; }
fi
echo "Flashing $FW -> $PORT"

if [ "$ERASE" = 1 ]; then
  $ESPTOOL --chip esp32 --port "$PORT" erase_flash
fi

# If a merged image exists, one write at 0x0 is simplest; else write the parts.
if [ -f "$FW/firmware-merged.bin" ]; then
  $ESPTOOL --chip esp32 --port "$PORT" --baud 460800 write_flash -z 0x0 "$FW/firmware-merged.bin"
else
  $ESPTOOL --chip esp32 --port "$PORT" --baud 460800 write_flash -z \
    0x1000  "$FW/bootloader.bin" \
    0x8000  "$FW/partitions.bin" \
    0xe000  "$FW/boot_app0.bin" \
    0x10000 "$FW/firmware.bin"
fi
echo "Done. If it hung at 'Connecting...', hold the BOOT button on the board and retry."
