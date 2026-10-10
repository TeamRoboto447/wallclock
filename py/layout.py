"""Layout = a list of modules {id?, handler, x, y, w|right, h|bottom|"fit", max_h?, opts?}.
x/y/w/h are px, or "<id>.<edge>[+/-n]" (edge: x y right bottom) naming an EARLIER module,
so the list resolves top to bottom with no cycles. h="fit" asks the handler's fit_height()."""
import importlib
import json
import os
import re

REF = re.compile(r"(\w+)\.(x|y|right|bottom)\s*(?:([+-])\s*(\d+))?$")


class Ctx:
    """Everything a handler may draw from; one object instead of a long argument list."""

    def __init__(self, size, fonts, now, mentors, students, parents, status, plan, today, priority, net):
        self.size = size
        self.font_big, self.font_mid, self.font_sm = fonts
        self.now = now
        self.mentors, self.students, self.parents = mentors, students, parents
        self.status = status
        self.plan, self.today, self.priority = plan, today, priority
        self.net = net


_seen = set()


def warn(msg):
    """Print each distinct problem once; the layout is re-resolved on every redraw."""
    if msg not in _seen:
        _seen.add(msg)
        print(f"layout: {msg}", flush=True)


def default_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "layouts", "wall.json")


def layout_path():
    return os.environ.get("TVGUI_LAYOUT") or default_path()


class LayoutFile:
    """The layout JSON, reloaded when its mtime changes. A bad edit keeps the last good
    layout (or the default one at startup) instead of blanking the display."""

    def __init__(self, path=None):
        self.path = path or layout_path()
        self.layout = None
        self.mtime = None
        self._bad = None
        self.get()

    def get(self):
        try:
            mt = os.stat(self.path).st_mtime
            if mt != self.mtime and mt != self._bad:
                try:
                    with open(self.path) as f:
                        data = json.load(f)
                    if not isinstance(data, list) or not all(isinstance(m, dict) for m in data):
                        raise ValueError("must be a list of objects")
                except ValueError:
                    self._bad = mt
                    raise
                self.layout, self.mtime = data, mt
        except (OSError, ValueError) as e:
            warn(f"{self.path}: {e}; keeping the previous layout")
            if self.layout is None and self.path != default_path():
                self.path = default_path()
                return self.get()
        return self.layout or []


def handler(name):
    return importlib.import_module(f"handlers.{name}")


def _val(v, placed):
    if isinstance(v, int):
        return v
    m = REF.match(str(v))
    if not m or m.group(1) not in placed:
        raise ValueError(f"bad reference {v!r} (must name an earlier module id)")
    x, y, w, h = placed[m.group(1)]
    base = {"x": x, "y": y, "right": x + w, "bottom": y + h}[m.group(2)]
    return base + (int(m.group(4)) if m.group(3) == "+" else -int(m.group(4) or 0))


def resolve(layout, ctx, get=handler):
    """-> [(module, (x, y, w, h))] in draw order. A module that cannot be placed is skipped
    with a warning; rects are clamped to the screen."""
    placed, out = {}, []
    sw, sh = ctx.size
    for i, m in enumerate(layout):
        name = m.get("handler", "?")
        try:
            x, y = _val(m["x"], placed), _val(m["y"], placed)
            w = _val(m["w"], placed) if "w" in m else _val(m["right"], placed) - x
            if m.get("h") == "fit":
                h = min(m["max_h"], get(name).fit_height(ctx, m.get("opts", {}), w, m["max_h"]))
            elif "h" in m:
                h = _val(m["h"], placed)
            else:
                h = _val(m["bottom"], placed) - y
        except Exception as e:
            warn(f"module {i} ({name}) skipped: {e!r}")
            continue
        x0, y0, x1, y1 = max(0, x), max(0, y), min(sw, x + w), min(sh, y + h)
        if x1 <= x0 or y1 <= y0:
            warn(f"module {i} ({name}) skipped: empty or off-screen ({x},{y},{w},{h})")
            continue
        if (x0, y0, x1 - x0, y1 - y0) != (x, y, w, h):
            warn(f"module {i} ({name}) clamped to the screen")
        rect = (x0, y0, x1 - x0, y1 - y0)
        if "id" in m:
            placed[m["id"]] = rect
        out.append((m, rect))
    return out


def render(layout, ctx):
    import pygame
    from panels import BG, LTRED

    surf = pygame.Surface(ctx.size)
    surf.fill(BG)
    for m, rect in resolve(layout, ctx):
        r = pygame.Rect(rect)
        try:
            handler(m["handler"]).draw(surf, r, ctx, m.get("opts", {}))
        except Exception as e:  # one broken module must not blank the screen
            warn(f"{m['handler']} draw failed: {e!r}")
            pygame.draw.rect(surf, LTRED, r, 3)
            surf.blit(ctx.font_sm.render(f"{m['handler']}: error", True, LTRED), (r.x + 12, r.y + 10))
    return surf
