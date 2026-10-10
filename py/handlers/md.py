"""opts: source = "priority" | "today"; title; paged (cycle one milestone per page);
chips = true puts the initials of people clocked in on a task (or its milestone) at the end of its row."""
from panels import CYCLE_SECS, _draw_md, today_pages


def draw(surf, rect, ctx, opts):
    items = getattr(ctx, opts["source"])
    title = opts.get("title", opts["source"])
    if opts.get("paged"):
        pages = today_pages(items, max(1, (rect.height - 128) // 32))
        if len(pages) > 1:
            i = (ctx.now // CYCLE_SECS) % len(pages)
            title, items = f"{title} ({i + 1}/{len(pages)})", pages[i]
        else:
            items = pages[0] if pages else []
    chips = None
    if opts.get("chips"):
        rows = [r for r in ctx.mentors + ctx.students + ctx.parents if len(r) > 7]

        def chips(kind, text, heading):
            if kind == "h":  # on the milestone itself, no particular task
                return [(r[7], r[2]) for r in rows if r[5] == text and not r[6]]
            return [(r[7], r[2]) for r in rows if r[5] == heading and r[6] == text]

    _draw_md(surf, rect, title, items, ctx.font_mid, ctx.font_sm, chips)
