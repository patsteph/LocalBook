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
    --paths="$SCRIPT_DIR" \
    --add-data="$SCRIPT_DIR/api:api" \
    --add-data="$SCRIPT_DIR/services:services" \
    --add-data="$SCRIPT_DIR/storage:storage" \
    --add-data="$SCRIPT_DIR/models:models" \
    --add-data="$SCRIPT_DIR/utils:utils" \
    --add-data="$SCRIPT_DIR/agents:agents" \
    --add-data="$SCRIPT_DIR/static:static" \
    --add-data="$SCRIPT_DIR/config.py:." \
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
    --hidden-import=api.memory \
    --hidden-import=api.graph \
    --hidden-import=api.constellation_ws \
    --hidden-import=api.updates \
    --hidden-import=services \
    --hidden-import=services.rag_engine \
    --hidden-import=services.document_processor \
    --hidden-import=services.audio_generator \
    --hidden-import=services.model_warmup \
    --hidden-import=services.memory_agent \
    --hidden-import=services.knowledge_graph \
    --hidden-import=services.topic_modeling \
    --hidden-import=storage \
    --hidden-import=storage.notebook_store \
    --hidden-import=storage.source_store \
    --hidden-import=storage.audio_store \
    --hidden-import=storage.highlights_store \
    --hidden-import=storage.skills_store \
    --hidden-import=storage.memory_store \
    --hidden-import=models \
    --hidden-import=models.memory \
    --hidden-import=models.knowledge_graph \
    --hidden-import=config \
    --hidden-import=utils \
    --hidden-import=agents \
    --hidden-import=agents.tools \
    --hidden-import=agents.state \
    --hidden-import=agents.supervisor \
    --hidden-import=services.web_scraper \
    --collect-all=sentence_transformers \
    --collect-all=torch \
    --collect-all=transformers \
    --collect-all=trafilatura \
    --collect-all=justext \
    --collect-all=whisper \
    --collect-all=bertopic \
    --collect-all=umap \
    --collect-all=hdbscan \
    --collect-submodules=pandas \
    --collect-data=lancedb \
    --collect-data=tiktoken \
    --hidden-import=sklearn.cluster \
    --hidden-import=trafilatura \
    --hidden-import=httpx \
    --hidden-import=youtube_transcript_api \
    --hidden-import=keyring \
    --hidden-import=dateparser \
    --hidden-import=fitz \
    --hidden-import=pdfplumber \
    --hidden-import=docx \
    --hidden-import=pptx \
    --hidden-import=openpyxl \
    --hidden-import=xlrd \
    --hidden-import=pandas._config \
    --hidden-import=moviepy \
    --hidden-import=anthropic \
    --hidden-import=openai \
    --hidden-import=multiprocessing \
    --hidden-import=rank_bm25 \
    --hidden-import=ebooklib \
    --hidden-import=nbformat \
    --hidden-import=odf \
    --hidden-import=pytesseract \
    --hidden-import=PIL \
    main.py

# Make the main executable... executable
chmod +x "$OUTPUT_DIR/localbook-backend/localbook-backend"

# Fix pandas._config not being bundled by PyInstaller
# This is a known PyInstaller issue with pandas - manually copy the _config module
if [ -d ".venv/lib/python3.11/site-packages/pandas/_config" ]; then
    echo -e "${YELLOW}Fixing pandas._config bundling issue...${NC}"
    cp -r .venv/lib/python3.11/site-packages/pandas/_config "$OUTPUT_DIR/localbook-backend/_internal/pandas/" 2>/dev/null || true
fi

echo -e "${GREEN}✓ Backend built: $OUTPUT_DIR/localbook-backend/${NC}"

# Sign all binaries for macOS Sequoia compatibility
# macOS Sequoia requires proper code signing for all .so/.dylib files
echo -e "${YELLOW}Signing binaries for macOS compatibility...${NC}"
find "$OUTPUT_DIR/localbook-backend/_internal" -name "*.so" -exec codesign --force --sign - {} \; 2>/dev/null
find "$OUTPUT_DIR/localbook-backend/_internal" -name "*.dylib" -exec codesign --force --sign - {} \; 2>/dev/null
codesign --force --sign - "$OUTPUT_DIR/localbook-backend/localbook-backend" 2>/dev/null
echo -e "${GREEN}✓ Code signing complete${NC}"

# Show size
SIZE=$(du -sh "$OUTPUT_DIR/localbook-backend" | cut -f1)
echo -e "${GREEN}✓ Bundle size: $SIZE${NC}"

# Cleanup build artifacts
rm -rf ./build
rm -f ./*.spec

echo -e "${GREEN}✓ Build complete!${NC}"
