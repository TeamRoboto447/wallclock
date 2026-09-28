#!/usr/bin/env python3
import datetime
import os
import queue
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from attendance import Store
from ctl import enroll_client, kiosk_cmd, listen, socket_path
from member import Member, Role
from nfc import EnrollSlot, split_here, start as nfc_start
from plan import current_week, load_md, load_plan, plan_path, today_path
from tts import backfill, play_greet, say, say_ready

W, H = 1920, 1080
FONT = "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"
BG = (0x22, 0x22, 0x22)
RED = (0xB5, 0x04, 0x04)
CORAL = (0xD8, 0x61, 0x3C)
BEIGE = (0xCF, 0xCA, 0xBE)
INK = (0xF9, 0xF9, 0xF9)
PANEL = (0x1A, 0x1A, 0x1A)
BORDER = (0x3A, 0x3A, 0x3A)
STATUS_BG = (0x3A, 0x0A, 0x0A)


def db_path():
    env = os.environ.get("TVGUI_DB")
    if env:
        return env
    p = "/var/lib/tvgui/attendance.sqlite"
    if os.path.isdir(os.path.dirname(p)):
        return p
    return "attendance.sqlite"


def usage():
    print("tvgui.py [kiosk]", file=sys.stderr)
    print(
        "tvgui.py enroll --name NAME --username USER --role mentor|student|parent [--pronounce TEXT]",
        file=sys.stderr,
    )
    print("tvgui.py blank|unblank|status|dump|scan|tts-backfill", file=sys.stderr)


def blank_secs():
    try:
        return max(0, int(os.environ.get("TVGUI_BLANK_SECS", "0")))
    except ValueError:
        return 0


def run_dir():
    if os.path.isdir("/run/tvgui"):
        return "/run/tvgui"
    return "/tmp"


def shot_path():
    return os.path.join(run_dir(), "screen.png")


def ui_path():
    return os.path.join(run_dir(), "ui.txt")


