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


def layout_path():
    return os.environ.get("TVGUI_LAYOUT") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "layouts", "wall.json")


def load_layout(path=None):
    with open(path or layout_path()) as f:
        return json.load(f)


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
    """-> [(module, (x, y, w, h))] in draw order."""
    placed, out = {}, []
    for m in layout:
        x, y = _val(m["x"], placed), _val(m["y"], placed)
        w = _val(m["w"], placed) if "w" in m else _val(m["right"], placed) - x
        if m.get("h") == "fit":
            h = min(m["max_h"], get(m["handler"]).fit_height(ctx, m.get("opts", {}), w, m["max_h"]))
        elif "h" in m:
            h = _val(m["h"], placed)
        else:
            h = _val(m["bottom"], placed) - y
        rect = (x, y, w, h)
        if "id" in m:
            placed[m["id"]] = rect
        out.append((m, rect))
    return out


def render(layout, ctx):
    import pygame
    from panels import BG

    surf = pygame.Surface(ctx.size)
    surf.fill(BG)
    for m, rect in resolve(layout, ctx):
        handler(m["handler"]).draw(surf, pygame.Rect(rect), ctx, m.get("opts", {}))
    return surf
