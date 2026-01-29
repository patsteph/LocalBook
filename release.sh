#!/bin/bash

# LocalBook Release Script
# Usage: ./release.sh [version]
# Example: ./release.sh 0.6.6

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
echo -e "${BLUE}              LocalBook Release Pipeline                     ${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

# =============================================================================
# Step 0: Version Management
# =============================================================================
CURRENT_VERSION=$(grep '"version"' package.json | head -1 | sed 's/.*"version": "\([^"]*\)".*/\1/')
echo -e "\n${YELLOW}Current version: ${CURRENT_VERSION}${NC}"

if [ -n "$1" ]; then
    NEW_VERSION="$1"
else
    echo -e "${YELLOW}Enter new version (or press Enter to keep ${CURRENT_VERSION}):${NC}"
    read -r NEW_VERSION
    NEW_VERSION=${NEW_VERSION:-$CURRENT_VERSION}
fi

if [ "$NEW_VERSION" != "$CURRENT_VERSION" ]; then
    echo -e "${YELLOW}Updating version to ${NEW_VERSION}...${NC}"
    
    # Update package.json
    sed -i '' "s/\"version\": \"${CURRENT_VERSION}\"/\"version\": \"${NEW_VERSION}\"/" package.json
    
    # Update Cargo.toml
    sed -i '' "s/^version = \"${CURRENT_VERSION}\"/version = \"${NEW_VERSION}\"/" src-tauri/Cargo.toml
    
    # Update tauri.conf.json
    sed -i '' "s/\"version\": \"${CURRENT_VERSION}\"/\"version\": \"${NEW_VERSION}\"/" src-tauri/tauri.conf.json
    
    # Update extension version to match app version
    EXT_CURRENT=$(grep '"version"' extension/package.json | head -1 | sed 's/.*"version": "\([^"]*\)".*/\1/')
    sed -i '' "s/\"version\": \"${EXT_CURRENT}\"/\"version\": \"${NEW_VERSION}\"/" extension/package.json

    # Update backend version (single source of truth for backend-reported app version)
    sed -i '' "s/^APP_VERSION = \".*\"/APP_VERSION = \"${NEW_VERSION}\"/" backend/version.py
    
    echo -e "${GREEN}✓ Version updated to ${NEW_VERSION} (app, extension, backend)${NC}"
fi

# =============================================================================
# Step 1: Pre-flight Checks
# =============================================================================
echo -e "\n${YELLOW}Step 1/9: Pre-flight checks...${NC}"

ERRORS=0

# Check for uncommitted changes (warning only)
if [ -n "$(git status --porcelain)" ]; then
    echo -e "${YELLOW}⚠ Warning: You have uncommitted changes${NC}"
fi

# TypeScript compilation check
echo -e "  Checking TypeScript..."
if ! npx tsc --noEmit 2>/dev/null; then
    echo -e "${RED}✗ TypeScript errors found${NC}"
    ERRORS=$((ERRORS + 1))
else
    echo -e "${GREEN}  ✓ TypeScript OK${NC}"
fi

# Python syntax check (all .py files in backend)
echo -e "  Checking Python syntax..."
PYTHON_ERRORS=0
for file in $(find backend -name "*.py" -not -path "*/\.*" -not -path "*/.venv/*"); do
    if ! python3 -m py_compile "$file" 2>/dev/null; then
        echo -e "${RED}    ✗ Syntax error in: $file${NC}"
        PYTHON_ERRORS=$((PYTHON_ERRORS + 1))
    fi
done

if [ $PYTHON_ERRORS -eq 0 ]; then
    echo -e "${GREEN}  ✓ Python syntax OK${NC}"
else
    ERRORS=$((ERRORS + PYTHON_ERRORS))
fi

# PyInstaller hidden imports check
echo -e "  Checking PyInstaller imports..."
if python3 scripts/check_pyinstaller_hidden_imports.py 2>/dev/null; then
    echo -e "${GREEN}  ✓ PyInstaller imports OK${NC}"
else
    echo -e "${RED}✗ PyInstaller import check failed${NC}"
    ERRORS=$((ERRORS + 1))
fi

# Check Ollama is available
echo -e "  Checking Ollama..."
if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo -e "${GREEN}  ✓ Ollama running${NC}"
else
    echo -e "${YELLOW}  ⚠ Ollama not running (will start during build)${NC}"
fi

