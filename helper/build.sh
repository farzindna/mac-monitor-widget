#!/bin/zsh
# Builds the Wi-Fi name helper. Rebuilding changes its signature, so macOS may ask for Location permission again.
set -e
here="${0:A:h}"
app="$HOME/Library/Application Support/mac-monitor-widget/WifiName.app"
mkdir -p "$app/Contents/MacOS"
cp "$here/Info.plist" "$app/Contents/Info.plist"
swiftc -O -o "$app/Contents/MacOS/WifiName" "$here/WifiName.swift" \
    -framework AppKit -framework CoreLocation -framework CoreWLAN
codesign --force --sign - "$app"
echo "built: $app"
