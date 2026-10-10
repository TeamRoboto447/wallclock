"""frc.nexus event status: one GET shared by every nexus_* handler (they refresh on the same interval)."""
import json
import os
import threading
import time
import urllib.request

API = "https://frc.nexus/api/v1/event/"
_lock = threading.Lock()
_cache = {}  # event key -> (fetched_at, data)


def event(key, max_age=10):
    """Event status dict. The lock is held across the request so concurrent handlers reuse one fetch."""
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < max_age:
            return hit[1]
        req = urllib.request.Request(API + key, headers={"Nexus-Api-Key": os.environ["FRC_NEXUS_API_KEY"]})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.load(r)
        _cache[key] = (time.time(), data)
        return data


def event_key(opts):
    return opts.get("event") or os.environ["TVGUI_NEXUS_EVENT"]


def team(opts):
    return opts.get("team") or os.environ.get("TVGUI_TEAM") or None


def upcoming(matches, team=None):
    """The match on the field and everything after it, in play order. Only one match is ever
    'On field', so earlier 'On field' matches are finished; with none on field nothing has run yet."""
    last = max((i for i, m in enumerate(matches) if m.get("status") == "On field"), default=0)
    out = matches[last:]
    if team:
        out = [m for m in out if team in m.get("redTeams", []) + m.get("blueTeams", [])]
    return out


def eta_text(target_ms, now_ms):
    mins = round((target_ms - now_ms) / 60000)
    return "now" if mins <= 0 else f"{mins} min" if mins < 90 else f"{mins // 60}h {mins % 60:02d}m"
