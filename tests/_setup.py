"""Shared test setup: offscreen Qt, no background workers, throwaway config file."""
import os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import widget  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402
widget.MacMonitorWidget._init_workers = lambda self: None
widget.CONFIG_PATH = widget.Path(tempfile.mkdtemp()) / "cfg.json"
app = QApplication.instance() or QApplication([])
FAILS = []
def ok(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)
def done():
    sys.exit(1 if FAILS else 0)
