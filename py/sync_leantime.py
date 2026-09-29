#!/usr/bin/env python3
"""One-way pull: Leantime project -> plan.md / today.md for the kiosk.

Runs as a daemon polling every LEANTIME_INTERVAL seconds (default 15) over one
kept-alive connection; a file is rewritten only when its content changed.
--once does a single sync and exits; --dry-run prints without writing.

priority.md holds unfinished High/Critical tasks; a done milestone past its
end date is left out of plan.md.
With LEANTIME_ROLL_FORWARD=1 an unfinished milestone (anything but Done,
so blocked too) whose end date is before today is moved, keeping its length,
to end this week's Sunday, written back to Leantime (needs an Editor key).
Status markers: [ ] new, [~] in progress/waiting, [!] blocked, [x] done.
Milestones become plan.md entries (7+ day span: "# Week of M/D - name",
shorter: "M/D - M/D --- name"). Tasks become today.md items grouped under their
first tag. Files are replaced atomically; on any error the old files stay.

Env: LEANTIME_API_KEY (required), LEANTIME_URL (default tasks.teamroboto.org),
LEANTIME_PROJECT (default 7), LEANTIME_TZ (default America/Los_Angeles).
"""
import datetime
import http.client
import json
import os
import sys
import time
import urllib.parse
import zoneinfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from plan import plan_path, priority_path, today_path

DEFAULT_URL = "https://tasks.teamroboto.org"
DONE = 0
ARCHIVED = -1
WEEK_DAYS = 6
MARKER = {0: "x", 4: "~", 2: "~", 1: "!"}  # anything else (New) is " "


class Client:
    def __init__(self, base, key):
        u = urllib.parse.urlparse(base)
        self.host, self.tls, self.key = u.netloc, u.scheme == "https", key
        self.conn = None

    def _connect(self):
        cls = http.client.HTTPSConnection if self.tls else http.client.HTTPConnection
        self.conn = cls(self.host, timeout=30)

    def call(self, method, params):
        body = json.dumps(
            {"jsonrpc": "2.0", "method": f"leantime.rpc.{method}", "params": params, "id": 1}
        )
        headers = {"x-api-key": self.key, "Content-Type": "application/json"}
        for attempt in (0, 1):  # a stale kept-alive connection gets one retry
            try:
                if self.conn is None:
                    self._connect()
                self.conn.request("POST", "/api/jsonrpc", body, headers)
                raw = self.conn.getresponse().read()
                break
            except (http.client.HTTPException, OSError):
                self.conn = None
                if attempt:
                    raise
        data = json.loads(raw)
        if "error" in data:
            raise RuntimeError(f"{method}: {data['error'].get('message')} {data['error'].get('data')}")
        return data["result"]


# Leantime returns UTC; its UI writes local midnight as UTC, so the calendar
# date a person picked only comes back after converting to Leantime's timezone.
TZ = zoneinfo.ZoneInfo(os.environ.get("LEANTIME_TZ", "America/Los_Angeles"))


def _date(s):
    s = str(s)
    if len(s) <= 10:
        return datetime.date.fromisoformat(s)
    utc = datetime.datetime.fromisoformat(s).replace(tzinfo=datetime.timezone.utc)
    return utc.astimezone(TZ).date()


def render_plan(milestones, today=None):
    today = today or datetime.date.today()
    rows = []
    for m in milestones:
        try:
            a, b = _date(m["editFrom"]), _date(m["editTo"])
        except (TypeError, ValueError):
            continue
        if m.get("status") == ARCHIVED:
            continue
        if m.get("status") == 0 and b < today:  # done and past its deadline
            continue
        rows.append((a, b, m["headline"].strip(), MARKER.get(m.get("status"), " ")))
    rows.sort(key=lambda r: (r[0], r[1]))
    lines = []
    for a, b, name, mark in rows:
        if (b - a).days >= WEEK_DAYS:
            lines.append(f"# [{mark}] Week of {a.month}/{a.day} - {name}")
        else:
            lines.append(f"[{mark}] {a.month}/{a.day} - {b.month}/{b.day} --- {name}")
    return "\n\n".join(lines) + "\n" if lines else ""


def render_today(tasks, keep=None):
    groups = {}
    for t in sorted(tasks, key=lambda t: t["id"]):
        if t.get("type") != "task" or t.get("status") == ARCHIVED:
            continue
        if keep and not keep(t):
            continue
        tag = (t.get("tags") or "").split(",")[0].strip() or "Other"
        mark = MARKER.get(t.get("status"), " ")
        groups.setdefault(tag, []).append(f"- [{mark}] {t['headline'].strip()}")
    return "\n\n".join(f"# {tag}\n" + "\n".join(items) for tag, items in groups.items()) + "\n" if groups else ""


