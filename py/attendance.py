import datetime
import os
import sqlite3
import time

from member import Member, Role


IN = "in"
OUT = "out"


class Punch:
    def __init__(self, member, direction):
        self.member = member
        self.direction = direction


class Store:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS people (
                username TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                pronounce TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'student'
            )"""
        )
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS punches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                ts INTEGER NOT NULL,
                direction TEXT NOT NULL
            )"""
        )
        try:
            self.conn.execute(
                "ALTER TABLE people ADD COLUMN role TEXT NOT NULL DEFAULT 'student'"
            )
        except sqlite3.OperationalError:
            pass
        try:
            self.conn.execute(
                "ALTER TABLE people ADD COLUMN enabled INTEGER NOT NULL DEFAULT 0"
            )
        except sqlite3.OperationalError:
            pass
        try:
            self.conn.execute("ALTER TABLE people ADD COLUMN tag_pronounce TEXT")
        except sqlite3.OperationalError:
            pass
        self.conn.commit()

    def upsert(self, member):
        self.conn.execute(
            """INSERT INTO people(username, name, pronounce, role) VALUES (?, ?, ?, ?)
               ON CONFLICT(username) DO UPDATE SET
                 name=excluded.name, pronounce=excluded.pronounce, role=excluded.role""",
            (member.username, member.name, member.pronounce, member.role),
        )
        self.conn.commit()

    def set_enabled(self, username, enabled):
        self.conn.execute(
            "UPDATE people SET enabled=? WHERE username=?",
            (int(bool(enabled)), username),
        )
        self.conn.commit()

    def set_tag_pronounce(self, username, pronounce):
        """Remember what the person's badge says, separate from the synced
        pronunciation, so it can be pushed back to Authentik later."""
        cur = self.conn.execute(
            "UPDATE people SET tag_pronounce=? WHERE username=? AND tag_pronounce IS NOT ?",
            (pronounce, username, pronounce),
        )
        if cur.rowcount:
            self.conn.commit()

    def tag_pronunciations(self):
        """[(username, name, synced pronounce, pronounce on the badge)] for
        everyone whose badge has been scanned."""
        return self.conn.execute(
            """SELECT username, name, pronounce, tag_pronounce FROM people
               WHERE tag_pronounce IS NOT NULL ORDER BY name COLLATE NOCASE"""
        ).fetchall()

    def get(self, username):
        return next((p for p in self.people() if p.username == username), None)

    def toggle(self, member, now_secs, debounce_secs=2):
        # The stored record (name, pronunciation, role) is authoritative once a
        # person is known, e.g. from the Authentik sync; a tag only seeds new people.
        tag_pronounce = member.pronounce
        known = self.get(member.username)
        if known:
            member = known
        else:
            self.upsert(member)
        self.set_tag_pronounce(member.username, tag_pronounce)
        row = self.conn.execute(
            "SELECT ts, direction FROM punches WHERE username=? ORDER BY id DESC LIMIT 1",
            (member.username,),
        ).fetchone()
        if row and now_secs - row[0] < debounce_secs:
            return None
        direction = OUT if row and row[1] == IN else IN
        self.conn.execute(
            "INSERT INTO punches(username, ts, direction) VALUES (?, ?, ?)",
            (member.username, now_secs, direction),
        )
        self.conn.commit()
        return Punch(member, direction)

    def people(self):
        rows = self.conn.execute(
            """SELECT username, name, pronounce, role, enabled FROM people
               ORDER BY name COLLATE NOCASE"""
        ).fetchall()
        out = []
        for username, name, pronounce, role, enabled in rows:
            m = Member.new(name, username, pronounce, Role.parse(role) or Role.STUDENT)
            if m:
                m.enabled = bool(enabled)
                out.append(m)
        return out

    def closed_secs(self, since=0):
        """{username: seconds} summed over completed in->out sessions, counting
        only the part of each that falls after `since`."""
        totals, started = {}, {}
        for username, ts, direction in self.conn.execute(
            "SELECT username, ts, direction FROM punches ORDER BY id"
        ):
            if direction == IN:
                started[username] = ts
            elif username in started:
                begin = max(started.pop(username), since)
                totals[username] = totals.get(username, 0) + max(0, ts - begin)
        return totals

    def who(self):
        rows = self.conn.execute(
            """SELECT p.username, p.name, p.pronounce, p.role, p.enabled, x.ts
                FROM people p
                JOIN (
                  SELECT username, MAX(id) AS id FROM punches GROUP BY username
                ) last ON last.username = p.username
                JOIN punches x ON x.id = last.id
                WHERE x.direction = 'in'
                ORDER BY p.name COLLATE NOCASE"""
        ).fetchall()
        out = []
        closed = self.closed_secs(year_start())
        for username, name, pronounce, role, enabled, ts in rows:
            m = Member.new(name, username, pronounce, Role.parse(role) or Role.STUDENT)
            if m:
                m.enabled = bool(enabled)
                m.closed_secs = closed.get(username, 0)
                out.append((m, ts))
        return out


def db_path():
    env = os.environ.get("TVGUI_DB")
    if env:
        return env
    p = "/var/lib/tvgui/attendance.sqlite"
    if os.path.isdir(os.path.dirname(p)):
        return p
    return "attendance.sqlite"


def year_start(now=None):
    """Local midnight of January 1st of the current year (the season reset)."""
    n = datetime.datetime.fromtimestamp(now if now is not None else time.time())
    return int(datetime.datetime(n.year, 1, 1).timestamp())


def now_secs():
    return int(time.time())
