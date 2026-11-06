#!/usr/bin/env bash
# Build LocalBook.mcpb, the Claude Desktop extension, into mcp-bridge/dist/.
# Usage: ./mcp-bridge/build.sh        then double-click dist/LocalBook.mcpb
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
out="$here/dist"
rm -rf "$out" && mkdir -p "$out/stage/server"
cp "$here/manifest.json" "$out/stage/"
cp "$here/server/index.js" "$out/stage/server/"
cp "$here/../src-tauri/icons/128x128.png" "$out/stage/icon.png"
# Keep the extension's version in step with the app's.
version="$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$here/../package.json" | head -1)"
if [ -n "$version" ]; then
  sed -i.bak "s/\"version\": *\"[^\"]*\"/\"version\": \"$version\"/" "$out/stage/manifest.json"
  rm -f "$out/stage/manifest.json.bak"
fi
node -e "JSON.parse(require('fs').readFileSync('$out/stage/manifest.json','utf8'))"
(cd "$out/stage" && zip -qr "$out/LocalBook.mcpb" .)
rm -rf "$out/stage"
echo "Built $out/LocalBook.mcpb"
