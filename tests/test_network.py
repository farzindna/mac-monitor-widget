from _setup import widget, app, ok, done

now = 1000.0
wifi = {"en1"}
fresh = lambda **k: {"ts": now - 3, "auth": "allowed", "services": True, "ssid": "", **k}

# 1 which interface: the VPN tunnel is listed first and must be skipped
with_vpn = """IPv4 network interface information
   utun4 : flags      : 0x7 (IPv4,IPv6,DNS)
           address    : 10.0.0.1
     en1 : flags      : 0x5 (IPv4,DNS)
           address    : 192.168.0.10
   REACH : flags 0x00000003 (Reachable,Transient Connection)
"""
ok("VPN on: real interface found under the tunnel", widget.active_interface(with_vpn) == "en1")
ok("REACH line is not an interface", widget.active_interface("   REACH : flags 0x00000003 (Reachable)\n") is None)
ok("only a tunnel: no network", widget.active_interface("   utun4 : flags : 0x7 (IPv4)\n") is None)
ok("nothing connected: no network", widget.active_interface("No IPv4 states found\n   REACH : flags 0x00000000 (Not Reachable)\n") is None)
ok("cable preferred over Wi-Fi when listed first", widget.active_interface("     en0 : flags : 0x5\n     en1 : flags : 0x5\n") == "en0")

# 2 which device is Wi-Fi
ports = "Hardware Port: Ethernet\nDevice: en0\nEthernet Address: aa\n\nHardware Port: Wi-Fi\nDevice: en1\nEthernet Address: bb\n"
ok("Wi-Fi device parsed, cable excluded", widget.wifi_devices(ports) == {"en1"})

# 3 what to show
label = widget.network_label
widget.WIFI_APP = widget.Path("/")  # "helper installed"
ok("Wi-Fi with name", label("en1", wifi, fresh(ssid="TestNet"), now) == ("TestNet", ""))
ok("cable never shows the old Wi-Fi name", label("en0", wifi, fresh(ssid="TestNet"), now)[0] == "کابل شبکه")
ok("no network", label(None, wifi, fresh(ssid="TestNet"), now)[0] == "بدون شبکه")
stale = fresh(ssid="OldModem"); stale["ts"] = now - widget.WIFI_STALE_S - 1
ok("stale helper file: old name is not believed", label("en1", wifi, stale, now)[0] == "وای‌فای")
ok("boundary: just inside the limit is believed", label("en1", wifi, dict(stale, ts=now - widget.WIFI_STALE_S + 1), now)[0] == "OldModem")
name, why = label("en1", wifi, fresh(auth="denied"), now)
ok("permission denied: generic word and a reason", name == "وای‌فای" and "Location" in why)
ok("helper never ran: generic word", label("en1", wifi, None, now)[0] == "وای‌فای")
widget.WIFI_APP = widget.Path("/nonexistent/WifiName.app")
ok("helper not installed: says how to build", "build.sh" in label("en1", wifi, None, now)[1])

# 4 on screen, in both cards
w = widget.MacMonitorWidget(); w._set_collapsed(True); app.processEvents()
h0 = w.height()
w._on_network("TestNet", ""); w.show(); app.processEvents()
ok("small card shows the name", w.mini_net.text() == "TestNet" and w.mini_net.isVisibleTo(w))
ok("full card holds it too", w.net_lbl.text() == "TestNet")
long_name = "A-very-long-network-name-from-the-neighbour-5G"
w._on_network(long_name, ""); app.processEvents()
ok("long name is shortened on small card", w.mini_net.text() != long_name and "…" in w.mini_net.text())
ok("  and card keeps its width", w.width() == widget.MINI_W)
ok("  full name stays in the tooltip", long_name in w.mini_net.toolTip())
w._on_network("مودم خونه", ""); ok("Persian name shown as is", w.mini_net.text() == "مودم خونه")
w._on_network("<b>x</b>", ""); app.processEvents()
ok("network name is never treated as markup", w.mini_net.textFormat() == widget.Qt.TextFormat.PlainText and w.net_lbl.textFormat() == widget.Qt.TextFormat.PlainText)
print("small card height:", w.height())
w._set_collapsed(False); app.processEvents()
ok("open card shows it", w.net_lbl.isVisibleTo(w) and not w.mini_net.isVisibleTo(w))
done()
