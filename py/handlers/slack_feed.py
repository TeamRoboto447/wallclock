"""Important Team Communications: newest messages from a Slack channel, newest first. The border shows how
fresh the newest one is: flashing blue/red under 1 min, red under 5, a thin outline otherwise.
opts: channel (default $TVGUI_SLACK_CHANNEL), title, limit."""
import pygame

import slack
from panels import (BEIGE, BLUE, BORDER, CORAL, INK, LTRED, MUTED, age_text, border_state, draw_freshness, panel_bg,
                    wrap_text)

INTERVAL = 15


def refresh(opts):
    return slack.messages(opts.get("channel"), opts.get("limit", 15))


def draw(surf, rect, ctx, opts):
    f = ctx.fetched("slack_feed", opts)
    msgs = (f.value if f else None) or []
    panel_bg(surf, rect)
    surf.blit(ctx.font_sm.render(opts.get("title", "Important Team Communications"), True, BEIGE), (rect.x + 20, rect.y + 16))
    draw_freshness(surf, rect, ctx.font_sm, f, ctx.now, 3 * INTERVAL)
    y = rect.y + 56
    for i, m in enumerate(msgs):
        font = ctx.font_mid if i == 0 else ctx.font_sm
        step = font.get_linesize()
        head = f"{m['author']}  ·  {age_text(max(0, ctx.now - m['ts']))} ago"
        if y + 28 > rect.bottom - 12:
            break
        surf.blit(ctx.font_sm.render(head, True, CORAL), (rect.x + 20, y))
        y += 30
        lines = wrap_text(font, m["text"], rect.width - 40)
        for j, line in enumerate(lines[: 6 if i == 0 else 3]):
            if y + step > rect.bottom - 12:
                break
            surf.blit(font.render(line, True, INK), (rect.x + 20, y))
            y += step
        y += 14
    if not msgs and f is not None and f.value is not None:
        surf.blit(ctx.font_sm.render("no messages", True, MUTED), (rect.x + 20, rect.y + 56))
    state = border_state(ctx.now - msgs[0]["ts"]) if msgs else "idle"
    if state == "flash":
        ctx.want_tick(0.5)
        pygame.draw.rect(surf, BLUE if ctx.beat % 2 == 0 else LTRED, rect, 8)
    elif state == "red":
        ctx.want_tick(1)  # so the switch to the idle border happens on time
        pygame.draw.rect(surf, LTRED, rect, 8)
    else:
        pygame.draw.rect(surf, BORDER, rect, 2)
