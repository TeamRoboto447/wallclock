# badge-kiosk

Full-screen Team Roboto shop kiosk: who’s-here list, ACR122U badge in/out, HDMI speech.

Python 3 + Debian Trixie packages only. No compile, no cross-build. Pygame on Xorg/Openbox (no Wayland).

## Raspberry Pi 3

Raspberry Pi OS Lite 64-bit (Trixie). SSH/kiosk user **`lvuser`** (FRC RoboRIO/SystemCore convention). HDMI via Xorg. Reader via **pcscd**. Speech: Piper **Bryce** → PipeWire HDMI sink (`alsa-hdmi`, never suspends).

NTAG write-password comes from `TVGUI_TAG_SECRET` in `/etc/tvgui/kiosk.env` (not in git). systemd loads it via `EnvironmentFile`.

```bash
./scripts/setup-pi.sh
# /etc/tvgui/kiosk.env must contain: TVGUI_TAG_SECRET=...
sudo systemctl start tvgui-kiosk
```

Enroll (kiosk running; hold the tag):

```bash
python3 /usr/local/share/tvgui/tvgui.py enroll \
  --name "Darrin Thompson" --username dthompson --role mentor
```

Fix a forgotten or late badge (moves the person's latest punch; the original
time is kept in the `punch_edits` table):

```bash
python3 /usr/local/share/tvgui/tvgui.py punches dthompson          # see recent punches
python3 /usr/local/share/tvgui/tvgui.py fix-punch dthompson 3:25pm --dry-run
python3 /usr/local/share/tvgui/tvgui.py fix-punch dthompson 3:25pm --note "forgot to badge"
# TIME: 3:25pm or 24-hour 15:25 (a bare "3:25" is refused as ambiguous)
# --date YYYY-MM-DD for a punch from another day
```

It will not move a punch to the future or out of order with the person's other
punches. The kiosk picks the change up within 30 seconds.

Attendance DB:

```bash
sqlite3 /var/lib/tvgui/attendance.sqlite
.tables
SELECT * FROM punches;
```

Tag URL: `https://members.teamroboto.org/?name=…&pronounce=…&username=…&role=mentor|student|parent`

Screen stays on. Live shot: `/run/tvgui/screen.png`. New and first-punch tags get a write password.

## Run on a laptop (no reader)

```bash
scripts/dev.sh
```

Uses `.venv` (needs `pygame`), a local DB and plan files in `.local/`, and a fake reader: type
`alice:mentor` (or `bob`, `carol:parent`) + Enter to tap a badge; a second tap badges out. Unknown
names are created. Needs `DISPLAY`. No enroll or tag writes.

If `kiosk.env` is present, its `AUTHENTIK_TOKEN` / `LEANTIME_API_KEY` turn on read-only syncs from
Authentik (every 30s) and Leantime (every 15s). The script unsets `LEANTIME_ROLL_FORWARD`, so nothing is
written back. Speech needs piper in `.local/piper/` (binary `piper`, voice `en_US-bryce-medium.onnx`);
without it you get "piper: no audio" and no sound.

## frc.nexus (pit display)

`FRC_NEXUS_API_KEY` (key), `TVGUI_NEXUS_EVENT` (event key, e.g. `demo1509`) and `TVGUI_TEAM` (our team number; the
schedule and next-match modules show only its matches) go in `kiosk.env`. `scripts/dev.sh` defaults the last two to the
demo event and team 800 unless `kiosk.env` sets them.

## Slack: Important Team Communications (pit display)

The `slack_feed` module shows the newest messages of one Slack channel, newest first. Its border shows how fresh the newest
one is: flashing blue/red under 1 minute, solid red under 5, a thin gray outline otherwise. Anything posted in that
channel appears on the screen.

Setup (one time, needs a Slack admin): create an internal app from this manifest (untested here, adjust in the Slack UI if it is rejected),
install it to the workspace, then `/invite @Pit Display` in the channel.

```json
{"display_information": {"name": "Pit Display"},
 "features": {"bot_user": {"display_name": "Pit Display", "always_online": false}},
 "oauth_config": {"scopes": {"bot": ["channels:history", "groups:history", "users:read"]}},
 "settings": {"org_deploy_enabled": false, "socket_mode_enabled": false, "token_rotation_enabled": false}}
```

`kiosk.env` gets `SLACK_BOT_TOKEN=xoxb-...` and `TVGUI_SLACK_CHANNEL=C0123456789` (the channel ID: channel details, bottom of the About tab).
Without a workspace, try it offline: `TVGUI_SLACK_FIXTURE=file.json` with `{"messages": [{"ts": "<unix>", "user": "U1", "text": "hi"}], "users": {"U1": "Dana"}}`.
Only mentions, links and `&amp;`-style escapes are cleaned up; bold, code, emoji and threads are not rendered.

