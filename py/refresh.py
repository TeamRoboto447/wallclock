"""Background data for handlers. A handler with refresh(opts) and INTERVAL (seconds) gets its own
daemon thread; draw() reads the cached result via ctx.fetched(name, opts) and never does I/O.
A failed refresh keeps the last good value and records the error, so the display can show it stale."""
import threading
import time

from layout import handler, module_key


class Fetched:
    def __init__(self):
        self.value = None
        self.at = None   # time.time() of the last success
        self.err = None  # repr of the last failure, None after a success

    def age(self, now):
        return None if self.at is None else max(0, now - self.at)


class Refresher:
    def __init__(self):
        self.state = {}    # module_key -> Fetched; handed to Ctx(data=...)
        self.version = 0   # bumps after every attempt; part of the kiosk redraw key
        self._stops = {}

    def sync(self, layout, get=handler):
        """Start threads for new modules, stop them for modules no longer in the layout.
        Identical handler+opts modules share one thread."""
        wanted = {}
        for m in layout:
            try:
                h = get(m["handler"])
            except Exception:
                continue  # layout.resolve/render already report unknown handlers
            if hasattr(h, "refresh"):
                wanted[module_key(m["handler"], m.get("opts"))] = (h, m.get("opts", {}))
        for k in [k for k in self._stops if k not in wanted]:
            self._stops.pop(k).set()
            self.state.pop(k, None)
        for k, (h, opts) in wanted.items():
            if k not in self._stops:
                self.state[k] = f = Fetched()
                self._stops[k] = stop = threading.Event()
                threading.Thread(target=self._run, args=(h, opts, f, stop), daemon=True).start()

    def _run(self, h, opts, f, stop):
        while not stop.is_set():
            try:
                f.value, f.at, f.err = h.refresh(opts), time.time(), None
            except Exception as e:
                f.err = repr(e)
            self.version += 1
            stop.wait(h.INTERVAL)
