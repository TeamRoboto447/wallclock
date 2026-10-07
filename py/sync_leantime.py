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
Dependencies ('Depends On'): a task waiting on an unfinished ticket shows as
blocked; a task others wait on counts as priority; plan.md lines carry
{id<depends-on} so the display can draw arrows; a moved milestone pushes its
dependents back so they start after it ends.
Status markers: [ ] new, [~] in progress/waiting, [!] blocked, [x] done.
Milestones become plan.md entries "M/D/YYYY - M/D/YYYY --- name" (start and end). Tasks become today.md items grouped under their
first tag. Files are replaced atomically; on any error the old files stay.

Env: LEANTIME_API_KEY (required), LEANTIME_URL (default tasks.teamroboto.org),
LEANTIME_PROJECT (default 7).
"""
import datetime
import http.client
import json
import os
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from plan import plan_path, priority_path, today_path

DEFAULT_URL = "https://tasks.teamroboto.org"
DONE = 0
ARCHIVED = -1
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


def _date(s):
    """The calendar day a person picked in Leantime. It stores UTC, and its UI
    saves a start as local midnight and an end as 23:59:59, in whatever
    timezone the editor uses (07:00 UTC for Los Angeles, 04:00 for Eastern), so
    the day is read from the value itself and not from a fixed timezone."""
    s = str(s)
    if len(s) <= 10:
        return datetime.date.fromisoformat(s)
    dt = datetime.datetime.fromisoformat(s)
    if dt.hour < 12 and (dt.minute, dt.second) == (59, 59):  # end of the picked day
        return (dt + datetime.timedelta(seconds=1)).date() - datetime.timedelta(days=1)
    return dt.date()


def dep_id(t):
    """id of the ticket this one 'Depends On', or 0. Leantime keeps that in
    `milestoneid` for a milestone (its UI field) and in `dependingTicketId` for
    a task, where `milestoneid` is instead the milestone the task belongs to."""
    fields = ("milestoneid", "dependingTicketId") if t.get("type") == "milestone" else ("dependingTicketId",)
    for f in fields:
        try:
            if int(t.get(f) or 0):
                return int(t[f])
        except (TypeError, ValueError):
            pass
    return 0


def unfinished(t):
    return t.get("status") not in (DONE, ARCHIVED)


def waiting_on(t, by_id):
    """The unfinished ticket this one depends on, if any."""
    dep = by_id.get(dep_id(t))
    return dep if dep is not None and unfinished(dep) else None


def blocking_ids(tickets):
    """Ids of unfinished tickets that some other unfinished ticket depends on."""
    by_id = {t["id"]: t for t in tickets}
    return {dep["id"] for t in tickets if unfinished(t) and (dep := waiting_on(t, by_id))}


def chain_order(rows):
    """Order rows [(start, end, ..., key, dep_key)] so milestones that depend on
    each other sit together: each unrelated chain in start order, and within a
    chain a milestone is followed by those that depend on it (start order),
    each of those followed by its own dependents."""
    rows = sorted(rows, key=lambda r: (r["start"], r["end"]))
    present = {r["key"] for r in rows}
    children = {}
    roots = []
    for r in rows:
        if r["dep"] in present and r["dep"] != r["key"]:
            children.setdefault(r["dep"], []).append(r)
        else:
            roots.append(r)
    ordered, seen = [], set()

    def visit(r):
        if r["key"] in seen:
            return
        seen.add(r["key"])
        ordered.append(r)
        for c in children.get(r["key"], []):
            visit(c)

    for r in roots + rows:  # `rows` again picks up members of a dependency cycle
        visit(r)
    return ordered


def render_plan(milestones, today=None):
    """plan.md text. A milestone waiting on an unfinished milestone it depends
    on is shown as blocked whatever its own status says; milestones that depend
    on each other are listed together (see chain_order)."""
    today = today or datetime.date.today()
    by_id = {m["id"]: m for m in milestones if "id" in m}
    rows = []
    for n, m in enumerate(milestones):
        try:
            a, b = _date(m["editFrom"]), _date(m["editTo"])
        except (TypeError, ValueError):
            continue
        if m.get("status") == ARCHIVED:
            continue
        if m.get("status") == 0 and b < today:  # done and past its deadline
            continue
        mid = m.get("id", 0)
        tag = f"{{{mid}<{dep_id(m)}}}" if dep_id(m) else f"{{{mid}}}"
        mark = "!" if unfinished(m) and waiting_on(m, by_id) else MARKER.get(m.get("status"), " ")
        key = mid if "id" in m else -(n + 1)  # fixtures without ids stay distinct
        line = f"[{mark}] {a.month}/{a.day}/{a.year} - {b.month}/{b.day}/{b.year} --- {m['headline'].strip()} {tag}"
        rows.append({"start": a, "end": b, "key": key, "dep": dep_id(m), "line": line})
    lines = [r["line"] for r in chain_order(rows)]
    return "\n\n".join(lines) + "\n" if lines else ""


NO_MILESTONE = "No milestone"


def render_today(tasks, keep=None, everything=None, hide_done_milestones=False):
    """today.md text, tasks grouped under their milestone (in milestone start
    order, tasks without one last), unfinished tasks before finished ones.
    `everything` is every ticket, milestones too, used to find milestone names
    and resolve 'Depends On': a task waiting on an unfinished ticket shows as
    blocked. With hide_done_milestones, a Done or archived milestone and all
    its tasks are left out."""
    by_id = {t["id"]: t for t in (everything if everything is not None else tasks)}

    def milestone_of(t):
        m = by_id.get(int(t.get("milestoneid") or 0))
        return m if m is not None and m.get("type") == "milestone" else None

    def order(m):
        if m is None:
            return (1, datetime.date.max, 0)
        try:
            return (0, _date(m["editFrom"]), m["id"])
        except (KeyError, TypeError, ValueError):
            return (0, datetime.date.max, m["id"])

    groups = {}  # milestone (or None) -> lines, keyed by id for hashing
    for t in sorted(tasks, key=lambda t: t["id"]):
        if t.get("type") != "task" or t.get("status") == ARCHIVED:
            continue
        if keep and not keep(t):
            continue
        ms = milestone_of(t)
        if hide_done_milestones and ms is not None and not unfinished(ms):
            continue
        mark, text = MARKER.get(t.get("status"), " "), t["headline"].strip()
        dep = waiting_on(t, by_id) if unfinished(t) else None
        if dep:
            mark, text = "!", f"{text} \u2190 waiting on {dep['headline'].strip()}"
        entry = groups.setdefault(ms["id"] if ms else 0, (ms, []))
        entry[1].append((t.get("status") == DONE, f"- [{mark}] {text}"))
    ordered = sorted(groups.values(), key=lambda g: order(g[0]))
    return "\n\n".join(
        f"# {ms['headline'].strip() if ms else NO_MILESTONE}\n"
        + "\n".join(line for _, line in sorted(lines, key=lambda x: x[0]))  # stable: open first
        for ms, lines in ordered
    ) + "\n" if ordered else ""


def is_priority(t, blockers=()):
    """Unfinished and either High/Critical (Leantime 1-2) or holding others up."""
    if not unfinished(t):
        return False
    if t.get("id") in blockers:
        return True
    try:
        return int(t.get("priority")) <= 2
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


def roll_values(ticket, start, end):
    """Full update payload with the milestone moved to start..end. Leantime
    clears any field left out of an update and shifts datetimes on every write,
    so everything is resent and the dates go back as date-only."""
    v = {k: ticket.get(k) for k in TICKET_KEYS}
    v["editFrom"], v["editTo"] = start.isoformat(), end.isoformat()
    v["dateToFinish"] = "" if v["dateToFinish"] in (ZERO, None) else str(v["dateToFinish"])[:10]
    v["timeFrom"], v["timeTo"] = v["timeFrom"] or "", v["timeTo"] or ""
    return v


def plan_moves(milestones, today):
    """{id: (start, end)} of milestones to move: each overdue one ends this
    Sunday (keeping its length), and anything that depends on a moved milestone
    is pushed to start the day after its prerequisite ends, keeping its length,
    and so on down the chain."""
    ms = [m for m in milestones if m.get("type") == "milestone"]
    span = lambda m: _date(m["editTo"]) - _date(m["editFrom"])
    sunday = week_deadline(today)
    moves = {m["id"]: (sunday - span(m), sunday) for m in ms if overdue(m, today)}
    queue = list(moves)
    for _ in range(200):  # also stops a dependency cycle
        if not queue:
            break
        rid = queue.pop(0)
        end = moves[rid][1]
        for d in ms:
            if dep_id(d) != rid or d["id"] == rid or not unfinished(d):
                continue
            start, stop = moves.get(d["id"]) or (_date(d["editFrom"]), _date(d["editTo"]))
            if start <= end:
                new = end + datetime.timedelta(days=1)
                moves[d["id"]] = (new, new + (stop - start))
                queue.append(d["id"])
    return moves


def roll_forward(client, milestones, today):
    global _roll_retry_at
    if time.time() < _roll_retry_at:
        return 0
    by_id = {m["id"]: m for m in milestones}
    moved = 0
    for mid, (start, end) in list(plan_moves(milestones, today).items())[:ROLL_BATCH]:
        try:
            full = client.call("tickets.getTicket", {"id": mid})
            client.call("tickets.updateTicket", {"values": roll_values(full, start, end)})
            moved += 1
            old = by_id[mid]
            print(f"moved milestone {mid} '{old['headline']}': "
                  f"{_date(old['editFrom'])}..{_date(old['editTo'])} -> {start}..{end}", flush=True)
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
    blockers = blocking_ids(tasks)
    changed = [
        name
        for name, path, text in (
            ("plan", plan_path(), render_plan(milestones)),
            ("today", today_path(), render_today(tasks, everything=tasks, hide_done_milestones=True)),
            ("priority", priority_path(),
             render_today(tasks, lambda t: is_priority(t, blockers), tasks)),
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
