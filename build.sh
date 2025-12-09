#!/bin/bash

# Build LocalBook for distribution
# Creates a .app bundle and .dmg installer

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}              LocalBook Production Build                     ${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

# Check prerequisites
echo -e "\n${YELLOW}Checking prerequisites...${NC}"

if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Error: Python 3 not found${NC}"
    exit 1
fi

if ! command -v node &> /dev/null; then
    echo -e "${RED}Error: Node.js not found${NC}"
    exit 1
fi

# Cargo only needed for full Tauri build, not just backend binary
if ! command -v cargo &> /dev/null; then
    if [ "$1" != "--rebuild" ] && [ "$1" != "--backend-only" ]; then
        echo -e "${RED}Error: Rust/Cargo not found. Install with:${NC}"
        echo -e "  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
        exit 1
    else
        echo -e "${YELLOW}Note: Cargo not found, skipping Tauri build${NC}"
        SKIP_TAURI=true
    fi
fi

echo -e "${GREEN}✓ Prerequisites checked${NC}"

# Determine target triple
ARCH=$(uname -m)
if [ "$ARCH" = "arm64" ]; then
    TARGET_TRIPLE="aarch64-apple-darwin"
else
    TARGET_TRIPLE="x86_64-apple-darwin"
fi
BACKEND_BINARY="src-tauri/binaries/localbook-backend-${TARGET_TRIPLE}"

# Step 1: Build backend binary
echo -e "\n${YELLOW}Step 1/3: Building backend binary...${NC}"
if [ ! -f "$BACKEND_BINARY" ] || [ "$1" = "--rebuild" ]; then
    cd backend
    
    # Ensure venv exists
    if [ ! -d ".venv" ]; then
        echo -e "${YELLOW}Creating virtual environment...${NC}"
        python3 -m venv .venv
    fi
    
    source .venv/bin/activate
    
    # Install dependencies
    echo -e "${YELLOW}Installing dependencies...${NC}"
    pip install -q -r requirements.txt
    
    # Build binary
    ./build_backend.sh
    cd ..
else
    echo -e "${GREEN}✓ Backend binary already exists (use --rebuild to force)${NC}"
fi

# Step 2: Install frontend dependencies
if [ "$SKIP_TAURI" != "true" ]; then
    echo -e "\n${YELLOW}Step 2/3: Installing frontend dependencies...${NC}"
    npm install --silent
    echo -e "${GREEN}✓ Frontend dependencies ready${NC}"

    # Step 3: Build Tauri app
    echo -e "\n${YELLOW}Step 3/3: Building Tauri application...${NC}"
    npm run tauri build

    echo -e "\n${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${GREEN}                    Build Complete!                          ${NC}"
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e ""
    echo -e "Output files:"
    echo -e "  ${BLUE}App:${NC} src-tauri/target/release/bundle/macos/LocalBook.app"
    echo -e "  ${BLUE}DMG:${NC} src-tauri/target/release/bundle/dmg/LocalBook_*.dmg"
else
    echo -e "\n${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${GREEN}              Backend Binary Built!                          ${NC}"
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e ""
    echo -e "Binary: ${BLUE}${BACKEND_BINARY}${NC}"
    echo -e ""
    echo -e "${YELLOW}To build the full Tauri app, install Rust:${NC}"
    echo -e "  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
fi
echo -e ""
echo -e "${YELLOW}Note: Users still need Ollama installed with models:${NC}"
echo -e "  brew install ollama"
echo -e "  ollama pull mistral-nemo:12b-instruct-2407-q4_K_M"
echo -e "  ollama pull phi4-mini"