def set_dpms(on):
    if not os.environ.get("DISPLAY"):
        return
    try:
        subprocess.run(
            ["xset", "dpms", "force", "on" if on else "off"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def save_ui(surf, state):
    try:
        import pygame

        pygame.image.save(surf, shot_path())
    except Exception as e:
        print(f"screenshot: {e}", flush=True)
    try:
        with state["lock"]:
            text = (
                f"blanked={int(state['blanked'])}\n"
                f"status={state['status']}\n"
                f"mentors={len(state['mentors'])}\n"
                f"students={len(state['students'])}\n"
                f"parents={len(state.get('parents', []))}\n"
            )
        with open(ui_path(), "w") as f:
            f.write(text)
    except Exception as e:
        print(f"ui.txt: {e}", flush=True)


def parse_enroll(args):
    name = username = pronounce = role = None
    i = 0
    while i < len(args):
        if args[i] == "--name" and i + 1 < len(args):
            name = args[i + 1]
            i += 2
        elif args[i] == "--username" and i + 1 < len(args):
            username = args[i + 1]
            i += 2
        elif args[i] == "--pronounce" and i + 1 < len(args):
            pronounce = args[i + 1]
            i += 2
        elif args[i] == "--role" and i + 1 < len(args):
            role = Role.parse(args[i + 1])
            if role is None:
                raise ValueError("role must be mentor, student, or parent")
            i += 2
        else:
            raise ValueError(f"unknown arg {args[i]}")
    m = Member.new(name, username, pronounce, role)
    if not m or role is None:
        raise ValueError("need --name --username --role")
    return m


def open_display():
    import pygame
    from pygame._sdl2.video import Window, Renderer

    pygame.init()
    fullscreen = bool(os.environ.get("DISPLAY"))
    if fullscreen:
        win = Window("Team Roboto", size=(W, H), fullscreen=True)
    else:
        os.environ.setdefault("SDL_VIDEODRIVER", "kmsdrm")
        win = Window("Team Roboto", size=(W, H), fullscreen=True)
    renderer = Renderer(win)
    print(f"display {win.size} driver={pygame.display.get_driver()}", flush=True)
    return win, renderer


def local_midnight():
    n = datetime.datetime.now().astimezone()
    return int(n.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def fmt_in(ts, now):
    start = max(int(ts), local_midnight())
    mins = max(0, now - start) // 60
    h, m = divmod(mins, 60)
    return f"{h}:{m:02d}"


DONE = (0x7A, 0x7A, 0x72)
MUTED = (0x6A, 0x6A, 0x62)


def _clip_blit(surf, img, pos, rect):
    x, y = pos
    if y + img.get_height() > rect.bottom - 8 or y < rect.top:
        return False
    surf.blit(img, (x, y))
    return True


def _draw_md(surf, rect, title, items, font_mid, font_sm):
    import pygame

    pygame.draw.rect(surf, PANEL, rect)
    surf.blit(font_sm.render(title, True, BEIGE), (rect.x + 20, rect.y + 16))
    y = rect.y + 52
    x = rect.x + 20
    max_w = rect.width - 40
    if not items:
        _clip_blit(surf, font_sm.render("—", True, MUTED), (x, y), rect)
        return
    for kind, text in items:
        if y >= rect.bottom - 28:
            break
        if kind == "h":
            y += 8
            img = font_mid.render(text, True, CORAL)
            if not _clip_blit(surf, img, (x, y), rect):
                break
            y += 40
            continue
        if kind == "done":
            mark, color = "[x]", DONE
        elif kind == "todo":
            mark, color = "[ ]", INK
        else:
            mark, color = "", INK
        line = f"{mark} {text}".strip()
        img = font_sm.render(line, True, color)
        if img.get_width() > max_w:
            img = font_sm.render(line[:80] + "…", True, color)
        if not _clip_blit(surf, img, (x, y), rect):
            break
        y += 32


def _draw_here(surf, rect, mentors, students, parents, font_sm, now):
    import pygame

    pygame.draw.rect(surf, PANEL, rect)
    surf.blit(font_sm.render("who's here", True, BEIGE), (rect.x + 20, rect.y + 16))
    y = rect.y + 52
    x = rect.x + 20
    for label, names in (
        (f"students ({len(students)})", students),
        (f"parents ({len(parents)})", parents),
        (f"mentors ({len(mentors)})", mentors),
    ):
        if y >= rect.bottom - 28:
            break
        img = font_sm.render(label, True, CORAL)
        if not _clip_blit(surf, img, (x, y), rect):
            break
        y += 32
        for name, ts in names:
            ns = font_sm.render(name, True, INK)
            dur = font_sm.render(fmt_in(ts, now), True, BEIGE)
            if not _clip_blit(surf, ns, (x, y), rect):
                return
            surf.blit(dur, (x + 8 + ns.get_width(), y))
            y += 30
        y += 10


def _draw_gantt(surf, rect, plan, font_mid, font_sm, now):
    import pygame

    pygame.draw.rect(surf, PANEL, rect)
    title = plan.get("title") or "plan"
    surf.blit(font_sm.render(title, True, BEIGE), (rect.x + 16, rect.y + 12))
    weeks = max(1, int(plan.get("weeks") or 8))
    bars = plan.get("bars") or []
    label_w = 160
    head_h = 48
    row_h = 32
    grid = pygame.Rect(
        rect.x + 16 + label_w,
        rect.y + head_h,
        rect.width - 32 - label_w,
        rect.height - head_h - 12,
    )
    if grid.width < 40 or grid.height < 24:
        return
    col_w = grid.width / weeks
    for i in range(weeks):
        x = int(grid.x + i * col_w)
        pygame.draw.line(surf, BORDER, (x, grid.y), (x, grid.bottom - 1), 1)
        tick = font_sm.render(f"W{i + 1}", True, MUTED)
        surf.blit(tick, (x + 4, rect.y + 20))
    pygame.draw.line(surf, BORDER, (grid.right - 1, grid.y), (grid.right - 1, grid.bottom - 1), 1)
    cur = current_week(plan.get("start"), weeks, now)
    if cur:
        cx = int(grid.x + (cur - 0.5) * col_w)
        pygame.draw.line(surf, RED, (cx, grid.y), (cx, grid.bottom - 1), 2)
    y = grid.y
    for done, name, lo, hi in bars:
        if y + row_h > grid.bottom:
            break
        label = font_sm.render(name[:22], True, INK)
        surf.blit(label, (rect.x + 16, y + 4))
        if lo and hi:
            x0 = int(grid.x + (lo - 1) * col_w) + 3
            x1 = int(grid.x + hi * col_w) - 3
            bar = pygame.Rect(x0, y + 6, max(4, x1 - x0), row_h - 12)
            if done:
                pygame.draw.rect(surf, CORAL, bar)
            else:
                pygame.draw.rect(surf, PANEL, bar)
                pygame.draw.rect(surf, BEIGE, bar, 2)
        y += row_h


def compose(
    size,
    font_big,
    font_mid,
    font_sm,
    mentors,
    students,
    parents,
    status,
    now,
    plan,
    today_items,
):
    import pygame

    w, h = size
    surf = pygame.Surface((w, h))
    surf.fill(BG)
    pad = 24
    surf.blit(font_mid.render("Team Roboto", True, RED), (pad, pad))

    status_h = 140
    col_top = pad + 56
    col_h = h - col_top - status_h - pad * 2
    gap = pad
    left_w = (w - pad * 2 - gap) // 3
    right_x = pad + left_w + gap
    right_w = w - pad - right_x
    plan_h = (col_h - gap) // 2
    left = pygame.Rect(pad, col_top, left_w, col_h)
    plan_r = pygame.Rect(right_x, col_top, right_w, plan_h)
    today_r = pygame.Rect(right_x, col_top + plan_h + gap, right_w, col_h - plan_h - gap)
    _draw_here(surf, left, mentors, students, parents, font_sm, now)
    if plan.get("bars"):
        _draw_gantt(surf, plan_r, plan, font_mid, font_sm, now)
    else:
        _draw_md(surf, plan_r, "plan", plan.get("items") or [], font_mid, font_sm)
    _draw_md(surf, today_r, "today", today_items, font_mid, font_sm)

    st = pygame.Rect(pad, h - pad - status_h, w - pad * 2, status_h)
    pygame.draw.rect(surf, STATUS_BG, st)
    surf.blit(font_sm.render("Badge reader", True, CORAL), (st.x + 18, st.y + 16))
    surf.blit(font_mid.render(status, True, INK), (st.x + 18, st.y + 48))
    return surf


def present(renderer, surf, tex=None):
    from pygame._sdl2.video import Texture

    w, h = surf.get_size()
    if tex is None:
        tex = Texture.from_surface(renderer, surf)
    renderer.draw_color = (0, 0, 0, 255)
    renderer.clear()
    tex.draw(dstrect=(0, 0, w, h))
    renderer.present()
    return tex


def kiosk():
    import pygame

    store = Store(db_path())
    who = store.who()
    mentors, students, parents = split_here(who)
    events = queue.Queue()
    slot = EnrollSlot()
    idle_limit = blank_secs()
    state = {
        "lock": threading.Lock(),
        "status": "Waiting for reader",
        "blanked": False,
        "mentors": mentors,
        "students": students,
        "parents": parents,
    }

    win, renderer = open_display()
    pygame.mouse.set_visible(False)
    font_big = pygame.font.Font(FONT, 40)
    font_mid = pygame.font.Font(FONT, 32)
    font_sm = pygame.font.Font(FONT, 22)
    status = "Waiting for reader"
    size = (W, H)
    now = int(time.time())
    plan, plan_mtime = load_plan(plan_path())
    today_items, today_mtime = load_md(today_path())
    surf = compose(
        size,
        font_big,
        font_mid,
        font_sm,
        mentors,
        students,
        parents,
        status,
        now,
        plan,
        today_items,
    )
    tex = present(renderer, surf)
    save_ui(surf, state)
    key = (
        tuple(mentors),
        tuple(students),
        tuple(parents),
        status,
        now // 60,
        plan_mtime,
        today_mtime,
    )
    last_active = time.monotonic()
    blanked = False
    drew_black = False

    def _ready():
        time.sleep(1.5)
        say_ready()

    threading.Thread(target=_ready, daemon=True).start()
    threading.Thread(target=backfill, args=(store,), daemon=True).start()
    threading.Thread(
        target=listen, args=(socket_path(), slot, events, state), daemon=True
    ).start()
    nfc_start(store, slot, events)
    clock = pygame.time.Clock()
    running = True
    while running:
        pygame.event.pump()
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT or (
                ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE
            ):
                running = False
        woke = False
        try:
            while True:
                item = events.get_nowait()
                kind = item[0]
                if kind == "status":
                    status = item[1]
                    if status not in (
                        "Hold your badge over the reader",
                        "Waiting for reader",
                    ):
                        woke = True
                elif kind == "here":
                    mentors, students, parents = item[1], item[2], item[3]
                    woke = True
                elif kind == "speak":
                    threading.Thread(target=say, args=(item[1],), daemon=True).start()
                    woke = True
                elif kind == "greet":
                    threading.Thread(
                        target=play_greet, args=(item[1], item[2]), daemon=True
                    ).start()
                    woke = True
                elif kind == "blank":
                    blanked = True
                elif kind == "unblank":
                    woke = True
        except queue.Empty:
            pass
        if woke:
            last_active = time.monotonic()
            if blanked:
                blanked = False
                drew_black = False
                set_dpms(True)
        if idle_limit and not blanked and time.monotonic() - last_active >= idle_limit:
            blanked = True
        with state["lock"]:
            state["status"] = status
            state["blanked"] = blanked
            state["mentors"] = mentors
            state["students"] = students
            state["parents"] = parents
        now = int(time.time())
        plan, plan_mtime = load_plan(plan_path())
        today_items, today_mtime = load_md(today_path())
        new_key = (
            tuple(mentors),
            tuple(students),
            tuple(parents),
            status,
            blanked,
            now // 60,
            plan_mtime,
            today_mtime,
        )
        try:
            if blanked:
                if not drew_black:
                    black = pygame.Surface(size)
                    black.fill((0, 0, 0))
                    tex = present(renderer, black)
                    save_ui(black, state)
                    set_dpms(False)
                    drew_black = True
                    key = new_key
            elif new_key != key:
                key = new_key
                surf = compose(
                    size,
                    font_big,
                    font_mid,
                    font_sm,
                    mentors,
                    students,
                    parents,
                    status,
                    now,
                    plan,
                    today_items,
                )
                tex = present(renderer, surf)
                save_ui(surf, state)
            else:
                tex = present(renderer, surf, tex)
        except Exception as e:
            print(f"draw: {e}", flush=True)
        clock.tick(10)
    pygame.quit()


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "kiosk"
    if cmd in ("kiosk",):
        kiosk()
    elif cmd == "scan":
        try:
            while True:
                sys.stdout.write(kiosk_cmd("SCAN", timeout=300))
                sys.stdout.write("---\n")
                sys.stdout.flush()
        except KeyboardInterrupt:
            pass
    elif cmd == "tts-backfill":
        try:
            backfill(Store(db_path()), force=False)
        except Exception as e:
            print(f"tts-backfill: {e}", file=sys.stderr)
            sys.exit(1)
    elif cmd in ("blank", "unblank", "status", "dump"):
        try:
            sys.stdout.write(kiosk_cmd(cmd.upper()))
        except Exception as e:
            print(f"{cmd}: {e}", file=sys.stderr)
            sys.exit(1)
    elif cmd == "enroll":
        try:
            m = parse_enroll(args[1:])
            enroll_client(m)
            print(f"enrolled {m.username}")
        except Exception as e:
            print(f"enroll: {e}", file=sys.stderr)
            usage()
            sys.exit(1)
    elif cmd in ("-h", "--help"):
        usage()
    else:
        print(f"unknown command {cmd}", file=sys.stderr)
        usage()
        sys.exit(2)


if __name__ == "__main__":
    main()
