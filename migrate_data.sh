#!/bin/bash
# LocalBook Data Migration Script
# Run this BEFORE upgrading from v0.1.x to v0.2.x+
# This copies your data from inside the app bundle to the safe external location

set -e

OLD_DATA="/Applications/LocalBook.app/Contents/Resources/resources/backend/localbook-backend/_internal/data"
ALT_OLD_DATA="/Applications/LocalBook.app/Contents/Resources/resources/backend/data"
NEW_DATA="$HOME/Library/Application Support/LocalBook"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "           LocalBook Data Migration Tool"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Find old data location
if [ -d "$OLD_DATA" ] && [ "$(ls -A "$OLD_DATA" 2>/dev/null)" ]; then
    SOURCE_DATA="$OLD_DATA"
elif [ -d "$ALT_OLD_DATA" ] && [ "$(ls -A "$ALT_OLD_DATA" 2>/dev/null)" ]; then
    SOURCE_DATA="$ALT_OLD_DATA"
else
    echo ""
    echo "❌ No data found in the old app bundle location."
    echo ""
    echo "This could mean:"
    echo "  1. You're already on v0.2.x+ (data is in Application Support)"
    echo "  2. LocalBook.app is not installed in /Applications"
    echo "  3. No notebooks have been created yet"
    echo ""
    
    if [ -d "$NEW_DATA" ] && [ "$(ls -A "$NEW_DATA" 2>/dev/null)" ]; then
        echo "✅ Data already exists in: $NEW_DATA"
        echo "   You're good to upgrade!"
    fi
    exit 0
fi

echo ""
echo "Found data at: $SOURCE_DATA"
echo "Will copy to:  $NEW_DATA"
echo ""

# Check if destination already has data
if [ -d "$NEW_DATA" ] && [ "$(ls -A "$NEW_DATA" 2>/dev/null)" ]; then
    echo "⚠️  Warning: Destination already has data!"
    echo "   Existing data will NOT be overwritten."
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Aborted."
        exit 1
    fi
fi

# Create destination
mkdir -p "$NEW_DATA"

# Copy data
echo "Copying data..."
cp -Rn "$SOURCE_DATA"/* "$NEW_DATA/" 2>/dev/null || true

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ Migration complete!"
echo ""
echo "Your data is now safely stored at:"
echo "   $NEW_DATA"
echo ""
echo "You can now safely replace LocalBook.app with the new version."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
