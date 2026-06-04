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

# Require Python 3.12+
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

# Install espeak-ng if not found (required by phonemizer for Kokoro TTS)
if ! command -v espeak-ng &> /dev/null; then
    echo -e "${YELLOW}espeak-ng not found. Installing (required for TTS)...${NC}"
    brew install espeak-ng
fi

# Check for Xcode Command Line Tools (required for Rust/Tauri compilation)
if ! xcode-select -p &> /dev/null; then
    echo -e "${YELLOW}Xcode Command Line Tools not found. Installing...${NC}"
    echo -e "${YELLOW}A dialog may appear — click 'Install' and wait for completion.${NC}"
    xcode-select --install
    echo -e "${YELLOW}Press Enter after Xcode Command Line Tools installation completes...${NC}"
    read -r
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
echo -e "\n${YELLOW}Step 1/4: Building backend...${NC}"
if [ ! -f "$BACKEND_EXE" ] || [ "$DO_REBUILD" = true ] || [ "$DO_CLEAN" = true ]; then
    cd backend
    
    # Clear stale bytecode — prevents phantom AttributeError from cached .pyc
    find . -maxdepth 4 -type d -name '__pycache__' -not -path './.venv/*' -exec rm -rf {} + 2>/dev/null || true
    echo -e "${GREEN}✓ Cleared stale __pycache__${NC}"
    
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
    
    # Install/upgrade dependencies
    echo -e "${YELLOW}Installing dependencies...${NC}"
    pip install --upgrade pip
    
    # Use pip install (not pip-sync) to avoid uninstall/reinstall churn
    # pip-sync removes packages not in requirements.txt (kokoro-mlx, en_core_web_sm)
    # which build_backend.sh then reinstalls — wasteful and noisy
    # Show progress on first install (fresh venv), quiet on subsequent
    if [ $(pip list 2>/dev/null | wc -l) -lt 20 ]; then
        echo -e "${YELLOW}First install — this takes ~10 minutes...${NC}"
        pip install -r requirements.txt
    else
        pip install -q -r requirements.txt
    fi
    
    # Verify critical packages are installed
    echo -e "${YELLOW}Verifying critical packages...${NC}"
    MISSING=""
    python -c "import rank_bm25" 2>/dev/null || MISSING="$MISSING rank-bm25"
    python -c "import ebooklib" 2>/dev/null || MISSING="$MISSING ebooklib"
    python -c "import odf" 2>/dev/null || MISSING="$MISSING odfpy"
    python -c "import nbformat" 2>/dev/null || MISSING="$MISSING nbformat"
    python -c "import misaki" 2>/dev/null || MISSING="$MISSING misaki"
    python -c "import soundfile" 2>/dev/null || MISSING="$MISSING soundfile"
    
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
echo -e "\n${YELLOW}Step 2/3: Installing frontend dependencies...${NC}"
npm install --silent
echo -e "${GREEN}✓ Frontend dependencies ready${NC}"

# Step 3: Build Tauri app (includes Vite frontend build via beforeBuildCommand)
echo -e "\n${YELLOW}Step 3/3: Building Tauri application...${NC}"

rm -rf dist/
# Build .app only by default. Tauri's bundle_dmg.sh uses AppleScript to set
# window metadata on the mounted DMG and fails on systems without
# Automation permission. For local dev / install we only need the .app.
# Use release.sh when you need a notarization-ready DMG.
npm run tauri build -- --bundles app

APP_PATH="src-tauri/target/release/bundle/macos/LocalBook.app"

