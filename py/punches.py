"""Correct and inspect badge punches (tvgui.py fix-punch / punches)."""
import datetime

from attendance import IN

FORMATS = ("%I:%M%p", "%I%p", "%H:%M")


def parse_time_of_day(text):
    """'3:25pm', '3pm' or a 24-hour time -> (hour, minute). A bare time such as
    '3:25' is ambiguous, so it is refused: use am/pm, 15:25, or 03:25."""
    s = (text or "").strip().lower().replace(" ", "")
    for fmt in FORMATS:
        try:
            t = datetime.datetime.strptime(s, fmt)
        except ValueError:
            continue
        if fmt == "%H:%M":
            hour_part = s.split(":")[0]
            if len(hour_part) == 1 and 1 <= int(hour_part) <= 12:
                break  # '3:25': morning or afternoon?
        return t.hour, t.minute
    raise ValueError(f"cannot read time {text!r} (use 3:25pm or 15:25)")


def fmt_ts(ts):
    d = datetime.datetime.fromtimestamp(ts)
    return f"{d:%a %Y-%m-%d} {d.hour % 12 or 12}:{d:%M:%S} {d:%p}"


def list_punches(store, username=None, limit=10):
    lines = []
    for p in store.recent_punches(username, limit):
        mark = f"  (corrected {p['edits']}x)" if p["edits"] else ""
        lines.append(f"{p['id']:>5}  {p['username']:<12} {p['direction']:<3} {fmt_ts(p['ts'])}{mark}")
    return lines or ["no punches"]


def fix_punch(store, username, time_of_day, date=None, note="", dry_run=False, now=None):
    """Move the person's latest punch to time_of_day on `date` (default: the day
    the punch was made). Returns the lines to print; raises ValueError if the
    change is not allowed."""
    latest = store.recent_punches(username, 1)
    if not latest:
        raise ValueError(f"no punches for {username!r}")
    p = latest[0]
    hour, minute = parse_time_of_day(time_of_day)
    day = datetime.date.fromisoformat(date) if date else datetime.date.fromtimestamp(p["ts"])
    new_ts = int(datetime.datetime(day.year, day.month, day.day, hour, minute).timestamp())
    kind = "clock-in" if p["direction"] == IN else "clock-out"
    lines = [f"{p['username']}  punch {p['id']} ({kind})",
             f"  from {fmt_ts(p['ts'])}", f"    to {fmt_ts(new_ts)}"]
    if new_ts == p["ts"]:
        raise ValueError("that is already the punch time")
    if dry_run:
        return lines + ["dry run: nothing changed"]
    store.edit_punch(p["id"], new_ts, note, now)
    return lines + ["done (original time kept in the punch_edits table)"]
