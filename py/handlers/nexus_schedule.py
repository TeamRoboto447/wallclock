"""Upcoming matches. opts: event (required), team (only that team's matches), title."""
import datetime

import pygame

import nexus
from panels import BEIGE, BLUE, CORAL, GREEN, INK, LTRED, PANEL, YELLOW, draw_freshness

INTERVAL = 15
TAGS = {"On field": ("FIELD", GREEN), "On deck": ("DECK", YELLOW), "Now queuing": ("QUEUE", CORAL)}  # other statuses show the time


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
    for m in nexus.upcoming(f.value.get("matches", []), opts.get("team")):
        if y + 30 > rect.bottom - 8:
            break
        start = m.get("times", {}).get("estimatedStartTime")
        clock = datetime.datetime.fromtimestamp(start / 1000).strftime("%I:%M").lstrip("0") if start else ""
        tag, tag_color = TAGS.get(m.get("status"), (clock, BEIGE))
        x = rect.x + 20
        for text, color, w in (
            (m["label"], GREEN if mine else INK, 190),
            (tag, tag_color, 100),
            (" ".join(m["redTeams"]), LTRED, 190),
            (" ".join(m["blueTeams"]), BLUE, 190),
        ):
            surf.blit(ctx.font_sm.render(text, True, color), (x, y))
            x += w
        y += 32
