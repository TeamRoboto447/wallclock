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
pip install pygame   # or your distro's python3-pygame
TVGUI_FAKE_NFC=1 TVGUI_WINDOWED=1 TVGUI_DB=./test.sqlite TVGUI_SOCK=/tmp/tvgui.sock python3 py/tvgui.py
```

Type `alice:mentor` (or `bob`, `carol:parent`) + Enter in that terminal to tap a badge;
a second tap badges out. Unknown names are created. Needs `DISPLAY`. No enroll or tag writes.

## Layout

| Path | Role |
|---|---|
| `py/tvgui.py` | Kiosk + enroll CLI |
| `py/nfc.py` | pcscd / NDEF / LED |
| `py/attendance.py` | SQLite toggle in/out, punch corrections |
| `py/punches.py` | `fix-punch` / `punches` commands |
| `py/xsession.sh` | openbox + kiosk |
| `deploy/tvgui-kiosk.service` | systemd |
