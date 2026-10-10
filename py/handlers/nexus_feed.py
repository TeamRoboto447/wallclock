"""Newest-first list from the event status. opts: event (required), kind = "parts" | "announcements", title."""
import pygame

import nexus
from panels import panel_bg, BEIGE, CORAL, INK, MUTED, age_text, clip_text, draw_freshness

INTERVAL = 15
KINDS = {  # kind -> (event key, title, row text)
    "parts": ("partsRequests", "parts requests", lambda r: f"{r.get('requestedByTeam', '?')}: {r.get('parts', '')}"),
    "announcements": ("announcements", "announcements", lambda r: r.get("announcement", "")),
}


def refresh(opts):
    return nexus.event(nexus.event_key(opts))


def draw(surf, rect, ctx, opts):
    field, title, row = KINDS[opts["kind"]]
    panel_bg(surf, rect)
    surf.blit(ctx.font_sm.render(opts.get("title", title), True, BEIGE), (rect.x + 20, rect.y + 16))
    f = ctx.fetched("nexus_feed", opts)
    draw_freshness(surf, rect, ctx.font_sm, f, ctx.now, 3 * INTERVAL)
    if f is None or f.value is None:
        return
    items = sorted(f.value.get(field, []), key=lambda r: r.get("postedTime", 0), reverse=True)
    if not items:
        surf.blit(ctx.font_sm.render("none", True, MUTED), (rect.x + 20, rect.y + 52))
    y = rect.y + 52
    for r in items:
        if y + 30 > rect.bottom - 8:
            break
        age = age_text(max(0, ctx.now - r.get("postedTime", 0) / 1000))
        tag = ctx.font_sm.render(f"{age} ago", True, CORAL)
        surf.blit(tag, (rect.right - 20 - tag.get_width(), y))
        surf.blit(ctx.font_sm.render(clip_text(ctx.font_sm, row(r), rect.width - 60 - tag.get_width()), True, INK), (rect.x + 20, y))
        y += 32