# ═══════════════════════════════════════════════════════════════════════════
# Post-Tauri Developer ID signing pass (release mode only)
# ═══════════════════════════════════════════════════════════════════════════
# The reason this lives here and not in build_backend.sh:
#
# PyInstaller creates `_internal/Python` as a SYMLINK to
# `Python.framework/Versions/3.13/Python`. When Tauri bundles the backend
# into the .app, its copier dereferences the symlink — so `_internal/Python`
# becomes a standalone regular file at a new path. Any signature applied
# to that symlink (in `src-tauri/resources/backend/...`) is invalid once
# the file moves to its post-deref location: the signed identifier
# (`org.python.python`) is now ambiguous between two physical files at
# different paths, and codesign verify reports "invalid Info.plist
# (plist or signature have been modified)" at runtime.
#
# Solution: do all the Developer ID work AFTER Tauri bundles, against
# the post-deref file structure. This pass is idempotent — run it as
# many times as you like; each run re-signs cleanly with --force.
#
# Triggers only when APPLE_SIGNING_IDENTITY is set (release builds);
# dev builds keep their adhoc signatures from build_backend.sh.
if [ -d "$APP_PATH" ] && [ -n "${APPLE_SIGNING_IDENTITY:-}" ]; then
    echo -e "\n${YELLOW}Post-Tauri signing: Developer ID + entitlements + runtime...${NC}"
    echo -e "${YELLOW}  Identity: $APPLE_SIGNING_IDENTITY${NC}"

    BACKEND_IN_APP="$APP_PATH/Contents/Resources/resources/backend/localbook-backend"
    BACKEND_INTERNAL_IN_APP="$BACKEND_IN_APP/_internal"
    BACKEND_EXE_IN_APP="$BACKEND_IN_APP/localbook-backend"
    ROOT_ENTITLEMENTS="src-tauri/entitlements.plist"

    if [ ! -f "$ROOT_ENTITLEMENTS" ]; then
        echo -e "${RED}✗ Expected entitlements file not found: $ROOT_ENTITLEMENTS${NC}"
        exit 1
    fi

    # 0. Restore Python.framework canonical symlink structure.
    #    PyInstaller produces a normal macOS framework layout:
    #
    #      Python.framework/
    #      ├── Python -> Versions/Current/Python  (symlink)
    #      ├── Resources -> Versions/Current/Resources  (symlink)
    #      └── Versions/
    #          ├── Current -> 3.13  (symlink)
    #          └── 3.13/
    #              ├── Python  (real file)
    #              └── Resources/
    #
    #    Tauri's bundler dereferences every symlink during the copy, so
    #    the .app ships with:
    #      - Python.framework/Python as a REAL FILE (5 MB duplicate)
    #      - Python.framework/Versions/Current MISSING
    #      - _internal/Python as a REAL FILE (5 MB duplicate of the
    #        framework's inner Python)
    #
    #    Apple's notary rejects this with "The signature of the binary
    #    is invalid" on Python.framework/Python — a regular file at a
    #    framework root path is structurally invalid for codesign.
    #
    #    Restore the symlinks before signing so the framework presents
    #    the canonical layout that codesign + notary understand.
    PFW="$BACKEND_INTERNAL_IN_APP/Python.framework"
    if [ -d "$PFW" ]; then
        echo -e "${YELLOW}  [0] Restoring Python.framework symlink structure (Tauri deref'd)...${NC}"
        # Versions/Current → 3.13 (must exist before relative symlinks below)
        if [ ! -L "$PFW/Versions/Current" ]; then
            rm -rf "$PFW/Versions/Current" 2>/dev/null || true
            (cd "$PFW/Versions" && ln -s 3.13 Current)
        fi
        # Python.framework/Python → Versions/Current/Python
        if [ ! -L "$PFW/Python" ]; then
            rm -f "$PFW/Python"
            (cd "$PFW" && ln -s Versions/Current/Python Python)
        fi
        # Python.framework/Resources → Versions/Current/Resources
        # (skip if the target doesn't exist — some Tauri builds drop it.)
        if [ ! -e "$PFW/Resources" ] && [ -d "$PFW/Versions/3.13/Resources" ]; then
            (cd "$PFW" && ln -s Versions/Current/Resources Resources)
        fi
        # _internal/Python → Python.framework/Versions/3.13/Python
        # (the symlink PyInstaller put at the top of _internal/)
        if [ ! -L "$BACKEND_INTERNAL_IN_APP/Python" ] && [ -f "$BACKEND_INTERNAL_IN_APP/Python" ]; then
            rm -f "$BACKEND_INTERNAL_IN_APP/Python"
            (cd "$BACKEND_INTERNAL_IN_APP" && ln -s Python.framework/Versions/3.13/Python Python)
        fi
        # Wipe any prior _CodeSignature directories from a previous
        # half-attempt — codesign --force will recreate them cleanly.
        rm -rf "$PFW/_CodeSignature" 2>/dev/null || true
        rm -rf "$PFW/Versions/3.13/_CodeSignature" 2>/dev/null || true
        echo -e "${GREEN}  ✓ Framework symlinks restored${NC}"
    fi

    # 1. Sign every Mach-O file inside the post-Tauri backend bundle,
    #    EXCLUDING anything inside Python.framework (framework signing
    #    handles those atomically in step 2).
    echo -e "${YELLOW}  [1/4] Scanning + signing Mach-O files inside _internal...${NC}"
    find "$BACKEND_INTERNAL_IN_APP" -type f \
        -not -path "*/Python.framework/*" -print0 | \
    while IFS= read -r -d '' f; do
        if file -b "$f" 2>/dev/null | grep -q "Mach-O"; then
            codesign --force --options runtime --timestamp \
                --sign "$APPLE_SIGNING_IDENTITY" "$f" >/dev/null 2>&1 || {
                echo -e "${RED}✗ codesign failed on $f${NC}"
                exit 1
            }
        fi
    done

    # 2. Sign Python.framework. Two-step process:
    #    a) Sign the inner Mach-O binary explicitly with Developer ID +
    #       runtime + timestamp. `codesign Path/To/Framework` does NOT
    #       propagate these flags into the inner executable — it only
    #       seals the framework's bundle structure (CodeResources). So
    #       without this explicit step, the inner Python binary retains
    #       whatever signature it had before (adhoc from build_backend.sh),
    #       and notarization rejects it: "not signed with valid Developer
    #       ID" + "signature does not include a secure timestamp".
    #    b) Sign the framework as a unit to update CodeResources to match
    #       the newly-signed inner binary.
    if [ -d "$BACKEND_INTERNAL_IN_APP/Python.framework" ]; then
        echo -e "${YELLOW}  [2a] Signing Python.framework inner binary (Versions/*/Python)...${NC}"
        find "$BACKEND_INTERNAL_IN_APP/Python.framework/Versions" \
                -mindepth 2 -maxdepth 2 -name "Python" -type f -print0 | \
        while IFS= read -r -d '' f; do
            codesign --force --options runtime --timestamp \
                --sign "$APPLE_SIGNING_IDENTITY" "$f" || {
                echo -e "${RED}✗ codesign failed on $f${NC}"
                exit 1
            }
        done

        echo -e "${YELLOW}  [2b] Signing Python.framework as framework (seals inner)...${NC}"
        codesign --force --options runtime --timestamp \
            --sign "$APPLE_SIGNING_IDENTITY" \
            "$BACKEND_INTERNAL_IN_APP/Python.framework" || {
            echo -e "${RED}✗ codesign failed on Python.framework${NC}"
            exit 1
        }
    fi

    # 3. Sign the backend main executable with the app's entitlements
    #    (JIT, library validation disable, etc. — see entitlements.plist).
    echo -e "${YELLOW}  [3/4] Signing backend executable with entitlements...${NC}"
    codesign --force --options runtime --timestamp \
        --entitlements "$ROOT_ENTITLEMENTS" \
        --sign "$APPLE_SIGNING_IDENTITY" \
        "$BACKEND_EXE_IN_APP" || {
        echo -e "${RED}✗ codesign failed on backend executable${NC}"
        exit 1
    }

    # 4. Re-sign the outer .app. Tauri signed it during `tauri build`, but
    #    we modified inner contents above — that broke the outer signature.
    #    Sign the .app last so the outer signature covers all inner files
    #    we just touched. Use the main app's entitlements via tauri.conf.json
    #    bundle.macOS (already in place via release.sh's identity injection).
    echo -e "${YELLOW}  [4/4] Re-signing outer .app...${NC}"
    codesign --force --options runtime --timestamp \
        --sign "$APPLE_SIGNING_IDENTITY" \
        "$APP_PATH" || {
        echo -e "${RED}✗ codesign failed on outer .app${NC}"
        exit 1
    }

    # Deep verify: catches any Mach-O we missed BEFORE we waste a 10-minute
    # notary round-trip. --strict is mandatory here; --verbose=1 prints
    # the chain.
    echo -e "${YELLOW}  Deep-verifying .app signature...${NC}"
    if ! codesign --verify --deep --strict --verbose=1 "$APP_PATH" 2>&1 | tail -5; then
        echo -e "${RED}✗ codesign --verify --deep --strict failed on $APP_PATH${NC}"
        exit 1
    fi
    echo -e "${GREEN}✓ Post-Tauri Developer ID signing complete (notarization-ready)${NC}"
fi

# Copy app to easy location. ditto preserves all metadata (extended
# attributes, code signatures, symlinks) which a plain `cp -r` may not.
if [ -d "$APP_PATH" ]; then
    rm -rf "./LocalBook.app"
    ditto "$APP_PATH" "./LocalBook.app"
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
