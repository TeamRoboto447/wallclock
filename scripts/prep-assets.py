#!/usr/bin/env python3
"""Build the display's brand assets (py/assets/) from the branding folder, e.g. ./branding (a symlink to
the team's Identity folder). Originals stay out of git; rerun when the artwork changes.
Usage: scripts/prep-assets.py [branding-dir]   (needs pygame, e.g. .venv/bin/python)"""
import os
import sys

import pygame

SRC = sys.argv[1] if len(sys.argv) > 1 else "branding"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "py", "assets")
W, H = 1920, 1080
DARKEN = 150  # 0-255 black overlay on the background so panels and text stay readable


def load(name):
    return pygame.image.load(os.path.join(SRC, name))


def save(img, name):
    path = os.path.join(OUT, name)
    pygame.image.save(img, path)
    print(f"{name}: {img.get_width()}x{img.get_height()} {os.path.getsize(path) // 1024} KB")


def main():
    pygame.init()
    os.makedirs(OUT, exist_ok=True)

    logo = load("Team Roboto Logo - Smooth.png")
    logo = logo.subsurface(logo.get_bounding_rect()).copy()  # drop the transparent padding
    h = 256
    save(pygame.transform.smoothscale(logo, (round(logo.get_width() * h / logo.get_height()), h)), "logo.png")

    art = load("banner yes.png")  # cover 1920x1080, then darken
    scale = max(W / art.get_width(), H / art.get_height())
    art = pygame.transform.smoothscale(art, (round(art.get_width() * scale), round(art.get_height() * scale)))
    bg = pygame.Surface((W, H))
    bg.blit(art, ((W - art.get_width()) // 2, (H - art.get_height()) // 2))
    shade = pygame.Surface((W, H))
    shade.set_alpha(DARKEN)
    bg.blit(shade, (0, 0))
    save(bg, "background.jpg")

    strip = load("badge baanner.png")
    save(pygame.transform.smoothscale(strip, (W, round(strip.get_height() * W / strip.get_width()))), "strip.jpg")


if __name__ == "__main__":
    main()
