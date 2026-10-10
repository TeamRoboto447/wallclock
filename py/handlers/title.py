from panels import RED


def draw(surf, rect, ctx, opts):
    surf.blit(ctx.font_mid.render(opts.get("text", "Team Roboto"), True, RED), (rect.x, rect.y))
