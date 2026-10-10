#!/bin/sh
# Laptop run: fake badge reader, windowed, local data, read-only syncs from Leantime/Authentik.
cd "$(dirname "$0")/.." || exit 1
mkdir -p .local
[ -f kiosk.env ] && { set -a; . ./kiosk.env; set +a; }
export TVGUI_NEXUS_EVENT=${TVGUI_NEXUS_EVENT:-demo1509} TVGUI_TEAM=${TVGUI_TEAM:-800}  # demo event + a team in it; kiosk.env overrides
unset LEANTIME_ROLL_FORWARD  # the only path that writes to Leantime
export TVGUI_FAKE_NFC=1 TVGUI_WINDOWED=1 SDL_AUDIODRIVER=dummy TVGUI_AUDIO_TARGET=
export TVGUI_DB=$PWD/.local/test.sqlite TVGUI_BOARD=$PWD/.local
export TVGUI_PIPER=$PWD/.local/piper/piper
export TVGUI_PIPER_MODEL=$PWD/.local/piper/en_US-bryce-medium.onnx
PY=.venv/bin/python
trap 'kill $(jobs -p) 2>/dev/null' EXIT INT TERM

if [ -n "$LEANTIME_API_KEY" ]; then $PY -u py/sync_leantime.py & else echo "dev: no LEANTIME_API_KEY, skipping Leantime sync"; fi
if [ -n "$AUTHENTIK_TOKEN" ]; then
  (while :; do $PY py/sync_authentik.py; sleep 30; done) &
else echo "dev: no AUTHENTIK_TOKEN, skipping Authentik sync"; fi

$PY py/tvgui.py