if [ $ERRORS -gt 0 ]; then
    echo -e "\n${RED}Pre-flight checks failed with ${ERRORS} error(s). Fix issues before release.${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Pre-flight checks passed${NC}"

# =============================================================================
# Step 2: Lock Dependencies
# =============================================================================
echo -e "\n${YELLOW}Step 2/9: Locking Python dependencies...${NC}"

cd backend
source .venv/bin/activate

# Check if pip-tools is installed
if ! command -v pip-compile &> /dev/null; then
    echo -e "  Installing pip-tools..."
    pip install pip-tools --quiet
fi

# Try pip-compile first (cleaner output with dependency provenance)
# Fall back to pip freeze if pip-compile fails due to transitive conflicts
echo -e "  Running pip-compile..."
if pip-compile requirements.in -o requirements.txt --quiet 2>/dev/null; then
    echo -e "${GREEN}  ✓ requirements.txt updated (pip-compile)${NC}"
else
    echo -e "${YELLOW}  ⚠ pip-compile failed (transitive conflicts), using pip freeze...${NC}"
    pip freeze > requirements.txt
    echo -e "${GREEN}  ✓ requirements.txt updated (pip freeze)${NC}"
fi

# Check if requirements.txt changed
if [ -n "$(git status --porcelain requirements.txt 2>/dev/null)" ]; then
    echo -e "${YELLOW}  ⚠ requirements.txt was updated - review changes${NC}"
fi

deactivate
cd ..

# =============================================================================
# Step 3: Run Workload Tests
# =============================================================================
echo -e "\n${YELLOW}Step 3/9: Running workload tests...${NC}"

cd backend
source .venv/bin/activate

# Run the comprehensive workload tests
if [ -f "scripts/local/test_all_workloads.py" ]; then
    echo -e "  Testing all workloads (PDF, embeddings, RAG, etc.)..."
    if python scripts/local/test_all_workloads.py 2>&1 | tail -5; then
        echo -e "${GREEN}  ✓ Workload tests passed${NC}"
    else
        echo -e "${RED}✗ Workload tests failed${NC}"
        deactivate
        cd ..
        exit 1
    fi
else
    echo -e "${YELLOW}  ⚠ Workload tests not found, skipping${NC}"
fi

deactivate
cd ..

# =============================================================================
# Step 4: Clean Build
# =============================================================================
echo -e "\n${YELLOW}Step 4/9: Building application...${NC}"

./build.sh --rebuild

if [ ! -f "./LocalBook.app/Contents/MacOS/localbooklm" ]; then
    echo -e "${RED}✗ Build failed - no app bundle found${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Build complete${NC}"

# =============================================================================
# Step 5: Build Browser Extension
# =============================================================================
echo -e "\n${YELLOW}Step 5/9: Building browser extension...${NC}"

cd extension
npm install --silent
npm run build

if [ -d "build/LocalBook-Extension" ]; then
    echo -e "${GREEN}✓ Browser extension built${NC}"
else
    echo -e "${RED}✗ Browser extension build failed${NC}"
    exit 1
fi
cd ..

# =============================================================================
# Step 6: Bundle Verification Tests
# =============================================================================
echo -e "\n${YELLOW}Step 6/9: Running bundle verification tests...${NC}"

# Test that app bundle has correct structure
echo -e "  Verifying app bundle structure..."
BUNDLE_OK=true
[ -f "./LocalBook.app/Contents/MacOS/localbooklm" ] || BUNDLE_OK=false
[ -d "./LocalBook.app/Contents/Resources/resources/backend" ] || BUNDLE_OK=false

if [ "$BUNDLE_OK" = true ]; then
    echo -e "${GREEN}  ✓ App bundle structure OK${NC}"
else
    echo -e "${RED}  ✗ App bundle structure incomplete${NC}"
    exit 1
fi

