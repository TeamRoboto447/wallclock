"""Stream Deck: location keys (arm a location for the next badge tap) and an admin page that opens only after
a mentor taps their badge. Optional hardware: needs the streamdeck and PIL packages and a deck, otherwise it
logs once and stops (or keeps retrying while no deck is attached), so displays without a deck are unaffected.
What the keys offer comes from the displayed layout's {"deck": {"pick": ...}} entry: "location" (the locations of its
roster module, for the pit) or "work" (active milestones, then optionally a priority task, for the shop); a layout
without the entry has only ADMIN. TVGUI_NO_DECK=1 disables the deck."""
import os
import threading
import time

from nfc import ADMIN, PENDING, WORK, split_here
from plan import load_md, load_plan, plan_path, priority_path

CONFIRM_SECS = 5
LONG_PRESS_SECS = 0.6  # holding a pick key this long shows who is on it instead of arming it
OVERLAY_SECS = 10
BRIGHTNESS = (30, 60, 100)
BLACK, INDIGO, RED, AMBER, SLATE, WHITE = (0, 0, 0), (0x1C, 0x10, 0x6A), (0xB5, 0x04, 0x04), (0xE6, 0xB4, 0x22), (0x2A, 0x2A, 0x3C), (255, 255, 255)


def layout_pick(layout):
    """What the layout's deck should offer: {"deck": {"pick": "location" | "work"}} in the layout, else nothing."""
    for m in layout or []:
        if isinstance(m.get("deck"), dict):
            return m["deck"].get("pick")
    return None


def layout_locations(layout):
    """Locations a layout asks for: those of its roster modules that group by location."""
    out = []
    for m in layout or []:
        o = m.get("opts") or {}
        if m.get("handler") == "here" and o.get("group_by") == "location":
            out += [loc for loc in o.get("locations", []) if loc not in out]
    return out


def _field(row, k):
    return row[k] if len(row) > k else None


