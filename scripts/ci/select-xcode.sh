#!/bin/bash
# Pick the newest stable Xcode of the wanted major version on a GitHub macOS runner; fall back to the default.
# Usage: scripts/ci/select-xcode.sh 27
set -euo pipefail
want="${1:-27}"
echo "Xcodes on this runner:"
ls -d /Applications/Xcode*.app 2>/dev/null || true
pick="$(ls -d /Applications/Xcode_${want}*.app 2>/dev/null | grep -vi beta | sort -V | tail -1 || true)"
if [ -n "$pick" ]; then
  echo "Selecting $pick"
  sudo xcode-select -s "$pick"
else
  echo "No stable Xcode ${want}.x found; keeping the default"
fi
xcodebuild -version
swift --version
