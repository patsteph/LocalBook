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
