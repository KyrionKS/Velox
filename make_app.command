#!/bin/bash
# Builds Velox.app, a double-clickable macOS app bundle (no Terminal window).
# Run this once after setup.command. Re-run it if you move the project folder.
set -e
cd "$(dirname "$0")"
ROOT="$(pwd)"
APP="$ROOT/Velox.app"

if [ ! -x "$ROOT/.venv/bin/python" ]; then
    echo "Please run setup.command first (no .venv found)."
    read -n 1 -s -r -p "Press any key to close…"
    exit 1
fi

echo "Building $APP …"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>Velox</string>
    <key>CFBundleDisplayName</key><string>Velox</string>
    <key>CFBundleIdentifier</key><string>io.velox.app</string>
    <key>CFBundleVersion</key><string>0.1.0</string>
    <key>CFBundleShortVersionString</key><string>0.1.0</string>
    <key>CFBundleExecutable</key><string>Velox</string>
    <key>CFBundleIconFile</key><string>Velox</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>LSMinimumSystemVersion</key><string>11.0</string>
    <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

# App icon (optional): assets/icon.png -> Contents/Resources/Velox.icns
ICON_SRC="$ROOT/assets/icon.png"
if [ -f "$ICON_SRC" ] && command -v iconutil >/dev/null 2>&1; then
    echo "Building app icon…"
    ICONSET="$APP/Contents/Resources/Velox.iconset"
    mkdir -p "$ICONSET"
    for size in 16 32 128 256 512; do
        sips -z "$size" "$size" "$ICON_SRC" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
        double=$((size * 2))
        sips -z "$double" "$double" "$ICON_SRC" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
    done
    iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/Velox.icns"
    rm -rf "$ICONSET"
fi

cat > "$APP/Contents/MacOS/Velox" <<LAUNCH
#!/bin/bash
cd "$ROOT"
exec "$ROOT/.venv/bin/python" -m velox.gui
LAUNCH
chmod +x "$APP/Contents/MacOS/Velox"

echo "✓ Created Velox.app — double-click it to start."
echo "  (If macOS refuses to open it, right-click → Open once.)"
echo
read -n 1 -s -r -p "Press any key to close…"
