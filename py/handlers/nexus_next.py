"""Next match (the team's, if opts.team is set). opts: event (required), team, title."""
import pygame

import nexus
from panels import BEIGE, BLUE, CORAL, INK, LTRED, MUTED, PANEL, draw_freshness

INTERVAL = 15


def refresh(opts):
    return nexus.event(opts["event"])


def draw(surf, rect, ctx, opts):
    pygame.draw.rect(surf, PANEL, rect)
    surf.blit(ctx.font_sm.render(opts.get("title", "next match"), True, BEIGE), (rect.x + 20, rect.y + 16))
    f = ctx.fetched("nexus_next", opts)
    draw_freshness(surf, rect, ctx.font_sm, f, ctx.now, 3 * INTERVAL)
    if f is None or f.value is None:
        return
    now_ms = ctx.now * 1000
    team = opts.get("team")
    nxt = nexus.upcoming(f.value.get("matches", []), team)
    x, y = rect.x + 20, rect.y + 56
    if not nxt:
        surf.blit(ctx.font_mid.render(f"no upcoming match for {team}" if team else "no upcoming match", True, MUTED), (x, y))
    else:
        m = nxt[0]
        t = m.get("times", {})
        eta = nexus.eta_text(t["estimatedQueueTime"], now_ms) if "estimatedQueueTime" in t else ""
        surf.blit(ctx.font_big.render(m["label"], True, CORAL), (x, y))
        surf.blit(ctx.font_mid.render(f"{m['status']}  (queues in {eta})" if eta not in ("", "now") else m["status"], True, INK), (x, y + 52))
        for i, (name, teams, color) in enumerate((("red", m["redTeams"], LTRED), ("blue", m["blueTeams"], BLUE))):
            surf.blit(ctx.font_mid.render(f"{name}:  " + "   ".join(teams), True, color), (x, y + 100 + i * 40))
    if f.value.get("nowQueuing"):
        surf.blit(ctx.font_sm.render(f"Now queuing: {f.value['nowQueuing']}", True, BEIGE), (x, rect.bottom - 36))
