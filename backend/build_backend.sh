#!/bin/bash

# Build the LocalBook backend as a standalone binary using PyInstaller
# This creates the sidecar binary that Tauri bundles with the app

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}Building LocalBook backend binary...${NC}"

# Ensure virtual environment exists
if [ ! -d ".venv" ]; then
    echo -e "${YELLOW}Creating virtual environment...${NC}"
    python3 -m venv .venv
fi

# Activate virtual environment
source .venv/bin/activate

# Install dependencies if needed
if ! python -c "import pyinstaller" 2>/dev/null; then
    echo -e "${YELLOW}Installing dependencies...${NC}"
    pip install -q -r requirements.txt
fi

# Determine target triple for Tauri sidecar naming
ARCH=$(uname -m)
OS=$(uname -s | tr '[:upper:]' '[:lower:]')

if [ "$ARCH" = "arm64" ]; then
    TARGET_TRIPLE="aarch64-apple-darwin"
elif [ "$ARCH" = "x86_64" ]; then
    TARGET_TRIPLE="x86_64-apple-darwin"
else
    echo -e "${RED}Unsupported architecture: $ARCH${NC}"
    exit 1
fi

BINARY_NAME="localbook-backend-${TARGET_TRIPLE}"
OUTPUT_DIR="../src-tauri/binaries"

echo -e "${YELLOW}Target: ${TARGET_TRIPLE}${NC}"

# Create output directory
mkdir -p "$OUTPUT_DIR"

# Run PyInstaller
echo -e "${YELLOW}Running PyInstaller...${NC}"
pyinstaller \
    --onefile \
    --name "localbook-backend" \
    --distpath "$OUTPUT_DIR" \
    --workpath "./build" \
    --specpath "./build" \
    --clean \
    --noconfirm \
    --paths="." \
    --hidden-import=api \
    --hidden-import=api.notebooks \
    --hidden-import=api.sources \
    --hidden-import=api.chat \
    --hidden-import=api.skills \
    --hidden-import=api.audio \
    --hidden-import=api.source_viewer \
    --hidden-import=api.web \
    --hidden-import=api.settings \
    --hidden-import=api.embeddings \
    --hidden-import=api.timeline \
    --hidden-import=api.export \
    --hidden-import=api.reindex \
    --hidden-import=services \
    --hidden-import=services.llm_service \
    --hidden-import=services.rag_engine \
    --hidden-import=services.document_processor \
    --hidden-import=services.audio_generator \
    --hidden-import=services.model_warmup \
    --hidden-import=storage \
    --hidden-import=storage.notebook_store \
    --hidden-import=storage.source_store \
    --hidden-import=storage.vector_store \
    --hidden-import=storage.skill_store \
    --hidden-import=storage.chat_store \
    --hidden-import=models \
    --hidden-import=models.chat \
    --hidden-import=config \
    --hidden-import=utils \
    --hidden-import=tiktoken_ext.openai_public \
    --hidden-import=tiktoken_ext \
    --hidden-import=sentence_transformers \
    --hidden-import=torch \
    --hidden-import=lancedb \
    --hidden-import=pyarrow \
    --hidden-import=uvicorn.logging \
    --hidden-import=uvicorn.loops \
    --hidden-import=uvicorn.loops.auto \
    --hidden-import=uvicorn.protocols \
    --hidden-import=uvicorn.protocols.http \
    --hidden-import=uvicorn.protocols.http.auto \
    --hidden-import=uvicorn.protocols.websockets \
    --hidden-import=uvicorn.protocols.websockets.auto \
    --hidden-import=uvicorn.lifespan \
    --hidden-import=uvicorn.lifespan.on \
    --collect-all=sentence_transformers \
    --collect-all=torch \
    --collect-all=transformers \
    --collect-data=lancedb \
    main.py

# Rename to target triple format for Tauri
mv "$OUTPUT_DIR/localbook-backend" "$OUTPUT_DIR/$BINARY_NAME"

# Make executable
chmod +x "$OUTPUT_DIR/$BINARY_NAME"

echo -e "${GREEN}✓ Backend binary built: $OUTPUT_DIR/$BINARY_NAME${NC}"

# Show size
SIZE=$(du -h "$OUTPUT_DIR/$BINARY_NAME" | cut -f1)
echo -e "${GREEN}✓ Binary size: $SIZE${NC}"

# Cleanup build artifacts
rm -rf ./build
rm -f ./*.spec

echo -e "${GREEN}✓ Build complete!${NC}"
