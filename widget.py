#!/usr/bin/env python3
"""
Mac Live Monitor Widget (PyQt6)
Frosted Glass Floating Widget for Real-Time Ping, RAM, and Swap Monitoring.
Designed for Farzin.
"""

import sys
import os
import re
import json
import time
import socket
import subprocess
import ctypes
import ctypes.util
from pathlib import Path

from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QTimer, QPoint, QRectF, QEvent
)
from PyQt6.QtGui import (
    QPainter, QColor, QLinearGradient, QPen, QPainterPath, QFont
)
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QBoxLayout, QLabel,
    QProgressBar, QPushButton, QFrame, QSizePolicy
)

CONFIG_PATH = Path.home() / ".config" / "mac_monitor_widget.json"
WIFI_FILE = Path.home() / ".config" / "mac_monitor_wifi.json"  # written by helper/WifiName; stays on this Mac
WIFI_APP = Path.home() / "Library" / "Application Support" / "mac-monitor-widget" / "WifiName.app"
WIFI_STALE_S = 20  # older than this, the helper's answer is not believed
SOUND_WARN = "/System/Library/Sounds/Basso.aiff"
SOUND_CHIME = "/System/Library/Sounds/Tink.aiff"
SOUND_VPN = "/System/Library/Sounds/Sosumi.aiff"   # VPN drop: must sound different from the internet-offline beep (Basso)
LOCK_PATH = Path.home() / ".config" / "mac_monitor_widget.lock"
FULL_W, MINI_W = 356, 192      # window widths: full card / small card (about the size of the macOS clock widget)
PANEL_RADIUS = 16                # corner radius of the solid panel (glass outside radius = PANEL_RADIUS + RIM - 1)
RIM = 12                       # width of the glass frame around the solid panel (like the macOS clock widget)
GLASS_SHADE = 0.32   # faint dark tint on the glass, like the system widgets (0 = milky system default)
GLASS_STYLE = 0      # NSGlassEffectView style: 0 = regular, 1 = clear (more see-through)
GLASS_DARK = False   # the text lives on the solid panel, so the glass rim can keep the light system look
GLASS_TINT = 1.0     # unused by the rim layout; kept so the glass code path stays unchanged
VPN_NAME = "RocketTunnel"     # macOS network-service name; `scutil --nc list` shows it

SWAP_WINDOW_TICKS = 10        # ~18 s of memory samples
SWAP_WARN_MB = 10             # fresh swap in that window → "busy" label only (macOS handles it; no colour, no sound)
SWAP_HEAVY_MB = 500           # this much fresh swap in the window counts as heavy …
HEAVY_HOLD_S = 90             # … but the card only turns amber once heavy load has lasted this long (still silent)
TEST_HOLD_S = 4               # a simulated test state stays on screen this long before live data returns

# Chip temperature colour stops (°C → RGB); colour is interpolated between them.
TEMP_STOPS = [(50, (16, 185, 129)), (72, (245, 158, 11)), (90, (239, 68, 68))]


def temp_color(t):
    if t <= TEMP_STOPS[0][0]:
        return TEMP_STOPS[0][1]
    for (t0, c0), (t1, c1) in zip(TEMP_STOPS, TEMP_STOPS[1:]):
        if t <= t1:
            f = (t - t0) / (t1 - t0)
            return tuple(round(a + (b - a) * f) for a, b in zip(c0, c1))
    return TEMP_STOPS[-1][1]


class ChipTemp:
    """Apple Silicon die temperature from the IOHID thermal sensors (no sudo, no extra package).
    There is no separate RAM sensor: on M-series Macs the memory sits in the same package as the chip.
    Uses private IOKit calls; if they ever stop working, read() returns None and the row hides."""
    def __init__(self):
        self.services = []
        try:
            vp = ctypes.c_void_p
            cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
            io = ctypes.cdll.LoadLibrary(ctypes.util.find_library("IOKit"))
            for lib, f, r, a in [
                (cf, "CFStringCreateWithCString", vp, [vp, ctypes.c_char_p, ctypes.c_uint32]),
                (cf, "CFNumberCreate", vp, [vp, ctypes.c_int, vp]),
                (cf, "CFDictionaryCreate", vp, [vp, vp, vp, ctypes.c_long, vp, vp]),
                (cf, "CFArrayGetCount", ctypes.c_long, [vp]),
                (cf, "CFArrayGetValueAtIndex", vp, [vp, ctypes.c_long]),
                (cf, "CFStringGetCString", ctypes.c_bool, [vp, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]),
                (cf, "CFRelease", None, [vp]),
                (io, "IOHIDEventSystemClientCreate", vp, [vp]),
                (io, "IOHIDEventSystemClientSetMatching", ctypes.c_int, [vp, vp]),
                (io, "IOHIDEventSystemClientCopyServices", vp, [vp]),
                (io, "IOHIDServiceClientCopyProperty", vp, [vp, vp]),
                (io, "IOHIDServiceClientCopyEvent", vp, [vp, ctypes.c_int64, ctypes.c_int32, ctypes.c_int64]),
                (io, "IOHIDEventGetFloatValue", ctypes.c_double, [vp, ctypes.c_int32]),
            ]:
                fn = getattr(lib, f); fn.restype = r; fn.argtypes = a
            self.cf, self.io = cf, io
            cfstr = lambda x: cf.CFStringCreateWithCString(None, x.encode(), 0x08000100)

            def cfnum(v):
                n = ctypes.c_int(v)
                return cf.CFNumberCreate(None, 3, ctypes.byref(n))
            keys = (vp * 2)(cfstr("PrimaryUsagePage"), cfstr("PrimaryUsage"))
            vals = (vp * 2)(cfnum(0xff00), cfnum(5))  # Apple vendor page, temperature sensors
            kcb = vp.in_dll(cf, "kCFTypeDictionaryKeyCallBacks")
            vcb = vp.in_dll(cf, "kCFTypeDictionaryValueCallBacks")
            match = cf.CFDictionaryCreate(None, keys, vals, 2, ctypes.addressof(kcb), ctypes.addressof(vcb))
            self.client = io.IOHIDEventSystemClientCreate(None)
            io.IOHIDEventSystemClientSetMatching(self.client, match)
            svcs = io.IOHIDEventSystemClientCopyServices(self.client)
            product = cfstr("Product")
            for i in range(cf.CFArrayGetCount(svcs) if svcs else 0):
                svc = cf.CFArrayGetValueAtIndex(svcs, i)
                name = io.IOHIDServiceClientCopyProperty(svc, product)
                buf = ctypes.create_string_buffer(128)
                if name and cf.CFStringGetCString(name, buf, 128, 0x08000100) and buf.value.startswith(b"PMU tdie"):
                    self.services.append(svc)
        except Exception:
            self.services = []

    def read(self):
        """Hottest die sensor in °C, or None."""
        best = None
        for svc in self.services:
            try:
                ev = self.io.IOHIDServiceClientCopyEvent(svc, 15, 0, 0)  # kIOHIDEventTypeTemperature
                if not ev:
                    continue
                t = self.io.IOHIDEventGetFloatValue(ev, 15 << 16)
                self.cf.CFRelease(ev)
                if 0 < t < 150:
                    best = t if best is None else max(best, t)
            except Exception:
                continue
        return best


class NSRect(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double), ("w", ctypes.c_double), ("h", ctypes.c_double)]


class ObjC:
    """Minimal Objective-C messaging through ctypes (no pyobjc needed)."""
    def __init__(self):
        lib = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        lib.objc_getClass.restype = ctypes.c_void_p
        lib.objc_getClass.argtypes = [ctypes.c_char_p]
        lib.sel_registerName.restype = ctypes.c_void_p
        lib.sel_registerName.argtypes = [ctypes.c_char_p]
        self.lib = lib
        self.addr = ctypes.cast(lib.objc_msgSend, ctypes.c_void_p).value

    def cls(self, name):
        return self.lib.objc_getClass(name.encode())

    def send(self, obj, sel, *args, argtypes=(), restype=ctypes.c_void_p):
        fn = ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes)(self.addr)
        return fn(obj, self.lib.sel_registerName(sel.encode()), *args)


