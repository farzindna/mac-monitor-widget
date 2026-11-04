#!/bin/bash
# Double-click to open the Mac Live Monitor widget. A second copy exits on its own (single-instance lock).
# nohup: closing this Terminal window does not kill the widget.
nohup /opt/homebrew/bin/python3 "$(dirname "$0")/widget.py" > /dev/null 2>&1 &
