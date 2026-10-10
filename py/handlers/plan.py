"""The plan (Gantt chart). opts: people = "count" shows how many people are working on each milestone."""
from collections import Counter

from panels import _draw_gantt, _draw_md, plan_height


def fit_height(ctx, opts, w, max_h):
    return plan_height(ctx.plan, max_h)


def draw(surf, rect, ctx, opts):
    if ctx.plan.get("bars"):
        rows = ctx.mentors + ctx.students + ctx.parents
        badges = Counter(r[5] for r in rows if len(r) > 5 and r[5]) if opts.get("people") == "count" else None
        _draw_gantt(surf, rect, ctx.plan, ctx.font_mid, ctx.font_sm, ctx.now, badges)
    else:
        _draw_md(surf, rect, "plan", ctx.plan.get("items") or [], ctx.font_mid, ctx.font_sm)
