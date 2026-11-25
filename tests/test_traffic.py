from _setup import widget, app, ok, done

SAMPLE_NETSTAT = """\
Name       Mtu   Network       Address            Ipkts Ierrs     Ibytes    Opkts Oerrs     Obytes  Coll
en1        1500  <Link#18>   02:00:00:00:00:00   1000     0    2048000      500     0     512000     0
en1        1500  192.168.2     192.168.2.103     1000     -    2048000      500     -     512000     -
utun6      4064  <Link#33>                          100     0      10000       50     0      20000     0
"""

ok("parse en1 <Link#> bytes", widget.parse_netstat_bytes(SAMPLE_NETSTAT, "en1") == (2048000, 512000))
ok("parse 10-col row without MAC", widget.parse_netstat_bytes(SAMPLE_NETSTAT, "utun6") == (10000, 20000))
ok("parse missing iface returns None", widget.parse_netstat_bytes(SAMPLE_NETSTAT, "en9") is None)
ok("parse None iface returns None", widget.parse_netstat_bytes(SAMPLE_NETSTAT, None) is None)

ok("format_rate B/s", widget.format_rate(512) == "512 B/s")
ok("format_rate KB/s", widget.format_rate(128 * 1024) == "128 KB/s")
ok("format_rate MB/s", widget.format_rate(2.5 * 1024 * 1024) == "2.5 MB/s")
ok("format_rate compact", widget.format_rate(64 * 1024, compact=True) == "64K" and widget.format_rate(1.5 * 1024 * 1024, compact=True) == "1.5M")
ok("format_bytes units", widget.format_bytes(500) == "500 B" and widget.format_bytes(512 * 1024) == "512 KB" and widget.format_bytes(42 * 1024 * 1024) == "42.0 MB")

t = widget.TrafficTracker()
s1 = t.update("en1", (1000000, 200000), 10.0)
ok("first tick has 0 speed and 0 total", s1["rx_bps"] == 0 and s1["rx_total"] == 0)

s2 = t.update("en1", (1000000 + 204800, 200000 + 51200), 12.0)
ok("second tick computes exact rate & total", abs(s2["rx_bps"] - 102400.0) < 1e-6 and abs(s2["tx_bps"] - 25600.0) < 1e-6 and s2["rx_total"] == 204800 and s2["tx_total"] == 51200)

s3 = t.update("en0", (5000, 1000), 13.0)
ok("iface switch gives 0 speed and keeps total", s3["rx_bps"] == 0 and s3["rx_total"] == 204800 and s3["tx_total"] == 51200)

s4 = t.update("en0", (2000, 500), 14.0)
ok("counter rollover ignored safely", s4["rx_bps"] == 0 and s4["rx_total"] == 204800)

s5 = t.update(None, None, 15.0)
ok("offline keeps total with 0 speed", s5["rx_bps"] == 0 and s5["rx_total"] == 204800)

t.reset()
s6 = t.update("en0", (12240, 2500), 16.0)
ok("reset zeroes totals cleanly", s6["rx_total"] == 0 and s6["tx_total"] == 0)

w = widget.MacMonitorWidget()
w.show()
app.processEvents()
ok("mini card still <= 200px tall", w.width() <= 200 and w.height() <= 200)

w._on_traffic_update({"iface": "en1", "rx_bps": 256 * 1024, "tx_bps": 32 * 1024, "rx_total": 10 * 1024 * 1024, "tx_total": 2 * 1024 * 1024})
w._refresh_mini()
ok("full card shows speed and total", "256 KB/s" in w.traffic_rate_lbl.text() and "32 KB/s" in w.traffic_rate_lbl.text() and "12.0 MB" in w.traffic_total_lbl.text())
ok("mini mirrors compact traffic", "↓256K" in w.mini_traffic.text() and "↑32K" in w.mini_traffic.text() and "12.0M" in w.mini_traffic.text())

w.traffic_reset_btn.click()
app.processEvents()
ok("reset button zeroes full and mini totals", "0 B" in w.traffic_total_lbl.text() and "· 0K" in w.mini_traffic.text())

done()