class PingWorker(QThread):
    """Background worker to check network ping / latency continuously."""
    ping_result = pyqtSignal(object, bool)  # latency_ms (or None), is_online

    def __init__(self, parent=None):
        super().__init__(parent)
        self.running = True
        self.consecutive_drops = 0

    def run(self):
        # Primary target is 8.8.8.8 via real ICMP ping, so the number matches `ping 8.8.8.8` in Terminal.
        # TCP:53 handshakes are only a fallback for networks that drop ICMP (and 1.1.1.1 only decides online/offline).
        tcp_hosts = [("8.8.8.8", 53), ("1.1.1.1", 53), ("4.2.2.4", 53)]
        while self.running:
            latency = None
            is_online = False
            try:
                res = subprocess.run(
                    ["ping", "-c", "1", "-W", "2500", "8.8.8.8"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=3.0
                )
                if res.returncode == 0:
                    m = re.search(r"time=([\d\.]+)\s*ms", res.stdout)
                    if m:
                        latency = float(m.group(1))
                        is_online = True
            except Exception:
                pass

            if not is_online:
                for host, port in tcp_hosts:
                    t0 = time.time()
                    try:
                        s = socket.create_connection((host, port), timeout=2.0)
                        s.close()
                        latency = (time.time() - t0) * 1000.0
                        is_online = True
                        break
                    except Exception:
                        continue

            if is_online:
                self.consecutive_drops = 0
                self.ping_result.emit(latency, True)
            else:
                self.consecutive_drops += 1
                if self.consecutive_drops >= 2:
                    self.ping_result.emit(None, False)
                else:
                    # Single dropped check: retry quickly before raising an alarm
                    for _ in range(5):
                        if not self.running:
                            return
                        time.sleep(0.1)
                    continue

            # Sleep 1.5 seconds between checks
            for _ in range(15):
                if not self.running:
                    return
                time.sleep(0.1)

    def stop(self):
        self.running = False


class VpnWorker(QThread):
    """Polls `scutil --nc status` for the VPN service: Connected / Connecting / Disconnecting / Disconnected / missing."""
    state_result = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.running = True

    @staticmethod
    def read_state():
        try:
            out = subprocess.run(["scutil", "--nc", "status", VPN_NAME], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, timeout=3).stdout
            first = out.strip().splitlines()[0].strip() if out.strip() else ""
        except Exception:
            return "unknown"
        known = {"Connected", "Connecting", "Disconnecting", "Disconnected", "Reasserting"}
        if first in known:
            return first
        return "missing" if first.startswith("No service") else "unknown"

    def run(self):
        while self.running:
            self.state_result.emit(self.read_state())
            for _ in range(15):
                if not self.running:
                    return
                time.sleep(0.1)

    def stop(self):
        self.running = False


def active_interface(nwi_text):
    """First real network interface in `scutil --nwi` output, skipping VPN tunnels (the Mac lists them first)."""
    for name in re.findall(r"^\s+([a-z]+\d+) : flags", nwi_text, re.M):  # the summary line "REACH" is not an interface
        if not name.startswith(("utun", "ipsec", "ppp", "tun", "tap", "lo", "awdl", "llw", "bridge")):
            return name
    return None


def parse_netstat_bytes(netstat_text, iface):
    """Extract (ibytes, obytes) for `iface` from `netstat -ibn` output, preferring the <Link#> row."""
    if not iface:
        return None
    fallback = None
    for line in netstat_text.splitlines():
        parts = line.split()
        if len(parts) < 10 or parts[0] != iface:
            continue
        if parts[-5].isdigit() and parts[-2].isdigit():
            pair = (int(parts[-5]), int(parts[-2]))
            if parts[2].startswith("<Link#"):
                return pair
            if fallback is None:
                fallback = pair
    return fallback


def format_rate(bps, compact=False):
    """Format bytes/sec as a human-readable speed string."""
    bps = max(0.0, float(bps or 0.0))
    if compact:
        if bps < 1024 * 1024:
            return f"{bps / 1024:.0f}K"
        return f"{bps / (1024 * 1024):.1f}M"
    if bps < 1024:
        return f"{bps:.0f} B/s"
    if bps < 1024 * 1024:
        return f"{bps / 1024:.0f} KB/s"
    return f"{bps / (1024 * 1024):.1f} MB/s"


def format_bytes(total, compact=False):
    """Format a byte count as KB / MB / GB."""
    total = max(0, int(total or 0))
    if compact:
        if total < 1024 * 1024:
            return f"{total / 1024:.0f}K"
        if total < 1024 ** 3:
            return f"{total / (1024 * 1024):.1f}M"
        return f"{total / (1024 ** 3):.2f}G"
    if total < 1024:
        return f"{total} B"
    if total < 1024 * 1024:
        return f"{total / 1024:.0f} KB"
    if total < 1024 ** 3:
        return f"{total / (1024 * 1024):.1f} MB"
    return f"{total / (1024 ** 3):.2f} GB"


class TrafficTracker:
    """Compute live RX/TX speeds and resettable session totals from cumulative interface counters."""

    def __init__(self):
        self.prev_iface = None
        self.prev_pair = None
        self.prev_ts = None
        self.rx_total = 0
        self.tx_total = 0

    def reset(self):
        self.rx_total = 0
        self.tx_total = 0

    def update(self, iface, pair, now):
        if iface is None or pair is None:
            self.prev_iface = iface
            self.prev_pair = None
            self.prev_ts = now
            return {
                "iface": iface,
                "rx_bps": 0.0,
                "tx_bps": 0.0,
                "rx_total": self.rx_total,
                "tx_total": self.tx_total,
            }
        ibytes, obytes = pair
        rx_bps, tx_bps = 0.0, 0.0
        if (
            self.prev_pair is not None
            and self.prev_iface == iface
            and self.prev_ts is not None
            and now > self.prev_ts
        ):
            prev_i, prev_o = self.prev_pair
            if ibytes >= prev_i and obytes >= prev_o:
                di = ibytes - prev_i
                do = obytes - prev_o
                dt = now - self.prev_ts
                rx_bps = di / dt
                tx_bps = do / dt
                self.rx_total += di
                self.tx_total += do
        self.prev_iface = iface
        self.prev_pair = (ibytes, obytes)
        self.prev_ts = now
        return {
            "iface": iface,
            "rx_bps": rx_bps,
            "tx_bps": tx_bps,
            "rx_total": self.rx_total,
            "tx_total": self.tx_total,
        }


class TrafficWorker(QThread):
    """Background worker polling live download/upload speed and cumulative usage on the active physical interface."""
    traffic_result = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.running = True
        self._reset_flag = False
        self.tracker = TrafficTracker()

    def reset(self):
        self._reset_flag = True

    def run(self):
        while self.running:
            if self._reset_flag:
                self._reset_flag = False
                self.tracker.reset()
            iface = active_interface(NetworkWorker._run("scutil", "--nwi"))
            pair = parse_netstat_bytes(NetworkWorker._run("netstat", "-ibn"), iface) if iface else None
            self.traffic_result.emit(self.tracker.update(iface, pair, time.monotonic()))
            for _ in range(10):
                if not self.running:
                    return
                time.sleep(0.1)

    def stop(self):
        self.running = False


def wifi_devices(hardware_ports_text):
    """Device names of the Wi-Fi ports in `networksetup -listallhardwareports` output."""
    return set(re.findall(r"Hardware Port: (?:Wi-Fi|AirPort)\s*\nDevice: (\w+)", hardware_ports_text))


def network_label(iface, wifi_ifaces, helper_info, now):
    """(name to show, why the Wi-Fi name is missing or ""). A stale helper file is never trusted."""
    if iface is None:
        return "بدون شبکه", ""
    if iface not in wifi_ifaces:
        return "کابل شبکه", ""
    info = helper_info or {}
    fresh = now - info.get("ts", 0) < WIFI_STALE_S
    if fresh and info.get("ssid"):
        return info["ssid"], ""
    if not WIFI_APP.exists():
        return "وای‌فای", "برنامه‌ی کمکی نصب نیست: zsh helper/build.sh"
    if fresh and (info.get("auth") != "allowed" or not info.get("services", True)):
        return "وای‌فای", "برای دیدن اسم شبکه، تو تنظیمات مک بخش Location Services اجازه‌ی WifiName رو بده."
    return "وای‌فای", ""


class NetworkWorker(QThread):
    """Reports the name of the network the Mac is on. The Wi-Fi name itself comes from helper/WifiName (see HANDOFF)."""
    network_result = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.running = True
        self._last_start = 0.0

    @staticmethod
    def _run(*cmd):
        try:
            return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=3).stdout
        except Exception:
            return ""

    @staticmethod
    def _read_helper():
        try:
            return json.loads(WIFI_FILE.read_text())
        except Exception:
            return None

    def _start_helper(self, now):
        """Start the helper if it is missing or silent. It exits by itself when this widget process is gone."""
        if WIFI_APP.exists() and now - self._last_start > WIFI_STALE_S:
            self._last_start = now
            # `open` makes the helper its own app in macOS's eyes, so the permission belongs to it and not to Python.
            self._run("open", "-n", "-g", "-j", str(WIFI_APP), "--args", "--parent", str(os.getpid()))

    def run(self):
        wifi_ifaces = wifi_devices(self._run("networksetup", "-listallhardwareports"))
        self._start_helper(time.time())  # a helper from the previous run is already on its way out
        while self.running:
            now = time.time()
            info = self._read_helper()
            iface = active_interface(self._run("scutil", "--nwi"))
            if iface in wifi_ifaces and now - (info or {}).get("ts", 0) >= WIFI_STALE_S:
                self._start_helper(now)
            self.network_result.emit(*network_label(iface, wifi_ifaces, info, now))
            for _ in range(30):
                if not self.running:
                    return
                time.sleep(0.1)

    def stop(self):
        self.running = False


