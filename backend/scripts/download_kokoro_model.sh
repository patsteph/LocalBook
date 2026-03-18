#!/bin/bash
# ──────────────────────────────────────────────────────────────────
# Download Kokoro-82M-bf16 TTS model from HuggingFace
# ──────────────────────────────────────────────────────────────────
#
# Manual fallback for environments where the automatic Python-based
# HuggingFace Hub download fails (e.g. SSL certificate issues on
# macOS Python 3.13).
#
# The app checks for this local cache FIRST before attempting a
# HuggingFace Hub download, so running this script once is enough.
#
# Usage:
#   bash scripts/download_kokoro_model.sh
#
# What it downloads (~348MB total):
#   - config.json           (model configuration)
#   - kokoro-v1_0.safetensors (327MB model weights)
#   - voices/*.safetensors  (54 voice style vectors, ~522KB each)
#
# The repo also contains ~2GB of WAV samples that are NOT needed
# for inference — this script skips them entirely.
# ──────────────────────────────────────────────────────────────────
set -e

REPO="mlx-community/Kokoro-82M-bf16"
BASE_URL="https://huggingface.co/${REPO}/resolve/main"
CACHE_DIR="$HOME/.cache/kokoro-mlx-model"

mkdir -p "$CACHE_DIR/voices"

echo "Downloading Kokoro-82M model to $CACHE_DIR ..."

# 1. config.json
if [ ! -f "$CACHE_DIR/config.json" ]; then
    echo "  Downloading config.json..."
    curl -k -sL -o "$CACHE_DIR/config.json" "${BASE_URL}/config.json"
    echo "  ✓ config.json ($(wc -c < "$CACHE_DIR/config.json") bytes)"
else
    echo "  ✓ config.json (cached)"
fi

# 2. Model weights (~327MB)
if [ ! -f "$CACHE_DIR/kokoro-v1_0.safetensors" ]; then
    echo "  Downloading kokoro-v1_0.safetensors (~327MB)..."
    curl -k -L --progress-bar -o "$CACHE_DIR/kokoro-v1_0.safetensors" "${BASE_URL}/kokoro-v1_0.safetensors"
    echo "  ✓ kokoro-v1_0.safetensors ($(du -h "$CACHE_DIR/kokoro-v1_0.safetensors" | cut -f1))"
else
    echo "  ✓ kokoro-v1_0.safetensors (cached)"
fi

# 3. Voice files — fetch list from HF API, then download each
echo "  Downloading voice files..."
VOICE_LIST=$(curl -k -sL "https://huggingface.co/api/models/${REPO}/tree/main/voices" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for f in data:
    if f['path'].endswith('.safetensors'):
        print(f['path'])
" 2>/dev/null || echo "")

if [ -z "$VOICE_LIST" ]; then
    echo "  Could not get voice list from API, using known defaults..."
    VOICE_LIST="voices/af_heart.safetensors voices/af_bella.safetensors voices/af_nicole.safetensors voices/af_sarah.safetensors voices/af_sky.safetensors voices/am_adam.safetensors voices/am_michael.safetensors voices/bf_emma.safetensors voices/bf_isabella.safetensors voices/bm_george.safetensors voices/bm_lewis.safetensors"
fi

COUNT=0
TOTAL=$(echo "$VOICE_LIST" | wc -w | tr -d ' ')
for voice_path in $VOICE_LIST; do
    COUNT=$((COUNT + 1))
    voice_file=$(basename "$voice_path")
    if [ ! -f "$CACHE_DIR/voices/$voice_file" ]; then
        printf "  [%d/%d] %s..." "$COUNT" "$TOTAL" "$voice_file"
        curl -k -sL -o "$CACHE_DIR/voices/$voice_file" "${BASE_URL}/${voice_path}"
        echo " ✓"
    else
        printf "  [%d/%d] %s (cached)\n" "$COUNT" "$TOTAL" "$voice_file"
    fi
done

echo ""
echo "Download complete!"
echo "Model directory: $CACHE_DIR"
echo "Total size: $(du -sh "$CACHE_DIR" | cut -f1)"
