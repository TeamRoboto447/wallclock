# Authentik integration — options

Notes on how this kiosk could pull from our Authentik instance
(`https://members.teamroboto.org`) instead of relying entirely on
hand-typed `tvgui.py enroll` calls. Nothing here is implemented yet —
this is a menu to choose from, not a plan already committed to.

## What exists today (as of this writing)

- `py/member.py`: a `Member` is just `name`, `username`, `pronounce`,
  `role`, where `role` is `mentor` / `student` / `parent`. That's the
  entire roster model — no subteam, rank, or "Enabled" concept.
- `py/attendance.py`: local SQLite `people` table mirrors those same
  four fields, populated only via `Store.upsert()`, called from the
  `enroll` CLI command.
- No existing network calls to Authentik or anywhere else — enrollment
  is fully offline/manual today.

## Authentik side, already in place

- Groups exist per role (`Students`, `Mentors`, `Parents`, `Team Lead`)
  and per subteam (`Build`, `CAD`, `Programming`, `Woodworking`, `Food`,
  `Identity`, `Awards` — `Electrical` folded into `Build`).
  A new `Enabled` group tracks the shop-floor gating status from the
  Student Handbook / Achievements and Badges wiki.
- `attributes.roboto` (JSON, under a `roboto` namespace) on each user
  carries the graduated data groups can't express well: `role`,
  `enabled` + `enabled_since`, per-job `jobs` ranks, and a `history`
  array (one entry per year, each with a `teams` list so a person can
  be on multiple subteams/roles in the same year, plus an optional
  `lead` list).
- API access is via a scoped `claude-readonly` token with view + add/change
  (no delete) on applications, OAuth2 providers, groups, and users. A
  read-only sync only needs `GET /api/v3/core/users/` (with
  `groups_obj` and `attributes` expanded).

## Option A — one-way pull, replace manual enroll

A `py/sync_authentik.py` script that:

1. `GET /api/v3/core/users/` from Authentik.
2. Maps Authentik group membership → wallclock `Role`:
   `Mentors` → `mentor`, `Students` → `student`, `Parents` → `parent`.
   (Ambiguous/missing mapping → skip and report, don't guess.)
3. Upserts into the existing `attendance.Store.upsert()` — same table,
   same schema, no migration needed for this option alone.
4. Run by hand or on a timer (cron/systemd timer on the Pi) before
   badge enrollment sessions, so names/usernames/roles stay in sync
   with Authentik instead of drifting from hand-typed enroll calls.

Smallest change, no schema modifications, no kiosk UI changes. Doesn't
surface subteam/Enabled/rank data anywhere in the kiosk — those stay
Authentik/wiki-only. NFC tags still need re-enrollment (or a re-enroll
step added to the sync) if a name/username actually changes.

## Option B — Option A, plus surface Enabled/subteam on the kiosk

Same pull as Option A, but:

- Add columns to the `people` table (or a separate `roboto_status`
  table keyed by `username`) for `enabled`, `enabled_since`, and a
  serialized `jobs` blob.
- Kiosk who's-here display (`py/tvgui.py`) shows an Enabled indicator
  and/or subteam icon next to each name, mirroring the printed-badge
  concept from the Achievements and Badges wiki book but on-screen
  instead of on paper.
- Still one-way (Authentik → wallclock); the kiosk never writes back.

## Option C — gate on Enabled status

Same as B, but the kiosk actually *enforces* something on scan instead
of just displaying it — e.g. refuse (or flag/log distinctly) an in-punch
for someone not yet in the `Enabled` Authentik group, matching the
Student Handbook's "until Enabled, no unescorted shop work" rule.

Biggest behavior change and the one most likely to cause a bad night
at the shop if the sync is stale or wrong (kiosk denies someone who
actually is enabled) — needs a clear fail-open vs. fail-closed decision
and a manual-override path before this is worth building. Probably not
first.

## Option D — write back from wallclock to Authentik

Push attendance data (last punch time, cumulative shop hours) into
`attributes.roboto` or a new attribute namespace, so Authentik/the
wiki/badges can show "hours this season" without a separate report.

Needs write access from the Pi to Authentik (new scoped token, not the
`claude-readonly` one), and a decision on how conflicts/multiple kiosks
resolve. Bigger scope than A–C; worth its own discussion, not bundled
into a "just add sync" task.

## Suggested order, if/when this moves forward

A → B → (C and D as separate, later decisions, not default follow-ons).
Nathan/Darrin should weigh in on C and D specifically before either is
built — both change what the kiosk *does*, not just what it *shows*.