class MemoryWorker(QThread):
    """Background worker to monitor RAM and Swap via native macOS utilities."""
    memory_result = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.running = True
        self.total_ram_bytes = self._get_total_ram()

    def _get_total_ram(self):
        try:
            out = subprocess.check_output(["sysctl", "-n", "hw.memsize"]).strip()
            return int(out)
        except Exception:
            return 16 * (1024**3)  # Default 16GB

    def _get_vm_stats(self):
        """(used_bytes, swapouts_bytes) — swapouts is a running total since boot."""
        try:
            vm = subprocess.check_output(["vm_stat"]).decode("utf-8")
            page_size = 4096
            m = re.search(r"page size of (\d+) bytes", vm)
            if m:
                page_size = int(m.group(1))

            stats = {}
            for line in vm.splitlines():
                parts = line.split(":")
                if len(parts) == 2:
                    k = parts[0].strip('\"').strip()
                    v = parts[1].strip().rstrip(".")
                    try:
                        stats[k] = int(v)
                    except ValueError:
                        pass

            wired = stats.get("Pages wired down", 0) * page_size
            compressed = stats.get("Pages occupied by compressor", 0) * page_size
            # Same formula as Activity Monitor's "Memory Used": App Memory (anonymous - purgeable) + Wired + Compressed.
            # (The old wired+active+compressed missed inactive anonymous pages, ~1+ GB low.)
            app_pages = max(stats.get("Anonymous pages", 0) - stats.get("Pages purgeable", 0), 0)
            used_bytes = app_pages * page_size + wired + compressed
            return used_bytes, stats.get("Swapouts", 0) * page_size
        except Exception:
            return 0, None

    def _get_pressure_level(self):
        """macOS's own memory pressure (the colour in Activity Monitor): 1 normal, 2 warn, 4 critical."""
        try:
            return int(subprocess.check_output(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"]).strip())
        except Exception:
            return 1

    def _get_swap_stats(self):
        try:
            out = subprocess.check_output(["sysctl", "vm.swapusage"]).decode("utf-8")
            m = re.search(r"total\s*=\s*([\d\.]+)([MGK]?)\s+used\s*=\s*([\d\.]+)([MGK]?)", out)
            if m:
                v_tot, u_tot, v_usd, u_usd = m.groups()
                mult_tot = 1024.0 if u_tot == "G" else (1.0 if u_tot == "M" else 0.001)
                mult_usd = 1024.0 if u_usd == "G" else (1.0 if u_usd == "M" else 0.001)
                return float(v_tot) * mult_tot, float(v_usd) * mult_usd
        except Exception:
            pass
        return 0.0, 0.0

    def _get_top_apps(self):
        apps = []
        try:
            out = subprocess.check_output(
                ["ps", "-axo", "rss,comm", "-r"],
                stderr=subprocess.DEVNULL
            ).decode("utf-8")
            lines = out.strip().splitlines()[1:]
            app_map = {}
            for l in lines:
                parts = l.strip().split(maxsplit=1)
                if len(parts) == 2:
                    rss_kb = int(parts[0])
                    raw_name = parts[1].split("/")[-1]
                    # Filter out helper threads / clean app name
                    clean_name = raw_name
                    if ".app" in parts[1]:
                        m = re.search(r"([^/]+)\.app", parts[1])
                        if m:
                            clean_name = m.group(1)
                    app_map[clean_name] = app_map.get(clean_name, 0) + rss_kb

            sorted_apps = sorted(app_map.items(), key=lambda x: x[1], reverse=True)
            for name, kb in sorted_apps[:3]:
                mb = kb / 1024.0
                if mb >= 1024:
                    apps.append(f"{name}: {mb/1024.0:.1f} GB")
                else:
                    apps.append(f"{name}: {int(mb)} MB")
        except Exception:
            pass
        return apps

    def run(self):
        # Swap written in the last ~18 s. macOS never gives used swap back after pressure passes,
        # so "swap used" alone stays high for days; only fresh swapping means the Mac is struggling now.
        history = []
        chip = ChipTemp()
        while self.running:
            used_bytes, swapouts = self._get_vm_stats()
            total_gb = self.total_ram_bytes / (1024**3)
            used_gb = used_bytes / (1024**3)
            used_pct = (used_bytes / self.total_ram_bytes) * 100.0 if self.total_ram_bytes else 0.0

            swap_tot_mb, swap_used_mb = self._get_swap_stats()
            top_apps = self._get_top_apps()

            swap_recent_mb = 0.0
            if swapouts is not None:
                history.append(swapouts)
                history = history[-SWAP_WINDOW_TICKS:]
                swap_recent_mb = max(0, history[-1] - history[0]) / (1024**2)

            data = {
                "total_gb": total_gb,
                "used_gb": used_gb,
                "used_pct": used_pct,
                "swap_total_mb": swap_tot_mb,
                "swap_used_mb": swap_used_mb,
                "swap_recent_mb": swap_recent_mb,
                "pressure": self._get_pressure_level(),
                "chip_temp": chip.read(),
                "top_apps": top_apps
            }
            self.memory_result.emit(data)

            # Update memory every 1.8 seconds
            for _ in range(18):
                if not self.running:
                    return
                time.sleep(0.1)

    def stop(self):
        self.running = False


class SparklineWidget(QWidget):
    """Custom smooth sparkline for network ping history."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.data = []
        self.max_points = 26
        self.setFixedHeight(34)
        self.line_color = QColor(16, 185, 129)
        self.fill_color = QColor(16, 185, 129, 35)

    def add_point(self, val):
        self.data.append(val)
        if len(self.data) > self.max_points:
            self.data.pop(0)
        self.update()

    def set_color_mode(self, mode):
        # mode: 'green', 'yellow', 'red'
        if mode == 'green':
            self.line_color = QColor(16, 185, 129)
            self.fill_color = QColor(16, 185, 129, 35)
        elif mode == 'yellow':
            self.line_color = QColor(245, 158, 11)
            self.fill_color = QColor(245, 158, 11, 40)
        else:
            self.line_color = QColor(239, 68, 68)
            self.fill_color = QColor(239, 68, 68, 45)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        valid_vals = [v for v in self.data if v is not None]
        w = self.width()
        h = self.height()

        if len(valid_vals) < 2:
            # Draw placeholder baseline
            painter.setPen(QPen(QColor(255, 255, 255, 25), 1, Qt.PenStyle.DashLine))
            painter.drawLine(0, h // 2, w, h // 2)
            return

        max_v = max(valid_vals)
        min_v = min(valid_vals)
        if max_v == min_v:
            max_v += 10
            min_v = max(0, min_v - 10)

        step_x = w / (self.max_points - 1)
        path = QPainterPath()
        first = True

        n = len(self.data)
        for i, val in enumerate(self.data):
            x = w - (n - 1 - i) * step_x
            if val is None:
                y = h - 2
            else:
                norm = (val - min_v) / (max_v - min_v)
                y = (h - 6) - (norm * (h - 12)) + 3

            if first:
                path.moveTo(x, y)
                first = False
            else:
                path.lineTo(x, y)

        # Gradient fill under line
        fill_path = QPainterPath(path)
        fill_path.lineTo(w, h)
        fill_path.lineTo(w - (n - 1) * step_x, h)
        fill_path.closeSubpath()

        painter.fillPath(fill_path, self.fill_color)
        painter.setPen(QPen(self.line_color, 2))
        painter.drawPath(path)

        # Draw latest point indicator
        last_val = self.data[-1]
        if last_val is not None:
            last_x = w - 4
            norm = (last_val - min_v) / (max_v - min_v)
            last_y = (h - 6) - (norm * (h - 12)) + 3
            painter.setBrush(self.line_color)
            painter.setPen(QPen(QColor(255, 255, 255, 220), 1.5))
            painter.drawEllipse(QPoint(int(last_x), int(last_y)), 3, 3)


class MacMonitorWidget(QWidget):
    """Main Glassmorphic Floating Monitor Widget."""
    def __init__(self):
        super().__init__()
        self.drag_position = QPoint()
        self.is_pinned_top = True  # first run: visible above other windows (📍 sends it behind)
        self.sound_enabled = True
        self.collapsed = True      # small card by default; a click on it opens the full card
        self._press_pos = QPoint()
        # Network and memory alerts are tracked separately; one must never clear the other.
        self.net_offline = False
        # VPN: "armed" once we have seen it Connected; a drop while armed that we did not cause is an alert.
        self.vpn_state = "unknown"
        self.vpn_armed = False
        self.vpn_dropped = False
        self.vpn_user_off = False   # set by our own button, so that disconnect stays silent
        self.mem_state = "normal"  # normal or warning (memory never goes "danger" any more)
        self._heavy_since = None   # when the current stretch of heavy memory load began
        self.mem_msg = ""
        self.traffic_data = {"iface": None, "rx_bps": 0.0, "tx_bps": 0.0, "rx_total": 0, "tx_total": 0}
        self.shown_state = None
        self.test_until = 0.0
        self.glass_scale = 1.0  # < 1 when the real macOS glass does the work and the card tint only helps readability
        self.glass = False      # real macOS blur (NSVisualEffectView) behind the card once installed
        self._fx = None
        self._card_state = "normal"

        self._load_config()
        self._init_window_flags()
        self._init_ui()
        self._init_workers()


    def _init_window_flags(self):
        # Frameless, translucent background
        # No Qt.SubWindow: on macOS it makes a top-level widget get no native window at all (invisible).
        flags = Qt.WindowType.FramelessWindowHint
        if self.is_pinned_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            flags |= Qt.WindowType.WindowStaysOnBottomHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def _load_config(self):
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "r") as f:
                    cfg = json.load(f)
                    self.saved_x = cfg.get("x", 100)
                    self.saved_y = cfg.get("y", 100)
                    self.is_pinned_top = cfg.get("pinned_top", True)
                    self.sound_enabled = cfg.get("sound_enabled", True)
                    self.collapsed = cfg.get("collapsed", True)
                    return
            except Exception:
                pass
        self.saved_x = 120
        self.saved_y = 100

    def _save_config(self):
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(CONFIG_PATH, "w") as f:
                json.dump({
                    "x": self.x(),
                    "y": self.y(),
                    "pinned_top": self.is_pinned_top,
                    "sound_enabled": self.sound_enabled,
                    "collapsed": self.collapsed
                }, f)
        except Exception:
            pass

    def _init_ui(self):
        self.setFixedWidth(MINI_W if self.collapsed else FULL_W)
        self.move(self.saved_x, self.saved_y)
        self.setStyleSheet("""
            QToolTip {
                background-color: #0f172a;
                color: #f8fafc;
                border: 1px solid rgba(148, 163, 184, 0.35);
                border-radius: 6px;
                padding: 5px 8px;
                font-size: 11px;
            }
        """)

        # Outer root layout
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(10, 10, 10, 10)

        # Container Card with Frosted Acrylic Glass styling
        self.card = QFrame(self)
        self.card.setObjectName("card")
        self._update_card_style("normal")

        # Soft drop shadow
        # No QGraphicsDropShadowEffect: on a see-through card it draws a blurry ghost under every label.

        # everything of the full card lives in full_box, so the small card can swap it out
        self.full_box = QWidget(self.card)
        card_layout = QVBoxLayout(self.full_box)
        card_layout.setContentsMargins(16, 14, 16, 16)
        card_layout.setSpacing(10)

        # --- HEADER ---
        header_layout = QHBoxLayout()
        header_layout.setDirection(QBoxLayout.Direction.RightToLeft)
        header_layout.setSpacing(8)

        self.status_dot = QLabel("●", self.card)
        self.status_dot.setStyleSheet("color: #10b981; font-size: 15px;")

        self.title_label = QLabel("پایشگر مک (زنده)", self.card)
        self.title_label.setStyleSheet("color: #e2e8f0; font-weight: bold; font-size: 13px;")

        header_layout.addWidget(self.status_dot)
        header_layout.addWidget(self.title_label)
        header_layout.addStretch()

        # Collapse button: full card -> small card
        self.collapse_btn = QPushButton("–", self.card)
        self.collapse_btn.setFixedSize(24, 24)
        self.collapse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.collapse_btn.setToolTip("جمع کردن (کوچیک شدن)")
        self.collapse_btn.setStyleSheet(self._button_style())
        self.collapse_btn.clicked.connect(lambda: self._set_collapsed(True))
        header_layout.addWidget(self.collapse_btn)

        # Audio mute toggle button
        self.sound_btn = QPushButton("🔊" if self.sound_enabled else "🔇", self.card)
        self.sound_btn.setFixedSize(24, 24)
        self.sound_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sound_btn.setToolTip("صدای هشدار")
        self.sound_btn.setStyleSheet(self._button_style())
        self.sound_btn.clicked.connect(self._toggle_sound)
        header_layout.addWidget(self.sound_btn)

        # Test simulation button
        # Pin toggle button (Top vs Desktop)
        self.pin_btn = QPushButton("📌" if self.is_pinned_top else "📍", self.card)
        self.pin_btn.setFixedSize(24, 24)
        self.pin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pin_btn.setToolTip("سنجاق به رو / چسبیدن به دسکتاپ")
        self.pin_btn.setStyleSheet(self._button_style())
        self.pin_btn.clicked.connect(self._toggle_pin)
        header_layout.addWidget(self.pin_btn)

        # Close button
        self.close_btn = QPushButton("✕", self.card)
        self.close_btn.setFixedSize(24, 24)
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.setStyleSheet(self._close_button_style())
        self.close_btn.clicked.connect(self.close)
        header_layout.addWidget(self.close_btn)

        card_layout.addLayout(header_layout)

        # --- SEPARATOR ---
        card_layout.addWidget(self._create_divider())

        # --- PING SECTION ---
        net_row = QHBoxLayout()
        net_icon = QLabel("📶", self.card)
        net_icon.setStyleSheet("font-size: 14px;")
        net_title = QLabel("شبکه", self.card)
        net_title.setStyleSheet("color: #e2e8f0; font-size: 12px; font-weight: 500;")
        self.net_lbl = QLabel("...", self.card)
        self.net_lbl.setStyleSheet("color: #e2e8f0; font-size: 12px; font-weight: bold;")
        self.net_lbl.setTextFormat(Qt.TextFormat.PlainText)  # a network name is someone else's text, never markup
        net_row.addWidget(net_icon)
        net_row.addWidget(net_title)
        net_row.addStretch()
        net_row.addWidget(self.net_lbl)
        card_layout.addLayout(net_row)

        ping_header = QHBoxLayout()
        ping_icon = QLabel("🌐", self.card)
        ping_icon.setStyleSheet("font-size: 14px;")
        ping_title = QLabel("پینگ اینترنت", self.card)
        ping_title.setStyleSheet("color: #e2e8f0; font-size: 12px; font-weight: 500;")

        self.ping_value = QLabel("-- ms", self.card)
        self.ping_value.setStyleSheet("color: #38bdf8; font-family: Menlo, monospace; font-size: 13px; font-weight: bold;")

        ping_header.addWidget(ping_icon)
        ping_header.addWidget(ping_title)
        ping_header.addStretch()
        ping_header.addWidget(self.ping_value)
        card_layout.addLayout(ping_header)

        # Sparkline widget
        self.sparkline = SparklineWidget(self.card)
        card_layout.addWidget(self.sparkline)

        # --- LIVE NETWORK TRAFFIC SECTION ---
        traffic_rate_row = QHBoxLayout()
        traffic_icon = QLabel("📶", self.card)
        traffic_icon.setStyleSheet("font-size: 13px;")
        traffic_title = QLabel("سرعت نت", self.card)
        traffic_title.setStyleSheet("color: #e2e8f0; font-size: 12px; font-weight: 500;")
        self.traffic_rate_lbl = QLabel("↓ 0 KB/s · ↑ 0 KB/s", self.card)
        self.traffic_rate_lbl.setStyleSheet("color: #94a3b8; font-family: Menlo, monospace; font-size: 11px; font-weight: bold;")
        traffic_rate_row.addWidget(traffic_icon)
        traffic_rate_row.addWidget(traffic_title)
        traffic_rate_row.addStretch()
        traffic_rate_row.addWidget(self.traffic_rate_lbl)
        card_layout.addLayout(traffic_rate_row)

        traffic_total_row = QHBoxLayout()
        self.traffic_total_lbl = QLabel("مصرف دوره: \u20660 KB\u2069", self.card)
        self.traffic_total_lbl.setStyleSheet("color: #cbd5e1; font-size: 11px;")
        self.traffic_reset_btn = QPushButton("↺", self.card)
        self.traffic_reset_btn.setFixedSize(22, 22)
        self.traffic_reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.traffic_reset_btn.setToolTip("صفر کردن شمارنده مصرف نت")
        self.traffic_reset_btn.setStyleSheet(self._button_style() + "QPushButton { font-size: 12px; padding: 0; }")
        self.traffic_reset_btn.clicked.connect(self._reset_traffic)
        traffic_total_row.addWidget(self.traffic_total_lbl)
        traffic_total_row.addStretch()
        traffic_total_row.addWidget(self.traffic_reset_btn)
        card_layout.addLayout(traffic_total_row)

        # --- SEPARATOR ---
        card_layout.addWidget(self._create_divider())

        # --- VPN SECTION ---
        vpn_row = QHBoxLayout()
        vpn_icon = QLabel("🛡", self.card)
        vpn_icon.setStyleSheet("font-size: 14px;")
        vpn_title = QLabel("وی‌پی‌ان", self.card)
        vpn_title.setStyleSheet("color: #e2e8f0; font-size: 12px; font-weight: 500;")
        self.vpn_lbl = QLabel("...", self.card)
        self.vpn_lbl.setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: bold;")
        self.vpn_btn = QPushButton("روشن", self.card)
        self.vpn_btn.setFixedHeight(24)
        self.vpn_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.vpn_btn.setToolTip(f"روشن/خاموش کردن {VPN_NAME}")
        self.vpn_btn.setStyleSheet(self._button_style() + "QPushButton { padding: 0 10px; font-size: 11px; }")
        self.vpn_btn.setEnabled(False)
        self.vpn_btn.clicked.connect(self._toggle_vpn)
        vpn_row.addWidget(vpn_icon)
        vpn_row.addWidget(vpn_title)
        vpn_row.addStretch()
        vpn_row.addWidget(self.vpn_lbl)
        vpn_row.addWidget(self.vpn_btn)
        card_layout.addLayout(vpn_row)

        # --- SEPARATOR ---
        card_layout.addWidget(self._create_divider())

        # --- RAM & SWAP SECTION ---
        ram_header = QHBoxLayout()
        ram_icon = QLabel("⚡", self.card)
        ram_icon.setStyleSheet("font-size: 14px;")
        ram_title = self.ram_title = QLabel("مصرف رم", self.card)
        ram_title.setStyleSheet("color: #e2e8f0; font-size: 12px; font-weight: 500;")

        self.ram_val_lbl = QLabel("-- / 16.0 GB", self.card)
        self.ram_val_lbl.setStyleSheet("color: #e2e8f0; font-family: Menlo, monospace; font-size: 12px;")

        ram_header.addWidget(ram_icon)
        ram_header.addWidget(ram_title)
        ram_header.addStretch()
        ram_header.addWidget(self.ram_val_lbl)
        card_layout.addLayout(ram_header)

        # RAM Progress Bar
        self.ram_bar = QProgressBar(self.card)
        self.ram_bar.setRange(0, 100)
        self.ram_bar.setValue(0)
        self.ram_bar.setTextVisible(False)
        self.ram_bar.setFixedHeight(8)
        self._update_bar_style("green")
        card_layout.addWidget(self.ram_bar)

        # Swap & Pressure status
        swap_layout = QHBoxLayout()
        self.swap_lbl = QLabel("سواپ: 0 MB", self.card)
        self.swap_lbl.setStyleSheet("color: #e2e8f0; font-size: 11px;")

        self.ram_status_badge = QLabel("رم عادی", self.card)
        self.ram_status_badge.setStyleSheet(
            "background-color: rgba(16, 185, 129, 0.15); color: #10b981; "
            "padding: 2px 7px; border-radius: 6px; font-size: 10px; font-weight: bold;"
        )

        swap_layout.addWidget(self.swap_lbl)
        swap_layout.addStretch()
        swap_layout.addWidget(self.ram_status_badge)
        card_layout.addLayout(swap_layout)

        # --- CHIP TEMPERATURE ---
        self.temp_box = QWidget(self.card)
        temp_v = QVBoxLayout(self.temp_box)
        temp_v.setContentsMargins(0, 2, 0, 0)
        temp_v.setSpacing(5)
        temp_header = QHBoxLayout()
        temp_icon = QLabel("🌡", self.temp_box)
        temp_icon.setStyleSheet("font-size: 14px;")
        temp_title = QLabel("دمای تراشه (رم هم روی همینه)", self.temp_box)
        temp_title.setStyleSheet("color: #e2e8f0; font-size: 12px; font-weight: 500;")
        self.temp_box.setToolTip("مک‌های M سنسور جدا برای رم ندارن؛ رم داخل همون بسته‌ی تراشه‌ست.\nاین عدد داغ‌ترین سنسور خود تراشه‌ست.")
        self.temp_val = QLabel("-- °C", self.temp_box)
        self.temp_badge = QLabel("", self.temp_box)
        temp_header.addWidget(temp_icon)
        temp_header.addWidget(temp_title)
        temp_header.addStretch()
        temp_header.addWidget(self.temp_badge)
        temp_header.addWidget(self.temp_val)
        temp_v.addLayout(temp_header)
        self.temp_bar = QProgressBar(self.temp_box)
        self.temp_bar.setRange(30, 100)
        self.temp_bar.setTextVisible(False)
        self.temp_bar.setFixedHeight(6)
        temp_v.addWidget(self.temp_bar)
        self.temp_box.setVisible(False)
        card_layout.addWidget(self.temp_box)

        # --- SMART ALERT BANNER (Hidden by default) ---
        self.alert_banner = QLabel("", self.card)
        self.alert_banner.setVisible(False)
        self.alert_banner.setWordWrap(True)
        self.alert_banner.setStyleSheet(
            "background-color: rgba(239, 68, 68, 0.2); border: 1px solid rgba(239, 68, 68, 0.5); "
            "color: #fca5a5; padding: 6px 10px; border-radius: 8px; font-size: 11px; font-weight: bold;"
        )
        card_layout.addWidget(self.alert_banner)

        # --- TOP APPS SECTION ---
        self.apps_container = QWidget(self.card)
        apps_layout = QVBoxLayout(self.apps_container)
        apps_layout.setContentsMargins(0, 4, 0, 0)
        apps_layout.setSpacing(3)

        self.apps_title = QLabel("برنامه‌های سنگین در حافظه:", self.apps_container)
        self.apps_title.setStyleSheet("color: #e2e8f0; font-size: 10px; font-weight: bold;")
        apps_layout.addWidget(self.apps_title)

        self.app_labels = [QLabel("", self.apps_container) for _ in range(3)]
        for lbl in self.app_labels:
            lbl.setStyleSheet("color: #f8fafc; font-size: 11px; font-family: Menlo, monospace;")
            apps_layout.addWidget(lbl)

        card_layout.addWidget(self.apps_container)

        self._init_mini()
        # the card itself stays see-through (the real glass sits behind it); the solid panel inside leaves a glass rim around it
        card_outer = QVBoxLayout(self.card)
        card_outer.setContentsMargins(RIM, RIM, RIM, RIM)
        card_outer.setSpacing(0)
        self.panel = QFrame(self.card)
        self.panel.setObjectName("panel")
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)
        panel_layout.addWidget(self.full_box)
        panel_layout.addWidget(self.mini_box)
        card_outer.addWidget(self.panel)
        self._update_card_style(self._card_state)
        root_layout.addWidget(self.card)
        self.full_box.setVisible(not self.collapsed)
        self.mini_box.setVisible(self.collapsed)
        self.mini_timer = QTimer(self)
        self.mini_timer.timeout.connect(self._refresh_mini)
        self.mini_timer.start(1000)
        self._refresh_mini()

    # --- SMALL CARD: ping, traffic, RAM, VPN and chip temperature in a square about the size of the clock widget ---
    def _init_mini(self):
        self.mini_box = QWidget(self.card)
        v = QVBoxLayout(self.mini_box)
        v.setContentsMargins(12, 8, 12, 9)
        v.setSpacing(3)
        top = QHBoxLayout()
        self.mini_dot = QLabel("●", self.mini_box)
        self.mini_dot.setStyleSheet("color: #10b981; font-size: 13px;")
        self.mini_vpn = QLabel("", self.mini_box)
        top.addWidget(self.mini_dot)
        top.addStretch()
        top.addWidget(self.mini_vpn)
        v.addLayout(top)
        self.mini_net = QLabel("...", self.mini_box)
        self.mini_net.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mini_net.setStyleSheet("color: #cbd5e1; font-size: 11px; font-weight: bold;")
        self.mini_net.setTextFormat(Qt.TextFormat.PlainText)
        self.mini_net.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)  # a long name must not widen the card
        v.addWidget(self.mini_net)
        self.mini_ping = QLabel("-- ms", self.mini_box)
        self.mini_ping.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.mini_ping)
        self.mini_spark = SparklineWidget(self.mini_box)
        self.mini_spark.setFixedHeight(22)
        v.addWidget(self.mini_spark)
        self.mini_traffic = QLabel("↓0K ↑0K · 0K", self.mini_box)
        self.mini_traffic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mini_traffic.setStyleSheet("color: #94a3b8; font-family: Menlo, monospace; font-size: 10px; font-weight: bold;")
        v.addWidget(self.mini_traffic)
        row = QHBoxLayout()
        self.mini_ram = QLabel("", self.mini_box)
        self.mini_temp = QLabel("", self.mini_box)
        row.addWidget(self.mini_ram)
        row.addStretch()
        row.addWidget(self.mini_temp)
        v.addLayout(row)
        self.mini_bar = QProgressBar(self.mini_box)
        self.mini_bar.setRange(0, 100)
        self.mini_bar.setTextVisible(False)
        self.mini_bar.setFixedHeight(6)
        v.addWidget(self.mini_bar)

    @staticmethod
    def _css_color(sheet, default="#e2e8f0"):
        m = re.search(r"(?<![-\w])color:\s*([^;]+);", sheet or "")
        return m.group(1).strip() if m else default

    def _on_network(self, name, why):
        self.network_name = name
        tip = f"{name}\n{why}" if why else name
        # Elided on the small card only; the full name stays in the tooltip.
        room = max(40, MINI_W - 2 * (10 + RIM + 12))
        self.mini_net.setText(self.mini_net.fontMetrics().elidedText(name, Qt.TextElideMode.ElideMiddle, room))
        self.mini_net.setToolTip(tip)
        self.net_lbl.setText(self.net_lbl.fontMetrics().elidedText(name, Qt.TextElideMode.ElideMiddle, FULL_W - 150))
        self.net_lbl.setToolTip(tip)

    def _refresh_mini(self):
        """Copy the live values from the full card's labels, so the two cards can never disagree."""
        self._keep_glass_awake()
        mono = "font-family: Menlo, monospace; font-weight: bold;"
        c = self._css_color(self.ping_value.styleSheet())
        up = "ms" in self.ping_value.text()
        self.mini_ping.setText(self.ping_value.text() if up else "قطع ✕")  # the full "قطع ارتباط ✕" does not fit
        self.mini_ping.setStyleSheet(f"color: {c}; font-size: {20 if up else 18}px; {mono}")
        self.mini_spark.data = list(self.sparkline.data)
        self.mini_spark.line_color, self.mini_spark.fill_color = self.sparkline.line_color, self.sparkline.fill_color
        self.mini_spark.update()
        td = self.traffic_data
        total_b = td.get("rx_total", 0) + td.get("tx_total", 0)
        self.mini_traffic.setText(
            f"↓{format_rate(td.get('rx_bps', 0), compact=True)} "
            f"↑{format_rate(td.get('tx_bps', 0), compact=True)} · "
            f"{format_bytes(total_b, compact=True)}"
        )
        self.mini_traffic.setStyleSheet(
            f"color: {self._css_color(self.traffic_rate_lbl.styleSheet(), '#94a3b8')}; "
            f"font-family: Menlo, monospace; font-size: 10px; font-weight: bold;"
        )
        self.mini_traffic.setToolTip(self.traffic_total_lbl.toolTip())
        self.mini_dot.setStyleSheet(self.status_dot.styleSheet().replace("15px", "13px"))
        word = {"Connected": "وصل ✓", "Connecting": "...", "Reasserting": "...", "Disconnecting": "...",
                "Disconnected": "قطع ✕" if self.vpn_dropped else "قطع"}.get(self.vpn_state, "—")
        col = "#ef4444" if self.vpn_dropped else self._css_color(self.vpn_lbl.styleSheet(), "#94a3b8")
        self.mini_vpn.setText("🛡 " + word)
        self.mini_vpn.setStyleSheet(f"color: {col}; font-size: 11px; font-weight: bold;")
        self.mini_ram.setText(f"⚡ {self.ram_bar.value()}%")
        self.mini_ram.setStyleSheet(f"color: #e2e8f0; font-size: 12px; {mono}")
        self.mini_bar.setValue(self.ram_bar.value())
        self.mini_bar.setStyleSheet(self.ram_bar.styleSheet())
        if self.temp_box.isHidden():
            self.mini_temp.setText("")
        else:
            self.mini_temp.setText("🌡 " + self.temp_val.text().replace(" °C", "°"))
            self.mini_temp.setStyleSheet(f"color: {self._css_color(self.temp_val.styleSheet())}; font-size: 12px; {mono}")

    def _set_collapsed(self, on):
        before = self.pos()
        self.collapsed = on
        self._refresh_mini()
        self.full_box.setVisible(not on)
        self.mini_box.setVisible(on)
        self.setFixedWidth(MINI_W if on else FULL_W)
        self.setMinimumHeight(0)
        self.setMaximumHeight(16777215)
        self.layout().activate()
        self.resize(self.width(), self.layout().sizeHint().height())
        scr = self.screen().availableGeometry() if self.screen() else None
        if scr:  # keep the top-left corner, but never let the bigger card run off the screen
            self.move(max(scr.left(), min(self.x(), scr.right() - self.width())), max(scr.top(), min(self.y(), scr.bottom() - self.height())))
        # Opening near a screen edge pushes the card inward; remember where the small card was and put it back on collapse,
        # unless the user moved the open card meanwhile (then the small card simply takes the open card's top-left corner).
        if not on:
            self._small_pos, self._open_pos = before, self.pos()
        elif getattr(self, "_small_pos", None) is not None and before == self._open_pos:
            self.move(self._small_pos)
        self._save_config()

    def _create_divider(self):
        line = QFrame(self.card)
        line.setFrameShape(QFrame.Shape.NoFrame)
        line.setFixedHeight(1)
        line.setStyleSheet("background-color: rgba(255, 255, 255, 0.16); border: none;")
        return line

    def _button_style(self):
        return """
            QPushButton {
                background-color: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                color: #cbd5e1;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.18);
            }
        """

    def _close_button_style(self):
        return """
            QPushButton {
                background-color: rgba(239, 68, 68, 0.12);
                border: 1px solid rgba(239, 68, 68, 0.25);
                border-radius: 6px;
                color: #f87171;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: rgba(239, 68, 68, 0.4);
                color: #ffffff;
            }
        """

    def _alpha(self, solid, glass):
        return round(glass * self.glass_scale, 3) if self.glass else solid

    def _update_card_style(self, state):
        self._card_state = state
        if state == "danger":
            border = "2px solid rgba(239, 68, 68, 0.85);"
            glow = "background-color: rgba(60, 14, 20, 0.94);"
        elif state == "warning":
            border = "1px solid rgba(245, 158, 11, 0.55);"
            glow = "background-color: rgba(16, 22, 36, 0.93);"
        else:
            border = "none;"  # no line of its own: the glass edge is the only frame
            glow = "background-color: rgba(16, 22, 36, 0.93);"

        if not hasattr(self, "panel"):
            return
        self.panel.setStyleSheet(f"""
            QFrame#panel {{
                {glow}
                border: {border}
                border-radius: {PANEL_RADIUS}px;
            }}
        """)

    def _update_bar_style(self, mode):
        if mode == "danger":
            grad = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ef4444, stop:1 #dc2626);"
        elif mode == "warning":
            grad = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #f59e0b, stop:1 #ea580c);"
        else:
            grad = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #10b981, stop:1 #06b6d4);"

        self.ram_bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: rgba(255, 255, 255, 0.08);
                border-radius: 4px;
            }}
            QProgressBar::chunk {{
                background: {grad};
                border-radius: 4px;
            }}
        """)

    def _play_sound(self, sound_path):
        if self.sound_enabled and os.path.exists(sound_path):
            try:
                subprocess.Popen(["afplay", sound_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass

    def _init_workers(self):
        # Ping Worker
        self.ping_worker = PingWorker()
        self.ping_worker.ping_result.connect(self._on_live_ping)
        self.ping_worker.start()

        # VPN Worker
        self.vpn_worker = VpnWorker()
        self.vpn_worker.state_result.connect(self._on_vpn_state)
        self.vpn_worker.start()
        self.net_worker = NetworkWorker()
        self.net_worker.network_result.connect(self._on_network)
        self.net_worker.start()

        # Memory Worker
        self.memory_worker = MemoryWorker()
        self.memory_worker.memory_result.connect(self._on_live_memory)
        self.memory_worker.start()

        # Traffic Worker
        self.traffic_worker = TrafficWorker()
        self.traffic_worker.traffic_result.connect(self._on_live_traffic)
        self.traffic_worker.start()

    def _testing(self):
        return time.time() < self.test_until

    def _on_live_ping(self, latency, is_online):
        if not self._testing():
            self._on_ping_update(latency, is_online)

    def _on_live_memory(self, data):
        if not self._testing():
            self._on_memory_update(data)

    def _on_live_traffic(self, data):
        if not self._testing():
            self._on_traffic_update(data)

    def _on_traffic_update(self, data):
        self.traffic_data = dict(data)
        rx_bps = data.get("rx_bps", 0.0)
        tx_bps = data.get("tx_bps", 0.0)
        rx_total = data.get("rx_total", 0)
        tx_total = data.get("tx_total", 0)
        total = rx_total + tx_total
        active = (rx_bps + tx_bps) >= 1024
        col = "#38bdf8" if active else "#94a3b8"
        self.traffic_rate_lbl.setText(f"↓ {format_rate(rx_bps)} · ↑ {format_rate(tx_bps)}")
        self.traffic_rate_lbl.setStyleSheet(
            f"color: {col}; font-family: Menlo, monospace; font-size: 11px; font-weight: bold;"
        )
        self.traffic_total_lbl.setText(f"مصرف دوره: \u2066{format_bytes(total)}\u2069")
        self.traffic_total_lbl.setToolTip(
            f"دانلود کل: {format_bytes(rx_total)} | آپلود کل: {format_bytes(tx_total)}"
        )

    def _reset_traffic(self):
        if hasattr(self, "traffic_worker"):
            self.traffic_worker.reset()
        self._on_traffic_update({
            "iface": self.traffic_data.get("iface"),
            "rx_bps": self.traffic_data.get("rx_bps", 0.0),
            "tx_bps": self.traffic_data.get("tx_bps", 0.0),
            "rx_total": 0,
            "tx_total": 0,
        })
        self._refresh_mini()

    def _on_ping_update(self, latency, is_online):
        self.sparkline.add_point(latency)

        if not is_online or latency is None:
            self.ping_value.setText("قطع ارتباط ✕")
            self.ping_value.setStyleSheet("color: #ef4444; font-family: Menlo, monospace; font-size: 13px; font-weight: bold;")
            self.sparkline.set_color_mode("red")
            self._set_net_offline(True)
        elif latency < 80:
            self.ping_value.setText(f"{latency:.0f} ms")
            self.ping_value.setStyleSheet("color: #10b981; font-family: Menlo, monospace; font-size: 13px; font-weight: bold;")
            self.sparkline.set_color_mode("green")
            self._set_net_offline(False)
        elif latency < 200:
            self.ping_value.setText(f"{latency:.0f} ms")
            self.ping_value.setStyleSheet("color: #f59e0b; font-family: Menlo, monospace; font-size: 13px; font-weight: bold;")
            self.sparkline.set_color_mode("yellow")
            self._set_net_offline(False)
        else:
            self.ping_value.setText(f"{latency:.0f} ms")
            self.ping_value.setStyleSheet("color: #f97316; font-family: Menlo, monospace; font-size: 13px; font-weight: bold;")
            self.sparkline.set_color_mode("yellow")
            self._set_net_offline(False)

    def _on_memory_update(self, data):
        used_gb = data["used_gb"]
        total_gb = data["total_gb"]
        pct = data["used_pct"]
        swap_used_mb = data["swap_used_mb"]
        swap_recent_mb = data.get("swap_recent_mb", 0.0)
        pressure = data.get("pressure", 1)
        top_apps = data["top_apps"]

        self.ram_title.setText(f"مصرف رم ({total_gb:.0f} گیگ)")
        self.ram_val_lbl.setText(f"{used_gb:.1f}/{total_gb:.0f} GB · {pct:.0f}%")
        self._show_temp(data.get("chip_temp"))
        self.ram_bar.setValue(int(pct))

        # Format swap text
        if swap_used_mb > 1024:
            swap_text = f"سواپ: \u2066{swap_used_mb/1024.0:.1f} GB\u2069"
        else:
            swap_text = f"سواپ: \u2066{swap_used_mb:.1f} MB\u2069"
        self.swap_lbl.setText(swap_text)

        # Update Top Apps labels
        if top_apps:
            self.apps_container.setVisible(True)
            for i, lbl in enumerate(self.app_labels):
                if i < len(top_apps):
                    lbl.setText(f"{i+1}. {top_apps[i]}")
                    lbl.setVisible(True)
                else:
                    lbl.setVisible(False)
        else:
            self.apps_container.setVisible(False)

        # Memory is information, not an alarm: a Mac swapping and compressing memory under load is normal and it copes.
        # So memory never beeps, never blinks and never turns the card red (red + sound stay for internet / VPN drops).
        # Only heavy load that LASTS (macOS's own critical level or heavy swapping, for HEAVY_HOLD_S) gives a calm amber edge.
        heavy = pressure >= 4 or swap_recent_mb >= SWAP_HEAVY_MB
        now = time.time()
        if not heavy:
            self._heavy_since = None
        elif self._heavy_since is None:
            self._heavy_since = now
        if heavy and now - self._heavy_since >= HEAVY_HOLD_S:
            mins = max(1, int((now - self._heavy_since) // 60))
            self._set_mem_state("warning", f"رم {mins} دقیقه‌ست زیر بار سنگینه. مک داره هندلش می‌کنه؛ فقط اگه کند شد یه برنامه‌ی سنگین رو ببند.")
            self._update_bar_style("warning")
            self.ram_status_badge.setText("بار سنگین")
            self.ram_status_badge.setStyleSheet(
                "background-color: rgba(245, 158, 11, 0.16); color: #fbbf24; "
                "padding: 2px 7px; border-radius: 6px; font-size: 10px; font-weight: bold;"
            )
        elif pressure >= 2 or swap_recent_mb >= SWAP_WARN_MB:
            self._set_mem_state("normal", "")
            self._update_bar_style("green")
            self.ram_status_badge.setText("مک داره مدیریتش می‌کنه")
            self.ram_status_badge.setStyleSheet(
                "background-color: rgba(148, 163, 184, 0.16); color: #cbd5e1; "
                "padding: 2px 7px; border-radius: 6px; font-size: 10px; font-weight: bold;"
            )
        else:
            self._set_mem_state("normal", "")
            self._update_bar_style("green")
            self.ram_status_badge.setText("رم آرام")
            self.ram_status_badge.setStyleSheet(
                "background-color: rgba(16, 185, 129, 0.15); color: #10b981; "
                "padding: 2px 7px; border-radius: 6px; font-size: 10px; font-weight: bold;"
            )

    def _set_mem_state(self, state, msg):
        self.mem_state = state  # silent on purpose: memory load is shown, never announced
        self.mem_msg = msg
        self._render_alerts()

    def _set_net_offline(self, offline):
        if offline and not self.net_offline:
            self._play_sound(SOUND_WARN)  # once per outage, not every ping
        if offline != self.net_offline:
            self.net_offline = offline
            self._render_alerts()

    def _on_vpn_state(self, state):
        prev = self.vpn_state
        self.vpn_state = state
        if state == "Connected":
            self.vpn_armed = True
            self.vpn_user_off = False
            self.vpn_dropped = False
        elif state == "Disconnected" and prev != "Disconnected":
            # Only a drop out of a connection we were watching counts; a failed attempt to connect does not.
            if self.vpn_armed and not self.vpn_user_off:
                self.vpn_dropped = True
                self._play_sound(SOUND_VPN)  # once per drop; own sound so it is not confused with internet-offline
            self.vpn_armed = False
        self._show_vpn()
        self._render_alerts()

    def _show_vpn(self):
        s = self.vpn_state
        text, color = {
            "Connected": ("وصل ✓", "#10b981"),
            "Connecting": ("در حال وصل‌شدن...", "#f59e0b"),
            "Reasserting": ("در حال وصل‌شدن...", "#f59e0b"),
            "Disconnecting": ("در حال قطع...", "#f59e0b"),
            "Disconnected": ("قطع", "#ef4444" if self.vpn_dropped else "#94a3b8"),
            "missing": (f"{VPN_NAME} پیدا نشد", "#94a3b8"),
        }.get(s, ("نامشخص", "#94a3b8"))
        self.vpn_lbl.setText(text)
        self.vpn_lbl.setStyleSheet(f"color: {color}; font-size: 12px; font-weight: bold;")
        self.vpn_btn.setText("خاموش" if s in ("Connected", "Connecting", "Reasserting") else "روشن")
        self.vpn_btn.setEnabled(s in ("Connected", "Disconnected"))

    def _toggle_vpn(self):
        if self.vpn_state == "Connected":
            self.vpn_user_off = True   # our own stop: must stay silent
            cmd = "stop"
        elif self.vpn_state == "Disconnected":
            self.vpn_user_off = False
            self.vpn_dropped = False
            cmd = "start"
        else:
            return
        self.vpn_btn.setEnabled(False)
        try:
            subprocess.Popen(["scutil", "--nc", cmd, VPN_NAME], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
        self._render_alerts()

    def _render_alerts(self):
        """One place decides banner and card colour from all alerts together."""
        msgs = (["هشدار: اتصال اینترنت قطع شد!"] if self.net_offline else []) \
            + ([f"هشدار: {VPN_NAME} ناگهانی قطع شد!"] if self.vpn_dropped else []) \
            + ([self.mem_msg] if self.mem_msg else [])
        if msgs:
            self.alert_banner.setText("\n".join(msgs))
            loud = self.net_offline or self.vpn_dropped  # red only for real outages; a memory note stays calm amber
            c = "239, 68, 68" if loud else "245, 158, 11"
            self.alert_banner.setStyleSheet(
                f"background-color: rgba({c}, {0.2 if loud else 0.12}); border: 1px solid rgba({c}, {0.5 if loud else 0.35}); "
                f"color: {'#fca5a5' if loud else '#fcd34d'}; padding: 6px 10px; border-radius: 8px; font-size: 11px; font-weight: bold;")
            self.alert_banner.setVisible(True)
        else:
            self.alert_banner.setVisible(False)

        state = "danger" if self.net_offline or self.vpn_dropped else self.mem_state
        dot = {"danger": "#ef4444", "warning": "#f59e0b"}.get(state, "#10b981")
        self.status_dot.setStyleSheet(f"color: {dot}; font-size: 15px;")
        if state == self.shown_state:
            return
        self.shown_state = state
        self._update_card_style(state)

    def _toggle_sound(self):
        self.sound_enabled = not self.sound_enabled
        self.sound_btn.setText("🔊" if self.sound_enabled else "🔇")
        self._save_config()

    def _toggle_pin(self):
        self.is_pinned_top = not self.is_pinned_top
        self.pin_btn.setText("📌" if self.is_pinned_top else "📍")
        pos = self.pos()
        self._init_window_flags()
        self.show()
        self.move(pos)
        QTimer.singleShot(50, self.install_glass)  # changing flags can give Qt a new native window
        self._save_config()

    def _cycle_test_state(self):
        """Simulate and cycle through all visual & sound alert states for testing."""
        if not hasattr(self, "_test_idx"):
            self._test_idx = 0
        else:
            self._test_idx = (self._test_idx + 1) % 4

        idx = self._test_idx
        self.test_until = time.time() + TEST_HOLD_S
        QTimer.singleShot(int(TEST_HOLD_S * 1000) + 100, self._end_test)
        if idx == 0:
            # Normal State
            self._on_ping_update(38.0, True)
            self._on_memory_update({
                "used_gb": 8.2, "total_gb": 16.0, "used_pct": 51.0,
                "swap_used_mb": 0.0, "swap_total_mb": 1024.0, "swap_recent_mb": 0.0, "pressure": 1,
                "top_apps": ["Chrome: 2.1 GB", "Slack: 850 MB", "Code: 620 MB"]
            })
            self.title_label.setText("پایشگر مک (تست: عادی)")
        elif idx == 1:
            # Warning State
            self._on_ping_update(115.0, True)
            self._on_memory_update({
                "used_gb": 12.8, "total_gb": 16.0, "used_pct": 80.0,
                "swap_used_mb": 95.0, "swap_total_mb": 1024.0, "swap_recent_mb": 0.0, "pressure": 2,
                "top_apps": ["Chrome: 4.8 GB", "Docker: 3.2 GB", "Code: 1.4 GB"]
            })
            self.title_label.setText("پایشگر مک (تست: هشدار رم)")
        elif idx == 2:
            # Danger State
            self._on_ping_update(210.0, True)
            self._on_memory_update({
                "used_gb": 14.6, "total_gb": 16.0, "used_pct": 91.0,
                "swap_used_mb": 920.0, "swap_total_mb": 2048.0, "swap_recent_mb": 300.0, "pressure": 4,
                "top_apps": ["Docker: 6.2 GB", "Chrome: 4.5 GB", "Python: 2.8 GB"]
            })
            self.title_label.setText("پایشگر مک (تست: خطر سواپ)")
        elif idx == 3:
            # Net Offline
            self._on_ping_update(None, False)
            self.title_label.setText("پایشگر مک (تست: قطعی نت)")

    def _end_test(self):
        if not self._testing():
            self.title_label.setText("پایشگر مک (زنده)")

    def _show_temp(self, t):
        if t is None:
            self.temp_box.setVisible(False)
            return
        self.temp_box.setVisible(True)
        r, g, b = temp_color(t)
        word = "خنک" if t < 60 else ("گرم" if t < 80 else "داغ")
        self.temp_val.setText(f"{t:.0f} °C")
        self.temp_val.setStyleSheet(f"color: rgb({r},{g},{b}); font-family: Menlo, monospace; font-size: 13px; font-weight: bold;")
        self.temp_badge.setText(word)
        self.temp_badge.setStyleSheet(
            f"background-color: rgba({r},{g},{b},0.18); color: rgb({r},{g},{b}); "
            "padding: 2px 7px; border-radius: 6px; font-size: 10px; font-weight: bold;")
        self.temp_bar.setValue(int(max(30, min(100, t))))
        self.temp_bar.setStyleSheet(f"""
            QProgressBar {{ background-color: rgba(255, 255, 255, 0.08); border-radius: 3px; }}
            QProgressBar::chunk {{ background-color: rgb({r},{g},{b}); border-radius: 3px; }}
        """)

    # --- GLASS: a native NSVisualEffectView (real background blur) placed behind the card ---
    def install_glass(self):
        try:
            o = ObjC()
            view = int(self.winId())
            win = o.send(view, "window")
            host = o.send(view, "superview") if win else None
            if not host:
                return
            if self._fx:
                o.send(self._fx, "removeFromSuperview")
            L = ctypes.c_long
            # macOS draws its own window shadow around whatever is opaque; together with the glass's own shadow
            # that made a second, offset frame under the card. The glass is the only frame we want.
            o.send(win, "setHasShadow:", False, argtypes=[ctypes.c_bool])
            glass_cls = o.cls("NSGlassEffectView")            # macOS 26+: the same "Liquid Glass" the system widgets use
            if glass_cls:
                fx = o.send(o.send(glass_cls, "alloc"), "initWithFrame:", NSRect(0, 0, 10, 10), argtypes=[NSRect])
                o.send(fx, "setCornerRadius:", float(PANEL_RADIUS + RIM - 1), argtypes=[ctypes.c_double])
                o.send(fx, "setStyle:", GLASS_STYLE, argtypes=[L])
                if GLASS_SHADE > 0:  # a faint dark tint: the system widgets' glass sits a touch darker than the wallpaper, ours looked milky
                    tint = o.send(o.cls("NSColor"), "colorWithWhite:alpha:", 0.0, GLASS_SHADE, argtypes=[ctypes.c_double, ctypes.c_double])
                    o.send(fx, "setTintColor:", tint, argtypes=[ctypes.c_void_p])
                # the glass's own drop shadow spills outside it and the window edge cut it into a faint hard rectangle
                o.send(fx, "setClipsToBounds:", True, argtypes=[ctypes.c_bool])
                if GLASS_DARK:  # the glass turns light over a bright wallpaper and our white text would wash out; pin it to the dark look
                    name = o.send(o.cls("NSString"), "stringWithUTF8String:", b"NSAppearanceNameDarkAqua", argtypes=[ctypes.c_char_p])
                    o.send(fx, "setAppearance:", o.send(o.cls("NSAppearance"), "appearanceNamed:", name, argtypes=[ctypes.c_void_p]), argtypes=[ctypes.c_void_p])
                self.glass_scale = GLASS_TINT
            else:                                              # older macOS: plain frosted blur
                fx = o.send(o.send(o.cls("NSVisualEffectView"), "alloc"), "initWithFrame:", NSRect(0, 0, 10, 10), argtypes=[NSRect])
                o.send(fx, "setMaterial:", 13, argtypes=[L])      # HUD window: dark, frosted
                o.send(fx, "setBlendingMode:", 0, argtypes=[L])   # blur what is behind the window
                o.send(fx, "setState:", 1, argtypes=[L])          # stay frosted even when the app is not active
                o.send(fx, "setWantsLayer:", True, argtypes=[ctypes.c_bool])
                layer = o.send(fx, "layer")
                o.send(layer, "setCornerRadius:", 20.0, argtypes=[ctypes.c_double])
                o.send(layer, "setMasksToBounds:", True, argtypes=[ctypes.c_bool])
                self.glass_scale = 1.0
            o.send(host, "addSubview:positioned:relativeTo:", fx, -1, view, argtypes=[ctypes.c_void_p, L, ctypes.c_void_p])  # below Qt's view
            self._objc, self._fx, self._fx_view, self._fx_host = o, fx, view, host
            self.glass = True
            self.card.installEventFilter(self)
            self._place_glass()
            self._update_card_style(self._card_state)
        except Exception as e:
            print("glass not available:", e, file=sys.stderr)

    def _keep_glass_awake(self):
        """NSGlassEffectView dims itself ("subdued") whenever our window is not the key window, which is almost always."""
        if self._fx and self.glass_scale is not None:
            try:
                self._objc.send(self._fx, "set_subduedState:", 0, argtypes=[ctypes.c_long])
            except Exception:
                pass

    def _place_glass(self):
        if not self._fx:
            return
        g = self.card.geometry()  # Qt's view is flipped (top-left origin), same as these coordinates
        r = self._objc.send(self._fx_view, "convertRect:toView:", NSRect(g.x(), g.y(), g.width(), g.height()), self._fx_host,
                            argtypes=[NSRect, ctypes.c_void_p], restype=NSRect)
        self._objc.send(self._fx, "setFrame:", r, argtypes=[NSRect])

    def eventFilter(self, obj, event):
        if obj is self.card and event.type() in (QEvent.Type.Resize, QEvent.Type.Move):
            self._place_glass()
        return False

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        event.accept()

    # --- DRAG & DROP LOGIC ---
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._press_pos = event.globalPosition().toPoint()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and not self.drag_position.isNull():
            self.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()

    def mouseReleaseEvent(self, event):
        # a click (not a drag) on the small card opens the full card
        click = (event.globalPosition().toPoint() - self._press_pos).manhattanLength() < 5
        if self.collapsed and click and event.button() == Qt.MouseButton.LeftButton:
            self._set_collapsed(False)
        else:
            self._save_config()
        event.accept()

    def closeEvent(self, event):
        self._save_config()
        self.ping_worker.stop()
        self.memory_worker.stop()
        self.vpn_worker.stop()
        self.vpn_worker.wait()
        self.net_worker.stop()
        self.net_worker.wait()
        self.traffic_worker.stop()
        self.traffic_worker.wait()
        self.ping_worker.wait()
        self.memory_worker.wait()
        event.accept()


def _single_instance():
    """Second copy (double-click while it already runs) exits quietly. The lock dies with the process."""
    import fcntl
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    f = open(LOCK_PATH, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit(0)
    return f


def _hide_dock_icon():
    """No Python rocket in the Dock (NSApplicationActivationPolicyAccessory), via the ObjC runtime — no pyobjc needed."""
    try:
        import ctypes
        import ctypes.util
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.sel_registerName.restype = ctypes.c_void_p
        send = objc.objc_msgSend
        send.restype = ctypes.c_void_p
        send.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        nsapp = send(objc.objc_getClass(b"NSApplication"), objc.sel_registerName(b"sharedApplication"))
        send.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
        send(nsapp, objc.sel_registerName(b"setActivationPolicy:"), 1)
    except Exception:
        pass


def main():
    lock = _single_instance()  # noqa: F841 — must stay open for the life of the app
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    _hide_dock_icon()
    widget = MacMonitorWidget()
    widget.show()
    QTimer.singleShot(0, widget.install_glass)

    if "--demo" in sys.argv:
        # Auto cycle every 3.5 seconds
        demo_timer = QTimer(widget)
        demo_timer.timeout.connect(widget._cycle_test_state)
        demo_timer.start(3500)
        widget._cycle_test_state()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()

