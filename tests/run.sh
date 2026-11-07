#!/bin/zsh
# Run every test file; non-zero exit if any fails.  Usage: zsh tests/run.sh
cd "$(dirname "$0")" || exit 1
rc=0
for t in test_*.py; do
  echo "== $t"
  QT_QPA_PLATFORM=offscreen /opt/homebrew/bin/python3 "$t" 2>&1 | grep -E "PASS|FAIL|Traceback|Error" || rc=1
  [ ${pipestatus[1]} -eq 0 ] || rc=1
done
exit $rc
