import datetime
import os
import re

RANGE_RE = re.compile(r"\|\s*(\d+)(?:\s*-\s*(\d+))?\s*$")
WEEK_OF_RE = re.compile(
    r"^#+\s*Week of\s+(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\s*[-–—:]\s*(.+)$",
    re.I,
)
EVENT_RE = re.compile(
    r"^(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\s*[-–—]\s*"
    r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\s*[-–—]+\s*(.+)$"
)


def board_dir():
    if os.path.isdir("/var/lib/tvgui"):
        return "/var/lib/tvgui"
    return os.path.dirname(os.path.abspath(__file__))


def plan_path():
    return os.path.join(board_dir(), "plan.md")


def today_path():
    return os.path.join(board_dir(), "today.md")


def parse_md(text):
    items = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if s.lower().startswith("start:") or s.lower().startswith("weeks:"):
            continue
        if s.startswith("#"):
            items.append(("h", s.lstrip("#").strip()))
        elif s.lower().startswith("- [x]"):
            items.append(("done", _strip_range(s[5:].strip())))
        elif s.startswith("- [ ]"):
            items.append(("todo", _strip_range(s[5:].strip())))
        elif s.startswith("- "):
            items.append(("todo", _strip_range(s[2:].strip())))
        else:
            items.append(("p", s))
    return items


def _strip_range(text):
    m = RANGE_RE.search(text)
    if not m:
        return text
    return text[: m.start()].strip()


def _range(text):
    m = RANGE_RE.search(text or "")
    if not m:
        return None, None
    lo = int(m.group(1))
    hi = int(m.group(2) or lo)
    if hi < lo:
        lo, hi = hi, lo
    return lo, hi


def _year(part, month, default_year):
    if part is None:
        return default_year
    y = int(part)
    if y < 100:
        y += 2000
    return y


def _mdy(month, day, year_part, default_year):
    return datetime.date(_year(year_part, month, default_year), int(month), int(day))


def _monday(d):
    return d - datetime.timedelta(days=d.weekday())


def parse_plan(text, today=None):
    if today is None:
        today = datetime.date.today()
    start = None
    weeks = None
    title = ""
    dated = []
    numbered = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        low = s.lower()
        if low.startswith("start:"):
            raw = s.split(":", 1)[1].strip()
            try:
                start = datetime.date.fromisoformat(raw)
            except ValueError:
                start = None
            continue
        if low.startswith("weeks:"):
            try:
                weeks = max(1, int(s.split(":", 1)[1].strip()))
            except ValueError:
                weeks = None
            continue
        wm = WEEK_OF_RE.match(s)
        if wm:
            d = _mdy(wm.group(1), wm.group(2), wm.group(3), today.year)
            mon = _monday(d)
            dated.append((False, wm.group(4).strip(), mon, mon + datetime.timedelta(days=6)))
            continue
        em = EVENT_RE.match(s)
        if em:
            a = _mdy(em.group(1), em.group(2), em.group(3), today.year)
            b = _mdy(em.group(4), em.group(5), em.group(6), today.year)
            if b < a:
                a, b = b, a
            dated.append((False, em.group(7).strip(), a, b))
            continue
        if s.startswith("#") and not title:
            title = s.lstrip("#").strip()
            continue
        done = None
        body = None
        if s.lower().startswith("- [x]"):
            done, body = True, s[5:].strip()
        elif s.startswith("- [ ]"):
            done, body = False, s[5:].strip()
        elif s.startswith("- "):
            done, body = False, s[2:].strip()
        if body is None:
            continue
        lo, hi = _range(body)
        name = _strip_range(body)
        if name:
            numbered.append((done, name, lo, hi))
    if dated:
        first = min(a for _, _, a, _ in dated)
        last = max(b for _, _, _, b in dated)
        if start is None:
            start = _monday(first)
        span = (last - start).days // 7 + 1
        if weeks is None:
            weeks = max(1, span)
    if weeks is None:
        weeks = 8
    bars = []
    for done, name, a, b in dated:
        lo = (a - start).days // 7 + 1 if start else None
        hi = (b - start).days // 7 + 1 if start else None
        if lo is not None:
            lo = max(1, lo)
            hi = min(weeks, max(lo, hi))
        bars.append((done, name, lo, hi))
    bars.extend(numbered)
    return {
        "start": start,
        "weeks": weeks,
        "title": title,
        "bars": bars,
        "items": parse_md(text),
    }


def current_week(start, weeks, now=None):
    if not start:
        return None
    if now is None:
        today = datetime.date.today()
    else:
        today = datetime.datetime.fromtimestamp(now).astimezone().date()
    w = (today - start).days // 7 + 1
    if w < 1 or w > weeks:
        return None
    return w


def load_md(path):
    try:
        st = os.stat(path)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        return parse_md(text), st.st_mtime
    except OSError:
        return [], 0


def load_plan(path):
    try:
        st = os.stat(path)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        return parse_plan(text), st.st_mtime
    except OSError:
        return parse_plan(""), 0