# Test extension build
echo -e "  Verifying extension build..."
if [ -d "extension/build/LocalBook-Extension" ]; then
    EXT_FILES=$(ls extension/build/LocalBook-Extension/*.js 2>/dev/null | wc -l)
    if [ "$EXT_FILES" -gt 0 ]; then
        echo -e "${GREEN}  ✓ Extension build OK (${EXT_FILES} JS files)${NC}"
    else
        echo -e "${RED}  ✗ Extension build incomplete - no JS files${NC}"
        exit 1
    fi
else
    echo -e "${RED}  ✗ Extension build directory missing${NC}"
    exit 1
fi

# Run bundle API tests (requires app to be running)
echo -e "  Running bundle API tests..."
echo -e "${YELLOW}  Starting LocalBook.app for testing...${NC}"
open ./LocalBook.app
sleep 8  # Wait for app to start

cd backend
source .venv/bin/activate

if [ -f "scripts/local/test_bundle.py" ]; then
    if python scripts/local/test_bundle.py 2>&1 | grep -E "^\\[|Passed:|Failed:|BUNDLE"; then
        # Check if tests passed
        if python scripts/local/test_bundle.py 2>&1 | grep -q "Bundle verification passed"; then
            echo -e "${GREEN}  ✓ Bundle API tests passed${NC}"
        else
            echo -e "${RED}  ✗ Bundle API tests failed${NC}"
            deactivate
            cd ..
            # Close the app
            osascript -e 'quit app "LocalBook"' 2>/dev/null || true
            exit 1
        fi
    fi
else
    echo -e "${YELLOW}  ⚠ Bundle tests not found, skipping${NC}"
fi

deactivate
cd ..

# Close the test app
osascript -e 'quit app "LocalBook"' 2>/dev/null || true
sleep 2

echo -e "${GREEN}✓ Bundle verification passed${NC}"

# =============================================================================
# Step 7: Create Release Archive
# =============================================================================
echo -e "\n${YELLOW}Step 7/9: Creating release archive...${NC}"

ARCHIVE_NAME="LocalBook-v${NEW_VERSION}.zip"
EXTENSION_ARCHIVE="LocalBook-Extension-v${NEW_VERSION}.zip"

# Remove old archives if exist
rm -f "$ARCHIVE_NAME" "$EXTENSION_ARCHIVE"

# Create main app zip
ditto -c -k --sequesterRsrc --keepParent "./LocalBook.app" "$ARCHIVE_NAME"
ARCHIVE_SIZE=$(du -h "$ARCHIVE_NAME" | cut -f1)
echo -e "${GREEN}✓ Created ${ARCHIVE_NAME} (${ARCHIVE_SIZE})${NC}"

# Create browser extension zip
cd extension/build
ditto -c -k --sequesterRsrc "LocalBook-Extension" "../../$EXTENSION_ARCHIVE"
cd ../..
EXT_SIZE=$(du -h "$EXTENSION_ARCHIVE" | cut -f1)
echo -e "${GREEN}✓ Created ${EXTENSION_ARCHIVE} (${EXT_SIZE})${NC}"

# =============================================================================
# Step 8: Git Operations
# =============================================================================
echo -e "\n${YELLOW}Step 8/9: Git operations...${NC}"

# Commit version changes if any
if [ -n "$(git status --porcelain package.json src-tauri/Cargo.toml src-tauri/tauri.conf.json extension/package.json backend/version.py 2>/dev/null)" ]; then
    echo -e "  Committing version bump..."
    git add package.json src-tauri/Cargo.toml src-tauri/tauri.conf.json extension/package.json backend/version.py
    git commit -m "chore: bump version to ${NEW_VERSION}"
    echo -e "${GREEN}  ✓ Version bump committed${NC}"
fi

echo -e "\n${YELLOW}Create git tag v${NEW_VERSION}? (y/n)${NC}"
read -r CREATE_TAG
if [ "$CREATE_TAG" = "y" ]; then
    git tag -a "v${NEW_VERSION}" -m "Release v${NEW_VERSION}"
    echo -e "${GREEN}  ✓ Tag v${NEW_VERSION} created${NC}"
fi

echo -e "${GREEN}✓ Git operations complete${NC}"

# =============================================================================
# Step 9: Summary
# =============================================================================
echo -e "\n${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}              Release v${NEW_VERSION} Ready!                  ${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e ""
echo -e "${BLUE}Files ready for upload:${NC}"
echo -e "  📦 ${ARCHIVE_NAME} (main app)"
echo -e "  🧩 ${EXTENSION_ARCHIVE} (browser extension)"
echo -e ""
echo -e "${BLUE}Next steps:${NC}"
echo -e "  1. Push to GitHub:  ${YELLOW}git push origin main --tags${NC}"
echo -e "  2. Create GitHub Release at: ${YELLOW}https://github.com/patsteph/LocalBook/releases/new${NC}"
echo -e "  3. Upload both archives to the release"
echo -e ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
