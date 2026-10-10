"""Drawing code for the kiosk screen: colors, sizes and the panel drawing functions."""
import datetime
import math

from attendance import year_start

FONT = "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"
BG = (0x0B, 0x0A, 0x1E)
RED = (0xB5, 0x04, 0x04)
CORAL = (0xD8, 0x61, 0x3C)
BEIGE = (0xD6, 0xD9, 0xE8)
INK = (0xF9, 0xF9, 0xF9)
GREEN = (0x5F, 0xD3, 0x7A)
BLUE = (0x4A, 0x9E, 0xE0)
YELLOW = (0xE6, 0xB4, 0x22)
LTRED = (0xE5, 0x48, 0x4D)
STATUS_COLOR = {"new": BLUE, "wip": YELLOW, "blocked": LTRED, "done": GREEN}
MUTED = (0x8A, 0x8C, 0xA8)
MD_MARK = {"more": ("", MUTED), "todo": ("[ ]", BLUE), "wip": ("[~]", YELLOW), "blocked": ("[!]", LTRED), "done": ("[x]", GREEN)}
PANEL = (0x0E, 0x0D, 0x24)
RADIUS = 6  # corner radius of panels and the Slack border
PANEL_ALPHA = 215  # panels are translucent so the background art shows through
BORDER = (0x4B, 0x43, 0xB0)
GRID = (0x2C, 0x2A, 0x5A)
STATUS_BG = (0x1C, 0x08, 0x5A)
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

    panel_bg(surf, rect)
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


ROW_H, HEAD_H, GAP = 30, 32, 10  # roster row, section heading and gap between sections
COL_W = 300  # width of one column when a section's names are split in two


