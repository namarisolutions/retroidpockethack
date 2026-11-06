#!/usr/bin/env bash
# Set up a *very small* local LLM for the Demon King advisor, on the handheld.
#
#   ./llm/setup-llm.sh            install server + default tiny model, start it
#   ./llm/setup-llm.sh --model <hf-repo> <file.gguf>
#   ./llm/setup-llm.sh --start    just start the server with the installed model
#
# Runs llama.cpp's OpenAI-compatible server (via llama-cpp-python) on CPU at
# 127.0.0.1:8080. The agent auto-detects it. Default model is Qwen2.5-0.5B
# Instruct (Q4_K_M, ~0.4 GB) - small enough for an ARM handheld, a few tok/s.
#
# Needs: internet (once, to download), ~1 GB free, and a C toolchain if no
# prebuilt wheel exists for your arch (pip will build llama-cpp-python).
set -e
DIR="$(cd "$(dirname "$0")/.." && pwd)"          # host/
cd "$DIR"
MODELS="$DIR/llm/models"
PORT=8080
CTX=2048
# A small, capable instruct model. Override with --model.
HF_REPO="Qwen/Qwen2.5-0.5B-Instruct-GGUF"
HF_FILE="qwen2.5-0.5b-instruct-q4_k_m.gguf"

mkdir -p "$MODELS"
PY="python3"
[ -x "$DIR/.venv/bin/python" ] && PY="$DIR/.venv/bin/python"

START_ONLY=0
case "$1" in
  --start) START_ONLY=1 ;;
  --model) HF_REPO="$2"; HF_FILE="$3" ;;
esac
MODEL="$MODELS/$HF_FILE"

install_server() {
  echo "== installing llama-cpp-python[server] (builds from source if no wheel; may take a few min)"
  if ! "$PY" -c 'import llama_cpp.server' 2>/dev/null; then
    "$PY" -m pip install -q --upgrade pip
    # OpenBLAS speeds CPU inference if available; harmless if the flag is ignored.
    CMAKE_ARGS="-DGGML_NATIVE=ON" "$PY" -m pip install -q "llama-cpp-python[server]" \
      || "$PY" -m pip install -q "llama-cpp-python[server]"
  fi
  "$PY" -c 'import llama_cpp; print("   llama-cpp-python", llama_cpp.__version__)'
}

download_model() {
  if [ -f "$MODEL" ]; then
    echo "== model present: $MODEL"
    return
  fi
  echo "== downloading $HF_REPO / $HF_FILE"
  local url="https://huggingface.co/$HF_REPO/resolve/main/$HF_FILE?download=true"
  if command -v curl >/dev/null 2>&1; then
    curl -L --fail -o "$MODEL.part" "$url"
  else
    wget -O "$MODEL.part" "$url"
  fi
  mv "$MODEL.part" "$MODEL"
  echo "   saved $(du -h "$MODEL" | cut -f1)"
}

start_server() {
  [ -f "$MODEL" ] || { echo "No model at $MODEL - run without --start first." >&2; exit 1; }
  echo "== starting llama server on 127.0.0.1:$PORT (model: $HF_FILE)"
  # Pin to localhost; the advisor reaches it at /v1. Threads = CPU count.
  local threads; threads="$(nproc 2>/dev/null || echo 4)"
  exec "$PY" -m llama_cpp.server --model "$MODEL" --host 127.0.0.1 --port "$PORT" \
    --n_ctx "$CTX" --n_threads "$threads" --chat_format qwen
}

if [ "$START_ONLY" = 1 ]; then
  start_server
fi

install_server
download_model

# Record the endpoint in the app config so backend "auto" finds it instantly.
"$PY" - "$PORT" "$HF_FILE" <<'PYEOF'
import json, os, sys
port, model = sys.argv[1], sys.argv[2]
cfgdir = os.path.expanduser(os.environ.get("CSK_CONFIG_DIR", "~/.config/csk"))
os.makedirs(cfgdir, exist_ok=True)
path = os.path.join(cfgdir, "config.json")
cfg = {}
try:
    cfg = json.load(open(path))
except Exception:
    pass
cfg.setdefault("agent", {})
cfg["agent"].update(backend="auto", url=f"http://127.0.0.1:{port}/v1/chat/completions", model=model)
json.dump(cfg, open(path, "w"), indent=1)
print(f"   wrote agent config -> {path}")
PYEOF

echo
echo "Setup done. Start the model with:"
echo "    ./llm/setup-llm.sh --start        (or enable the csk-llm systemd service)"
echo "Then in the app: Menu > Consult the Demon King  (footer will read 'advisor: local LLM')."
