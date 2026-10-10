"""Next match of our team (opts team, else $TVGUI_TEAM), compact: a title row and one line. opts: event, team, title."""
import pygame

import nexus
from panels import panel_bg, BEIGE, BLUE, CORAL, INK, LTRED, MUTED, draw_freshness

INTERVAL = 15
SEP = "  ·  "


def refresh(opts):
    return nexus.event(nexus.event_key(opts))


def draw(surf, rect, ctx, opts):
    panel_bg(surf, rect)
    team = nexus.team(opts)
    title = opts.get("title") or "next match" + (f" (team {team})" if team else "")
    surf.blit(ctx.font_sm.render(title, True, BEIGE), (rect.x + 20, rect.y + 16))
    f = ctx.fetched("nexus_next", opts)
    draw_freshness(surf, rect, ctx.font_sm, f, ctx.now, 3 * INTERVAL)
    if f is None or f.value is None:
        return
    if f.value.get("nowQueuing"):
        x = rect.x + 20 + ctx.font_sm.size(title)[0] + 40
        surf.blit(ctx.font_sm.render(f"Now queuing: {f.value['nowQueuing']}", True, BEIGE), (x, rect.y + 16))
    nxt = nexus.upcoming(f.value.get("matches", []), team)
    if not nxt:
        text = f"no upcoming match for {team}" if team else "no upcoming match"
        surf.blit(ctx.font_mid.render(text, True, MUTED), (rect.x + 20, rect.y + 50))
        return
    m = nxt[0]
    t = m.get("times", {})
    eta = nexus.eta_text(t["estimatedQueueTime"], ctx.now * 1000) if "estimatedQueueTime" in t else ""
    status = f"{m['status']} (queues in {eta})" if eta not in ("", "now") else m["status"]
    segs = [(m["label"], CORAL), (status, INK), ("red " + " ".join(m["redTeams"]), LTRED), ("blue " + " ".join(m["blueTeams"]), BLUE)]
    for font in (ctx.font_mid, ctx.font_sm):  # the smaller font only if the line would not fit
        if sum(font.size(s)[0] for s, _ in segs) + font.size(SEP)[0] * (len(segs) - 1) <= rect.width - 40:
            break
    x, y = rect.x + 20, rect.y + 50
    for i, (text, color) in enumerate(segs):
        if i:
            img = font.render(SEP, True, MUTED)
            surf.blit(img, (x, y))
            x += img.get_width()
        img = font.render(text, True, color)
        surf.blit(img, (x, y))
        x += img.get_width()
