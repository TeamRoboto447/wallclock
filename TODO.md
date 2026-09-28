# TODO

- [x] Python kiosk on Pi 3 (Xorg + openbox + pygame).
- [x] ACR122U pcscd, NDEF member URLs, mentor/student columns.
- [x] Enroll over unix socket; SQLite punches.

## HDMI boot race

Pi 3 KMS: if the TV is off, on another input, or EDID is late, boot logs `Cannot find any crtc or sizes`, Xorg sees `HDMI-1 disconnected` and creates a dummy 1024×768 framebuffer. Later hotplug shows connected + EDID but **enabled=disabled**; pygame stays 1024×768; `alsa-hdmi` fails (errno 524). Workaround: `xrandr --output HDMI-1 --mode 1920x1080` then restart `tvgui-kiosk`. Fix: wait for HDMI `connected` before X, and/or udev `xrandr --auto` + kiosk restart on hotplug. Optional: `video=HDMI-A-1:1920x1080@60D` on cmdline.

## Keep the screen on (no full blank)

- [x] Idle DPMS blank disabled; HDMI stays up.

## Later

HDMI-CEC input switch (TV did not ACK). Calendar on HDMI.

Pi touchscreen + battery (portable / competition). Or a who’s-here web UI on a second TV when two displays are available.
