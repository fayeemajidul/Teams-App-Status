#!/bin/bash
# Install (or remove) the Teams status scheduler as a launchd LaunchAgent.
# Safe to re-run: it always replaces whatever is already loaded, and it never
# overwrites a config.json you have already edited.
#
#   bash install.sh                 install / reinstall
#   bash install.sh --reset-config  also restore config.json to the shipped one
#   bash install.sh --uninstall     remove
#
# Why it copies the files: macOS protects ~/Documents, ~/Desktop and ~/Downloads.
# A background launchd agent cannot read from those folders, so the running copy
# lives in ~/Library/Application Support instead.

set -euo pipefail

LABEL="com.fayeem.teams-status"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$HOME/Library/Application Support/teams-status"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PYTHON="/usr/bin/python3"
STATE_DIR="$HOME/.config/teams-status"

unload_if_loaded() {
    if launchctl print "gui/$UID/$LABEL" >/dev/null 2>&1; then
        launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
    fi
}

if [[ "${1:-}" == "--uninstall" ]]; then
    unload_if_loaded
    rm -f "$PLIST"
    echo "Removed the schedule ($LABEL)."
    echo "Left in place: $INSTALL_DIR and your logs in $STATE_DIR"
    echo "Your Teams status stays exactly as it is now."
    exit 0
fi

if [[ ! -f "$SRC/teams_status.py" || ! -f "$SRC/config.json" ]]; then
    echo "error: teams_status.py and config.json must sit next to install.sh" >&2
    exit 1
fi

if ! "$PYTHON" --version >/dev/null 2>&1; then
    echo "error: $PYTHON is not working yet." >&2
    echo "Run 'python3 --version' in Terminal once and accept the developer tools" >&2
    echo "install macOS offers, then run this again." >&2
    exit 1
fi

echo "Installing $LABEL"
mkdir -p "$INSTALL_DIR" "$HOME/Library/LaunchAgents" "$STATE_DIR"

cp "$SRC/teams_status.py" "$INSTALL_DIR/teams_status.py"
chmod +x "$INSTALL_DIR/teams_status.py"

if [[ -f "$INSTALL_DIR/config.json" && "${1:-}" != "--reset-config" ]]; then
    echo "  keeping your existing $INSTALL_DIR/config.json"
else
    cp "$SRC/config.json" "$INSTALL_DIR/config.json"
    echo "  installed config.json"
fi

echo "  running copy: $INSTALL_DIR/teams_status.py"

# --------------------------------------------------------------------------
# Build a small app bundle to launch the script.
#
# macOS grants Accessibility permission per executable. A bare "python3" run by
# launchd is not something you can meaningfully grant (and the grant would move
# whenever Xcode or the OS updates python). Wrapping the launch in a tiny signed
# .app gives macOS one stable thing named "TeamsStatus" to hold the permission.
#
# Only the launcher lives inside the bundle, so editing teams_status.py or
# config.json later does not break the code signature or the permission.
# --------------------------------------------------------------------------
APP="$INSTALL_DIR/TeamsStatus.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"

cat > "$APP/Contents/Info.plist" <<'PLIST_EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>TeamsStatus</string>
    <key>CFBundleDisplayName</key>
    <string>TeamsStatus</string>
    <key>CFBundleIdentifier</key>
    <string>com.fayeem.teams-status</string>
    <key>CFBundleExecutable</key>
    <string>TeamsStatus</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>LSBackgroundOnly</key>
    <true/>
</dict>
</plist>
PLIST_EOF

cat > "$APP/Contents/MacOS/TeamsStatus" <<'LAUNCH_EOF'
#!/bin/bash
# Thin launcher. Kept deliberately tiny and stable so the code signature (and
# therefore the Accessibility permission) survives edits to the Python script.
exec /usr/bin/python3 "$HOME/Library/Application Support/teams-status/teams_status.py" "$@"
LAUNCH_EOF
chmod +x "$APP/Contents/MacOS/TeamsStatus"

# Ad-hoc signature gives the bundle a stable identity for the permission system.
codesign --force --sign - "$APP" >/dev/null 2>&1 || \
    echo "  note: could not code-sign the bundle (permission may need re-granting after updates)"

echo "  launcher:     $APP"

# config.json is the single source of truth; the script derives the triggers.
"$PYTHON" "$INSTALL_DIR/teams_status.py" plist > "$PLIST"

unload_if_loaded
launchctl bootstrap "gui/$UID" "$PLIST"
launchctl enable "gui/$UID/$LABEL" 2>/dev/null || true

echo
echo "Scheduled runs:"
"$PYTHON" "$INSTALL_DIR/teams_status.py" plan | sed -n '/launchd will fire/,$p'

echo
echo "Applying the correct status for right now..."
"$PYTHON" "$INSTALL_DIR/teams_status.py" tick || true

cat <<EOF

Done.

Edit your schedule here (no reinstall needed):
    $INSTALL_DIR/config.json
...then re-run this installer so the trigger times match:
    bash "$SRC/install.sh"

Logs:    $STATE_DIR/teams-status.log
Check:   $PYTHON "$INSTALL_DIR/teams_status.py" status
Remove:  bash "$SRC/install.sh" --uninstall

If nothing happened, macOS is still withholding permission. Open
System Settings > Privacy & Security > Accessibility and switch on the app you
ran this from (Terminal, iTerm, ...), then run the installer again.
EOF
