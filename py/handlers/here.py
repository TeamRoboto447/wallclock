"""Who's here. opts: group_by = "role" (default) | "location"; locations = headings in order (location mode);
times = false -> no time columns, names flow as wrapped text; min_w = narrowest width for w="fit".
With time columns, a panel at least 2*COL_W wide puts each section's names in two columns."""
from panels import COL_W, _draw_here, _draw_here_flow, rows_height


def groups(ctx, opts):
    if opts.get("group_by") != "location":
        return [
            (f"students ({len(ctx.students)})", ctx.students),
            (f"parents ({len(ctx.parents)})", ctx.parents),
            (f"mentors ({len(ctx.mentors)})", ctx.mentors),
        ]
    rows = sorted(ctx.mentors + ctx.students + ctx.parents, key=lambda r: r[0].lower())
    names = list(opts.get("locations") or [])
    names += sorted({r[4] for r in rows if r[4] and r[4] not in names})  # a location the layout doesn't list
    out = [(f"{n} ({len(g)})", g) for n in names for g in [[r for r in rows if r[4] == n]]]
    lost = [r for r in rows if not r[4]]
    return out + ([(f"unassigned ({len(lost)})", lost)] if lost else [])


def fit_width(ctx, opts, max_w, h=None):
    """Wide enough for the longest name; with time columns, two columns once one column would not fit in h."""
    size = lambda t: ctx.font_sm.size(t)[0]
    gs = groups(ctx, opts)
    widest = max([size("who's here")] + [size(label) for label, _ in gs] + [size(r[0]) for _, rows in gs for r in rows])
    min_w = opts.get("min_w", 220)
    if opts.get("times") is False:
        return min(max_w, max(min_w, widest + 40))
    if rows_height(gs) > (h or 788) - 52:
        return min(max_w, max(min_w, 2 * COL_W + 16))
    return min(max_w, max(min_w, widest + 250))


def draw(surf, rect, ctx, opts):
    gs = groups(ctx, opts)
    if opts.get("times") is False:
        _draw_here_flow(surf, rect, gs, ctx.font_sm)
    else:
        _draw_here(surf, rect, gs, ctx.font_sm, ctx.now, True, 2 if rect.width >= 2 * COL_W + 16 else 1)
