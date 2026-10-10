import pygame

from panels import CORAL, INK, STATUS_BG, panel_bg


def draw(surf, rect, ctx, opts):
    panel_bg(surf, rect, STATUS_BG, 235)
    surf.blit(ctx.font_sm.render(opts.get("label", "Badge reader"), True, CORAL), (rect.x + 18, rect.y + 16))
    surf.blit(ctx.font_mid.render(ctx.status, True, INK), (rect.x + 18, rect.y + 48))
