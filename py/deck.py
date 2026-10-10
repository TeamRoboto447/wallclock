"""Stream Deck: location keys (arm a location for the next badge tap) and an admin page that opens only after
a mentor taps their badge. Optional hardware: needs the streamdeck and PIL packages and a deck, otherwise it
logs once and stops (or keeps retrying while no deck is attached), so displays without a deck are unaffected.
Env: TVGUI_DECK_LOCATIONS (comma list, must match the layout's locations), TVGUI_NO_DECK=1 to disable."""
import os
import threading
import time

from nfc import ADMIN, PENDING, split_here

DEFAULT_LOCATIONS = "pit,practice field,stands,field,cafeteria"
CONFIRM_SECS = 5
BRIGHTNESS = (30, 60, 100)
BLACK, INDIGO, RED, AMBER, SLATE, WHITE = (0, 0, 0), (0x1C, 0x10, 0x6A), (0xB5, 0x04, 0x04), (0xE6, 0xB4, 0x22), (0x2A, 0x2A, 0x3C), (255, 255, 255)


class Deck:
    """Key contents and key presses, with no hardware in it (tested directly)."""

    def __init__(self, store, state, event_q, locations=None):
        self.store, self.state, self.event_q = store, state, event_q
        self.locations = locations or [s.strip() for s in os.environ.get("TVGUI_DECK_LOCATIONS", DEFAULT_LOCATIONS).split(",") if s.strip()]
        self.confirm_until = 0
        self.bright_i = 1

    def _rows(self):
        with self.state["lock"]:
            return self.state["mentors"] + self.state["students"] + self.state["parents"]

    def keys(self, now, n=15):
        """[(label, sub, background, foreground) or None] for each of the n keys, ADMIN/BACK on the last."""
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
                ("LOC REQ", "ON" if os.environ.get("TVGUI_REQUIRE_LOCATION") else "OFF", SLATE, WHITE),
                ("BRIGHT", f"{BRIGHTNESS[self.bright_i]}%", SLATE, WHITE),
            ]):
                specs[i] = spec
            specs[n - 1] = ("BACK", "", INDIGO, WHITE)
            return specs
        armed = PENDING.peek(now)
        counts = {}
        for r in self._rows():
            counts[r[4]] = counts.get(r[4], 0) + 1
        for i, loc in enumerate(self.locations[: n - 1]):
            specs[i] = (loc, "TAP BADGE" if loc == armed else str(counts.get(loc, 0)), RED if loc == armed else INDIGO, WHITE)
        specs[n - 1] = ("TAP", "MENTOR", AMBER, BLACK) if ADMIN.waiting(now) else ("ADMIN", "", SLATE, WHITE)
        return specs

    def press(self, i, now, n=15):
        if ADMIN.is_open(now):
            ADMIN.touch(now)
            self._admin(i, now, n)
        elif i == n - 1:
            ADMIN.arm(now)
            self.event_q.put(("status", "Admin: tap a mentor badge"))
        elif i < len(self.locations):
            PENDING.arm(self.locations[i], self.event_q)

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
            os.environ["TVGUI_REQUIRE_LOCATION"] = "" if os.environ.get("TVGUI_REQUIRE_LOCATION") else "1"
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

    def fit(text, px):
        while px > 9 and d.textlength(text, font=font(px)) > size[0] - 6:
            px -= 1
        return font(px)

    words = label.upper().split(" ") if len(label) > 8 else [label.upper()]
    top = 6 if sub else (size[1] - 22 * len(words)) // 2
    for k, w in enumerate(words):
        f = fit(w, 18)
        d.text((size[0] // 2, top + k * 19), w, font=f, fill=fg, anchor="mt")
    if sub:
        d.text((size[0] // 2, size[1] - 6), sub, font=fit(sub, 28 if sub.isdigit() else 14), fill=fg, anchor="mb")
    return img


def _serve(logic, deck, PILHelper):
    deck.open()
    deck.reset()
    n = deck.key_count()
    size = deck.key_image_format()["size"]

    def on_key(_deck, key, down):
        if down:
            try:
                logic.press(key, time.time(), n)
            except Exception as e:  # a failing action must not kill the deck's read thread
                print(f"deck: key {key}: {e!r}", flush=True)

    deck.set_key_callback(on_key)
    shown, bright = {}, None
    while deck.is_open():
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
                time.sleep(5)
                continue
            print("deck: connected", flush=True)
            said = False
            _serve(logic, deck, PILHelper)
        except Exception as e:
            if not said:
                print(f"deck: {e!r}; retrying", flush=True)
                said = True
        finally:
            try:
                if deck is not None:
                    deck.reset()
                    deck.close()
            except Exception:
                pass
        time.sleep(5)


def start(store, state, event_q):
    if os.environ.get("TVGUI_NO_DECK"):
        return None
    t = threading.Thread(target=_loop, args=(Deck(store, state, event_q),), daemon=True)
    t.start()
    return t


def sheet(path):
    """Write a PNG contact sheet of the key pages (main, armed, waiting for a mentor, admin, confirm) for review."""
    from PIL import Image

    from attendance import Store

    rows = [(f"P{i}", 0, True, 0, loc) for i, loc in enumerate(["pit", "pit", "stands", "field", "field", "field", "cafeteria"])]
    state = {"lock": threading.Lock(), "mentors": rows[:2], "students": rows[2:], "parents": [], "blanked": False}
    logic = Deck(Store(":memory:"), state, __import__("queue").Queue())
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
