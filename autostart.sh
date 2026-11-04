#!/bin/zsh
# Open the widget at every login:   zsh autostart.sh on
# Stop opening it at login:         zsh autostart.sh off
# launchd waits up to 30 min for /Volumes/ai-scientist (a disk image) to mount before starting.
# Closing the widget with ✕ exits cleanly and launchd leaves it closed until next login; a crash is restarted.
LABEL=com.farzin.mac-monitor-widget
PLIST=~/Library/LaunchAgents/$LABEL.plist
WIDGET=/Volumes/ai-scientist/00_tools/mac-monitor-widget/widget.py
DOMAIN=gui/$(id -u)

launchctl bootout $DOMAIN/$LABEL 2>/dev/null
if [[ $1 == off ]]; then
  rm -f $PLIST
  echo "autostart off (an open widget stays open until you close it)"
  exit 0
fi
[[ $1 == on ]] || { echo "usage: zsh autostart.sh on|off"; exit 1; }

cat > $PLIST <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string><string>-c</string>
    <string>for i in {1..360}; do [ -f "$WIDGET" ] &amp;&amp; exec /opt/homebrew/bin/python3 "$WIDGET"; sleep 5; done; echo "volume never mounted"; exit 0</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>LimitLoadToSessionType</key><string>Aqua</string>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/mac-monitor-widget.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/mac-monitor-widget.log</string>
</dict>
</plist>
PL
plutil -lint $PLIST >/dev/null || { echo "plist invalid"; exit 1; }
launchctl bootstrap $DOMAIN $PLIST && echo "autostart on — widget starting now"
