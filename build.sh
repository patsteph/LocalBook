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

# Require Python 3.12+ (liquid-audio TTS requires 3.12+)
PYTHON_CMD=""
if command -v python3.12 &> /dev/null; then
    PYTHON_CMD="python3.12"
elif [ -f "/opt/homebrew/bin/python3.12" ]; then
    PYTHON_CMD="/opt/homebrew/bin/python3.12"
elif [ -f "/usr/local/bin/python3.12" ]; then
    PYTHON_CMD="/usr/local/bin/python3.12"
elif command -v python3.13 &> /dev/null; then
    PYTHON_CMD="python3.13"
elif command -v python3 &> /dev/null; then
    PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.minor}')")
    if [ "$PY_VER" -ge 12 ]; then
        PYTHON_CMD="python3"
    fi
fi

if [ -z "$PYTHON_CMD" ]; then
    echo -e "${YELLOW}Python 3.12+ not found. Installing...${NC}"
    brew install python@3.12
    PYTHON_CMD="/opt/homebrew/bin/python3.12"
    [ -f "$PYTHON_CMD" ] || PYTHON_CMD="/usr/local/bin/python3.12"
fi

echo -e "${GREEN}Using Python: $PYTHON_CMD${NC}"

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

# Install ffmpeg if not found (for audio/video transcription)
if ! command -v ffmpeg &> /dev/null; then
    echo -e "${YELLOW}ffmpeg not found. Installing...${NC}"
    brew install ffmpeg
fi

# Install Tesseract if not found (for image OCR)
if ! command -v tesseract &> /dev/null; then
    echo -e "${YELLOW}Tesseract not found. Installing...${NC}"
    brew install tesseract
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

# Parse arguments
DO_REBUILD=false
DO_CLEAN=false
for arg in "$@"; do
    case $arg in
        --rebuild) DO_REBUILD=true ;;
        --clean) DO_CLEAN=true ;;
    esac
done

# Step 1: Build backend
echo -e "\n${YELLOW}Step 1/3: Building backend...${NC}"
if [ ! -f "$BACKEND_EXE" ] || [ "$DO_REBUILD" = true ] || [ "$DO_CLEAN" = true ]; then
    cd backend
    
    # Clean rebuild: remove venv entirely to ensure fresh install
    if [ "$DO_CLEAN" = true ]; then
        echo -e "${YELLOW}Clean build: removing existing virtual environment...${NC}"
        rm -rf .venv
    fi
    
    # Ensure venv exists with Python 3.12+
    if [ ! -d ".venv" ]; then
        echo -e "${YELLOW}Creating virtual environment with $PYTHON_CMD...${NC}"
        $PYTHON_CMD -m venv .venv
    fi
    
    source .venv/bin/activate
    
    # Install/upgrade dependencies using pip-sync for exact reproducibility
    echo -e "${YELLOW}Installing dependencies...${NC}"
    pip install --upgrade pip pip-tools
    
    # Use pip-sync for exact version matching (removes extra packages too)
    # Falls back to pip install if pip-sync fails (e.g., hash mismatch on new platform)
    if pip-sync requirements.txt 2>/dev/null; then
        echo -e "${GREEN}✓ Dependencies synced exactly${NC}"
    else
        echo -e "${YELLOW}pip-sync failed, falling back to pip install...${NC}"
        pip install -r requirements.txt
    fi
    
    # Verify critical packages are installed
    echo -e "${YELLOW}Verifying critical packages...${NC}"
    MISSING=""
    python -c "import rank_bm25" 2>/dev/null || MISSING="$MISSING rank-bm25"
    python -c "import ebooklib" 2>/dev/null || MISSING="$MISSING ebooklib"
    python -c "import odf" 2>/dev/null || MISSING="$MISSING odfpy"
    python -c "import nbformat" 2>/dev/null || MISSING="$MISSING nbformat"
    
    if [ -n "$MISSING" ]; then
        echo -e "${YELLOW}Installing missing packages:$MISSING${NC}"
        pip install $MISSING
    fi
    
    echo -e "${GREEN}✓ Dependencies installed${NC}"
    
    # Build binary
    ./build_backend.sh
    cd ..
else
    echo -e "${GREEN}✓ Backend binary already exists (use --rebuild to force, --clean for fresh venv)${NC}"
fi

# Step 2: Install frontend dependencies
echo -e "\n${YELLOW}Step 2/4: Installing frontend dependencies...${NC}"
npm install --silent
echo -e "${GREEN}✓ Frontend dependencies ready${NC}"

# Step 3: Clean and rebuild frontend (prevents stale cache issues)
echo -e "\n${YELLOW}Step 3/4: Rebuilding frontend (clean)...${NC}"
rm -rf dist/
npm run build
echo -e "${GREEN}✓ Frontend rebuilt${NC}"

# Step 4: Build Tauri app
echo -e "\n${YELLOW}Step 4/4: Building Tauri application...${NC}"
npm run tauri build

# Copy app to easy location
APP_PATH="src-tauri/target/release/bundle/macos/LocalBook.app"
if [ -d "$APP_PATH" ]; then
    rm -rf "./LocalBook.app"
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

# System 2: Main model for conversation and reasoning (64K context)
if ! echo "$MODELS" | grep -q "olmo-3:7b-instruct"; then
    echo -e "${YELLOW}Downloading olmo-3:7b-instruct model (~4GB)...${NC}"
    ollama pull olmo-3:7b-instruct
fi

# System 1: Fast model for quick responses (Microsoft Phi-4 mini)
if ! echo "$MODELS" | grep -q "phi4-mini"; then
    echo -e "${YELLOW}Downloading phi4-mini model (~2GB)...${NC}"
    ollama pull phi4-mini
fi

# Embedding model (1024 dims, frontier quality)
if ! echo "$MODELS" | grep -q "snowflake-arctic-embed2"; then
    echo -e "${YELLOW}Downloading snowflake-arctic-embed2 model (~500MB)...${NC}"
    ollama pull snowflake-arctic-embed2
fi

echo -e "${GREEN}✓ AI models ready${NC}"
echo -e ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Ready! Launch LocalBook.app or copy to /Applications       ${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
