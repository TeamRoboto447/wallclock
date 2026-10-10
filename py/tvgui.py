#!/usr/bin/env python3
import os
import queue
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from attendance import Store, db_path
from layout import Ctx, LayoutFile, render as render_layout
from ctl import enroll_client, kiosk_cmd, listen, socket_path
from member import Member, Role
from netstatus import probe as net_probe
from nfc import EnrollSlot, split_here, start as nfc_start
from punches import fix_punch, list_punches
from plan import load_md, load_plan, plan_path, priority_path, today_path
from panels import CYCLE_SECS, FONT
from tts import backfill, play_enabled, play_greet, say, say_ready

W, H = 1920, 1080
HERE_REFRESH_SECS = 30
NET_POLL_SECS = 10


def usage():
    print("tvgui.py [kiosk]", file=sys.stderr)
    print(
        "tvgui.py enroll --name NAME --username USER --role mentor|student|parent [--pronounce TEXT]",
        file=sys.stderr,
    )
    print("tvgui.py blank|unblank|status|dump|scan|tts-backfill", file=sys.stderr)
    print("tvgui.py punches [USER] [--limit N]", file=sys.stderr)
    print(
        "tvgui.py fix-punch USER TIME [--date YYYY-MM-DD] [--note TEXT] [--dry-run]"
        "   (TIME: 3:25pm or 15:25; fixes USER's latest punch)",
        file=sys.stderr,
    )


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
    if os.environ.get("TVGUI_WINDOWED"):  # laptop: resizable window instead of fullscreen
        win = Window("Team Roboto", size=(W // 2, H // 2), resizable=True)
    elif fullscreen:
        win = Window("Team Roboto", size=(W, H), fullscreen=True)
    else:
        os.environ.setdefault("SDL_VIDEODRIVER", "kmsdrm")
        win = Window("Team Roboto", size=(W, H), fullscreen=True)
    renderer = Renderer(win)
    if os.environ.get("TVGUI_WINDOWED"):
        renderer.logical_size = (W, H)
    print(f"display {win.size} driver={pygame.display.get_driver()}", flush=True)
    return win, renderer


def newly_enabled(prev, who):
    """Members now clocked in whose enabled flag just went from False to True.
    `prev` ({username: enabled}) is updated to the current state; someone seen
    for the first time, or already enabled, is not announced."""
    newly = [m for m, _ in who if m.enabled and prev.get(m.username) is False]
    prev.clear()
    prev.update({m.username: m.enabled for m, _ in who})
    return newly


def announce_enabled(members):
    """Speak each name in turn (one thread so clips don't overlap)."""

    def run():
        for m in members:
            try:
                play_enabled(m)
            except Exception as e:
                print(f"enabled announce {m.username}: {e}", flush=True)

    threading.Thread(target=run, daemon=True).start()


def enabled_status(members):
    names = [m.name for m in members]
    return " and ".join(names) + (" is now enabled" if len(names) == 1 else " are now enabled")


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

    net = {"state": "unknown", "bars": 0}

    def _net_loop():
        while True:
            try:
                p = net_probe()
                net["state"], net["bars"] = p["state"], p["bars"]
            except Exception as e:
                print(f"net probe: {e}", flush=True)
                net["state"], net["bars"] = "down", 0
            time.sleep(NET_POLL_SECS)

    threading.Thread(target=_net_loop, daemon=True).start()
    store = Store(db_path())
    who = store.who()
    prev_enabled = {}
    newly_enabled(prev_enabled, who)
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
    layouts = LayoutFile()
    fonts = (font_big, font_mid, font_sm)

    def frame():
        return render_layout(
            layouts.get(),
            Ctx(size, fonts, now, mentors, students, parents, status, plan, today_items,
                priority_items, (net["state"], net["bars"])),
        )

    now = int(time.time())
    plan, plan_mtime = load_plan(plan_path())
    today_items, today_mtime = load_md(today_path())
    priority_items, priority_mtime = load_md(priority_path())
    surf = frame()
    tex = present(renderer, surf)
    save_ui(surf, state)
    key = (
        tuple(mentors),
        tuple(students),
        tuple(parents),
        status,
        now // CYCLE_SECS,
        plan_mtime,
        today_mtime,
        priority_mtime,
        layouts.mtime,
        net["state"],
        net["bars"],
    )
    last_refresh = now
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
                    fresh = newly_enabled(prev_enabled, store.who())
                    if fresh:
                        announce_enabled(fresh)
                        status = enabled_status(fresh)
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
        if now - last_refresh >= HERE_REFRESH_SECS:
            last_refresh = now
            try:
                rows = store.who()
                mentors, students, parents = split_here(rows)
                fresh = newly_enabled(prev_enabled, rows)
                if fresh:
                    announce_enabled(fresh)
                    status = enabled_status(fresh)
                    last_active = time.monotonic()
                    if blanked:
                        blanked = False
                        drew_black = False
                        set_dpms(True)
            except Exception as e:
                print(f"refresh: {e}", flush=True)
        plan, plan_mtime = load_plan(plan_path())
        today_items, today_mtime = load_md(today_path())
        priority_items, priority_mtime = load_md(priority_path())
        layouts.get()  # re-stat the layout file so an edit triggers a redraw
        new_key = (
            tuple(mentors),
            tuple(students),
            tuple(parents),
            status,
            blanked,
            now // CYCLE_SECS,
            plan_mtime,
            today_mtime,
            priority_mtime,
            layouts.mtime,
            net["state"],
            net["bars"],
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
                surf = frame()
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
    elif cmd == "render":  # headless sample screen, used for the golden-image test
        from fixture import save

        save(args[1] if len(args) > 1 else "screen-fixture.png")
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
    elif cmd == "punches":
        rest = args[1:]
        limit = 10
        if "--limit" in rest:
            i = rest.index("--limit")
            limit = int(rest[i + 1])
            del rest[i : i + 2]
        print("\n".join(list_punches(Store(db_path()), rest[0] if rest else None, limit)))
    elif cmd == "fix-punch":
        try:
            rest = args[1:]
            opts = {"--date": None, "--note": ""}
            for flag in opts:
                if flag in rest:
                    i = rest.index(flag)
                    opts[flag] = rest[i + 1]
                    del rest[i : i + 2]
            dry = "--dry-run" in rest
            rest = [r for r in rest if r != "--dry-run"]
            if len(rest) != 2:
                raise ValueError("need USER and TIME")
            print("\n".join(fix_punch(Store(db_path()), rest[0], rest[1], opts["--date"], opts["--note"], dry)))
        except (ValueError, IndexError) as e:
            print(f"fix-punch: {e}", file=sys.stderr)
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
