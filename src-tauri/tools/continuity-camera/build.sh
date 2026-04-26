#!/usr/bin/env bash
# Build the Continuity Camera helper as a universal (arm64 + x86_64)
# binary suitable for bundling as a Tauri sidecar.
#
# Dual-mode signing:
#   Developer ID mode:
#     Set DEVELOPER_ID_APPLICATION (or APPLE_SIGNING_IDENTITY) to the full
#     "Developer ID Application: Your Name (TEAMID)" string. The binary is
#     hardened-runtime signed with that identity and the camera entitlement
#     so it is suitable for notarization + distribution.
#
#   Adhoc / local-dev mode (default when no identity is provided):
#     Signed with "-" (adhoc). Entitlements are still embedded, which is
#     sufficient for the binary to run on the machine it was built on with
#     the standard TCC camera prompt. NOT suitable for distribution.
#
# Outputs:
#   ./dist/continuity-camera                              (universal)
#   ./dist/continuity-camera-aarch64-apple-darwin         (Tauri naming)
#   ./dist/continuity-camera-x86_64-apple-darwin          (Tauri naming)
#   ../../binaries/continuity-camera-*-apple-darwin       (what Tauri bundles)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Accept either DEVELOPER_ID_APPLICATION (legacy) or APPLE_SIGNING_IDENTITY
# (new convention shared with backend + release.sh). If neither is set, we
# fall back to adhoc signing — intentional, so end-user builds via install.sh
# succeed without a Developer Program membership.
SIGN_IDENTITY="${DEVELOPER_ID_APPLICATION:-${APPLE_SIGNING_IDENTITY:-}}"

SRC="ContinuityCamera.swift"
ENT="ContinuityCamera.entitlements"
PLIST="ContinuityCamera-Info.plist"
OUT_DIR="dist"
mkdir -p "$OUT_DIR"

# CLI binaries don't ship a real Info.plist by default, but TCC + Continuity
# Camera both consult one. We embed the plist into the __TEXT,__info_plist
# section via a linker flag so the binary self-describes its bundle id,
# camera usage string, and (critically on macOS 14+) the
# NSCameraUseContinuityCameraDeviceType opt-in.
PLIST_FLAGS=(-Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker "$PLIST")

echo "[build] Compiling arm64…"
swiftc -target arm64-apple-macos14.0 -O "${PLIST_FLAGS[@]}" "$SRC" -o "$OUT_DIR/cc-arm64"

echo "[build] Compiling x86_64…"
swiftc -target x86_64-apple-macos14.0 -O "${PLIST_FLAGS[@]}" "$SRC" -o "$OUT_DIR/cc-x64"

echo "[build] Creating universal binary…"
lipo -create "$OUT_DIR/cc-arm64" "$OUT_DIR/cc-x64" -output "$OUT_DIR/continuity-camera"
rm -f "$OUT_DIR/cc-arm64" "$OUT_DIR/cc-x64"

if [[ -n "$SIGN_IDENTITY" ]]; then
    echo "[build] Signing with Developer ID: $SIGN_IDENTITY"
    codesign --force --options runtime \
        --entitlements "$ENT" \
        --sign "$SIGN_IDENTITY" \
        --timestamp \
        "$OUT_DIR/continuity-camera"
else
    echo "[build] No signing identity provided — adhoc signing for local use."
    echo "        (Distribution requires DEVELOPER_ID_APPLICATION or APPLE_SIGNING_IDENTITY.)"
    # Adhoc signing still embeds entitlements; the binary runs locally and
    # triggers the standard TCC camera prompt on first use.
    codesign --force \
        --entitlements "$ENT" \
        --sign - \
        "$OUT_DIR/continuity-camera"
fi

echo "[build] Verifying signature…"
codesign -dvvv "$OUT_DIR/continuity-camera" 2>&1 \
    | grep -E "(Authority|TeamIdentifier|Signature|Timestamp)" || true

# Tauri expects sidecars named <name>-<target-triple>. Since we produced a
# universal binary, hardlink it under both triples so either build target
# picks it up without any per-arch build step.
ln -f "$OUT_DIR/continuity-camera" "$OUT_DIR/continuity-camera-aarch64-apple-darwin"
ln -f "$OUT_DIR/continuity-camera" "$OUT_DIR/continuity-camera-x86_64-apple-darwin"

# Copy into src-tauri/binaries/ (the location referenced by tauri.conf.json's
# externalBin entry). Directory is gitignored — regenerated every build.
BIN_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)/binaries"
mkdir -p "$BIN_DIR"
cp -f "$OUT_DIR/continuity-camera-aarch64-apple-darwin" "$BIN_DIR/"
cp -f "$OUT_DIR/continuity-camera-x86_64-apple-darwin"  "$BIN_DIR/"

echo "[build] Done."
echo "       Sidecar installed at: $BIN_DIR/continuity-camera-*-apple-darwin"
