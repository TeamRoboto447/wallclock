"""Time, date and wifi glyph, right-aligned in its rect (drawn over the status bar)."""
import datetime

from panels import BEIGE, INK, _draw_wifi


def draw(surf, rect, ctx, opts):
    dt = datetime.datetime.fromtimestamp(ctx.now)
    clock = ctx.font_big.render(dt.strftime("%I:%M %p").lstrip("0"), True, INK)
    date = ctx.font_sm.render(dt.strftime("%a %b ") + str(dt.day), True, BEIGE)
    clock_x = rect.right - 18 - clock.get_width()
    surf.blit(clock, (clock_x, rect.y + 30))
    _draw_wifi(surf, clock_x - 54, rect.y + 30 + clock.get_height() - 8, ctx.net[0], ctx.net[1])
    surf.blit(date, (rect.right - 18 - date.get_width(), rect.y + 30 + clock.get_height() + 4))
