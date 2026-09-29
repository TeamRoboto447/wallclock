#!/usr/bin/env python3
"""One-way roster pull: Authentik users -> local attendance store.

Env: AUTHENTIK_TOKEN (required), AUTHENTIK_URL (default members.teamroboto.org),
TVGUI_DB (same as the kiosk).
"""
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from attendance import Store
from member import Member, Role

DEFAULT_URL = "https://members.teamroboto.org"
ENABLED_GROUP = "Enabled"
ROLE_GROUPS = (("Mentors", Role.MENTOR), ("Students", Role.STUDENT), ("Parents", Role.PARENT))
EXCLUDE = {"akadmin"}


def fetch_users(base, token):
    url = f"{base.rstrip('/')}/api/v3/core/users/?page_size=200&is_active=true"
    users = []
    while url:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
        users.extend(data["results"])
        nxt = data["pagination"].get("next")
        url = f"{base.rstrip('/')}/api/v3/core/users/?page_size=200&is_active=true&page={nxt}" if nxt else None
    return users


def convert(user):
    """Return (member, enabled, note). member is None when the user is skipped;
    note then says why (or is None for silently ignored accounts)."""
    username = user.get("username", "")
    if user.get("type") != "internal" or username in EXCLUDE:
        return None, False, None
    groups = {g["name"] for g in user.get("groups_obj", [])}
    roles = [r for g, r in ROLE_GROUPS if g in groups]
    if not roles:
        return None, False, f"{username}: no Mentors/Students/Parents group"
    role = roles[0]  # ROLE_GROUPS order is precedence: mentor > student > parent
    roboto = (user.get("attributes") or {}).get("roboto") or {}
    m = Member.new(user.get("name"), username, roboto.get("pronunciation"), role)
    if not m:
        return None, False, f"{username}: missing name"
    note = f"{username}: in {len(roles)} role groups, using {role}" if len(roles) > 1 else None
    return m, ENABLED_GROUP in groups, note


def authentik_pronunciation(user):
    roboto = (user.get("attributes") or {}).get("roboto") or {}
    return (roboto.get("pronunciation") or "").strip() or None


def badge_name(user):
    roboto = (user.get("attributes") or {}).get("roboto") or {}
    return (roboto.get("badge_name") or "").strip() or None


def display_names(people):
    """people: [(full_name, badge_name_or_None)] -> display name for each.
    A badge_name is used verbatim. Otherwise the first name, or 'First L' when
    another member has the same first name, or the full name if that still clashes."""
    firsts = [full.split()[0].lower() for full, _ in people]
    out = []
    for (full, badge), first in zip(people, firsts):
        if badge:
            out.append(badge)
            continue
        parts = full.split()
        out.append(parts[0] if firsts.count(first) == 1 or len(parts) < 2
                   else f"{parts[0]} {parts[-1][0].upper()}")
    short = list(out)
    for i, (full, badge) in enumerate(people):
        if not badge and short.count(short[i]) > 1:
            out[i] = full
    return out


def sync(users, store, dry_run=False):
    added = updated = 0
    notes = []
    known = {p.username: p for p in store.people()}
    converted = []
    for u in users:
        m, enabled, note = convert(u)
        if note:
            notes.append(note)
        if m:
            converted.append((u, m, enabled))
    names = display_names([(m.name, badge_name(u)) for u, m, _ in converted])
    badge_says = {u: t for u, _, _, t in store.tag_pronunciations() if t}
    for (u, m, enabled), display in zip(converted, names):
        m.name = display
        if not authentik_pronunciation(u) and m.username in badge_says:
            m.pronounce = badge_says[m.username]  # better than the bare full name
        old = known.get(m.username)
        if old is None:
            added += 1
        elif old != m or old.enabled != enabled:
            updated += 1
        if not dry_run:
            store.upsert(m)
            store.set_enabled(m.username, enabled)
        print(f"{'+' if old is None else '~' if (old != m or old.enabled != enabled) else '='} "
              f"{m.username:<14} {m.role:<8} {m.name:<18} {'enabled' if enabled else ''}")
    return added, updated, notes


def main():
    token = os.environ.get("AUTHENTIK_TOKEN")
    if not token:
        print("sync_authentik: AUTHENTIK_TOKEN not set", file=sys.stderr)
        return 1
    dry_run = "--dry-run" in sys.argv[1:]
    from tvgui import db_path

    users = fetch_users(os.environ.get("AUTHENTIK_URL", DEFAULT_URL), token)
    added, updated, notes = sync(users, Store(db_path()), dry_run)
    for n in notes:
        print(f"note: {n}", file=sys.stderr)
    print(f"{'would sync' if dry_run else 'synced'}: {added} new, {updated} changed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
