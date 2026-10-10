import pygame

from panels import CORAL, INK, STATUS_BG


def draw(surf, rect, ctx, opts):
    pygame.draw.rect(surf, STATUS_BG, rect)
    surf.blit(ctx.font_sm.render(opts.get("label", "Badge reader"), True, CORAL), (rect.x + 18, rect.y + 16))
    surf.blit(ctx.font_mid.render(ctx.status, True, INK), (rect.x + 18, rect.y + 48))
