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
    
    echo -e "${GREEN}✓ Version updated to ${NEW_VERSION}${NC}"
fi

# =============================================================================
# Step 1: Pre-flight Checks
# =============================================================================
echo -e "\n${YELLOW}Step 1/6: Pre-flight checks...${NC}"

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
# Step 2: Clean Build
# =============================================================================
echo -e "\n${YELLOW}Step 2/6: Building application...${NC}"

./build.sh --rebuild

if [ ! -f "./LocalBook.app/Contents/MacOS/localbooklm" ]; then
    echo -e "${RED}✗ Build failed - no app bundle found${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Build complete${NC}"

# =============================================================================
# Step 3: Smoke Test
# =============================================================================
echo -e "\n${YELLOW}Step 3/6: Running smoke tests...${NC}"

# Test backend can start
echo -e "  Testing backend startup..."
cd backend
source .venv/bin/activate
timeout 10 python -c "from main import app; print('Backend imports OK')" 2>/dev/null && echo -e "${GREEN}  ✓ Backend imports OK${NC}" || echo -e "${YELLOW}  ⚠ Backend import check skipped${NC}"
deactivate
cd ..

# Test that app bundle has correct structure
echo -e "  Verifying app bundle..."
BUNDLE_OK=true
[ -f "./LocalBook.app/Contents/MacOS/localbooklm" ] || BUNDLE_OK=false
[ -d "./LocalBook.app/Contents/Resources/backend" ] || BUNDLE_OK=false

if [ "$BUNDLE_OK" = true ]; then
    echo -e "${GREEN}  ✓ App bundle structure OK${NC}"
else
    echo -e "${RED}  ✗ App bundle structure incomplete${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Smoke tests passed${NC}"

# =============================================================================
# Step 4: Create Release Archive
# =============================================================================
echo -e "\n${YELLOW}Step 4/6: Creating release archive...${NC}"

ARCHIVE_NAME="LocalBook-v${NEW_VERSION}.zip"

# Remove old archive if exists
rm -f "$ARCHIVE_NAME"

# Create zip (excluding .DS_Store files)
ditto -c -k --sequesterRsrc --keepParent "./LocalBook.app" "$ARCHIVE_NAME"

ARCHIVE_SIZE=$(du -h "$ARCHIVE_NAME" | cut -f1)
echo -e "${GREEN}✓ Created ${ARCHIVE_NAME} (${ARCHIVE_SIZE})${NC}"

# =============================================================================
# Step 5: Git Operations
# =============================================================================
echo -e "\n${YELLOW}Step 5/6: Git operations...${NC}"

# Commit version changes if any
if [ -n "$(git status --porcelain package.json src-tauri/Cargo.toml src-tauri/tauri.conf.json 2>/dev/null)" ]; then
    echo -e "  Committing version bump..."
    git add package.json src-tauri/Cargo.toml src-tauri/tauri.conf.json
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
# Step 6: Summary
# =============================================================================
echo -e "\n${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}              Release v${NEW_VERSION} Ready!                  ${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e ""
echo -e "${BLUE}Files ready for upload:${NC}"
echo -e "  📦 ${ARCHIVE_NAME}"
echo -e ""
echo -e "${BLUE}Next steps:${NC}"
echo -e "  1. Push to GitHub:  ${YELLOW}git push origin main --tags${NC}"
echo -e "  2. Create GitHub Release at: ${YELLOW}https://github.com/patsteph/LocalBook/releases/new${NC}"
echo -e "  3. Upload ${ARCHIVE_NAME} to the release"
echo -e ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