class Deck:
    """Key contents and key presses, with no hardware in it (tested directly)."""

    VIEW_SECS = 20  # a milestone's task page returns to the main page after this long

    def __init__(self, store, state, event_q, layout=None, locations=None):
        self.store, self.state, self.event_q = store, state, event_q
        self._layout, self._locations = layout, locations  # layout: callable giving the displayed layout
        self.confirm_until = 0
        self.bright_i = 1
        self._work, self._work_at = ([], {}), -1e9  # (milestones, {milestone: [priority tasks]}), cached
        self.page, self.milestone, self.view_until = 0, None, 0
        self.overlay_until = 0  # while the who-overlay is up, the last key is CLOSE

    @property
    def pick(self):
        if self._locations is not None:
            return "location"
        return layout_pick(self._layout() if self._layout else [])

    @property
    def locations(self):
        if self._locations is not None:
            return self._locations
        return layout_locations(self._layout() if self._layout else []) if self.pick == "location" else []

    def _rows(self):
        with self.state["lock"]:
            return self.state["mentors"] + self.state["students"] + self.state["parents"]

    def work(self, now):
        """Active milestones (plan.md, not done) and the priority tasks of each (priority.md); re-read every 5 s."""
        if now - self._work_at >= 5:
            plan, _ = load_plan(plan_path())
            items, _ = load_md(priority_path())
            tasks, head = {}, None
            for kind, text in items:
                if kind == "h":
                    head = text
                elif kind != "done" and head:
                    tasks.setdefault(head, []).append(text)
            self._work, self._work_at = ([b[1] for b in plan.get("bars", []) if b[0] != "done"], tasks), now
        return self._work

    def keys(self, now, n=15):
        specs = self._keys(now, n)
        if now < self.overlay_until and not ADMIN.is_open(now):
            specs[n - 1] = ("CLOSE", "overlay", RED, WHITE)
        return specs

    def _keys(self, now, n=15):
        """[(label, sub-label, background, foreground) or None] for each of the n keys, ADMIN/BACK on the last."""
        specs = [None] * n
        if ADMIN.is_open(now):
            here = len(self._rows())
            confirming = now < self.confirm_until
            with self.state["lock"]:
                blanked = self.state["blanked"]
            for i, spec in enumerate([
                ("CONFIRM?", "tap again", RED, WHITE) if confirming else ("CLOCK OUT", f"all {here}", SLATE, WHITE),
                ("LAYOUT", "next", SLATE, WHITE),
                ("SCREEN", "off" if blanked else "on", SLATE, WHITE),
                ("AUDIO", "test", SLATE, WHITE),
                ("INFO", "", SLATE, WHITE),
                ("REQUIRE", "ON" if os.environ.get(self._require_flag()) else "OFF", SLATE, WHITE),
                ("BRIGHT", f"{BRIGHTNESS[self.bright_i]}%", SLATE, WHITE),
            ]):
                specs[i] = spec
            specs[n - 1] = ("BACK", "", INDIGO, WHITE)
            return specs
        if self.pick == "work":
            return self._work_keys(now, n, specs)
        armed = PENDING.peek(now)
        locations = self.locations
        counts = {}
        for r in self._rows():
            counts[r[4]] = counts.get(r[4], 0) + 1
        for i, loc in enumerate(locations[: n - 1]):
            specs[i] = (loc, "TAP BADGE" if loc == armed else str(counts.get(loc, 0)), RED if loc == armed else INDIGO, WHITE)
        specs[n - 1] = self._admin_key(now)
        return specs

    def _admin_key(self, now):
        return ("TAP", "MENTOR", AMBER, BLACK) if ADMIN.waiting(now) else ("ADMIN", "", SLATE, WHITE)

    def _work_keys(self, now, n, specs):
        milestones, tasks = self.work(now)
        rows = self._rows()
        armed = WORK.peek(now)
        slots = n - 3
        if self.milestone is not None and now < self.view_until:  # task page of one milestone
            for i, t in enumerate(tasks.get(self.milestone, [])[:slots]):
                mine = armed == (self.milestone, t)
                count = sum(1 for r in rows if _field(r, 5) == self.milestone and _field(r, 6) == t)
                specs[i] = (t, "TAP BADGE" if mine else str(count), RED if mine else INDIGO, WHITE)
            whole = armed == (self.milestone, None)
            specs[n - 2] = ("WHOLE", "TAP BADGE" if whole else "milestone", RED if whole else INDIGO, WHITE)
            specs[n - 1] = ("BACK", "", SLATE, WHITE)
            return specs
        self.milestone = None
        pages = max(1, -(-len(milestones) // slots))
        self.page %= pages
        for i, name in enumerate(milestones[self.page * slots:(self.page + 1) * slots]):
            mine = bool(armed) and armed[0] == name
            count = sum(1 for r in rows if _field(r, 5) == name)
            specs[i] = (name, "TAP BADGE" if mine else str(count), RED if mine else INDIGO, WHITE)
        if len(milestones) > slots:
            specs[n - 3] = ("NEXT", f"{self.page + 1}/{pages}", SLATE, WHITE)
        general = armed == ("General", None)
        specs[n - 2] = ("GENERAL", "TAP BADGE" if general else str(sum(1 for r in rows if _field(r, 5) == "General")),
                        RED if general else INDIGO, WHITE)
        specs[n - 1] = self._admin_key(now)
        return specs

    def who_event(self, i, now, n):
        """('overlay', title, [(name, enabled)], secs) for the pick key i, or None if it is not one."""
        rows, title, want = self._rows(), None, None
        if self.pick == "work":
            milestones, tasks = self.work(now)
            slots = n - 3
            if self.milestone is not None and now < self.view_until:
                mine = tasks.get(self.milestone, [])[:slots]
                if i < len(mine):
                    title, want = f"{self.milestone} › {mine[i]}", lambda r: _field(r, 5) == self.milestone and _field(r, 6) == mine[i]
                elif i == n - 2:
                    title, want = self.milestone, lambda r: _field(r, 5) == self.milestone
            else:
                page = milestones[self.page * slots:(self.page + 1) * slots]
                if i < len(page):
                    title, want = page[i], lambda r: _field(r, 5) == page[i]
                elif i == n - 2:
                    title, want = "General", lambda r: _field(r, 5) == "General"
        elif self.pick == "location" and i < len(self.locations):
            title, want = self.locations[i], lambda r: _field(r, 4) == self.locations[i]
        if title is None:
            return None
        people = sorted(((r[0], r[2]) for r in rows if want(r)), key=lambda p: p[0].lower())
        return ("overlay", title, people, OVERLAY_SECS)

    def press(self, i, now, n=15, long=False):
        if now < self.overlay_until and i == n - 1 and not ADMIN.is_open(now):  # CLOSE
            self.overlay_until = 0
            self.event_q.put(("overlay_close",))
            return
        if long and not ADMIN.is_open(now):
            event = self.who_event(i, now, n)
            if event:
                self.overlay_until = now + OVERLAY_SECS
                self.event_q.put(event)
                return
        if ADMIN.is_open(now):
            ADMIN.touch(now)
            self._admin(i, now, n)
        elif self.pick == "work":
            self._work_press(i, now, n)
        elif i == n - 1:
            ADMIN.arm(now)
            self.event_q.put(("status", "Admin: tap a mentor badge"))
        elif i < len(self.locations):
            PENDING.arm(self.locations[i], self.event_q)

    def _work_press(self, i, now, n):
        milestones, tasks = self.work(now)
        slots = n - 3
        if self.milestone is not None and now < self.view_until:
            mine = tasks.get(self.milestone, [])[:slots]
            if i == n - 1:
                self.milestone = None
            elif i == n - 2:
                WORK.arm((self.milestone, None), self.event_q)
                self.milestone = None
            elif i < len(mine):
                WORK.arm((self.milestone, mine[i]), self.event_q)
                self.milestone = None
            return
        page = milestones[self.page * slots:(self.page + 1) * slots]
        if i == n - 1:
            ADMIN.arm(now)
            self.event_q.put(("status", "Admin: tap a mentor badge"))
        elif i == n - 2:
            WORK.arm(("General", None), self.event_q)
        elif i == n - 3 and len(milestones) > slots:
            self.page += 1
        elif i < len(page):
            if tasks.get(page[i]):
                self.milestone, self.view_until = page[i], now + self.VIEW_SECS
            else:
                WORK.arm((page[i], None), self.event_q)

    def _require_flag(self):
        return "TVGUI_REQUIRE_WORK" if self.pick == "work" else "TVGUI_REQUIRE_LOCATION"

    def _admin(self, i, now, n):
        if i == n - 1:
            ADMIN.close()
            return
        if i != 0:
            self.confirm_until = 0
        print(f"admin: key {i}", flush=True)  # audit trail
        if i == 0:
            if now < self.confirm_until:
                self.confirm_until = 0
                people = [m for m, _ in self.store.who()]
                for m in people:
                    self.store.toggle(m, int(now), 0)
                self.event_q.put(("here",) + split_here(self.store.who()))
                self.event_q.put(("status", f"{len(people)} people clocked out"))
                print(f"admin: clocked out {len(people)}", flush=True)
            else:
                self.confirm_until = now + CONFIRM_SECS
        elif i == 1:
            self.event_q.put(("layout_next",))
        elif i == 2:
            with self.state["lock"]:
                blanked = self.state["blanked"]
            self.event_q.put(("unblank",) if blanked else ("blank",))
        elif i == 3:
            self.event_q.put(("speak", "Audio test"))
        elif i == 4:
            self.event_q.put(("info",))
        elif i == 5:
            flag = self._require_flag()
            os.environ[flag] = "" if os.environ.get(flag) else "1"
        elif i == 6:
            self.bright_i = (self.bright_i + 1) % len(BRIGHTNESS)

    @property
    def brightness(self):
        return BRIGHTNESS[self.bright_i]


def render(spec, size=(72, 72)):
    """A key image (PIL) for one key spec."""
    from PIL import Image, ImageDraw, ImageFont

    from panels import FONT

    label, sub, bg, fg = spec
    img = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(img)

    def font(px):
        try:
            return ImageFont.truetype(FONT, px)
        except OSError:
            return ImageFont.load_default()

    def wrap(text, px):
        lines, line = [], ""
        for word in text.split():
            trial = f"{line} {word}".strip()
            if line and d.textlength(trial, font=font(px)) > size[0] - 6:
                lines.append(line)
                line = word
            else:
                line = trial
        return lines + ([line] if line else [])

    room = 2 if sub else 3
    label = label.upper()
    for px in (18, 15, 13, 11, 9):  # biggest type whose wrapped lines fit
        lines = wrap(label, px)
        if len(lines) <= room and all(d.textlength(w, font=font(px)) <= size[0] - 4 for w in lines):
            break
    else:
        lines = lines[:room]
    top = 6 if sub else (size[1] - (px + 3) * len(lines)) // 2
    for k, w in enumerate(lines):
        d.text((size[0] // 2, top + k * (px + 2)), w, font=font(px), fill=fg, anchor="mt")
    if sub:
        f = font(28 if sub.isdigit() else 14)
        while f.size > 9 and d.textlength(sub, font=f) > size[0] - 6:
            f = font(f.size - 1)
        d.text((size[0] // 2, size[1] - 6), sub, font=f, fill=fg, anchor="mb")
    return img


_stop = threading.Event()
_thread = None


def stop():
    """Release the deck and end the thread. Must run before the process exits: the library's read thread
    keeps a leftover process alive, and with it the USB device, so the next start cannot open the deck."""
    _stop.set()
    if _thread is not None:
        _thread.join(timeout=3)


def _serve(logic, deck, PILHelper):
    deck.open()
    deck.reset()
    n = deck.key_count()
    size = deck.key_image_format()["size"]

    down_at = {}

    def on_key(_deck, key, down):  # keys act on release so a long press can mean "who is on this"
        now = time.time()
        if down:
            down_at[key] = now
            return
        try:
            logic.press(key, now, n, long=now - down_at.pop(key, now) >= LONG_PRESS_SECS)
        except Exception as e:  # a failing action must not kill the deck's read thread
            print(f"deck: key {key}: {e!r}", flush=True)

    deck.set_key_callback(on_key)
    shown, bright = {}, None
    while deck.is_open() and not _stop.is_set():
        specs = logic.keys(time.time(), n)
        for i, spec in enumerate(specs):
            if spec != shown.get(i):
                deck.set_key_image(i, PILHelper.to_native_format(deck, render(spec or ("", "", BLACK, WHITE), size)))
                shown[i] = spec
        if logic.brightness != bright:
            bright = logic.brightness
            deck.set_brightness(bright)
        time.sleep(0.25)


def _loop(logic):
    try:
        from StreamDeck.DeviceManager import DeviceManager
        from StreamDeck.ImageHelpers import PILHelper
        import PIL  # noqa: F401
    except ImportError as e:
        print(f"deck: Stream Deck support not installed ({e})", flush=True)
        return
    said = False
    while True:
        deck = None
        try:
            decks = DeviceManager().enumerate()
            deck = decks[0] if decks else None
            if deck is None:
                _stop.wait(5)
                if _stop.is_set():
                    return
                continue
            print("deck: connected", flush=True)
            said = False
            _serve(logic, deck, PILHelper)
        except Exception as e:
            if not said:
                print(f"deck: {e!r}; retrying", flush=True)
                said = True
        finally:
            for release in ("reset", "close"):  # close must run even if reset fails, or the read thread leaks
                try:
                    getattr(deck, release)() if deck is not None else None
                except Exception:
                    pass
        if _stop.wait(5):
            return


def start(store, state, event_q, layout=None):
    global _thread
    if os.environ.get("TVGUI_NO_DECK"):
        return None
    _stop.clear()
    _thread = threading.Thread(target=_loop, args=(Deck(store, state, event_q, layout),), daemon=True)
    _thread.start()
    return _thread


def sheet(path):
    """Write a PNG contact sheet of the key pages (main, armed, waiting for a mentor, admin, confirm) for review."""
    from PIL import Image

    from attendance import Store

    rows = [(f"P{i}", 0, True, 0, loc) for i, loc in enumerate(["pit", "pit", "stands", "field", "field", "field", "cafeteria"])]
    state = {"lock": threading.Lock(), "mentors": rows[:2], "students": rows[2:], "parents": [], "blanked": False}
    logic = Deck(Store(":memory:"), state, __import__("queue").Queue(), locations=["pit", "practice field", "stands", "field", "cafeteria"])
    now = 1000.0
    pages = []
    pages.append(("main", logic.keys(now)))
    PENDING.arm("practice field", now=now)
    pages.append(("armed", logic.keys(now + 1)))
    PENDING.loc = None
    ADMIN.arm(now)
    pages.append(("waiting", logic.keys(now + 1)))
    ADMIN.unlock(now)
    pages.append(("admin", logic.keys(now + 1)))
    logic.confirm_until = now + 5
    pages.append(("confirm", logic.keys(now + 1)))
    ADMIN.close()
    work = Deck(Store(":memory:"), state, __import__("queue").Queue(), lambda: [{"deck": {"pick": "work"}}])
    work._work, work._work_at = (["Pit-side wallclock", "Shop Organization", "Tune Shooting", "Practice", "BoilerBot", "Prepare for Kick-Off"],
                                 {"Shop Organization": ["Organize Build Space", "Organize Staging Room"], "Tune Shooting": ["Tune Targetting with QuestNav Localization"]}), 1e12
    state["students"] = [("P", 0, True, 0, None, "Shop Organization", "Organize Build Space", "P")] * 2 + [("Q", 0, True, 0, None, "Tune Shooting", None, "Q")]
    pages.append(("work", work.keys(now)))
    work.press(1, now)
    pages.append(("tasks", work.keys(now + 1)))
    work.press(0, now + 1)
    pages.append(("armed", work.keys(now + 2)))
    pad, key = 8, 72
    sheet_img = Image.new("RGB", (5 * (key + pad) + pad, len(pages) * (3 * (key + pad) + 30) + pad), (20, 20, 20))
    for p, (name, specs) in enumerate(pages):
        for i, spec in enumerate(specs):
            img = render(spec or ("", "", BLACK, WHITE))
            sheet_img.paste(img, (pad + (i % 5) * (key + pad), pad + 30 + p * (3 * (key + pad) + 30) + (i // 5) * (key + pad)))
    sheet_img.save(path)


if __name__ == "__main__":
    import sys

    if sys.argv[1:2] == ["sheet"]:
        sheet(sys.argv[2] if len(sys.argv) > 2 else "decksheet.png")
