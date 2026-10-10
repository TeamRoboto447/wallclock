"""Drawing code for the kiosk screen: colors, sizes and the panel drawing functions."""
import datetime
import math

from attendance import year_start

FONT = "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"
BG = (0x22, 0x22, 0x22)
RED = (0xB5, 0x04, 0x04)
CORAL = (0xD8, 0x61, 0x3C)
BEIGE = (0xCF, 0xCA, 0xBE)
INK = (0xF9, 0xF9, 0xF9)
GREEN = (0x5F, 0xD3, 0x7A)
BLUE = (0x4A, 0x9E, 0xE0)
YELLOW = (0xE6, 0xB4, 0x22)
LTRED = (0xE5, 0x48, 0x4D)
STATUS_COLOR = {"new": BLUE, "wip": YELLOW, "blocked": LTRED, "done": GREEN}
MUTED = (0x6A, 0x6A, 0x62)
MD_MARK = {"more": ("", MUTED), "todo": ("[ ]", BLUE), "wip": ("[~]", YELLOW), "blocked": ("[!]", LTRED), "done": ("[x]", GREEN)}
PANEL = (0x1A, 0x1A, 0x1A)
BORDER = (0x3A, 0x3A, 0x3A)
GRID = (0x2A, 0x2A, 0x2A)
STATUS_BG = (0x3A, 0x0A, 0x0A)
CYCLE_SECS = 10
GANTT_HEAD_H = 64
GANTT_ROW_H = 32
GANTT_PAD = 12


