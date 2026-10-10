"""Upcoming matches. opts: event (required), team (only that team's matches), title."""
import datetime

import pygame

import nexus
from panels import BEIGE, BLUE, GREEN, INK, LTRED, MUTED, PANEL, draw_freshness

INTERVAL = 15


def refresh(opts):
    return nexus.event(opts["event"])


def draw(surf, rect, ctx, opts):
    pygame.draw.rect(surf, PANEL, rect)
    surf.blit(ctx.font_sm.render(opts.get("title", "match schedule"), True, BEIGE), (rect.x + 20, rect.y + 16))
    f = ctx.fetched("nexus_schedule", opts)
    draw_freshness(surf, rect, ctx.font_sm, f, ctx.now, 3 * INTERVAL)
    if f is None or f.value is None:
        return
    mine = opts.get("team")
    y = rect.y + 52
    for m in nexus.upcoming(f.value.get("matches", []), ctx.now * 1000, opts.get("team")):
        if y + 30 > rect.bottom - 8:
            break
        start = m.get("times", {}).get("estimatedStartTime")
        clock = datetime.datetime.fromtimestamp(start / 1000).strftime("%I:%M").lstrip("0") if start else ""
        x = rect.x + 20
        for text, color, w in (
            (m["label"], GREEN if mine else INK, 200),
            (clock, BEIGE, 90),
            (" ".join(m["redTeams"]), LTRED, 190),
            (" ".join(m["blueTeams"]), BLUE, 190),
        ):
            surf.blit(ctx.font_sm.render(text, True, color), (x, y))
            x += w
        y += 32
