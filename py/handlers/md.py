"""opts: source = "priority" | "today"; title; paged (cycle one milestone per page)."""
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
    _draw_md(surf, rect, title, items, ctx.font_mid, ctx.font_sm)
