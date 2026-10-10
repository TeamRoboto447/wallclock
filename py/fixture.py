"""Fixed sample data and a headless render of the kiosk screen (tvgui.py render)."""
import datetime

from member import Member, Role
from nfc import split_here
from plan import parse_md, parse_plan

NOW = int(datetime.datetime(2026, 10, 8, 14, 30).timestamp())
SIZE = (1920, 1080)

PLAN = """[ ] 9/28/2026 - 10/15/2026 --- Shop Organization {90}
[ ] 9/28/2026 - 10/23/2026 --- Pit-side wallclock {89}
[ ] 10/5/2026 - 10/11/2026 --- Tune Shooting {67<66}
[!] 10/12/2026 - 10/23/2026 --- Practice {68<67}
"""
TODAY = """# Pit-side wallclock
- [x] Wiring fix
- [~] Layout modules
- [ ] Match schedule panel
- [ ] Part requests panel

# Shop Organization
- [ ] Label bins
- [!] Order shelving
"""
PRIORITY = """- [!] Order shelving
- [~] Layout modules
- [ ] Charge batteries
"""


def _who():
    rows = [("Ana Reyes", Role.MENTOR, 3 * 3600), ("Sam Lee", Role.STUDENT, 5400), ("Kai Wong", Role.STUDENT, 0),
            ("Priya Nair", Role.STUDENT, 7200), ("Jo Park", Role.PARENT, 0)]
    out = []
    for i, (name, role, closed) in enumerate(rows):
        m = Member(name, name.split()[0].lower(), name, role)
        m.enabled = i != 2
        m.closed_secs = closed
        out.append((m, NOW - (i + 1) * 1500))
    return split_here(out)


def render(size=SIZE):
    import pygame
    from layout import Ctx, LayoutFile, render as render_layout
    from panels import FONT

    pygame.font.init()
    mentors, students, parents = _who()
    fonts = tuple(pygame.font.Font(FONT, n) for n in (40, 32, 22))
    ctx = Ctx(size, fonts, NOW, mentors, students, parents, "Ana Reyes badged in",
              parse_plan(PLAN, today=datetime.date(2026, 10, 8)), parse_md(TODAY), parse_md(PRIORITY), ("ok", 3))
    return render_layout(LayoutFile().get(), ctx)


def save(path):
    import pygame

    pygame.image.save(render(), path)
