#!/usr/bin/env python3
"""Network watchdog: reconnect when the link drops and log why.

Runs as root (deploy/tvgui-netwatch.service). Every INTERVAL seconds it checks
the default route and gateway. After consecutive failures it escalates:

    2 fails  nmcli device connect      (~30 s)
    4 fails  wifi radio off/on         (~1 min)
    8 fails  restart NetworkManager    (~2 min)
   20 fails  reload the wifi driver    (~5 min, at most once per 30 min)

It never reboots unless NETWATCH_REBOOT_AFTER=<minutes> is set. Every drop and
recovery is written to LOG with a snapshot (signal, power/undervoltage flags,
NetworkManager and kernel messages) so the cause can be read afterwards.
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from netstatus import probe

INTERVAL = int(os.environ.get("NETWATCH_INTERVAL", "15"))
LOG = os.environ.get("NETWATCH_LOG", "/var/lib/tvgui/netwatch.log")
LOG_MAX = 1_000_000
DRIVER_COOLDOWN = 30 * 60
ACTIONS = ((2, "reconnect"), (4, "radio-cycle"), (8, "restart-nm"), (20, "reload-driver"))

# vcgencmd get_throttled bits
THROTTLE_BITS = {
    0: "undervoltage now", 1: "arm freq capped now", 2: "throttled now",
    3: "soft temp limit now", 16: "undervoltage since boot",
    17: "freq capped since boot", 18: "throttled since boot", 19: "soft temp limit since boot",
}


def action_for(fails):
    """The escalation step to run when the failure count reaches `fails`."""
    return dict(ACTIONS).get(fails)


def decode_throttled(value):
    return [name for bit, name in THROTTLE_BITS.items() if value >> bit & 1] or ["ok"]


def run(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout + r.stderr).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return f"[{cmd[0]}: {e}]"


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > LOG_MAX:
            with open(LOG, "rb") as f:
                f.seek(-LOG_MAX // 5, os.SEEK_END)
                tail = f.read().split(b"\n", 1)[-1]
            with open(LOG, "wb") as f:
                f.write(tail)
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def snapshot(p):
    iface = p["iface"] or "wlan0"
    throttled = run(["vcgencmd", "get_throttled"])
    try:
        flags = decode_throttled(int(throttled.split("=")[1], 16))
    except (IndexError, ValueError):
        flags = [throttled]
    lines = [
        f"  route: iface={p['iface']} gateway={p['gateway']} gw_ok={p['gateway_ok']} "
        f"internet_ok={p['internet_ok']} signal={p['signal_dbm']} dBm",
        f"  power: {', '.join(flags)} ({run(['vcgencmd', 'measure_temp'])})",
        f"  nm: {run(['nmcli', '-t', '-f', 'DEVICE,STATE,CONNECTION', 'device'])!r}",
        f"  dns: {run(['getent', 'hosts', 'tasks.teamroboto.org'], 5) or 'FAILED'}",
    ]
    recent = run(["journalctl", "-u", "NetworkManager", "-u", "wpa_supplicant",
                  "--since", "-3min", "--no-pager", "-o", "short"])
    kernel = run(["sh", "-c", f"dmesg -T | grep -i -E 'brcmf|{iface}|undervolt' | tail -6"])
    for label, text in (("nm/wpa", recent), ("kernel", kernel)):
        for row in text.splitlines()[-8:]:
            lines.append(f"  {label}: {row}")
    return "\n".join(lines)


def do_action(name, iface):
    log(f"action: {name}")
    if name == "reconnect":
        out = run(["nmcli", "device", "connect", iface], 30)
    elif name == "radio-cycle":
        run(["nmcli", "radio", "wifi", "off"])
        time.sleep(2)
        out = run(["nmcli", "radio", "wifi", "on"])
    elif name == "restart-nm":
        out = run(["systemctl", "restart", "NetworkManager"], 60)
    elif name == "reload-driver":
        run(["modprobe", "-r", "brcmfmac"], 30)
        time.sleep(2)
        out = run(["modprobe", "brcmfmac"], 30)
    else:
        return
    log(f"action {name} result: {out or 'ok'}")


def main():
    reboot_after = int(os.environ.get("NETWATCH_REBOOT_AFTER", "0")) * 60
    fails = 0
    down_since = None
    last_driver = 0.0
    log(f"netwatch started (interval {INTERVAL}s)")
    while True:
        p = probe()
        if p["gateway_ok"]:
            if down_since is not None:
                log(f"UP after {int(time.time() - down_since)}s\n{snapshot(p)}")
            fails, down_since = 0, None
            if not p["internet_ok"]:
                if fails == 0 and int(time.time()) % 300 < INTERVAL:
                    log("gateway reachable but no internet (not a local link problem)")
        else:
            fails += 1
            if down_since is None:
                down_since = time.time()
                log(f"DOWN\n{snapshot(p)}")
            step = action_for(fails)
            if step == "reload-driver" and time.time() - last_driver < DRIVER_COOLDOWN:
                step = None
            if step:
                if step == "reload-driver":
                    last_driver = time.time()
                do_action(step, p["iface"] or "wlan0")
            if reboot_after and time.time() - down_since > reboot_after:
                log(f"down for over {reboot_after // 60} min; rebooting")
                run(["systemctl", "reboot"])
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
