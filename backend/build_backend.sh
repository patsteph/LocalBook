#!/bin/bash

# Build the LocalBook backend as a standalone bundle using PyInstaller
# This creates a folder that Tauri bundles as a resource with the app

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}Building LocalBook backend...${NC}"

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

OUTPUT_DIR="../src-tauri/resources/backend"

echo -e "${YELLOW}Output: ${OUTPUT_DIR}${NC}"

# Clean previous build
rm -rf "$OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR"

# Run PyInstaller in onedir mode (more reliable for complex apps)
echo -e "${YELLOW}Running PyInstaller (onedir mode)...${NC}"
pyinstaller \
    --onedir \
    --name "localbook-backend" \
    --distpath "$OUTPUT_DIR" \
    --workpath "./build" \
    --specpath "./build" \
    --clean \
    --noconfirm \
    --paths="." \
    --add-data="api:api" \
    --add-data="services:services" \
    --add-data="storage:storage" \
    --add-data="models:models" \
    --add-data="utils:utils" \
    --add-data="config.py:." \
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
    --collect-all=sentence_transformers \
    --collect-all=torch \
    --collect-all=transformers \
    --collect-data=lancedb \
    --collect-data=tiktoken \
    main.py

# Make the main executable... executable
chmod +x "$OUTPUT_DIR/localbook-backend/localbook-backend"

echo -e "${GREEN}✓ Backend built: $OUTPUT_DIR/localbook-backend/${NC}"

# Show size
SIZE=$(du -sh "$OUTPUT_DIR/localbook-backend" | cut -f1)
echo -e "${GREEN}✓ Bundle size: $SIZE${NC}"

# Cleanup build artifacts
rm -rf ./build
rm -f ./*.spec

echo -e "${GREEN}✓ Build complete!${NC}"
