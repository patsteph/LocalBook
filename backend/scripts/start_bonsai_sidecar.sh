#!/usr/bin/env bash
# start_bonsai_sidecar.sh — Developer convenience script for Phase 1 of the
# Bonsai-8B integration. Starts llama-server with the Bonsai Q1_0 GGUF on
# port 8090 (matches the default in services/llm_provider.py).
#
# One-time setup (requires Homebrew llama.cpp >= b8712 OR a source build
# from upstream master — brew's formula lags, so source build is often needed):
#   # Option A: brew (if formula is fresh enough)
#   brew install llama.cpp && llama-server --version   # must be >= b8712
#
#   # Option B: build from source (use this if brew is stale)
#   brew install cmake
#   git clone https://github.com/ggml-org/llama.cpp.git ~/src/llama.cpp
#   cd ~/src/llama.cpp
#   cmake -B build -DGGML_METAL=ON -DLLAMA_CURL=OFF
#   cmake --build build --config Release -j --target llama-server
#   export PATH="$HOME/src/llama.cpp/build/bin:$PATH"
#
#   mkdir -p ~/.localbook/models/bonsai
#   curl -L -o ~/.localbook/models/bonsai/Bonsai-8B-Q1_0.gguf \
#     https://huggingface.co/prism-ml/Bonsai-8B-gguf/resolve/main/Bonsai-8B-Q1_0.gguf
#
# Usage:
#   ./backend/scripts/start_bonsai_sidecar.sh          # foreground
#   ./backend/scripts/start_bonsai_sidecar.sh --bg     # background (logs to /tmp)
#
# Phase 1 scope: dev-only helper. Phase 2 (Labs toggle) will add automated
# lifecycle management. Phase 3 (GA) may bundle the binary + model.

set -euo pipefail

MODEL_PATH="${BONSAI_MODEL_PATH:-$HOME/.localbook/models/bonsai/Bonsai-8B-Q1_0.gguf}"
PORT="${BONSAI_PORT:-8090}"
CTX_SIZE="${BONSAI_CTX_SIZE:-4096}"   # Bonsai coherence degrades past ~4K
NGL="${BONSAI_NGL:-99}"                # offload all layers to Metal

# Prefer source-built binary if present; fall back to PATH.
if [[ -x "$HOME/src/llama.cpp/build/bin/llama-server" ]]; then
  LLAMA_SERVER_BIN="$HOME/src/llama.cpp/build/bin/llama-server"
elif command -v llama-server >/dev/null 2>&1; then
  LLAMA_SERVER_BIN="$(command -v llama-server)"
else
  echo "ERROR: llama-server not found. See comments at top of this script." >&2
  exit 1
fi

if [[ ! -f "$MODEL_PATH" ]]; then
  echo "ERROR: Bonsai model not found at $MODEL_PATH" >&2
  echo "Download with:" >&2
  echo "  mkdir -p \"$(dirname "$MODEL_PATH")\"" >&2
  echo "  curl -L -o \"$MODEL_PATH\" \\" >&2
  echo "    https://huggingface.co/prism-ml/Bonsai-8B-gguf/resolve/main/Bonsai-8B-Q1_0.gguf" >&2
  exit 1
fi

if lsof -iTCP:"$PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
  echo "ERROR: port $PORT is already in use. Stop the existing process or set BONSAI_PORT." >&2
  exit 1
fi

THREADS="$(sysctl -n hw.perflevel0.physicalcpu 2>/dev/null || sysctl -n hw.ncpu)"

CMD=(
  "$LLAMA_SERVER_BIN"
  -m "$MODEL_PATH"
  --host 127.0.0.1
  --port "$PORT"
  -ngl "$NGL"
  --ctx-size "$CTX_SIZE"
  --threads "$THREADS"
)

echo "Starting Bonsai sidecar:"
printf '  %s\n' "${CMD[@]}"

if [[ "${1:-}" == "--bg" ]]; then
  LOG_OUT="/tmp/bonsai-server.log"
  LOG_ERR="/tmp/bonsai-server.err"
  nohup "${CMD[@]}" >"$LOG_OUT" 2>"$LOG_ERR" &
  echo "Started in background (PID $!)"
  echo "  stdout: $LOG_OUT"
  echo "  stderr: $LOG_ERR"
  echo "Verify: curl -sS http://127.0.0.1:$PORT/health"
else
  exec "${CMD[@]}"
fi
