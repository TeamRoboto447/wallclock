from panels import _draw_here


def draw(surf, rect, ctx, opts):
    _draw_here(surf, rect, ctx.mentors, ctx.students, ctx.parents, ctx.font_sm, ctx.now)
