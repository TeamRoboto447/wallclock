"""Network health probe shared by the kiosk icon and netwatch.py.

Needs no privileges: it reads /proc and /sys, pings the gateway, and opens a
TCP connection to a public address (no DNS involved).
"""
import socket
import struct
import subprocess

INET_PROBES = (("1.1.1.1", 443), ("8.8.8.8", 53))


def default_route():
    """(interface, gateway ip) of the default route, or (None, None)."""
    try:
        with open("/proc/net/route") as f:
            for line in f.read().splitlines()[1:]:
                p = line.split()
                if p[1] == "00000000" and int(p[3], 16) & 2:
                    return p[0], socket.inet_ntoa(struct.pack("<L", int(p[2], 16)))
    except (OSError, IndexError, ValueError):
        pass
    return None, None


def signal_dbm(iface):
    try:
        with open("/proc/net/wireless") as f:
            for line in f.read().splitlines()[2:]:
                p = line.split()
                if p and p[0].rstrip(":") == iface:
                    return float(p[3].rstrip("."))
    except (OSError, IndexError, ValueError):
        pass
    return None


def bars(dbm, wired=False):
    """0-3 signal bars; a wired link always shows full."""
    if wired:
        return 3
    if dbm is None:
        return 1
    return 3 if dbm > -60 else 2 if dbm > -70 else 1


def classify(link, gateway_ok, internet_ok):
    if not link or not gateway_ok:
        return "down"
    return "ok" if internet_ok else "limited"


def _ping(host):
    try:
        return subprocess.run(
            ["ping", "-c", "1", "-W", "2", host],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _tcp(host, port):
    try:
        with socket.create_connection((host, port), timeout=3):
            return True
    except OSError:
        return False


def probe():
    iface, gateway = default_route()
    gateway_ok = bool(gateway) and _ping(gateway)
    internet_ok = gateway_ok and any(_tcp(h, p) for h, p in INET_PROBES)
    wired = bool(iface) and not iface.startswith("wl")
    dbm = None if wired or not iface else signal_dbm(iface)
    return {
        "iface": iface,
        "gateway": gateway,
        "gateway_ok": gateway_ok,
        "internet_ok": internet_ok,
        "signal_dbm": dbm,
        "bars": bars(dbm, wired),
        "state": classify(bool(iface), gateway_ok, internet_ok),
    }
