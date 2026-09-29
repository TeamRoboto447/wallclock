# Leantime API — notes for a project display

Notes on how this kiosk could pull project/task data from our Leantime
instance (`https://tasks.teamroboto.org`). Nothing here is implemented
yet, and **no live API call has been made** — everything below was read
from the code in the running container (2026-09-28), not exercised.

## Instance facts

- App: custom app `leantime` on the TrueNAS box (`172.16.0.172`), host
  port `30300`, image `leantime/leantime:latest`, MySQL 8.4 alongside.
- Public URL goes through our NGINX with Let's Encrypt.
- Login is Authentik OIDC only (`LEAN_DISABLE_LOGIN_FORM=true`). The API
  does not use that login; it uses API keys (below).
- New projects are forced to "accessible to all users" by a DB trigger
  (`zp_projects_default_all`), so a display user will see most projects.

## What the API is

Leantime exposes a **JSON-RPC 2.0** API through the `Api` domain
(`app/Domain/Api/Controllers/Jsonrpc.php`).

- Endpoint: `POST https://tasks.teamroboto.org/api/jsonrpc`
- Auth: `x-api-key: <key>` header (a Bearer token is also accepted as a
  fallback). Keys are created in the Leantime UI (there are `NewApiKey`
  / `DelAPIKey` / `ApiKey` controllers). The key acts with the rights of
  the user/role it belongs to — not yet confirmed exactly which.
- Authorization is checked per method; a denied call comes back as
  JSON-RPC error `-32001`.
- Request shape (from memory, **verify before relying on it**):

```json
{"jsonrpc":"2.0","method":"leantime.rpc.<domain>.<service>.<method>","params":{},"id":1}
```

- Domains with services that mention the RPC layer: Projects, Clients,
  Calendar, Dashboard, Reactions. Tickets (tasks), Sprints, Timesheets,
  Users, Wiki, Comments and Files domains exist but their RPC methods
  have not been checked.
- Also present: canvas (`Canvas`, `Goalcanvas`) API controllers, `I18n`.

## Things that do NOT exist in this version

- No general-purpose webhooks / push events for third-party receivers —
  a display has to **poll**. (Leantime does send its *own* notifications
  out to Slack/Discord/Mattermost/Zulip/Telegram: per-project setting
  "Slack" takes an incoming-webhook URL, code in
  `Domain/Notifications/Services/Messengers.php`. Fixed format, and an
  SSRF guard (`OutboundUrlGuard`) blocks disallowed destinations, so it
  is not a way to feed a LAN service.)
- No OpenAPI/Swagger spec shipped in the container.

## Other routes (unverified)

- Calendar has a personal iCal feed (`.ics`); URL and auth not checked.
- `CsvImport` and `Reports` domains exist; not looked at.
- Direct read-only DB access (`zp_tickets`, `zp_projects`) would work but
  bypasses Leantime permissions and can break on upgrade. Last resort.

## Suggested shape for the display

1. Make a dedicated Leantime user with the least role needed (read-only)
   and add it only to the projects to be shown, then create an API key
   for it.
2. Have a small server-side poller call the JSON-RPC API on a schedule
   and cache the result; the display reads the cache. Keep the key out of
   the browser and out of git (same pattern as `TVGUI_TAG_SECRET` in
   `/etc/tvgui/kiosk.env`).
3. Keep the poller on the LAN or behind Authentik — Leantime itself is
   public via NGINX.

## How to find the real method names

Inside the container, list the service classes and read the RPC
registration / docblocks:

```bash
sudo docker exec ix-leantime-leantime-1 sh -c \
  "grep -rn 'jsonrpc\|@api' /var/www/html/app/Domain/*/Services | head -50"
```

Then make one read-only call with a test key and record the working
request here.