def rows_height(groups, cols=1):
    """Height of the roster rows for `cols` columns of names under each heading."""
    return sum(HEAD_H + -(-len(rows) // cols) * ROW_H + GAP for _, rows in groups)


def _more(surf, rect, font_sm, x, n):
    surf.blit(font_sm.render(f"+{n} more", True, MUTED), (x, rect.bottom - 30))


def _draw_here(surf, rect, groups, font_sm, now, times=True, cols=1):
    """groups: [(heading, rows)], rows = (name, ts, enabled, closed_secs, ...). With cols=2 each heading
    stays whole and its names run down two columns, each with its own time cells."""
    import pygame

    panel_bg(surf, rect)
    surf.blit(font_sm.render("who's here", True, BEIGE), (rect.x + 20, rect.y + 16))
    grid_top = rect.y + 46
    pygame.draw.line(surf, GRID, (rect.x + 8, grid_top), (rect.right - 8, grid_top), 1)
    tw, reserve = (100, 84) if cols == 1 else (72, 58)  # spacing of the time columns / room kept left of them
    labels = ("meeting", "total") if cols == 1 else ("mtg", "total")
    cw = (rect.width - 16) / cols
    spans = []  # per column: name x, left edge, right edge, meeting right edge, total right edge
    for i in range(cols):
        c0, c1 = rect.x + 8 + round(i * cw), rect.x + 8 + round((i + 1) * cw)
        total_r = c1 - 12
        spans.append((c0 + 12, c0, c1, total_r - tw, total_r))
        if times:
            for label, right in zip(labels, (total_r - tw, total_r)):
                img = font_sm.render(label, True, MUTED)
                surf.blit(img, (right - img.get_width(), rect.y + 16))
            for gx in (total_r - tw - tw + 22, total_r - tw + 22):
                pygame.draw.line(surf, GRID, (gx, grid_top), (gx, rect.bottom - 8), 1)
    y = rect.y + 52
    skipped = 0
    for gi, (label, names) in enumerate(groups):
        img = font_sm.render(label, True, CORAL)
        if y >= rect.bottom - 28 or not _clip_blit(surf, img, (spans[0][0], y), rect):
            skipped += sum(len(r) for _, r in groups[gi:])
            break
        y += HEAD_H
        per = -(-len(names) // cols)
        for ci, (x, c0, c1, meet_r, total_r) in enumerate(spans):
            for k, (name, ts, enabled, closed, *_) in enumerate(names[ci * per:(ci + 1) * per]):
                yy = y + k * ROW_H
                max_name = meet_r - reserve - x if times else c1 - 12 - x
                while len(name) > 1 and font_sm.size(name)[0] > max_name:
                    name = name[:-1]
                ns = font_sm.render(name, True, GREEN if enabled else INK)
                if not _clip_blit(surf, ns, (x, yy), rect):
                    skipped += 1
                    continue
                if times:
                    meet = font_sm.render(fmt_in(ts, now), True, BEIGE)
                    tot = font_sm.render(fmt_total(closed + max(0, now - max(int(ts), year_start(now)))), True, BEIGE)
                    surf.blit(meet, (meet_r - meet.get_width(), yy))
                    surf.blit(tot, (total_r - tot.get_width(), yy))
                pygame.draw.line(surf, GRID, (c0, yy + 28), (c1, yy + 28), 1)
        y += per * ROW_H + GAP
    if skipped:
        _more(surf, rect, font_sm, spans[0][0], skipped)


def _draw_here_flow(surf, rect, groups, font_sm):
    """No time columns: each heading is followed by its names as ' · '-separated text wrapped to the panel."""
    import pygame

    panel_bg(surf, rect)
    surf.blit(font_sm.render("who's here", True, BEIGE), (rect.x + 20, rect.y + 16))
    pygame.draw.line(surf, GRID, (rect.x + 8, rect.y + 46), (rect.right - 8, rect.y + 46), 1)
    x0, x1 = rect.x + 20, rect.right - 20
    sep_w = font_sm.size(" · ")[0]
    y = rect.y + 52
    skipped = 0
    for gi, (label, names) in enumerate(groups):
        if y + ROW_H > rect.bottom - 8:
            skipped += sum(len(r) for _, r in groups[gi:])
            break
        surf.blit(font_sm.render(label, True, CORAL), (x0, y))
        y += HEAD_H
        x = x0
        for i, (name, _ts, enabled, *_) in enumerate(names):
            while len(name) > 1 and font_sm.size(name)[0] > x1 - x0:
                name = name[:-1]
            w = font_sm.size(name)[0]
            if i and x + sep_w + w <= x1:
                surf.blit(font_sm.render(" · ", True, MUTED), (x, y))
                x += sep_w
            elif i:
                x, y = x0, y + ROW_H
            if y + ROW_H > rect.bottom - 8:
                skipped += len(names) - i + sum(len(r) for _, r in groups[gi + 1:])
                break
            surf.blit(font_sm.render(name, True, GREEN if enabled else INK), (x, y))
            x += w
        else:
            y += (ROW_H if names else 0) + GAP
            continue
        break
    if skipped:
        _more(surf, rect, font_sm, x0, skipped)


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

    panel_bg(surf, rect)
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


def wrap_text(font, text, max_w):
    """Word-wrap text into lines no wider than max_w (an over-long word is split)."""
    lines, line = [], ""
    for word in text.split():
        while font.size(word)[0] > max_w and len(word) > 1:  # split a word wider than the box
            cut = len(word) - 1
            while cut > 1 and font.size(word[:cut])[0] > max_w:
                cut -= 1
            if line:
                lines.append(line)
                line = ""
            lines.append(word[:cut])
            word = word[cut:]
        trial = f"{line} {word}".strip()
        if line and font.size(trial)[0] > max_w:
            lines.append(line)
            line = word
        else:
            line = trial
    return lines + ([line] if line else [])


def border_state(age_secs):
    """Urgency of the newest Slack message: thick flashing border under 1 min, red under 5, else idle."""
    return "flash" if age_secs < 60 else "red" if age_secs < 300 else "idle"


_shells = {}


def panel_bg(surf, rect, color=PANEL, alpha=PANEL_ALPHA):
    """Translucent panel fill with a thin brand-blue outline."""
    import pygame

    key = (rect.size, color, alpha)
    if key not in _shells:
        _shells[key] = pygame.Surface(rect.size, pygame.SRCALPHA)
        pygame.draw.rect(_shells[key], (*color, alpha), _shells[key].get_rect(), border_radius=RADIUS)
    surf.blit(_shells[key], rect.topleft)
    pygame.draw.rect(surf, BORDER, rect, 1, border_radius=RADIUS)
