#!/bin/sh
# CSK launcher. Works from Steam (Non-Steam Game), Desktop Mode, or SSH.
#   ./run.sh               live with ESP32
#   ./run.sh --demo        no hardware
#   ./run.sh --headless    terminal only
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" || exit 1

PY=python3
[ -x "$DIR/.venv/bin/python" ] && PY="$DIR/.venv/bin/python"

# Prefer the stable symlink created by the udev rule.
if [ -e /dev/csk-esp32 ] && ! printf '%s\n' "$@" | grep -q -- '--port'; then
  set -- --port /dev/csk-esp32 "$@"
fi

# Only for non-Steam launchers that grab the pad exclusively (Steam Input makes this unnecessary).
if [ "$CSK_GPTOKEYB" = "1" ] && command -v gptokeyb >/dev/null 2>&1; then
  gptokeyb -1 "python3" -c "$DIR/csk.gptk" &
  GPTK=$!
fi

exec_log="$HOME/csk-logs/last-run.txt"
mkdir -p "$HOME/csk-logs"
"$PY" -m csk "$@" 2>"$exec_log"
RC=$?
[ -n "$GPTK" ] && kill "$GPTK" 2>/dev/null
exit $RC
