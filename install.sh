#!/bin/bash
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LocalBook Installer
# One-command installation for macOS
#
# Fresh install:
#   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/patsteph/LocalBook/master/install.sh)"
#
# Upgrade existing install:
#   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/patsteph/LocalBook/master/install.sh)" -- --upgrade
#
# Requirements: macOS 12.0+, Apple Silicon or Intel Mac, ~20GB free disk space
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

main() {
    set -euo pipefail

    # ── Constants ────────────────────────────────────────────────────────────
    readonly REPO_URL="https://github.com/patsteph/LocalBook.git"
    readonly REPO_BRANCH="master"
    readonly INSTALL_CONFIG="$HOME/.localbook"
    readonly DEFAULT_INSTALL_DIR="$HOME/LocalBook"
    readonly APP_BUNDLE="LocalBook.app"
    readonly DATA_DIR="$HOME/Library/Application Support/LocalBook"
    readonly MIN_MACOS_MAJOR=12
    readonly MIN_DISK_GB=20

    # ── Colors ───────────────────────────────────────────────────────────────
    readonly RED='\033[0;31m'
    readonly GREEN='\033[0;32m'
    readonly YELLOW='\033[1;33m'
    readonly BLUE='\033[0;34m'
    readonly CYAN='\033[0;36m'
    readonly BOLD='\033[1m'
    readonly DIM='\033[2m'
    readonly NC='\033[0m'

    # ── State ────────────────────────────────────────────────────────────────
    INSTALL_DIR=""
    PYTHON_CMD=""
    TOTAL_STEPS=9
    CURRENT_STEP=0
    START_TIME=$(date +%s)

    # ── Parse Arguments ──────────────────────────────────────────────────────
    UPGRADE_MODE=false
    for arg in "$@"; do
        case "$arg" in
            --upgrade|-u) UPGRADE_MODE=true ;;
        esac
    done

    # ── Error Handler ────────────────────────────────────────────────────────
    on_error() {
        local exit_code=$?
        echo ""
        echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "${RED}  Installation failed (exit code: $exit_code)${NC}"
        echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo ""
        echo -e "  ${YELLOW}You can safely re-run this script to resume.${NC}"
        if [ -n "$INSTALL_DIR" ]; then
            echo -e "  ${YELLOW}Source directory: ${INSTALL_DIR}${NC}"
        fi
        echo ""
        exit 1
    }
    trap 'on_error' ERR

    # ═══════════════════════════════════════════════════════════════════════
    # UI HELPERS
    # ═══════════════════════════════════════════════════════════════════════

    print_banner() {
        echo ""
        echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "${BLUE}${BOLD}              LocalBook Installer                          ${NC}"
        echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "  ${DIM}Privacy-focused document analysis powered by local AI${NC}"
        echo ""
    }

    step() {
        CURRENT_STEP=$1
        local msg="$2"
        echo ""
        echo -e "${BLUE}[Step ${CURRENT_STEP}/${TOTAL_STEPS}]${NC} ${BOLD}${msg}${NC}"
    }

    info() {
        echo -e "  ${CYAN}i${NC}  $*"
    }

    success() {
        echo -e "  ${GREEN}✓${NC}  $*"
    }

    warn() {
        echo -e "  ${YELLOW}!${NC}  $*"
    }

    fail() {
        echo -e "  ${RED}✗${NC}  $*"
    }

    # Read user input — uses /dev/tty for curl|bash compatibility
    ask_yn() {
        local prompt="$1"
        local answer
        printf "  ${CYAN}?${NC}  %s " "$prompt" >&2
        read -r answer < /dev/tty
        [[ "$answer" =~ ^[Yy] ]]
    }

    ask_input() {
        local prompt="$1"
        local default="$2"
        local answer
        if [ -n "$default" ]; then
            printf "  ${CYAN}?${NC}  %s [%s]: " "$prompt" "$default" >&2
        else
            printf "  ${CYAN}?${NC}  %s: " "$prompt" >&2
        fi
        read -r answer < /dev/tty
        echo "${answer:-$default}"
    }

    elapsed_time() {
        local end_time=$(date +%s)
        local elapsed=$((end_time - START_TIME))
        local minutes=$((elapsed / 60))
        local seconds=$((elapsed % 60))
        if [ $minutes -gt 0 ]; then
            echo "${minutes}m ${seconds}s"
        else
            echo "${seconds}s"
        fi
    }

    # ═══════════════════════════════════════════════════════════════════════
    # SYSTEM CHECKS
    # ═══════════════════════════════════════════════════════════════════════

    check_macos_version() {
        local macos_ver
        macos_ver=$(sw_vers -productVersion 2>/dev/null || echo "0.0")
        local major
        major=$(echo "$macos_ver" | cut -d. -f1)

        if [ "$major" -lt "$MIN_MACOS_MAJOR" ]; then
            fail "macOS $MIN_MACOS_MAJOR.0+ required (found $macos_ver)"
            exit 1
        fi
        success "macOS $macos_ver"
    }

    check_disk_space() {
        local available_gb
        available_gb=$(df -g "$HOME" | awk 'NR==2 {print $4}')

        if [ "$available_gb" -lt "$MIN_DISK_GB" ]; then
            fail "At least ${MIN_DISK_GB}GB free disk space required (found ${available_gb}GB)"
            exit 1
        fi
        success "Disk space: ${available_gb}GB available"
    }

    check_architecture() {
        local arch
        arch=$(uname -m)
        if [ "$arch" = "arm64" ]; then
            success "Apple Silicon detected"
        else
            success "Intel Mac detected"
        fi
    }

    # ═══════════════════════════════════════════════════════════════════════
    # PREREQUISITE INSTALLERS
    # ═══════════════════════════════════════════════════════════════════════

    ensure_homebrew() {
        if command -v brew &>/dev/null; then
            success "Homebrew"
            return
        fi
        info "Installing Homebrew (you may be prompted for your password)..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" < /dev/tty

        # Add to PATH for Apple Silicon
        if [ -f "/opt/homebrew/bin/brew" ]; then
            eval "$(/opt/homebrew/bin/brew shellenv)"
        fi

        if command -v brew &>/dev/null; then
            success "Homebrew installed"
        else
            fail "Homebrew installation failed"
            exit 1
        fi
    }

    ensure_xcode_clt() {
        if xcode-select -p &>/dev/null; then
            success "Xcode Command Line Tools"
            return
        fi
        info "Installing Xcode Command Line Tools..."
        info "A system dialog may appear — click 'Install' and wait for it to complete."
        xcode-select --install 2>/dev/null || true
        echo ""
        info "Press Enter after the Xcode installation completes..."
        read -r < /dev/tty
        if xcode-select -p &>/dev/null; then
            success "Xcode Command Line Tools installed"
        else
            fail "Xcode Command Line Tools installation failed"
            exit 1
        fi
    }

    find_python() {
        PYTHON_CMD=""
        if command -v python3.13 &>/dev/null; then
            PYTHON_CMD="python3.13"
        elif command -v python3.12 &>/dev/null; then
            PYTHON_CMD="python3.12"
        elif [ -f "/opt/homebrew/bin/python3.12" ]; then
            PYTHON_CMD="/opt/homebrew/bin/python3.12"
        elif [ -f "/usr/local/bin/python3.12" ]; then
            PYTHON_CMD="/usr/local/bin/python3.12"
        elif command -v python3 &>/dev/null; then
            local py_minor
            py_minor=$(python3 -c "import sys; print(sys.version_info.minor)" 2>/dev/null || echo "0")
            if [ "$py_minor" -ge 12 ]; then
                PYTHON_CMD="python3"
            fi
        fi
    }

    ensure_python() {
        find_python
        if [ -n "$PYTHON_CMD" ]; then
            local ver
            ver=$($PYTHON_CMD --version 2>&1)
            success "Python: $ver"
            return
        fi
        info "Installing Python 3.12..."
        brew install python@3.12
        find_python
        if [ -n "$PYTHON_CMD" ]; then
            success "Python 3.12 installed"
        else
            fail "Python 3.12+ installation failed"
            exit 1
        fi
    }

    ensure_node() {
        if command -v node &>/dev/null; then
            local ver
            ver=$(node --version 2>&1)
            success "Node.js: $ver"
            return
        fi
        info "Installing Node.js..."
        brew install node
        success "Node.js installed"
    }

    ensure_rust() {
        if command -v cargo &>/dev/null; then
            local ver
            ver=$(rustc --version 2>&1 | awk '{print $2}')
            success "Rust: $ver"
            return
        fi
        info "Installing Rust (non-interactive)..."
        curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
        # shellcheck disable=SC1091
        source "$HOME/.cargo/env" 2>/dev/null || true
        if command -v cargo &>/dev/null; then
            success "Rust installed"
        else
            fail "Rust installation failed"
            exit 1
        fi
    }

    ensure_ollama() {
        if command -v ollama &>/dev/null; then
            success "Ollama"
            return
        fi
        info "Installing Ollama..."
        brew install ollama
        if command -v ollama &>/dev/null; then
            success "Ollama installed"
        else
            fail "Ollama installation failed"
            exit 1
        fi
    }

    ensure_brew_pkg() {
        local cmd="$1"
        local pkg="$2"
        local label="${3:-$pkg}"
        if command -v "$cmd" &>/dev/null; then
            success "$label"
            return
        fi
        info "Installing $label..."
        brew install "$pkg"
        success "$label installed"
    }

    # ═══════════════════════════════════════════════════════════════════════
    # BUILD FUNCTIONS
    # ═══════════════════════════════════════════════════════════════════════

    clone_repo() {
        step 2 "Downloading LocalBook source"

        if [ -d "$INSTALL_DIR/.git" ]; then
            info "Source directory exists, pulling latest..."
            cd "$INSTALL_DIR"
            git pull origin "$REPO_BRANCH"
        else
            info "Cloning repository..."
            git clone --branch "$REPO_BRANCH" "$REPO_URL" "$INSTALL_DIR"
            cd "$INSTALL_DIR"
        fi

        # Read version from source
        local version
        version=$(grep '"version"' package.json | head -1 | sed 's/.*"version": "\([^"]*\)".*/\1/')
        success "Source downloaded (v${version})"
    }

    build_backend() {
        step 3 "Building backend"
        info "This packages the Python backend with all dependencies"
        info "First-time build takes ~10 minutes"

        cd "$INSTALL_DIR/backend"

        # Create virtual environment
        if [ ! -d ".venv" ]; then
            info "Creating Python virtual environment..."
            $PYTHON_CMD -m venv .venv
        fi

        # shellcheck disable=SC1091
        source .venv/bin/activate

        # Install Python dependencies
        info "Installing Python dependencies..."
        info "(This is the longest sub-step — may take 5-10 minutes)"
        pip install --upgrade pip -q 2>/dev/null || true
        if ! pip install -r requirements.txt; then
            fail "Python dependency installation failed"
            deactivate
            exit 1
        fi

        # Verify critical packages
        local missing=""
        python -c "import rank_bm25" 2>/dev/null || missing="$missing rank-bm25"
        python -c "import ebooklib" 2>/dev/null || missing="$missing ebooklib"
        python -c "import odf" 2>/dev/null || missing="$missing odfpy"
        python -c "import nbformat" 2>/dev/null || missing="$missing nbformat"
        python -c "import misaki" 2>/dev/null || missing="$missing misaki"
        python -c "import soundfile" 2>/dev/null || missing="$missing soundfile"

        if [ -n "$missing" ]; then
            info "Installing missing packages:$missing"
            # shellcheck disable=SC2086
            pip install $missing
        fi

        # Build the backend binary (PyInstaller)
        info "Building backend binary (PyInstaller)..."
        ./build_backend.sh

        deactivate
        cd "$INSTALL_DIR"

        success "Backend built"
    }

    build_tauri_app() {
        step 4 "Building application"
        info "Compiling the native macOS app"
        info "First-time Rust compilation takes 10-20 minutes"

        cd "$INSTALL_DIR"

        # Install frontend dependencies
        info "Installing frontend dependencies..."
        npm install --silent

        # Build Tauri app (includes Vite frontend build)
        info "Building Tauri application..."
        rm -rf dist/
        npm run tauri build

        # Verify build succeeded
        local app_path="src-tauri/target/release/bundle/macos/$APP_BUNDLE"
        if [ -d "$app_path" ]; then
            rm -rf "./$APP_BUNDLE"
            cp -r "$app_path" "./$APP_BUNDLE"
            success "Application built"
        else
            fail "Application build failed — no app bundle found"
            exit 1
        fi
    }

    build_extension() {
        step 5 "Building browser extension"

        cd "$INSTALL_DIR/extension"
        npm install --silent 2>&1 | tail -1 || true
        npm run build 2>&1 | tail -3 || true
        cd "$INSTALL_DIR"

        if [ -d "$INSTALL_DIR/extension/build/LocalBook-Extension" ]; then
            success "Browser extension built"
        else
            warn "Extension build had issues (non-fatal — app will still work)"
        fi
    }

    download_models() {
        step 6 "Downloading AI models"
        info "This downloads ~7GB of language models via Ollama"
        info "Download speed depends on your internet connection"

        # Start Ollama if not running
        if ! curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
            info "Starting Ollama service..."
            ollama serve >/dev/null 2>&1 &
            local retries=0
            while ! curl -s http://localhost:11434/api/tags >/dev/null 2>&1; do
                sleep 2
                retries=$((retries + 1))
                if [ $retries -gt 15 ]; then
                    fail "Ollama failed to start after 30 seconds"
                    exit 1
                fi
            done
            success "Ollama service started"
        fi

        local models
        models=$(ollama list 2>/dev/null || echo "")

        # Main model (System 2: deep reasoning, chat, synthesis)
        if echo "$models" | grep -q "olmo-3:7b-instruct"; then
            success "olmo-3:7b-instruct (already downloaded)"
        else
            info "Downloading olmo-3:7b-instruct (~4GB) — main reasoning model..."
            ollama pull olmo-3:7b-instruct
            success "olmo-3:7b-instruct downloaded"
        fi

        # Fast model (System 1: quick extraction, classification)
        if echo "$models" | grep -q "phi4-mini"; then
            success "phi4-mini (already downloaded)"
        else
            info "Downloading phi4-mini (~2GB) — fast response model..."
            ollama pull phi4-mini
            success "phi4-mini downloaded"
        fi

        # Embedding model (vector search)
        if echo "$models" | grep -q "snowflake-arctic-embed2"; then
            success "snowflake-arctic-embed2 (already downloaded)"
        else
            info "Downloading snowflake-arctic-embed2 (~500MB) — embedding model..."
            ollama pull snowflake-arctic-embed2
            success "snowflake-arctic-embed2 downloaded"
        fi

        echo ""
        success "All AI models ready"
    }

    setup_storage() {
        step 7 "Setting up data storage"

        if [ -d "$DATA_DIR" ]; then
            # Existing data found — report what's there
            success "Existing data directory found: $DATA_DIR"

            # Check for SQLite database
            if [ -f "$DATA_DIR/localbook.db" ]; then
                local db_size
                db_size=$(du -h "$DATA_DIR/localbook.db" 2>/dev/null | cut -f1 | tr -d ' ')
                success "Existing database found (${db_size})"
            fi

            # Check for LanceDB vectors
            if [ -d "$DATA_DIR/lancedb" ]; then
                local lance_tables
                lance_tables=$(ls -d "$DATA_DIR/lancedb/notebook_"* 2>/dev/null | wc -l | tr -d ' ')
                if [ "$lance_tables" -gt 0 ]; then
                    success "Existing vector data found (${lance_tables} notebook(s))"
                fi
            fi

            info "Your existing data will be preserved and available on launch."
        else
            mkdir -p "$DATA_DIR"
            mkdir -p "$DATA_DIR/lancedb"
            success "Created data directory: $DATA_DIR"
        fi
    }

    install_to_applications() {
        step 8 "Installing to Applications"

        local src="$INSTALL_DIR/$APP_BUNDLE"

        if [ ! -d "$src" ]; then
            fail "App bundle not found at $src"
            exit 1
        fi

        # Close the app if it's running
        if pgrep -f "LocalBook" >/dev/null 2>&1; then
            info "Closing running LocalBook instance..."
            osascript -e 'quit app "LocalBook"' 2>/dev/null || true
            sleep 2
        fi

        # Remove previous version if exists
        if [ -d "/Applications/$APP_BUNDLE" ]; then
            info "Removing previous version from /Applications..."
            rm -rf "/Applications/$APP_BUNDLE"
        fi

        info "Copying to /Applications..."
        cp -r "$src" "/Applications/$APP_BUNDLE"

        # Clear quarantine attribute (macOS Gatekeeper)
        info "Clearing quarantine attributes..."
        xattr -cr "/Applications/$APP_BUNDLE" 2>/dev/null || true

        # Save install configuration for future upgrades
        echo "$INSTALL_DIR" > "$INSTALL_CONFIG"

        success "Installed to /Applications/$APP_BUNDLE"
    }

    # ═══════════════════════════════════════════════════════════════════════
    # SUMMARY & LAUNCH
    # ═══════════════════════════════════════════════════════════════════════

    print_summary() {
        local version
        version=$(grep '"version"' "$INSTALL_DIR/package.json" | head -1 | sed 's/.*"version": "\([^"]*\)".*/\1/')
        local elapsed
        elapsed=$(elapsed_time)

        echo ""
        echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "${GREEN}${BOLD}           Installation Complete!                           ${NC}"
        echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo ""
        echo -e "  ${BOLD}Version:${NC}     v${version}"
        echo -e "  ${BOLD}App:${NC}         /Applications/${APP_BUNDLE}"
        echo -e "  ${BOLD}Data:${NC}        ${DATA_DIR}"
        echo -e "  ${BOLD}Source:${NC}      ${INSTALL_DIR}"
        echo -e "  ${BOLD}Time:${NC}        ${elapsed}"

        # Extension info
        local ext_dir="$INSTALL_DIR/extension/build/LocalBook-Extension"
        if [ -d "$ext_dir" ]; then
            echo ""
            echo -e "  ${BOLD}Browser Extension:${NC}"
            echo -e "  ${ext_dir}"
            echo ""
            echo -e "  ${DIM}To load the extension in Chrome/Brave/Edge:${NC}"
            echo -e "  ${DIM}1. Go to chrome://extensions${NC}"
            echo -e "  ${DIM}2. Enable \"Developer mode\" (top right)${NC}"
            echo -e "  ${DIM}3. Click \"Load unpacked\"${NC}"
            echo -e "  ${DIM}4. Select the directory above${NC}"
        fi

        echo ""
        echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    }

    offer_launch() {
        echo ""
        if ask_yn "Launch LocalBook now? (y/n)"; then
            info "Launching LocalBook..."
            open "/Applications/$APP_BUNDLE"
            echo ""
            info "LocalBook is starting. It may take a moment on first launch."
        else
            echo ""
            info "You can launch LocalBook anytime from /Applications or Spotlight."
        fi
        echo ""
    }

    # ═══════════════════════════════════════════════════════════════════════
    # UPGRADE FLOW
    # ═══════════════════════════════════════════════════════════════════════

    upgrade_flow() {
        # Resolve install directory
        if [ -z "$INSTALL_DIR" ]; then
            if [ -f "$INSTALL_CONFIG" ]; then
                INSTALL_DIR=$(cat "$INSTALL_CONFIG")
            fi
        fi

        if [ -z "$INSTALL_DIR" ] || [ ! -d "$INSTALL_DIR/.git" ]; then
            fail "No existing LocalBook installation found."
            info "Run without --upgrade for a fresh install."
            exit 1
        fi

        cd "$INSTALL_DIR"

        local current_ver
        current_ver=$(grep '"version"' package.json | head -1 | sed 's/.*"version": "\([^"]*\)".*/\1/')

        echo ""
        echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "${BLUE}${BOLD}              LocalBook Upgrade                             ${NC}"
        echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo ""
        info "Install location: ${BOLD}${INSTALL_DIR}${NC}"
        info "Current version:  ${BOLD}v${current_ver}${NC}"

        # Fetch latest from remote
        info "Checking for updates..."
        git fetch origin "$REPO_BRANCH" 2>/dev/null

        local local_rev remote_rev
        local_rev=$(git rev-parse HEAD)
        remote_rev=$(git rev-parse "origin/$REPO_BRANCH")

        if [ "$local_rev" = "$remote_rev" ]; then
            echo ""
            success "Already up to date (v${current_ver})"
            echo ""
            return
        fi

        # Show changes
        local remote_ver
        remote_ver=$(git show "origin/$REPO_BRANCH:package.json" 2>/dev/null | grep '"version"' | head -1 | sed 's/.*"version": "\([^"]*\)".*/\1/')

        echo ""
        info "New version available: ${BOLD}v${remote_ver:-unknown}${NC}"
        echo ""
        echo -e "  ${DIM}Changes:${NC}"
        git --no-pager diff --stat HEAD "origin/$REPO_BRANCH" 2>/dev/null | sed 's/^/    /'
        echo ""

        if ! ask_yn "Upgrade to v${remote_ver:-latest}? (y/n)"; then
            info "Upgrade cancelled."
            return
        fi

        TOTAL_STEPS=6
        START_TIME=$(date +%s)

        echo ""

        # Step 1: Pull changes
        step 1 "Pulling latest changes"

        # Stash any local modifications to prevent merge conflicts
        local stash_result
        stash_result=$(git stash 2>&1)
        if [[ "$stash_result" != *"No local changes"* ]]; then
            info "Stashed local modifications (will restore after upgrade)"
        fi

        git pull origin "$REPO_BRANCH"
        success "Source updated"

        # Restore stashed changes if any
        if [[ "$stash_result" != *"No local changes"* ]]; then
            git stash pop 2>/dev/null || warn "Could not restore local modifications (may need manual merge)"
        fi

        # Step 2: Check prerequisites
        step 2 "Checking prerequisites"
        find_python
        if [ -z "$PYTHON_CMD" ]; then
            ensure_python
        fi
        ensure_brew_pkg "ffmpeg" "ffmpeg"
        ensure_brew_pkg "tesseract" "tesseract"
        ensure_brew_pkg "espeak-ng" "espeak-ng"
        success "Prerequisites OK"

        # Step 3: Rebuild backend
        step 3 "Rebuilding backend"
        cd "$INSTALL_DIR/backend"
        # shellcheck disable=SC1091
        source .venv/bin/activate
        info "Updating Python dependencies..."
        pip install -q -r requirements.txt 2>&1 | tail -3 || true
        info "Building backend binary..."
        ./build_backend.sh
        deactivate
        cd "$INSTALL_DIR"
        success "Backend rebuilt"

        # Step 4: Rebuild app
        step 4 "Rebuilding application"
        cd "$INSTALL_DIR"
        npm install --silent 2>&1 | tail -1 || true
        rm -rf dist/
        info "Building Tauri application (this may take several minutes)..."
        npm run tauri build
        local app_path="src-tauri/target/release/bundle/macos/$APP_BUNDLE"
        if [ -d "$app_path" ]; then
            rm -rf "./$APP_BUNDLE"
            cp -r "$app_path" "./$APP_BUNDLE"
        fi
        success "Application rebuilt"

        # Step 5: Rebuild extension
        step 5 "Rebuilding browser extension"
        cd "$INSTALL_DIR/extension"
        npm install --silent 2>&1 | tail -1 || true
        npm run build 2>&1 | tail -3 || true
        cd "$INSTALL_DIR"
        success "Extension rebuilt"

        # Step 6: Install to Applications
        step 6 "Installing to Applications"

        # Close the app if it's running
        if pgrep -f "LocalBook" >/dev/null 2>&1; then
            info "Closing running LocalBook instance..."
            osascript -e 'quit app "LocalBook"' 2>/dev/null || true
            sleep 2
        fi

        if [ -d "/Applications/$APP_BUNDLE" ]; then
            rm -rf "/Applications/$APP_BUNDLE"
        fi
        cp -r "$INSTALL_DIR/$APP_BUNDLE" "/Applications/$APP_BUNDLE"
        xattr -cr "/Applications/$APP_BUNDLE" 2>/dev/null || true
        success "Installed to /Applications/$APP_BUNDLE"

        # Summary
        local new_ver elapsed
        new_ver=$(grep '"version"' "$INSTALL_DIR/package.json" | head -1 | sed 's/.*"version": "\([^"]*\)".*/\1/')
        elapsed=$(elapsed_time)

        echo ""
        echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "${GREEN}${BOLD}           Upgrade Complete!                                ${NC}"
        echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo ""
        echo -e "  ${BOLD}Version:${NC}  v${current_ver} -> v${new_ver}"
        echo -e "  ${BOLD}Time:${NC}     ${elapsed}"
        echo ""
        echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

        offer_launch
    }

    # ═══════════════════════════════════════════════════════════════════════
    # FRESH INSTALL FLOW
    # ═══════════════════════════════════════════════════════════════════════

    fresh_install() {
        # Time estimate
        info "This will install LocalBook and all dependencies."
        info "Estimated time: 30-60 minutes (first install)"
        info "Disk space needed: ~20GB (source + models + app)"
        echo ""

        # Ask install location
        INSTALL_DIR=$(ask_input "Install location" "$DEFAULT_INSTALL_DIR")

        # Expand ~ if present
        INSTALL_DIR="${INSTALL_DIR/#\~/$HOME}"

        # Validate the chosen path won't clobber something unexpected
        if [ -d "$INSTALL_DIR" ] && [ ! -d "$INSTALL_DIR/.git" ]; then
            local item_count
            item_count=$(ls -A "$INSTALL_DIR" 2>/dev/null | wc -l | tr -d ' ')
            if [ "$item_count" -gt 0 ]; then
                warn "$INSTALL_DIR exists and is not empty."
                if ! ask_yn "Continue anyway? Files may be overwritten. (y/n)"; then
                    info "Installation cancelled."
                    exit 0
                fi
            fi
        fi

        echo ""
        info "Install location: ${BOLD}${INSTALL_DIR}${NC}"
        echo ""

        # ── Step 1: Prerequisites ────────────────────────────────────────
        step 1 "Checking system & installing prerequisites"

        check_macos_version
        check_disk_space
        check_architecture
        echo ""
        ensure_xcode_clt

        # Git comes with Xcode CLT — verify it's available
        if ! command -v git &>/dev/null; then
            fail "git not found even after Xcode CLT install"
            exit 1
        fi
        success "git"

        ensure_homebrew
        ensure_python
        ensure_node
        ensure_rust
        ensure_ollama
        ensure_brew_pkg "ffmpeg" "ffmpeg" "ffmpeg (audio/video processing)"
        ensure_brew_pkg "tesseract" "tesseract" "Tesseract (OCR)"
        ensure_brew_pkg "espeak-ng" "espeak-ng" "espeak-ng (TTS phonemizer)"

        echo ""
        success "All prerequisites installed"

        # ── Step 2: Clone ────────────────────────────────────────────────
        clone_repo

        # ── Step 3: Build Backend ────────────────────────────────────────
        build_backend

        # ── Step 4: Build App ────────────────────────────────────────────
        build_tauri_app

        # ── Step 5: Build Extension ──────────────────────────────────────
        build_extension

        # ── Step 6: Download Models ──────────────────────────────────────
        download_models

        # ── Step 7: Setup Storage ────────────────────────────────────────
        setup_storage

        # ── Step 8: Install to Applications ──────────────────────────────
        install_to_applications

        # ── Step 9: Done ─────────────────────────────────────────────────
        step 9 "Finishing up"
        success "Installation complete"

        # ── Summary ──────────────────────────────────────────────────────
        print_summary
        offer_launch
    }

    # ═══════════════════════════════════════════════════════════════════════
    # ENTRY POINT
    # ═══════════════════════════════════════════════════════════════════════

    print_banner

    # Check for existing install (auto-detect)
    if [ "$UPGRADE_MODE" = false ] && [ -f "$INSTALL_CONFIG" ]; then
        local existing_dir
        existing_dir=$(cat "$INSTALL_CONFIG" 2>/dev/null)
        if [ -n "$existing_dir" ] && [ -d "$existing_dir/.git" ]; then
            info "Existing installation found: ${BOLD}${existing_dir}${NC}"
            echo ""
            if ask_yn "Would you like to upgrade? (y/n)"; then
                UPGRADE_MODE=true
                INSTALL_DIR="$existing_dir"
            fi
        else
            # Stale config — source directory was deleted
            warn "Previous install path (${existing_dir:-unknown}) no longer exists."
            info "Will proceed with a fresh install."
            rm -f "$INSTALL_CONFIG"
        fi
    fi

    if [ "$UPGRADE_MODE" = true ]; then
        upgrade_flow
    else
        fresh_install
    fi
}

# Wrap everything in main() so partial curl downloads don't execute partial scripts
main "$@"
