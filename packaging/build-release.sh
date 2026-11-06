#!/usr/bin/env bash
# Build a self-contained CSK / Demon King release tarball.
#
#   packaging/build-release.sh [--no-firmware]
#
# Produces  dist/csk-demon-king-<version>.tar.gz  containing:
#   - the host Python app + pyproject (pip-installable, gives a `csk` command)
#   - install.sh (venv + udev + Non-Steam Game), run.sh, flash.sh
#   - prebuilt ESP32 firmware (merged + parts) so no PlatformIO is needed to flash
#   - firmware source for rebuilds, systemd unit, docs
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' host/csk/__init__.py)"
NAME="csk-demon-king-$VERSION"
STAGE="$(mktemp -d)/$NAME"
BUILD_FW=1
[ "$1" = "--no-firmware" ] && BUILD_FW=0

echo "== packaging $NAME"
mkdir -p "$STAGE"

# ---- host app (exclude venvs, caches, logs) --------------------------------
echo "   staging host app"
tar -C host -cf - \
  --exclude='.venv' --exclude='.flashvenv' --exclude='__pycache__' \
  --exclude='*.pyc' --exclude='.pytest_cache' --exclude='csk/data/oui_full.csv' \
  . | tar -C "$STAGE" -xf -

# ---- firmware source -------------------------------------------------------
echo "   staging firmware source"
mkdir -p "$STAGE/firmware"
tar -C firmware -cf - --exclude='.pio' platformio.ini src | tar -C "$STAGE/firmware" -xf -

# ---- prebuilt firmware -----------------------------------------------------
build_env() {
  local env="$1" chip="$2"
  local out="$STAGE/firmware/prebuilt/$env"
  mkdir -p "$out"
  if [ "$BUILD_FW" = 1 ]; then
    echo "   building firmware ($env)"
    ( cd firmware && pio run -e "$env" >/dev/null )
  fi
  local b="firmware/.pio/build/$env"
  if [ ! -f "$b/firmware.bin" ]; then
    echo "   !! no build for $env - skipping prebuilt (flash from source instead)"
    rmdir "$out" 2>/dev/null || true
    return
  fi
  local boot_app0
  boot_app0="$(ls "$HOME"/.platformio/packages/framework-arduinoespressif32/tools/partitions/boot_app0.bin 2>/dev/null | head -n1)"
  cp "$b/firmware.bin" "$b/bootloader.bin" "$b/partitions.bin" "$out/"
  [ -n "$boot_app0" ] && cp "$boot_app0" "$out/boot_app0.bin"
  # merged single-file image for the easiest flash
  local esptool
  esptool="$(ls "$HOME"/.platformio/packages/tool-esptoolpy/esptool.py 2>/dev/null | head -n1)"
  if [ -n "$esptool" ] && [ -f "$out/boot_app0.bin" ]; then
    python3 "$esptool" --chip "$chip" merge_bin -o "$out/firmware-merged.bin" \
      --flash_mode dio --flash_freq 40m --flash_size 4MB \
      0x1000 "$out/bootloader.bin" 0x8000 "$out/partitions.bin" \
      0xe000 "$out/boot_app0.bin" 0x10000 "$out/firmware.bin" >/dev/null
  fi
  ( cd "$out" && sha256sum ./*.bin > SHA256SUMS )
  echo "      prebuilt -> firmware/prebuilt/$env ($(du -sh "$out" | cut -f1))"
}
build_env devkitv1 esp32

# ---- top-level convenience + version --------------------------------------
echo "$VERSION" > "$STAGE/VERSION"
cat > "$STAGE/INSTALL.txt" <<EOF
THE UNKILLABLE DEMON KING - install

On the handheld (Desktop Mode terminal):
  1. ./install.sh          set up the app (venv + ESP32 permissions + add to Steam)
  2. ./flash.sh            flash the ESP32 over USB  (prebuilt, no PlatformIO needed)
  3. ./run.sh --demo       try it with no hardware, or ./run.sh for live

24/7 monitor service:    see systemd/csk-monitor.service
Full docs:               README.md
EOF

# ---- tar -------------------------------------------------------------------
mkdir -p "$ROOT/dist"
TARBALL="$ROOT/dist/$NAME.tar.gz"
tar -C "$(dirname "$STAGE")" -czf "$TARBALL" "$NAME"
rm -rf "$(dirname "$STAGE")"
echo "== done: dist/$NAME.tar.gz ($(du -sh "$TARBALL" | cut -f1))"