def local_midnight():
    n = datetime.datetime.now().astimezone()
    return int(n.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def fmt_in(ts, now):
    start = max(int(ts), local_midnight())
    mins = max(0, now - start) // 60
    h, m = divmod(mins, 60)
    return f"{h}:{m:02d}"


def fmt_total(secs):
    h, m = divmod(max(0, int(secs)) // 60, 60)
    return f"{h}:{m:02d}"
DONE = (0x7A, 0x7A, 0x72)


def _clip_blit(surf, img, pos, rect):
    x, y = pos
    if y + img.get_height() > rect.bottom - 8 or y < rect.top:
        return False
    surf.blit(img, (x, y))
    return True


def _draw_md(surf, rect, title, items, font_mid, font_sm):
    import pygame

    pygame.draw.rect(surf, PANEL, rect)
    surf.blit(font_sm.render(title, True, BEIGE), (rect.x + 20, rect.y + 16))
    y = rect.y + 52
    x = rect.x + 20
    max_w = rect.width - 40
    if not items:
        _clip_blit(surf, font_sm.render("—", True, MUTED), (x, y), rect)
        return
    for kind, text in items:
        if y >= rect.bottom - 28:
            break
        if kind == "h":
            y += 8
            img = font_mid.render(text, True, CORAL)
            if not _clip_blit(surf, img, (x, y), rect):
                break
            y += 40
            continue
        mark, color = MD_MARK.get(kind, ("", INK))
        line = f"{mark} {text}".strip()
        img = font_sm.render(line, True, color)
        if img.get_width() > max_w:
            while len(line) > 1 and font_sm.size(line + "…")[0] > max_w:
                line = line[:-1]
            img = font_sm.render(line.rstrip() + "…", True, color)
        if not _clip_blit(surf, img, (x, y), rect):
            break
        y += 32


def _draw_here(surf, rect, groups, font_sm, now, times=True):
    """groups: [(heading, rows)], rows = (name, ts, enabled, closed_secs, ...)."""
    import pygame

    pygame.draw.rect(surf, PANEL, rect)
    surf.blit(font_sm.render("who's here", True, BEIGE), (rect.x + 20, rect.y + 16))
    total_r = rect.right - 20
    meet_r = total_r - 100
    grid_top = rect.y + 46
    if times:
        for label, right in (("meeting", meet_r), ("total", total_r)):
            img = font_sm.render(label, True, MUTED)
            surf.blit(img, (right - img.get_width(), rect.y + 16))
        for gx in (meet_r - 78, meet_r + 22):
            pygame.draw.line(surf, GRID, (gx, grid_top), (gx, rect.bottom - 8), 1)
    pygame.draw.line(surf, GRID, (rect.x + 8, grid_top), (rect.right - 8, grid_top), 1)
    y = rect.y + 52
    x = rect.x + 20
    for label, names in groups:
        if y >= rect.bottom - 28:
            break
        img = font_sm.render(label, True, CORAL)
        if not _clip_blit(surf, img, (x, y), rect):
            break
        y += 32
        for name, ts, enabled, closed, *_ in names:
            color = GREEN if enabled else INK
            max_name = meet_r - 84 - x if times else rect.right - 20 - x
            while len(name) > 1 and font_sm.size(name)[0] > max_name:
                name = name[:-1]
            ns = font_sm.render(name, True, color)
            if not _clip_blit(surf, ns, (x, y), rect):
                return
            if times:
                meet = font_sm.render(fmt_in(ts, now), True, BEIGE)
                tot = font_sm.render(
                    fmt_total(closed + max(0, now - max(int(ts), year_start(now)))), True, BEIGE
                )
                surf.blit(meet, (meet_r - meet.get_width(), y))
                surf.blit(tot, (total_r - tot.get_width(), y))
            pygame.draw.line(surf, GRID, (rect.x + 8, y + 28), (rect.right - 8, y + 28), 1)
            y += 30
        y += 10


def today_offset_weeks(start, weeks, now):
    """Where `now` falls on the plan, in weeks from its start (days and the time
    of day included), or None if it is before the plan starts or after it ends."""
    if not start:
        return None
    n = datetime.datetime.fromtimestamp(now)
    days = (n.date() - start).days + (n.hour * 3600 + n.minute * 60 + n.second) / 86400
    return days / 7 if 0 <= days < weeks * 7 else None


def _draw_gantt(surf, rect, plan, font_mid, font_sm, now):
    import pygame

    pygame.draw.rect(surf, PANEL, rect)
    title = plan.get("title") or "plan"
    surf.blit(font_sm.render(title, True, BEIGE), (rect.x + 16, rect.y + 12))
    weeks = max(1, int(plan.get("weeks") or 8))
    bars = plan.get("bars") or []
    label_w = 290
    head_h = GANTT_HEAD_H
    row_h = GANTT_ROW_H
    grid = pygame.Rect(
        rect.x + 16 + label_w,
        rect.y + head_h,
        rect.width - 32 - label_w,
        rect.height - head_h - GANTT_PAD,
    )
    if grid.width < 40 or grid.height < 24:
        return
    col_w = grid.width / weeks
    step = max(1, math.ceil(100 / col_w))  # thin the week labels when weeks are narrow
    this_year = datetime.datetime.fromtimestamp(now).year
    for i in range(weeks):
        x = int(grid.x + i * col_w)
        pygame.draw.line(surf, BORDER, (x, grid.y), (x, grid.bottom - 1), 1)
        if plan.get("start") and i % step == 0:
            d = plan["start"] + datetime.timedelta(days=7 * i)
            label = f"{d.month}/{d.day}" + (f"/{d.year % 100}" if d.year != this_year else "")
            if col_w * step >= 150:
                label = f"W{i + 1} {label}"
            surf.blit(font_sm.render(label, True, MUTED), (x + 4, rect.y + 10))
        elif not plan.get("start") and i % step == 0:
            surf.blit(font_sm.render(f"W{i + 1}", True, MUTED), (x + 4, rect.y + 10))
    pygame.draw.line(surf, BORDER, (grid.right - 1, grid.y), (grid.right - 1, grid.bottom - 1), 1)
    off = today_offset_weeks(plan.get("start"), weeks, now)
    if off is not None:  # the line sits at the exact moment, with a "today" tag above it
        cx = int(grid.x + off * col_w)
        tag = font_sm.render("today", True, INK)
        box = pygame.Rect(0, grid.y - 28, tag.get_width() + 14, 24)
        box.x = max(grid.x, min(cx - box.width // 2, grid.right - box.width))
        pygame.draw.rect(surf, RED, box, border_radius=4)
        surf.blit(tag, (box.x + 7, box.y + (box.height - tag.get_height()) // 2))
        pygame.draw.line(surf, RED, (cx, box.bottom), (cx, grid.bottom - 1), 2)
    y = grid.y
    placed = {}  # milestone id -> (row y, left x, right x, status)
    for status, name, lo, hi, d0, d1, bid, bdep in bars:
        if y + row_h > grid.bottom:
            break
        if font_sm.size(name)[0] > label_w - 12:
            while len(name) > 1 and font_sm.size(name + "…")[0] > label_w - 12:
                name = name[:-1]
            name = name.rstrip() + "…"
        label = font_sm.render(name, True, INK)
        surf.blit(label, (rect.x + 16, y + 4))
        if lo and hi:
            if d0 is not None:  # day-accurate span
                x0 = int(grid.x + d0 * col_w) + 2
                x1 = int(grid.x + d1 * col_w) - 2
            else:
                x0 = int(grid.x + (lo - 1) * col_w) + 3
                x1 = int(grid.x + hi * col_w) - 3
            bar = pygame.Rect(x0, y + 6, max(4, x1 - x0), row_h - 12)
            pygame.draw.rect(surf, STATUS_COLOR.get(status, BLUE), bar)
            if bid is not None:
                placed[bid] = (y, bar.left, bar.right, status, bdep)
        y += row_h
    _draw_dependencies(surf, placed, row_h)


def _draw_dependencies(surf, placed, row_h):
    """An arrow from the end of each prerequisite bar to the start of the bar
    that depends on it; red, with an outlined bar, if the dependent starts
    before its prerequisite ends."""
    import pygame

    for bid, (y, left, right, status, dep) in placed.items():
        if dep not in placed:
            continue
        py, _, pright, pstatus, _ = placed[dep]
        conflict = left <= pright
        color = LTRED if conflict else BEIGE
        my, py_mid = y + row_h // 2, py + row_h // 2
        turn = pright + 8
        pygame.draw.lines(surf, color, False, [(pright, py_mid), (turn, py_mid), (turn, my), (left - 5, my)], 2)
        pygame.draw.polygon(surf, color, [(left - 1, my), (left - 8, my - 5), (left - 8, my + 5)])
        if left <= pright:  # starts before its prerequisite ends
            pygame.draw.rect(surf, LTRED, pygame.Rect(left, y + 6, max(4, right - left), row_h - 12), 3)


def today_pages(items, cap):
    """Split md items into pages of at most cap task rows, one heading
    (milestone) per page. Only unfinished tasks make pages; finished ones fill
    whatever room is left on the last page and are never given pages of their
    own, with a '+N more done' line for any that don't fit."""
    groups = []
    for kind, text in items:
        if kind == "h" or not groups:
            groups.append((text if kind == "h" else None, []))
            if kind == "h":
                continue
        groups[-1][1].append((kind, text))
    pages = []
    for head, rows in groups:
        if not rows and head is None:
            continue
        open_rows = [r for r in rows if r[0] != "done"]
        done_rows = [r for r in rows if r[0] == "done"]
        chunks = [open_rows[i:i + cap] for i in range(0, len(open_rows), cap)] or [[]]
        last = chunks[-1]
        room = max(0, cap - len(last))
        if done_rows and len(done_rows) <= room:
            last += done_rows
        elif done_rows and room >= 1:  # keep one row for the summary
            keep = room - 1
            last += done_rows[:keep] + [("more", f"+{len(done_rows) - keep} more done")]
        for c in chunks:
            pages.append(([("h", head)] if head else []) + c)
    return pages


def plan_height(plan, cap):
    if not plan.get("bars"):
        return min(cap, 120)
    need = GANTT_HEAD_H + len(plan["bars"]) * GANTT_ROW_H + GANTT_PAD
    return min(cap, need)
NET_COLOR = {"ok": GREEN, "limited": YELLOW, "down": LTRED}


def _draw_wifi(surf, cx, base_y, state, bars):
    """Wifi glyph: a dot and three arcs, lit by signal bars, colored by state."""
    import pygame

    color = NET_COLOR.get(state, MUTED)
    lit = bars if state in ("ok", "limited") else 0
    pygame.draw.circle(surf, color if state != "down" else MUTED, (cx, base_y), 4)
    for i, r in enumerate((13, 23, 33)):
        c = color if i < max(lit, 1 if state in ("ok", "limited") else 0) else BORDER
        rect = pygame.Rect(cx - r, base_y - r, 2 * r, 2 * r)
        pygame.draw.arc(surf, c, rect, math.radians(48), math.radians(132), 4)
    if state == "down":
        pygame.draw.line(surf, LTRED, (cx - 24, base_y - 34), (cx + 24, base_y + 6), 4)


def age_text(secs):
    return f"{int(secs)}s" if secs < 90 else f"{int(secs // 60)}m" if secs < 5400 else f"{int(secs // 3600)}h"


def draw_freshness(surf, rect, font, f, now, stale_after):
    """Top-right note on a network-fed panel: nothing while fresh, else how old the data is."""
    if f is None or f.at is None:
        text, color = ("no data yet" if f is None or f.err is None else "offline"), MUTED
    elif f.err or now - f.at > stale_after:
        text, color = f"{'offline, ' if f.err else ''}updated {age_text(now - f.at)} ago", YELLOW
    else:
        return
    img = font.render(text, True, color)
    surf.blit(img, (rect.right - 16 - img.get_width(), rect.y + 16))


def clip_text(font, text, max_w):
    if font.size(text)[0] <= max_w:
        return text
    while len(text) > 1 and font.size(text + "…")[0] > max_w:
        text = text[:-1]
    return text.rstrip() + "…"
