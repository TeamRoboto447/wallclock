from panels import _draw_gantt, _draw_md, plan_height


def fit_height(ctx, opts, w, max_h):
    return plan_height(ctx.plan, max_h)


def draw(surf, rect, ctx, opts):
    if ctx.plan.get("bars"):
        _draw_gantt(surf, rect, ctx.plan, ctx.font_mid, ctx.font_sm, ctx.now)
    else:
        _draw_md(surf, rect, "plan", ctx.plan.get("items") or [], ctx.font_mid, ctx.font_sm)