## Stream Deck (pit display)

Optional. The kiosk starts a Stream Deck thread when the `streamdeck` + `PIL` packages and a deck are present (silently idle
otherwise; `TVGUI_NO_DECK=1` turns it off). It releases the deck when the kiosk closes (window, Esc, Ctrl-C or SIGTERM), so a restart
reconnects; if a crashed process (kill -9) ever leaves it stuck, unplug and replug it. Laptop: `.venv/bin/pip install streamdeck pillow` (and `hidapi`). Pi:
`setup-pi.sh` installs `python3-elgato-streamdeck python3-pil libhidapi-libusb0` and `deploy/60-streamdeck.rules` (untested on the Pi).

- **Top-row keys = locations**, taken from the layout on screen: the `locations` of its roster (`here`) module when it groups by location
  (pit layout: pit, practice field, stands, field, cafeteria). A layout without locations (the shop wall) has none, and the keys change when the
  layout is switched. Each shows the head-count; press one, then badge within 30 s.
- **Bottom-right key = ADMIN.** Press it, then a *mentor* taps their badge (no punch happens); the admin page opens for 60 s of idle time.
  Keys: clock everyone out (press twice within 5 s), next layout, screen on/off, audio test, info (status bar), require-location on/off,
  brightness, back. Admin presses are logged (`admin: ...`).
- Preview the key images without hardware: `.venv/bin/python py/deck.py sheet /tmp/decksheet.png`.

## Working on a project (shop display)

The shop layout (`py/layouts/shop.json`) has `{"deck": {"pick": "work"}}`, so the Stream Deck offers the active milestones
(from `plan.md`, with a head-count each), then optionally one of that milestone's **priority** tasks (`priority.md`), plus a GENERAL key.
Press a key, then badge within 30 s; armed + tap while clocked in switches project. **Hold** a milestone, task or
location key (about 0.6 s) to show who is on it as an overlay on the screen for 10 s instead (keys act on release); the bottom-right key turns into a red
CLOSE key while the overlay is up. Holding **GENERAL** shows an overview instead: every active milestone, then General
and anyone on no project, each with the people on it. The choice is stored on the open clock-in
(`punches.milestone`, `punches.task`; current state only, no history). `TVGUI_REQUIRE_WORK=1` refuses clock-ins without a pick
(toggle it on the admin page: REQUIRE). Without a deck, the laptop fake reader accepts `#Shop Organization > Organize Build Space`.

On screen: the plan chart shows a green counter at the end of each milestone's bar (`"people": "count"` on the plan module) and the
priority list shows the initials of the people on each task (`"chips": true` on the `md` module). Initials come from the Authentik
full name at sync (`people.initials`); until the next sync they fall back to letters of the display name.
Not built yet (designed in the plan): a "working on" board, unassigned highlighting, a roster project column.

## Locations (pit display)

`py/layouts/pit.json` groups who's-here by location instead of role. A location is armed first
(the Stream Deck will do this; for now `python3 py/tvgui.py location pit`, or type `@pit` in the
laptop fake reader) and the next badge tap uses it, for 30 seconds. Armed + tap while clocked in
moves the person; a plain tap clocks out. Set `TVGUI_REQUIRE_LOCATION=1` on the pit display to refuse
clock-ins without one; leave it unset on the wall display. Try it:
`TVGUI_REQUIRE_LOCATION=1 TVGUI_LAYOUT=py/layouts/pit.json scripts/dev.sh`, then in another terminal
`.venv/bin/python py/tvgui.py location pit` (the default socket is shared, so no env needed; commands that read the DB such as `punches` need `TVGUI_DB=.local/test.sqlite`).

## Layout

| Path | Role |
|---|---|
| `py/tvgui.py` | Kiosk + enroll CLI |
| `py/layout.py`, `py/layouts/*.json` | Layout = list of modules (position, size, handler); `TVGUI_LAYOUT` picks the file |
| `py/handlers/*.py` | One file per module: `draw(surf, rect, ctx, opts)`, optional `fit_height` |
| `py/panels.py` | Colors and the drawing code the handlers use |
| `py/assets/`, `scripts/prep-assets.py` | Brand art (background, logo, gear strip), rebuilt from `./branding` (a symlink to the Identity folder, not in git) |
| `py/nfc.py` | pcscd / NDEF / LED |
| `py/attendance.py` | SQLite toggle in/out, punch corrections |
| `py/punches.py` | `fix-punch` / `punches` commands |
| `py/xsession.sh` | openbox + kiosk |
| `deploy/tvgui-kiosk.service` | systemd |
