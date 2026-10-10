"""Who's here. opts: group_by = "role" (default) | "location"; locations = headings in order (location mode)."""
from panels import _draw_here


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


def draw(surf, rect, ctx, opts):
    _draw_here(surf, rect, groups(ctx, opts), ctx.font_sm, ctx.now)
