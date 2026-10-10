"""Draws a picture from py/assets (or an absolute path), scaled once and cached.
opts: file; fit = "contain" (default, keeps the whole picture) | "cover" (fills the box, cropping the overflow)."""
import os

import pygame

ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
_cache = {}


def fit_size(w, h, box_w, box_h, fit="contain"):
    """Size to scale a w x h picture to for a box_w x box_h box."""
    scale = (max if fit == "cover" else min)(box_w / w, box_h / h)
    return max(1, round(w * scale)), max(1, round(h * scale))


def scaled(path, box, fit):
    key = (path, box, fit)
    if key not in _cache:
        img = pygame.image.load(path)
        img = pygame.transform.smoothscale(img, fit_size(*img.get_size(), *box, fit))
        if pygame.display.get_surface():
            img = img.convert_alpha()
        _cache[key] = img
    return _cache[key]


def draw(surf, rect, ctx, opts):
    img = scaled(os.path.join(ASSETS, opts["file"]), rect.size, opts.get("fit", "contain"))
    # centre it; for "cover" the overflow is cropped by the clip rect
    clip = surf.get_clip()
    surf.set_clip(rect)
    surf.blit(img, (rect.x + (rect.w - img.get_width()) // 2, rect.y + (rect.h - img.get_height()) // 2))
    surf.set_clip(clip)
