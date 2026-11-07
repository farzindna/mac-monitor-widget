import json
from _setup import widget, app, ok, done
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtTest import QTest
w = widget.MacMonitorWidget(); w.show(); app.processEvents()
ok("default = small card", w.collapsed and w.width() == widget.MINI_W and w.mini_box.isVisible() and not w.full_box.isVisible())
ok("small card about clock size (<=200)", w.width() <= 200 and w.height() <= 200)
w._on_ping_update(181.0, True); w._on_memory_update({"used_gb": 7.6, "total_gb": 16, "used_pct": 47, "swap_used_mb": 0, "swap_total_mb": 1, "swap_recent_mb": 0, "pressure": 1, "top_apps": []})
w._show_temp(37.0); w._on_vpn_state("Connected"); w._refresh_mini()
ok("mini mirrors ping/ram/temp/vpn", w.mini_ping.text() == "181 ms" and "47%" in w.mini_ram.text() and "37" in w.mini_temp.text() and "وصل" in w.mini_vpn.text())
w._on_ping_update(None, False); w._refresh_mini()
ok("offline: short red text in mini", "قطع" in w.mini_ping.text() and "ارتباط" not in w.mini_ping.text() and "ef4444" in w.mini_ping.styleSheet().lower())
w._on_ping_update(30.0, True)
QTest.mouseClick(w, Qt.MouseButton.LeftButton, pos=QPoint(60, 60)); app.processEvents()
ok("click opens full card", not w.collapsed and w.width() == widget.FULL_W)
w.collapse_btn.click(); app.processEvents(); ok("collapse button shrinks", w.collapsed)
QTest.mousePress(w, Qt.MouseButton.LeftButton, pos=QPoint(60, 60)); QTest.mouseMove(w, QPoint(90, 90)); QTest.mouseRelease(w, Qt.MouseButton.LeftButton, pos=QPoint(90, 90)); app.processEvents()
ok("drag does not open", w.collapsed)
w._set_collapsed(False); QTest.mouseClick(w, Qt.MouseButton.LeftButton, pos=QPoint(170, 20)); app.processEvents()
ok("click on full card does not collapse", not w.collapsed)
ok("state saved", json.load(open(widget.CONFIG_PATH))["collapsed"] is False)
# position: open near the edge, collapse returns to the exact spot; moved while open -> new corner
w._set_collapsed(True); scr = w.screen().availableGeometry()
start = QPoint(scr.right() - 100, scr.bottom() - 100); w.move(start); w._set_collapsed(False)
ok("open near edge stays on screen", w.x() + w.width() <= scr.right() + 1 and w.pos() != start)
w._set_collapsed(True); ok("collapse returns to old spot", w.pos() == start)
w.move(start); w._set_collapsed(False); w.move(QPoint(300, 200)); w._set_collapsed(True)
ok("moved while open: keeps new corner", w.pos() == QPoint(300, 200))
done()
