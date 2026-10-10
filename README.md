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

## Locations (pit display)

`py/layouts/pit.json` groups who's-here by location instead of role. A location is armed first
(the Stream Deck will do this; for now `python3 py/tvgui.py location pit`, or type `@pit` in the
laptop fake reader) and the next badge tap uses it, for 30 seconds. Armed + tap while clocked in
moves the person; a plain tap clocks out. Set `TVGUI_REQUIRE_LOCATION=1` on the pit display to refuse
clock-ins without one; leave it unset on the wall display. Try it:
`TVGUI_REQUIRE_LOCATION=1 TVGUI_LAYOUT=py/layouts/pit.json scripts/dev.sh`

## Layout

| Path | Role |
|---|---|
| `py/tvgui.py` | Kiosk + enroll CLI |
| `py/layout.py`, `py/layouts/*.json` | Layout = list of modules (position, size, handler); `TVGUI_LAYOUT` picks the file |
| `py/handlers/*.py` | One file per module: `draw(surf, rect, ctx, opts)`, optional `fit_height` |
| `py/panels.py` | Colors and the drawing code the handlers use |
| `py/nfc.py` | pcscd / NDEF / LED |
| `py/attendance.py` | SQLite toggle in/out, punch corrections |
| `py/punches.py` | `fix-punch` / `punches` commands |
| `py/xsession.sh` | openbox + kiosk |
| `deploy/tvgui-kiosk.service` | systemd |
