#!/usr/bin/env bash
# Daily fantasy briefing on this Mac via launchd: runs at 12:00 and 18:00 local time.
# A run missed while the Mac is asleep runs once when it wakes.
#   scripts/install_briefing.sh            install (or update) and load
#   scripts/install_briefing.sh uninstall  remove
#   scripts/install_briefing.sh run        trigger one run now
#   scripts/install_briefing.sh status     show launchd status
set -euo pipefail
LABEL=com.nbafantasy.briefing
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$ROOT/backend/cache/briefings"
DOMAIN="gui/$(id -u)"

case "${1:-install}" in
  uninstall)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"; echo "Removed $LABEL"; exit 0 ;;
  run)
    launchctl kickstart -p "$DOMAIN/$LABEL" >/dev/null
    for _ in $(seq 1 45); do
      launchctl print "$DOMAIN/$LABEL" | grep -q "state = running" || { tail -5 "$LOG_DIR/launchd.log"; exit 0; }
      sleep 1
    done
    cat <<'HINT'
The briefing is still running after 45s. It's most likely blocked by macOS privacy protection,
because this project lives in ~/Documents. Fix: System Settings > Privacy & Security >
Full Disk Access > "+" > press Cmd+Shift+G and paste the Python path below > Open, then turn it on:
HINT
    echo "  $(readlink -f "$ROOT/backend/.venv/bin/python")"
    echo "(If macOS showed an 'access files in your Documents folder' prompt instead, click Allow.)"
    exit 1 ;;
  status) launchctl print "$DOMAIN/$LABEL" | grep -E "state|last exit|runs|path" ; exit 0 ;;
  install) ;;
  *) echo "usage: $0 [install|uninstall|run|status]"; exit 1 ;;
esac

[ -x "$ROOT/backend/.venv/bin/python" ] || { echo "Run ./run.sh once first (creates backend/.venv)"; exit 1; }
mkdir -p "$LOG_DIR" "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$ROOT/backend/.venv/bin/python</string>
    <string>$ROOT/backend/scripts/daily_briefing.py</string>
  </array>
  <key>WorkingDirectory</key><string>$ROOT/backend</string>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Hour</key><integer>12</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>18</integer><key>Minute</key><integer>0</integer></dict>
  </array>
  <key>StandardOutPath</key><string>$LOG_DIR/launchd.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/launchd.log</string>
</dict>
</plist>
PLIST
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST"
echo "Installed $LABEL: runs daily at 12:00 and 18:00. Log: $LOG_DIR/launchd.log"