def is_priority(t):
    """High or Critical (Leantime 1-2) and not finished."""
    try:
        return int(t.get("priority")) <= 2 and t.get("status") != 0
    except (TypeError, ValueError):
        return False


ZERO = "0000-00-00 00:00:00"
TICKET_KEYS = (
    "id", "headline", "type", "description", "projectId", "editorId", "userId",
    "dateToFinish", "status", "planHours", "tags", "sprint", "storypoints",
    "hourRemaining", "priority", "acceptanceCriteria", "editFrom", "editTo",
    "timeFrom", "timeTo", "dependingTicketId", "milestoneid", "sortIndex",
)
ROLL_BATCH = 20
ROLL_BACKOFF_SECS = 600
_roll_retry_at = 0.0


def week_deadline(today):
    """Sunday of the week containing today (today itself on a Sunday)."""
    return today + datetime.timedelta(days=6 - today.weekday())


def overdue(m, today):
    """An unfinished (new, in progress or blocked) milestone past its end date."""
    if m.get("type") != "milestone" or m.get("status") in (DONE, ARCHIVED):
        return False
    try:
        return _date(m["editFrom"]) <= _date(m["editTo"]) < today
    except (KeyError, TypeError, ValueError):
        return False


def roll_values(ticket, today):
    """Full update payload with the milestone shifted so it ends this Sunday,
    keeping its length. Leantime clears any field left out of an update and
    shifts datetimes on every write, so everything is resent and the dates
    go back as date-only."""
    v = {k: ticket.get(k) for k in TICKET_KEYS}
    span = _date(ticket["editTo"]) - _date(ticket["editFrom"])
    end = week_deadline(today)
    v["editFrom"], v["editTo"] = (end - span).isoformat(), end.isoformat()
    v["dateToFinish"] = "" if v["dateToFinish"] in (ZERO, None) else str(v["dateToFinish"])[:10]
    v["timeFrom"], v["timeTo"] = v["timeFrom"] or "", v["timeTo"] or ""
    return v


def roll_forward(client, milestones, today):
    global _roll_retry_at
    if time.time() < _roll_retry_at:
        return 0
    moved = 0
    for m in [m for m in milestones if overdue(m, today)][:ROLL_BATCH]:
        try:
            full = client.call("tickets.getTicket", {"id": m["id"]})
            values = roll_values(full, today)
            client.call("tickets.updateTicket", {"values": values})
            moved += 1
            print(f"rolled milestone {m['id']} '{m['headline']}': ends "
                  f"{str(m['editTo'])[:10]} -> {values['editTo']}", flush=True)
        except Exception as e:
            print(f"roll forward failed ({e}); pausing 10 min", flush=True)
            _roll_retry_at = time.time() + ROLL_BACKOFF_SECS
            break
    return moved


def write_atomic(path, text):
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def write_if_changed(path, text):
    try:
        with open(path) as f:
            if f.read() == text:
                return False
    except FileNotFoundError:
        pass
    write_atomic(path, text)
    return True


def sync_once(client, crit, roll=False):
    milestones = client.call("tickets.getAllMilestones", crit)
    tasks = client.call("tickets.getAll", crit)
    if roll and roll_forward(client, milestones, datetime.date.today()):
        milestones = client.call("tickets.getAllMilestones", crit)
    changed = [
        name
        for name, path, text in (
            ("plan", plan_path(), render_plan(milestones)),
            ("today", today_path(), render_today(tasks)),
            ("priority", priority_path(), render_today(tasks, is_priority)),
        )
        if write_if_changed(path, text)
    ]
    return changed


def main():
    key = os.environ.get("LEANTIME_API_KEY")
    if not key:
        print("sync_leantime: LEANTIME_API_KEY not set", file=sys.stderr)
        return 1
    client = Client(os.environ.get("LEANTIME_URL", DEFAULT_URL), key)
    crit = {"searchCriteria": {"currentProject": int(os.environ.get("LEANTIME_PROJECT", "7"))}}
    args = sys.argv[1:]
    if "--dry-run" in args:
        print(f"--- plan.md\n{render_plan(client.call('tickets.getAllMilestones', crit))}"
              f"--- today.md\n{render_today(client.call('tickets.getAll', crit))}", end="")
        return 0
    roll = os.environ.get("LEANTIME_ROLL_FORWARD") == "1"
    interval = max(5, int(os.environ.get("LEANTIME_INTERVAL", "15")))
    failing = False
    while True:
        try:
            changed = sync_once(client, crit, roll)
            if changed or failing:
                print(f"synced: {', '.join(changed) or 'no changes'}", flush=True)
            failing = False
        except Exception as e:
            if not failing:
                print(f"sync_leantime: {e} (keeping last files, retrying)", flush=True)
            failing = True
        if "--once" in args:
            return 1 if failing else 0
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
