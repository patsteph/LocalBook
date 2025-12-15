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

# Check for Homebrew (needed to install dependencies)
if ! command -v brew &> /dev/null; then
    echo -e "${YELLOW}Homebrew not found. Installing...${NC}"
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    
    # Add brew to path for Apple Silicon
    if [ -f "/opt/homebrew/bin/brew" ]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
fi

# Install Python if not found
if ! command -v python3 &> /dev/null; then
    echo -e "${YELLOW}Python not found. Installing...${NC}"
    brew install python
fi

# Install Node.js if not found
if ! command -v node &> /dev/null; then
    echo -e "${YELLOW}Node.js not found. Installing...${NC}"
    brew install node
fi

# Install Ollama if not found
if ! command -v ollama &> /dev/null; then
    echo -e "${YELLOW}Ollama not found. Installing...${NC}"
    brew install ollama
fi

# Install Rust if not found
if ! command -v cargo &> /dev/null; then
    echo -e "${YELLOW}Rust not found. Installing automatically...${NC}"
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
    source "$HOME/.cargo/env"
    
    if ! command -v cargo &> /dev/null; then
        echo -e "${RED}Error: Failed to install Rust${NC}"
        exit 1
    fi
    echo -e "${GREEN}✓ Rust installed${NC}"
fi

echo -e "${GREEN}✓ Prerequisites checked${NC}"

BACKEND_DIR="src-tauri/resources/backend/localbook-backend"
BACKEND_EXE="$BACKEND_DIR/localbook-backend"

# Step 1: Build backend
echo -e "\n${YELLOW}Step 1/3: Building backend...${NC}"
if [ ! -f "$BACKEND_EXE" ] || [ "$1" = "--rebuild" ]; then
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
echo -e "\n${YELLOW}Step 2/3: Installing frontend dependencies...${NC}"
npm install --silent
echo -e "${GREEN}✓ Frontend dependencies ready${NC}"

# Step 3: Build Tauri app
echo -e "\n${YELLOW}Step 3/3: Building Tauri application...${NC}"
npm run tauri build

# Copy app to easy location
APP_PATH="src-tauri/target/release/bundle/macos/LocalBook.app"
if [ -d "$APP_PATH" ]; then
    cp -r "$APP_PATH" "./LocalBook.app"
fi

echo -e "\n${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}                    Build Complete!                          ${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e ""
echo -e "${GREEN}Your app is ready:${NC}"
echo -e "  ${BLUE}./LocalBook.app${NC}"
echo -e ""
echo -e "To install, drag LocalBook.app to your Applications folder, or run:"
echo -e "  ${BLUE}cp -r LocalBook.app /Applications/${NC}"
echo -e ""

# Download Ollama models if not present
echo -e "${YELLOW}Checking AI models...${NC}"

# Start Ollama if not running
if ! curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    ollama serve > /dev/null 2>&1 &
    sleep 2
fi

MODELS=$(ollama list 2>/dev/null || echo "")
if ! echo "$MODELS" | grep -q "mistral-nemo"; then
    echo -e "${YELLOW}Downloading mistral-nemo model (~7GB)...${NC}"
    ollama pull mistral-nemo:12b-instruct-2407-q4_K_M
fi

if ! echo "$MODELS" | grep -q "phi4-mini"; then
    echo -e "${YELLOW}Downloading phi4-mini model (~2.5GB)...${NC}"
    ollama pull phi4-mini
fi

echo -e "${GREEN}✓ AI models ready${NC}"
echo -e ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Ready! Launch LocalBook.app or copy to /Applications       ${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
