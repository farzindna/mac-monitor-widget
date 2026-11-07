from _setup import widget, app, ok, done

w = widget.MacMonitorWidget(); w._set_collapsed(False); app.processEvents()
sounds = []; w._play_sound = lambda p: sounds.append(p.split("/")[-1])
clock = [1000.0]; widget.time.time = lambda: clock[0]
mem = lambda p=1, r=0: {"used_gb": 14, "total_gb": 16, "used_pct": 88, "swap_used_mb": 2000, "swap_total_mb": 3000, "swap_recent_mb": r, "pressure": p, "top_apps": []}
step = lambda d, s=1.8: (w._on_memory_update(d), clock.__setitem__(0, clock[0] + s))

# 1 pressure 2 (what the Mac really showed) and moderate swapping: calm label, nothing else
for _ in range(20): step(mem(p=2, r=150))
ok("pressure 2 + 150MB swap: state normal", w.mem_state == "normal")
ok("  badge says Mac is handling it", "مدیریت" in w.ram_status_badge.text())
ok("  no banner, no sound", not w.alert_banner.isVisibleTo(w) and not sounds)
# 2 boundary: heavy (critical) but short (< HEAVY_HOLD_S) -> still calm
for _ in range(20): step(mem(p=4))   # 36 s
ok("critical for 36 s: still no amber", w.mem_state == "normal" and not sounds)
# 3 heavy sustained > HEAVY_HOLD_S -> calm amber, silent, never red, no blinking
for _ in range(40): step(mem(p=4))   # 108 s in total
ok("critical for 108 s: amber warning", w.mem_state == "warning" and w.shown_state == "warning")
ok("  silent", not sounds)
ok("  banner amber, not red", w.alert_banner.isVisibleTo(w) and "245, 158, 11" in w.alert_banner.styleSheet())
ok("  no blinking machinery left", not hasattr(w, "pulse_timer"))
# 4 load gone -> normal at once; a new heavy stretch restarts the clock
step(mem(p=1)); ok("load gone: normal again", w.mem_state == "normal" and not w.alert_banner.isVisibleTo(w))
for _ in range(10): step(mem(r=800))  # 18 s of heavy swapping
ok("new heavy stretch restarts timer (18 s -> calm)", w.mem_state == "normal")
# 5 negative control: real outages are still loud
step(mem()); w._on_ping_update(None, False)
ok("internet down: red + one beep", w.shown_state == "danger" and sounds == ["Basso.aiff"] and "239, 68, 68" in w.alert_banner.styleSheet())
w._on_ping_update(30.0, True); w._on_vpn_state("Connected"); sounds.clear(); w._on_vpn_state("Disconnected")
ok("vpn drop: red + Sosumi", w.shown_state == "danger" and sounds == ["Sosumi.aiff"])
done()
