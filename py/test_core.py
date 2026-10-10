import datetime
import hashlib
import os
import re
import tempfile
import unittest

from attendance import IN, OUT, Store, year_start
from member import Member, Role, initials_of
from nfc import AUTH0_USER, cfg_pages_for_storage, tag_pack, tag_pwd
from plan import parse_md, parse_plan
from tts import (
    PAUSE_SECS,
    _silence,
    _stitch,
    enrolled_phrase,
    greet_phrase,
    lead_prompt,
    wav_path,
)
import ndef
from sync_authentik import convert, display_names
from sync_leantime import (
    blocking_ids, chain_order, is_priority, overdue, plan_moves, render_plan,
    render_today, roll_values, week_deadline,
)
from panels import fmt_total, plan_height, today_offset_weeks, today_pages
from tvgui import enabled_status, newly_enabled
from nfc import ADMIN, PENDING, WORK, _run_fake, punch_status, split_here, tap
from deck import Deck, layout_locations, layout_pick, render as render_key
from layout import next_layout
from handlers.here import fit_width, groups as here_groups
from layout import LayoutFile, module_key, resolve
from refresh import Refresher
import nexus
from panels import age_text, border_state, rows_height, wrap_text
import slack
from handlers.image import fit_size
from punches import fix_punch, list_punches, parse_time_of_day
from netstatus import bars, classify
from netwatch import action_for, decode_throttled


class MemberTests(unittest.TestCase):
    def test_roundtrip(self):
        m = Member.new("Darrin Thompson", "dthompson", "Darrin Thompson", Role.MENTOR)
        url = m.to_url()
        self.assertIn("role=mentor", url)
        self.assertEqual(Member.from_url(url), m)
        self.assertEqual(Member.from_uri_payload(m.to_uri_payload()), m)

    def test_default_role(self):
        m = Member.from_url(
            "https://members.teamroboto.org/?name=Jane+Doe&pronounce=Jane+Doe&username=jdoe"
        )
        self.assertEqual(m.role, Role.STUDENT)

    def test_parent_role(self):
        m = Member.new("Pat Parent", "pparent", None, Role.PARENT)
        self.assertEqual(m.role, Role.PARENT)
        self.assertIn("role=parent", m.to_url())
        self.assertEqual(Member.from_url(m.to_url()), m)


class NdefTests(unittest.TestCase):
    def test_pages(self):
        m = Member.new("Darrin Thompson", "dthompson", None, Role.MENTOR)
        pages = ndef.tag_pages(m)
        self.assertEqual(pages[0], ndef.CC)
        user = b"".join(pages[1:])
        self.assertEqual(ndef.parse_member_from_user_memory(user), m)


class TagPwdTests(unittest.TestCase):
    def setUp(self):
        os.environ["TVGUI_TAG_SECRET"] = "test-secret"

    def test_lengths(self):
        self.assertEqual(len(tag_pwd()), 4)
        self.assertEqual(len(tag_pack()), 2)
        self.assertEqual(AUTH0_USER, 0x04)

    def test_stable(self):
        key = hashlib.sha256(b"test-secret").digest()
        self.assertEqual(tag_pwd(), key[:4])
        self.assertEqual(tag_pack(), key[4:6])
        self.assertNotEqual(tag_pwd(), bytes([0xFF, 0xFF, 0xFF, 0xFF]))

    def test_missing_secret(self):
        del os.environ["TVGUI_TAG_SECRET"]
        with self.assertRaises(RuntimeError):
            tag_pwd()

    def test_cfg_pages(self):
        self.assertEqual(cfg_pages_for_storage(0x0F), (0x29, 0x2A, 0x2B, 0x2C))
        self.assertEqual(cfg_pages_for_storage(0x11), (0x83, 0x84, 0x85, 0x86))
        self.assertEqual(cfg_pages_for_storage(0x13), (0xE3, 0xE4, 0xE5, 0xE6))
        self.assertEqual(cfg_pages_for_storage(0x00), cfg_pages_for_storage(0x11))


class StoreTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        self.store = Store(self.path)

    def tearDown(self):
        os.unlink(self.path)

    def test_toggle(self):
        m = Member.new("Jane Doe", "jdoe", None, Role.STUDENT)
        p = self.store.toggle(m, 1000, 2)
        self.assertEqual(p.direction, IN)
        self.assertEqual(self.store.who(), [(m, 1000)])
        p = self.store.toggle(m, 1003, 2)
        self.assertEqual(p.direction, OUT)
        self.assertEqual(self.store.who(), [])

    def test_debounce(self):
        m = Member.new("Jane Doe", "jdoe", None, Role.STUDENT)
        self.assertIsNotNone(self.store.toggle(m, 1000, 2))
        self.assertIsNone(self.store.toggle(m, 1001, 2))

    def test_people(self):
        m = Member.new("Jane Doe", "jdoe", "Jane", Role.STUDENT)
        self.store.upsert(m)
        self.assertEqual(self.store.people(), [m])


class TtsTests(unittest.TestCase):
    def test_greet_phrase(self):
        m = Member.new("Jane Doe", "jdoe", "Jane", Role.STUDENT)
        self.assertEqual(greet_phrase(m, IN), "Welcome student. Jane")
        self.assertEqual(greet_phrase(m, OUT), "good bye student. Jane")
        self.assertEqual(enrolled_phrase(m), "Jane enrolled")
        self.assertEqual(lead_prompt(m, IN), "_welcome-student")
        self.assertEqual(lead_prompt(m, OUT), "_goodbye-student")
        self.assertTrue(wav_path("jdoe", IN).endswith("jdoe-in.wav"))

    def test_stitch_pause(self):
        import wave

        d = tempfile.mkdtemp()
        a = os.path.join(d, "a.wav")
        b = os.path.join(d, "b.wav")
        out = os.path.join(d, "out.wav")
        pcm = b"\x00\x01" * 100
        for path in (a, b):
            with wave.open(path, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(22050)
                w.writeframes(pcm)
        _stitch(out, (a, b))
        with wave.open(out, "rb") as w:
            frames = w.readframes(w.getnframes())
        self.assertEqual(len(frames), 400 + len(_silence(PAUSE_SECS)))


class PlanTests(unittest.TestCase):
    def test_parse_md(self):
        items = parse_md(
            "# 2018 Robot Restore\n"
            "- [x] Wiring fix\n"
            "- [ ] Battery Holder\n"
        )
        self.assertEqual(
            items,
            [
                ("h", "2018 Robot Restore"),
                ("done", "Wiring fix"),
                ("todo", "Battery Holder"),
            ],
        )

    def test_parse_week_of_and_event(self):
        g = parse_plan(
            "# Week of 9/21 - Remove Climber/Start Vision Change\n"
            "\n"
            "# Week of 9/28 - Tune Shooting\n"
            "\n"
            "10/23 - 10/24 --- BoilerBot\n",
            today=datetime.date(2026, 9, 22),
        )
        self.assertEqual(g["start"].isoformat(), "2026-09-21")
        names = [b[1] for b in g["bars"]]
        self.assertEqual(
            names,
            [
                "Remove Climber/Start Vision Change",
                "Tune Shooting",
                "BoilerBot",
            ],
        )
        self.assertEqual(g["bars"][0][2:4], (1, 1))
        self.assertEqual(g["bars"][1][2:4], (2, 2))
        self.assertEqual(g["bars"][2][2:4], (5, 5))

    def test_parse_plan_gantt(self):
        g = parse_plan(
            "# 2026\n"
            "start: 2026-01-10\n"
            "weeks: 8\n"
            "- [x] Gear box 5:1 | 1-2\n"
            "- [ ] Battery Holder | 2-4\n"
            "- [ ] Quest Mount | 3\n"
        )
        self.assertEqual(g["title"], "2026")
        self.assertEqual(g["weeks"], 8)
        self.assertEqual(g["start"].isoformat(), "2026-01-10")
        self.assertEqual(
            g["bars"],
            [
                ("done", "Gear box 5:1", 1, 2, None, None, None, None),
                ("new", "Battery Holder", 2, 4, None, None, None, None),
                ("new", "Quest Mount", 3, 3, None, None, None, None),
            ],
        )


class SyncTests(unittest.TestCase):
    def user(self, groups, **kw):
        u = {"username": "jdoe", "name": "Jane Doe", "type": "internal",
             "groups_obj": [{"name": g} for g in groups], "attributes": {}}
        u.update(kw)
        return u

    def test_enabled_student(self):
        m, enabled, note = convert(self.user(["Students", "Enabled", "Build"]))
        self.assertEqual((m.role, enabled, note), (Role.STUDENT, True, None))

    def test_mentor_beats_parent(self):
        m, _, note = convert(self.user(["Parents", "Mentors"]))
        self.assertEqual(m.role, Role.MENTOR)
        self.assertIsNotNone(note)

    def test_skips(self):
        self.assertIsNone(convert(self.user(["Enabled"]))[0])
        self.assertIsNone(convert(self.user(["Mentors"], type="service_account"))[0])
        self.assertIsNone(convert(self.user(["Mentors"], username="akadmin"))[0])

    def test_pronunciation(self):
        u = self.user(["Students"], attributes={"roboto": {"pronunciation": "JAYN DOH"}})
        self.assertEqual(convert(u)[0].pronounce, "JAYN DOH")

    def test_display_names(self):
        people = [
            ("Ryan Mejeur", None), ("Ryan Sallee", None), ("Darrin Thompson", None),
            ("Nathan Rockhill", "Nathan (KE9BCL)"), ("Jon Mejeur", None),
        ]
        self.assertEqual(
            display_names(people),
            ["Ryan M", "Ryan S", "Darrin", "Nathan (KE9BCL)", "Jon"],
        )

    def test_display_names_badge_counts_and_full_fallback(self):
        # a badge name doesn't hide the real first name from the clash check
        self.assertEqual(
            display_names([("Nathan Rockhill", "Nate"), ("Nathan Lee", None)]),
            ["Nate", "Nathan L"],
        )
        # same first name and last initial: fall back to full names
        self.assertEqual(
            display_names([("Sam Lee", None), ("Sam Long", None)]),
            ["Sam Lee", "Sam Long"],
        )

    def test_scan_keeps_stored_name(self):
        store = Store(":memory:")
        store.upsert(Member.new("Jane", "jdoe", "Jane Doe"))
        tag = Member.new("Jane Doe", "jdoe", "Jane Doe")
        self.assertEqual(store.toggle(tag, 1000).member.name, "Jane")
        self.assertEqual(store.who()[0][0].name, "Jane")

    def test_scan_stores_badge_pronunciation(self):
        store = Store(":memory:")
        store.upsert(Member.new("Jane", "jdoe", "Jane Doe"))  # synced value
        tag = Member.new("Jane Doe", "jdoe", "JAYN DOH")
        store.toggle(tag, 1000)
        self.assertEqual(store.tag_pronunciations(), [("jdoe", "Jane", "Jane Doe", "JAYN DOH")])
        self.assertEqual(store.people()[0].pronounce, "Jane Doe")  # synced value untouched
        store.upsert(Member.new("Jane", "jdoe", "Jane D"))  # a sync run
        self.assertEqual(store.tag_pronunciations()[0][3], "JAYN DOH")

    def test_sync_prefers_authentik_then_badge_pronunciation(self):
        store = Store(":memory:")
        users = [
            self.user(["Students"], username="a", name="Ann Lee"),
            self.user(["Students"], username="b", name="Bo Kim",
                      attributes={"roboto": {"pronunciation": "BOH"}}),
            self.user(["Students"], username="c", name="Cy Fox"),
        ]
        for u, t in (("a", "AN"), ("b", "BEE"), ("c", None)):
            store.upsert(Member.new("x", u, "x"))
            if t:
                store.set_tag_pronounce(u, t)
        from sync_authentik import sync
        sync(users, store)
        got = {p.username: p.pronounce for p in store.people()}
        self.assertEqual(got, {"a": "AN", "b": "BOH", "c": "Cy Fox"})

    def test_closed_secs_and_split_here_rows(self):
        store = Store(":memory:")
        m = Member.new("Jane Doe", "jdoe")
        b = year_start()
        for ts in (b + 1000, b + 4600, b + 10000):  # out after 3600s, then in (open)
            store.toggle(m, ts)
        self.assertEqual(store.closed_secs(), {"jdoe": 3600})
        _, students, _ = split_here(store.who())
        self.assertEqual(students, [("Jane Doe", b + 10000, False, 3600, None, None, None, "JD")])
        self.assertEqual(fmt_total(3600 + 125), "1:02")
        self.assertEqual(fmt_total(37 * 3600 + 1800), "37:30")

    def test_newly_enabled_only_on_false_to_true_while_here(self):
        def person(user, enabled):
            m = Member.new(user, user)
            m.enabled = enabled
            return (m, 1000)
        prev = {}
        self.assertEqual(newly_enabled(prev, [person("a", False), person("b", True)]), [])  # first sight
        got = newly_enabled(prev, [person("a", True), person("b", True), person("c", True)])
        self.assertEqual([m.username for m in got], ["a"])   # c is new, b was already enabled
        self.assertEqual(newly_enabled(prev, [person("a", True)]), [])  # no repeat
        newly_enabled(prev, [person("a", False)])             # loses it, then regains it
        self.assertEqual([m.username for m in newly_enabled(prev, [person("a", True)])], ["a"])
        self.assertEqual(newly_enabled(prev, []), [])          # clocked out: forgotten
        self.assertEqual(newly_enabled(prev, [person("a", True)]), [])  # back in already enabled

    def test_enabled_status_text(self):
        a, b = Member.new("Ann", "a"), Member.new("Bo", "b")
        self.assertEqual(enabled_status([a]), "Ann is now enabled")
        self.assertEqual(enabled_status([a, b]), "Ann and Bo are now enabled")

    def test_parse_time_of_day(self):
        self.assertEqual(parse_time_of_day("3:25pm"), (15, 25))
        self.assertEqual(parse_time_of_day("3:25 PM"), (15, 25))
        self.assertEqual(parse_time_of_day("12:05am"), (0, 5))
        self.assertEqual(parse_time_of_day("3pm"), (15, 0))
        self.assertEqual(parse_time_of_day("15:25"), (15, 25))
        self.assertEqual(parse_time_of_day("03:25"), (3, 25))   # leading zero: 24-hour
        for bad in ("3:25", "9:00", "25:00", "noon", ""):        # ambiguous or unreadable
            with self.assertRaises(ValueError):
                parse_time_of_day(bad)

    def test_fix_punch_corrects_the_latest_punch_and_keeps_the_original(self):
        store = Store(":memory:")
        m = Member.new("Jane Doe", "jdoe")
        day = datetime.datetime(2026, 10, 7)
        at = lambda h, mi=0, s=0: int(day.replace(hour=h, minute=mi, second=s).timestamp())
        store.toggle(m, at(9))        # in
        store.toggle(m, at(11))       # out
        store.toggle(m, at(15, 56, 4))  # in, the late badge-in
        now = at(16)
        lines = fix_punch(store, "jdoe", "3:25pm", note="forgot to badge", now=now)
        self.assertTrue(lines[-1].startswith("done"))
        latest = store.recent_punches("jdoe", 1)[0]
        self.assertEqual((latest["direction"], latest["ts"], latest["edits"]), ("in", at(15, 25), 1))
        old, new, _, note = store.punch_edits(latest["id"])[0]
        self.assertEqual((old, new, note), (at(15, 56, 4), at(15, 25), "forgot to badge"))
        self.assertIn("(corrected 1x)", list_punches(store, "jdoe")[0])
        self.assertEqual(store.closed_secs(), {"jdoe": 2 * 3600})   # the 9-11 session is untouched

    def test_fix_punch_refuses_bad_changes_and_dry_run_changes_nothing(self):
        store = Store(":memory:")
        m = Member.new("Jane Doe", "jdoe")
        day = datetime.datetime(2026, 10, 7)
        at = lambda h, mi=0: int(day.replace(hour=h, minute=mi).timestamp())
        store.toggle(m, at(9))
        store.toggle(m, at(11))
        store.toggle(m, at(15))
        now = at(16)
        for bad, why in (("10:30am", "previous"), ("5pm", "future"), ("3pm", "already")):
            with self.assertRaises(ValueError) as ctx:
                fix_punch(store, "jdoe", bad, now=now)
            self.assertIn(why, str(ctx.exception))
        with self.assertRaises(ValueError):
            fix_punch(store, "nobody", "3pm", now=now)
        lines = fix_punch(store, "jdoe", "2:30pm", dry_run=True, now=now)
        self.assertEqual(lines[-1], "dry run: nothing changed")
        self.assertEqual(store.recent_punches("jdoe", 1)[0]["ts"], at(15))
        self.assertEqual(store.recent_punches("jdoe", 1)[0]["edits"], 0)

    def test_totals_reset_at_january_first(self):
        store = Store(":memory:")
        m = Member.new("Jane Doe", "jdoe")
        jan1 = year_start(datetime.datetime(2026, 6, 1).timestamp())
        self.assertEqual(jan1, int(datetime.datetime(2026, 1, 1).timestamp()))
        for ts in (jan1 - 3600, jan1 + 1800, jan1 + 5000, jan1 + 6000):
            store.toggle(m, ts)  # session across new year (1800s counts), then 1000s
        self.assertEqual(store.closed_secs(jan1), {"jdoe": 1800 + 1000})
        self.assertEqual(store.closed_secs(), {"jdoe": 5400 + 1000})

    def test_unchanged_roster_is_not_rewritten(self):
        from sync_authentik import sync
        store = Store(":memory:")
        users = [self.user(["Students", "Enabled"], username="a", name="Ann Lee"),
                 self.user(["Students"], username="b", name="Bo Kim")]
        self.assertEqual(sync(users, store, verbose=False)[:2], (2, 0))
        writes = []
        real_upsert, real_enabled = store.upsert, store.set_enabled
        store.upsert = lambda m: (writes.append(m.username), real_upsert(m))
        store.set_enabled = lambda u, e: (writes.append(u), real_enabled(u, e))
        self.assertEqual(sync(users, store, verbose=False)[:2], (0, 0))
        self.assertEqual(writes, [])  # nothing changed, nothing written
        users[1]["groups_obj"].append({"name": "Enabled"})  # Bo becomes enabled
        self.assertEqual(sync(users, store, verbose=False)[:2], (0, 1))
        self.assertEqual(writes, ["b", "b"])

    def test_enabled_survives_punch_upsert(self):
        store = Store(":memory:")
        m = Member.new("Jane Doe", "jdoe")
        store.upsert(m)
        store.set_enabled("jdoe", True)
        store.toggle(m, 1000)
        self.assertTrue(store.people()[0].enabled)
        self.assertTrue(store.who()[0][0].enabled)


class LeantimeRenderTests(unittest.TestCase):
    def test_plan(self):
        ms = [
            {"id": 1, "headline": "Event", "editFrom": "2026-10-23 19:00:00", "editTo": "2026-10-24 19:00:00"},
            {"id": 2, "headline": "Week A", "editFrom": "2026-09-28 19:00:00", "editTo": "2026-10-04 19:00:00"},
        ]
        self.assertEqual(
            render_plan(ms), "[ ] 9/28/2026 - 10/4/2026 --- Week A {2}\n\n[ ] 10/23/2026 - 10/24/2026 --- Event {1}\n"
        )

    def test_plan_status_roundtrip(self):
        ms = [
            {"headline": "A", "editFrom": "2026-09-28", "editTo": "2026-10-04", "status": 4},
            {"headline": "B", "editFrom": "2026-10-23", "editTo": "2026-10-24", "status": 0},
        ]
        g = parse_plan(render_plan(ms), today=datetime.date(2026, 9, 28))
        self.assertEqual([(b[0], b[1]) for b in g["bars"]], [("wip", "A"), ("done", "B")])

    def test_done_past_milestone_hidden(self):
        ms = [
            {"headline": "Old", "editFrom": "2026-09-14", "editTo": "2026-09-20", "status": 0},
            {"headline": "Old undone", "editFrom": "2026-09-14", "editTo": "2026-09-20", "status": 3},
            {"headline": "Done today", "editFrom": "2026-09-21", "editTo": "2026-09-28", "status": 0},
        ]
        out = render_plan(ms, today=datetime.date(2026, 9, 28))
        self.assertNotIn("Old\n", out.replace("Old undone", ""))
        self.assertIn("Old undone", out)
        self.assertIn("Done today", out)

    def test_ui_style_dates_use_leantime_timezone(self):
        # The UI stores Sun 9/21 00:00 -> Sun 9/27 23:59:59 (Los Angeles) as UTC.
        done = {"headline": "Old", "status": 0,
                "editFrom": "2026-09-21 07:00:00", "editTo": "2026-09-28 06:59:59"}
        event = {"headline": "Ev", "status": 1,
                 "editFrom": "2026-10-23 07:00:00", "editTo": "2026-10-25 06:59:59"}
        self.assertEqual(render_plan([done], today=datetime.date(2026, 9, 27)),
                         "[x] 9/21/2026 - 9/27/2026 --- Old {0}\n")
        self.assertEqual(render_plan([done], today=datetime.date(2026, 9, 28)), "")
        self.assertEqual(render_plan([event], today=datetime.date(2026, 10, 1)),
                         "[!] 10/23/2026 - 10/24/2026 --- Ev {0}\n")

    def test_multi_week_milestone_keeps_its_end(self):
        ms = [{"headline": "Long", "editFrom": "2026-09-28", "editTo": "2026-10-18", "status": 3},
              {"headline": "One", "editFrom": "2026-09-28", "editTo": "2026-10-04", "status": 3}]
        g = parse_plan(render_plan(ms, today=datetime.date(2026, 9, 28)), today=datetime.date(2026, 9, 28))
        self.assertEqual([(b[1], b[2], b[3]) for b in g["bars"]], [("One", 1, 1), ("Long", 1, 3)])

    def test_dates_read_the_same_for_any_editor_timezone(self):
        for start, end in (("2026-10-05 07:00:00", "2026-10-13 06:59:59"),   # Los Angeles
                           ("2026-10-05 04:00:00", "2026-10-13 03:59:59"),   # Eastern
                           ("2026-10-05", "2026-10-12"),
                           ("2026-10-05 19:00:00", "2026-10-12 19:00:00")):  # written by the API
            ms = [{"headline": "M", "editFrom": start, "editTo": end, "status": 3}]
            self.assertEqual(render_plan(ms, today=datetime.date(2026, 10, 1)),
                             "[ ] 10/5/2026 - 10/12/2026 --- M {0}\n")

    def test_bar_ends_on_its_end_day_not_the_week_end(self):
        ms = [{"headline": "A", "editFrom": "2026-09-28", "editTo": "2026-10-04", "status": 3},
              {"headline": "Tune", "editFrom": "2026-10-05", "editTo": "2026-10-12", "status": 3}]
        g = parse_plan(render_plan(ms, today=datetime.date(2026, 9, 28)), today=datetime.date(2026, 9, 28))
        a, tune = g["bars"]
        self.assertEqual((a[4], a[5]), (0.0, 1.0))           # a full week
        self.assertEqual(tune[4], 1.0)                        # starts at week 2
        self.assertAlmostEqual(tune[5], 15 / 7)               # ends 1 day into week 3

    def test_priority_filter(self):
        mk = lambda p, s: {"priority": p, "status": s}
        self.assertTrue(is_priority(mk("2", 3)))
        self.assertTrue(is_priority(mk(1, 4)))
        self.assertFalse(is_priority(mk("3", 3)))
        self.assertFalse(is_priority(mk("1", 0)))
        self.assertFalse(is_priority(mk(None, 3)))

    def test_week_deadline_is_sunday(self):
        for day in range(28, 32):  # Mon 2026-09-28 .. Thu 10-01
            d = datetime.date(2026, 9, 28) + datetime.timedelta(days=day - 28)
            self.assertEqual(week_deadline(d), datetime.date(2026, 10, 4))
        self.assertEqual(week_deadline(datetime.date(2026, 10, 4)), datetime.date(2026, 10, 4))

    def test_overdue(self):
        today = datetime.date(2026, 9, 28)
        mk = lambda s, a, b, typ="milestone": {
            "type": typ, "status": s, "editFrom": a + " 19:00:00", "editTo": b + " 19:00:00"}
        self.assertTrue(overdue(mk(3, "2026-09-14", "2026-09-20"), today))
        self.assertTrue(overdue(mk(1, "2026-09-14", "2026-09-20"), today))  # blocked
        self.assertTrue(overdue(mk(4, "2026-09-14", "2026-09-27"), today))  # in progress
        self.assertFalse(overdue(mk(0, "2026-09-14", "2026-09-20"), today))  # done
        self.assertFalse(overdue(mk(3, "2026-09-21", "2026-09-28"), today))  # ends today
        self.assertFalse(overdue(mk(3, "2026-09-14", "2026-09-20", "task"), today))
        self.assertFalse(overdue({"type": "milestone", "status": 3,
                                  "editFrom": "0000-00-00 00:00:00", "editTo": "0000-00-00 00:00:00"}, today))

    def test_roll_values_moves_dates_and_keeps_fields(self):
        m = {"id": 5, "headline": "h", "type": "milestone", "status": 3, "tags": "#1f77b4",
             "dateToFinish": "0000-00-00 00:00:00", "editFrom": "2026-09-14 19:00:00",
             "editTo": "2026-09-20 19:00:00", "timeFrom": None, "description": "d",
             "dependingTicketId": 4}
        v = roll_values(m, datetime.date(2026, 9, 28), datetime.date(2026, 10, 4))
        self.assertEqual((v["editFrom"], v["editTo"]), ("2026-09-28", "2026-10-04"))
        self.assertEqual((v["tags"], v["description"], v["dateToFinish"], v["timeFrom"], v["dependingTicketId"]),
                         ("#1f77b4", "d", "", "", 4))

    def test_overdue_milestone_rolls_to_sunday_keeping_length(self):
        today = datetime.date(2026, 9, 30)  # Wed; the week ends Sun 10/4
        mk = lambda i, a, b, **kw: dict({"id": i, "type": "milestone", "status": 3,
                                        "editFrom": a, "editTo": b}, **kw)
        moves = plan_moves([mk(1, "2026-09-14", "2026-09-20"), mk(2, "2026-09-14", "2026-09-15")], today)
        self.assertEqual(moves[1], (datetime.date(2026, 9, 28), datetime.date(2026, 10, 4)))
        self.assertEqual(moves[2], (datetime.date(2026, 10, 3), datetime.date(2026, 10, 4)))

    def test_cascade_pushes_dependents_after_their_prerequisite(self):
        today = datetime.date(2026, 9, 30)
        mk = lambda i, a, b, dep=0, **kw: dict({"id": i, "type": "milestone", "status": 3,
                                               "editFrom": a, "editTo": b, "dependingTicketId": dep}, **kw)
        ms = [
            mk(1, "2026-09-14", "2026-09-20"),                 # overdue prerequisite -> ends 10/4
            mk(2, "2026-09-21", "2026-09-27", dep=1),          # overdue dependent (7 days) -> 10/5..10/11
            mk(3, "2026-10-05", "2026-10-11", dep=2),          # not overdue, but must follow 2 -> 10/12..10/18
            mk(4, "2026-10-25", "2026-10-31", dep=3),          # already after 3 -> unchanged
            mk(5, "2026-09-01", "2026-09-05", dep=1, status=0),  # done -> never moved
        ]
        moves = plan_moves(ms, today)
        d = datetime.date
        self.assertEqual(moves[1], (d(2026, 9, 28), d(2026, 10, 4)))
        self.assertEqual(moves[2], (d(2026, 10, 5), d(2026, 10, 11)))
        self.assertEqual(moves[3], (d(2026, 10, 12), d(2026, 10, 18)))
        self.assertNotIn(4, moves)
        self.assertNotIn(5, moves)

    def test_milestone_dependency_is_read_from_milestoneid(self):
        from sync_leantime import dep_id
        ms = {"type": "milestone", "milestoneid": 65, "dependingTicketId": 0}
        self.assertEqual(dep_id(ms), 65)
        self.assertEqual(dep_id({"type": "milestone", "milestoneid": None, "dependingTicketId": 7}), 7)
        # for a task, milestoneid is its milestone, not a dependency
        self.assertEqual(dep_id({"type": "task", "milestoneid": 65, "dependingTicketId": 0}), 0)
        self.assertEqual(dep_id({"type": "task", "milestoneid": 65, "dependingTicketId": 9}), 9)
        ms_list = [{"id": 1, "type": "milestone", "status": 3, "editFrom": "2026-09-14", "editTo": "2026-09-20", "milestoneid": None},
                   {"id": 2, "type": "milestone", "status": 3, "editFrom": "2026-09-21", "editTo": "2026-09-27", "milestoneid": 1}]
        moves = plan_moves(ms_list, datetime.date(2026, 9, 30))
        self.assertEqual(moves[2][0], datetime.date(2026, 10, 5))

    def test_milestone_waiting_on_unfinished_milestone_shows_blocked(self):
        mk = lambda i, status, dep=None: {"id": i, "headline": f"M{i}", "status": status,
                                          "type": "milestone", "milestoneid": dep,
                                          "editFrom": "2026-10-05", "editTo": "2026-10-11"}
        today = datetime.date(2026, 10, 1)
        # M2 is New but waits on M1 (in progress): shown blocked; M1 itself is not
        text = render_plan([mk(1, 4), mk(2, 3, 1)], today=today)
        self.assertIn("[~] 10/5/2026 - 10/11/2026 --- M1", text)
        self.assertIn("[!] 10/5/2026 - 10/11/2026 --- M2 {2<1}", text)
        # once M1 is done, M2 is no longer blocked; a done milestone is never blocked
        text = render_plan([mk(1, 0), mk(2, 3, 1), mk(3, 0, 2)], today=today)
        self.assertIn("[ ] 10/5/2026 - 10/11/2026 --- M2", text)
        self.assertIn("[x] 10/5/2026 - 10/11/2026 --- M3", text)
        # an explicit Blocked status still shows blocked with no dependency
        self.assertIn("[!] 10/5/2026 - 10/11/2026 --- M4", render_plan([mk(4, 1)], today=today))

    def test_plan_keeps_the_year_so_next_years_milestones_sort_last(self):
        ms = [{"id": 1, "headline": "Kick-Off", "editFrom": "2027-01-01 05:00:00", "editTo": "2027-01-07 04:59:59", "status": 3},
              {"id": 2, "headline": "Now", "editFrom": "2026-10-05 04:00:00", "editTo": "2026-10-12 03:59:59", "status": 3}]
        text = render_plan(ms, today=datetime.date(2026, 10, 1))
        self.assertIn("--- Kick-Off {1}", text)
        g = parse_plan(text, today=datetime.date(2026, 10, 1))
        self.assertEqual(g["start"], datetime.date(2026, 10, 5))
        (now, kick) = g["bars"]
        self.assertEqual((now[1], now[2]), ("Now", 1))
        self.assertEqual((kick[1], kick[2], kick[3]), ("Kick-Off", 13, 14))  # Fri 1/1 .. Wed 1/6 crosses a week
        self.assertEqual(g["weeks"], 14)

    def test_connected_milestones_are_listed_together(self):
        mk = lambda i, a, b, dep=None: {"id": i, "headline": f"M{i}", "status": 3, "type": "milestone",
                                        "editFrom": a, "editTo": b, "milestoneid": dep}
        ms = [
            mk(1, "2026-10-05", "2026-10-11"),                # chain A root
            mk(9, "2026-10-06", "2026-10-30"),                # unrelated, starts between A's members
            mk(2, "2026-10-12", "2026-10-18", 1),             # A: depends on 1
            mk(3, "2026-10-19", "2026-10-25", 2),             # A: depends on 2
            mk(4, "2026-10-14", "2026-10-15", 1),             # A: also depends on 1, starts before 3
            mk(5, "2026-10-01", "2026-10-02"),                # unrelated, earliest
        ]
        text = render_plan(ms, today=datetime.date(2026, 10, 1))
        order = [int(x) for x in re.findall(r"--- M(\d+)", text)]
        self.assertEqual(order, [5, 1, 2, 3, 4, 9])  # 1 then its dependents depth-first, 9 and 5 apart

    def test_chain_order_survives_a_cycle_and_missing_prerequisite(self):
        d = datetime.date
        row = lambda k, dep, day: {"start": d(2026, 10, day), "end": d(2026, 10, day), "key": k, "dep": dep, "line": str(k)}
        self.assertEqual(sorted(r["key"] for r in chain_order([row(1, 2, 1), row(2, 1, 2)])), [1, 2])
        self.assertEqual([r["key"] for r in chain_order([row(7, 99, 3), row(8, 7, 1)])], [7, 8])  # 8 depends on 7; 7's own prerequisite is absent

    def test_dependency_cycle_terminates(self):
        today = datetime.date(2026, 9, 30)
        ms = [{"id": 1, "type": "milestone", "status": 3, "editFrom": "2026-09-14", "editTo": "2026-09-20", "dependingTicketId": 2},
              {"id": 2, "type": "milestone", "status": 3, "editFrom": "2026-09-14", "editTo": "2026-09-20", "dependingTicketId": 1}]
        self.assertEqual(set(plan_moves(ms, today)), {1, 2})

    def test_today_lists_open_tasks_first_and_drops_done_milestones(self):
        ms = lambda i, status, start: {"id": i, "type": "milestone", "status": status, "headline": f"M{i}",
                                       "editFrom": start, "editTo": start}
        task = lambda i, m, s=3: {"id": i, "type": "task", "status": s, "milestoneid": m, "headline": f"t{i}"}
        everything = [ms(10, 4, "2026-10-05"), ms(11, 0, "2026-09-28"),     # 11 is Done
                      task(1, 10, 0), task(2, 10, 3), task(3, 10, 0), task(4, 11, 3), task(5, 0, 0), task(6, 0)]
        tasks = [t for t in everything if t["type"] == "task"]
        text = render_today(tasks, everything=everything, hide_done_milestones=True)
        self.assertEqual(text, "# M10\n- [ ] t2\n- [x] t1\n- [x] t3\n\n# No milestone\n- [ ] t6\n- [x] t5\n")
        self.assertNotIn("M11", text)
        self.assertNotIn("t4", text)
        # the priority list keeps tasks of finished milestones
        self.assertIn("M11", render_today(tasks, everything=everything))

    def test_task_waiting_on_dependency_shows_blocked(self):
        tasks = [
            {"id": 1, "type": "task", "status": 3, "tags": "T", "headline": "Build", "dependingTicketId": 0},
            {"id": 2, "type": "task", "status": 3, "tags": "T", "headline": "Test", "dependingTicketId": 1},
            {"id": 3, "type": "task", "status": 0, "tags": "T", "headline": "Old", "dependingTicketId": 1},
        ]
        self.assertEqual(render_today(tasks),
                         "# No milestone\n- [ ] Build\n- [!] Test \u2190 waiting on Build\n- [x] Old\n")
        tasks[0]["status"] = 0  # prerequisite finished: no longer blocked
        self.assertIn("- [ ] Test\n", render_today(tasks))

    def test_blockers_count_as_priority(self):
        tasks = [
            {"id": 1, "type": "task", "status": 3, "priority": "3", "dependingTicketId": 0, "headline": "a"},
            {"id": 2, "type": "task", "status": 3, "priority": "3", "dependingTicketId": 1, "headline": "b"},
        ]
        blockers = blocking_ids(tasks)
        self.assertEqual(blockers, {1})
        self.assertTrue(is_priority(tasks[0], blockers))
        self.assertFalse(is_priority(tasks[1], blockers))

    def test_plan_line_carries_id_and_dependency(self):
        ms = [{"id": 66, "headline": "A", "editFrom": "2026-09-28", "editTo": "2026-10-04", "status": 3},
              {"id": 67, "headline": "B", "editFrom": "2026-10-05", "editTo": "2026-10-11", "status": 3,
               "dependingTicketId": 66}]
        text = render_plan(ms, today=datetime.date(2026, 9, 28))
        self.assertIn("--- B {67<66}", text)
        g = parse_plan(text, today=datetime.date(2026, 9, 28))
        self.assertEqual([(b[1], b[6], b[7]) for b in g["bars"]], [("A", 66, None), ("B", 67, 66)])

    def test_md_statuses(self):
        text = "- [ ] a\n- [~] b\n- [!] c\n- [x] d\n"
        self.assertEqual([k for k, _ in parse_md(text)], ["todo", "wip", "blocked", "done"])

    def test_today_groups_by_milestone(self):
        ms = lambda i, start: {"id": i, "type": "milestone", "status": 3, "headline": f"M{i}",
                               "editFrom": start, "editTo": start}
        task = lambda i, m, s=3, **kw: dict({"id": i, "type": "task", "status": s, "milestoneid": m,
                                            "tags": "ignored", "headline": f"t{i}"}, **kw)
        everything = [ms(10, "2026-10-05"), ms(11, "2026-09-28"),  # 11 starts first
                      task(1, 10), task(2, 0), task(3, 11, 0), task(4, 10, -1), task(5, 99)]
        text = render_today([t for t in everything if t["type"] == "task"], everything=everything)
        self.assertEqual(text, "# M11\n- [x] t3\n\n# M10\n- [ ] t1\n\n# No milestone\n- [ ] t2\n- [ ] t5\n")
        # without milestone info every task lands under "No milestone"
        self.assertEqual(render_today([task(1, 10)]), "# No milestone\n- [ ] t1\n")


class LayoutTests(unittest.TestCase):
    def test_today_pages_one_per_milestone_and_paginate_open_tasks(self):
        items = [("h", "A"), ("todo", "1"), ("todo", "2"), ("todo", "3"), ("h", "B"), ("done", "x")]
        pages = today_pages(items, 2)
        self.assertEqual(
            pages,
            [
                [("h", "A"), ("todo", "1"), ("todo", "2")],
                [("h", "A"), ("todo", "3")],
                [("h", "B"), ("done", "x")],
            ],
        )

    def test_today_line_is_at_the_exact_day(self):
        start = datetime.date(2026, 9, 28)
        at = lambda y, mo, d, h=0: datetime.datetime(y, mo, d, h).timestamp()
        self.assertEqual(today_offset_weeks(start, 5, at(2026, 9, 28)), 0.0)
        self.assertAlmostEqual(today_offset_weeks(start, 5, at(2026, 10, 6, 12)), 8.5 / 7)  # Tue of week 2
        self.assertIsNone(today_offset_weeks(start, 5, at(2026, 9, 27, 23)))   # before the plan
        self.assertIsNone(today_offset_weeks(start, 5, at(2026, 11, 2)))       # after it
        self.assertIsNone(today_offset_weeks(None, 5, at(2026, 10, 6)))

    def test_finished_tasks_never_add_pages(self):
        items = [("h", "A"), ("todo", "1"), ("done", "d1"), ("done", "d2"), ("done", "d3"), ("done", "d4"),
                 ("h", "B")] + [("done", f"b{i}") for i in range(7)]
        pages = today_pages(items, 4)
        # A: one open task + 4 finished, 4 rows: two finished shown, the other two summarised
        self.assertEqual(pages[0], [("h", "A"), ("todo", "1"), ("done", "d1"), ("done", "d2"), ("more", "+2 more done")])
        self.assertEqual([p[0] for p in pages], [("h", "A"), ("h", "B")])  # one page each
        # B has only finished tasks: a single page, capped, with a summary of the rest
        self.assertEqual(len(pages[1]) - 1, 4)
        self.assertEqual(pages[1][-1], ("more", "+4 more done"))

    def test_finished_fill_leftover_room_on_last_open_page(self):
        items = [("h", "A")] + [("todo", str(i)) for i in range(5)] + [("done", "d")]
        pages = today_pages(items, 4)
        self.assertEqual(len(pages), 2)                       # 4 open + 1 open
        self.assertEqual(pages[1][1:], [("todo", "4"), ("done", "d")])  # done fills the room

    def test_today_pages_empty(self):
        self.assertEqual(today_pages([], 5), [])

    def test_plan_height_scales_and_caps(self):
        bars = lambda n: {"bars": [("new", "x", 1, 1)] * n}
        self.assertEqual(plan_height(bars(2), 400), 64 + 2 * 32 + 12)
        self.assertEqual(plan_height(bars(50), 400), 400)


class NetTests(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(classify(True, True, True), "ok")
        self.assertEqual(classify(True, True, False), "limited")
        self.assertEqual(classify(True, False, False), "down")
        self.assertEqual(classify(False, False, False), "down")

    def test_bars(self):
        self.assertEqual([bars(d) for d in (-45, -65, -75, None)], [3, 2, 1, 1])
        self.assertEqual(bars(None, wired=True), 3)

    def test_escalation_ladder(self):
        got = [(n, action_for(n)) for n in range(1, 21) if action_for(n)]
        self.assertEqual(got, [(2, "reconnect"), (4, "radio-cycle"), (8, "restart-nm"), (20, "reload-driver")])

    def test_decode_throttled(self):
        self.assertEqual(decode_throttled(0), ["ok"])
        self.assertEqual(decode_throttled(0x50005),
                         ["undervoltage now", "throttled now", "undervoltage since boot", "throttled since boot"])


class FakeNfcTests(unittest.TestCase):
    def test_taps_toggle_and_create(self):
        import queue
        with tempfile.TemporaryDirectory() as d:
            store = Store(os.path.join(d, "a.sqlite"))
            q = queue.Queue()
            _run_fake(store, None, q, ["alice:mentor\n", "\n"])
            kinds = [q.get_nowait() for _ in range(q.qsize())]
            self.assertEqual(kinds[1][0], "greet")
            self.assertEqual(kinds[1][2], IN)
            self.assertEqual(store.get("alice").role, Role.MENTOR)
            self.assertEqual([m.username for m, _ in store.who()], ["alice"])


class GoldenRenderTests(unittest.TestCase):
    """The default screen must not change unless py/golden/shop.png is regenerated on purpose:
    .venv/bin/python py/tvgui.py render py/golden/shop.png (pixels depend on this machine's FreeSans/pygame)."""

    def test_default_screen_matches_golden(self):
        try:
            import pygame
        except ImportError:
            self.skipTest("pygame not installed")
        from fixture import render

        golden = pygame.image.load(os.path.join(os.path.dirname(__file__), "golden", "shop.png"))
        self.assertEqual(pygame.image.tobytes(render(), "RGB"), pygame.image.tobytes(golden, "RGB"))


class LayoutResolveTests(unittest.TestCase):
    class H:
        @staticmethod
        def fit_height(ctx, opts, w, max_h):
            return opts["want"]

    ctx = type("C", (), {"size": (200, 100)})()
    get = staticmethod(lambda name: LayoutResolveTests.H)

    def rects(self, layout):
        return [r for _, r in resolve(layout, self.ctx, self.get)]

    def test_px_refs_fit_and_stretch(self):
        layout = [
            {"id": "a", "handler": "h", "x": 10, "y": 5, "w": 50, "h": "fit", "max_h": 30, "opts": {"want": 99}},
            {"handler": "h", "x": "a.right+4", "y": "a.bottom+2", "right": 150, "bottom": 90},
        ]
        self.assertEqual(self.rects(layout), [(10, 5, 50, 30), (64, 37, 86, 53)])  # fit capped at max_h

    def test_bad_modules_are_skipped_not_fatal(self):
        layout = [
            {"handler": "h", "x": "later.x", "y": 0, "w": 10, "h": 10},   # unknown/forward id
            {"handler": "h", "x": 0, "y": 0, "w": 10},                    # no h or bottom
            {"handler": "h", "x": 500, "y": 0, "w": 10, "h": 10},         # off-screen
            {"handler": "h", "x": 190, "y": 90, "w": 50, "h": 50},        # clamped
        ]
        self.assertEqual(self.rects(layout), [(190, 90, 10, 10)])

    def test_layout_file_reloads_and_keeps_last_good(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "l.json")
            open(p, "w").write('[{"handler": "a"}]')
            lf = LayoutFile(p)
            self.assertEqual(lf.get(), [{"handler": "a"}])
            open(p, "w").write("{not json")
            os.utime(p, (1, 1))
            self.assertEqual(lf.get(), [{"handler": "a"}])   # bad edit: previous layout stays
            open(p, "w").write('[{"handler": "b"}]')
            os.utime(p, (2, 2))
            self.assertEqual(lf.get(), [{"handler": "b"}])
            import json, layout
            self.assertEqual(LayoutFile(os.path.join(d, "missing.json")).get(), json.load(open(layout.default_path())))  # falls back to the default

    def test_broken_handler_only_marks_its_own_rect(self):
        try:
            import pygame
        except ImportError:
            self.skipTest("pygame not installed")
        from layout import Ctx, render as render_layout

        bad = LayoutFile().get() + [{"handler": "nope", "x": 0, "y": 0, "w": 50, "h": 50}]
        pygame.font.init()
        f = pygame.font.Font(None, 20)
        ctx = Ctx((1920, 1080), (f, f, f), 0, [], [], [], "", {}, [], [], ("ok", 3))
        self.assertEqual(render_layout(bad, ctx).get_size(), (1920, 1080))  # no exception


class RefreshTests(unittest.TestCase):
    def wait(self, cond, secs=2.0):
        import time
        end = time.time() + secs
        while time.time() < end and not cond():
            time.sleep(0.005)
        return cond()

    def handlers(self, calls):
        import types

        def refresh(opts):
            calls.append(opts)
            if opts.get("fail"):
                raise RuntimeError("down")
            return len(calls)

        mod = types.SimpleNamespace(INTERVAL=0.01, refresh=refresh)
        plain = types.SimpleNamespace()  # no refresh(): no thread
        return lambda name: mod if name == "net" else plain

    def test_refresh_runs_in_background_and_stops_when_module_removed(self):
        calls = []
        r = Refresher()
        layout = [{"handler": "net", "opts": {"a": 1}}, {"handler": "net", "opts": {"a": 1}}, {"handler": "plain"}]
        r.sync(layout, self.handlers(calls))
        key = module_key("net", {"a": 1})
        self.assertEqual(list(r.state), [key])                  # duplicates share one thread; plain has none
        self.assertTrue(self.wait(lambda: r.state[key].at is not None and r.state[key].value >= 2))
        r.sync([{"handler": "plain"}], self.handlers(calls))
        self.assertEqual(r.state, {})
        n = len(calls)
        import time
        time.sleep(0.1)
        self.assertLessEqual(len(calls) - n, 1)                  # thread stopped (at most one in-flight call)

    def test_failed_refresh_keeps_last_good_value_and_records_error(self):
        import types
        flip = {"fail": False}

        def refresh(opts):
            if flip["fail"]:
                raise RuntimeError("down")
            return "ok"

        mod = types.SimpleNamespace(INTERVAL=0.01, refresh=refresh)
        r = Refresher()
        r.sync([{"handler": "x"}], lambda n: mod)
        f = r.state[module_key("x", {})]
        self.assertTrue(self.wait(lambda: f.value == "ok"))
        flip["fail"] = True
        self.assertTrue(self.wait(lambda: f.err is not None))
        self.assertEqual(f.value, "ok")                          # stale value is kept
        self.assertIsNotNone(f.at)
        flip["fail"] = False
        self.assertTrue(self.wait(lambda: f.err is None))
        r.sync([], None)

    def test_age_text(self):
        self.assertEqual([age_text(s) for s in (5, 89, 90, 3600, 5400, 7300)], ["5s", "89s", "1m", "60m", "1h", "2h"])


class NexusTests(unittest.TestCase):
    def match(self, label, status, red=("1",), blue=("2",)):
        return {"label": label, "status": status, "redTeams": list(red), "blueTeams": list(blue), "times": {}}

    def test_upcoming_starts_at_the_last_on_field_match(self):
        ms = [self.match("p1", "On field"), self.match("q1", "On field"), self.match("q2", "On field", red=("447",)),
              self.match("q3", "On deck"), self.match("q4", "Now queuing", red=("447",)), self.match("q5", "Queuing soon")]
        self.assertEqual([m["label"] for m in nexus.upcoming(ms)], ["q2", "q3", "q4", "q5"])  # p1, q1 are finished
        self.assertEqual([m["label"] for m in nexus.upcoming(ms, "447")], ["q2", "q4"])
        self.assertEqual([m["label"] for m in nexus.upcoming(ms[3:])], ["q3", "q4", "q5"])    # none on field yet: all
        self.assertEqual(nexus.upcoming([]), [])

    def test_eta_text(self):
        m = 60_000
        self.assertEqual([nexus.eta_text(t * m, 0) for t in (-3, 0, 12, 89, 125)], ["now", "now", "12 min", "89 min", "2h 05m"])


class LocationTests(unittest.TestCase):
    def setUp(self):
        PENDING.loc = None
        self.store = Store(":memory:")
        self.m = Member.new("Jane Doe", "jdoe")

    def tap(self, now, require=True):
        import unittest.mock as mock
        env = {"TVGUI_REQUIRE_LOCATION": "1"} if require else {}
        with mock.patch.dict(os.environ, env, clear=False):
            if not require:
                os.environ.pop("TVGUI_REQUIRE_LOCATION", None)
            return tap(self.store, self.m, now)

    def where(self):
        return [m.location for m, _ in self.store.who()]

    def test_clock_in_needs_a_location_when_required(self):
        self.assertEqual(self.tap(100), (None, "Pick a location first"))
        self.assertEqual(self.store.who(), [])
        PENDING.arm("  Practice   Field ")
        punch, note = self.tap(200)
        self.assertEqual((punch.direction, note), (IN, None))
        self.assertEqual(self.where(), ["practice field"])        # normalised
        self.assertIsNone(PENDING.take())                          # consumed by the tap

    def test_wall_display_does_not_require_one(self):
        punch, _ = self.tap(100, require=False)
        self.assertEqual(punch.direction, IN)
        self.assertEqual(self.where(), [None])

    def test_armed_tap_while_in_moves_and_plain_tap_clocks_out(self):
        PENDING.arm("pit")
        self.tap(100)
        PENDING.arm("stands")
        self.assertEqual(self.tap(200), (None, "Jane Doe moved to stands"))
        self.assertEqual(self.where(), ["stands"])
        punch, _ = self.tap(300)
        self.assertEqual(punch.direction, OUT)
        self.assertEqual(self.store.who(), [])

    def test_armed_location_expires(self):
        PENDING.arm("pit", now=1000)
        self.assertIsNone(PENDING.take(now=1000 + PENDING.SECS))
        PENDING.arm("pit", now=1000)
        self.assertEqual(PENDING.take(now=1000 + PENDING.SECS - 1), "pit")

    def test_here_groups_by_location_in_layout_order(self):
        class C:
            mentors = [("Zed", 0, False, 0, "pit")]
            students = [("Amy", 0, False, 0, "stands"), ("Bo", 0, False, 0, "pit"), ("Cy", 0, False, 0, "garage"), ("Di", 0, False, 0, None)]
            parents = []
        out = here_groups(C, {"group_by": "location", "locations": ["pit", "practice field", "stands"]})
        self.assertEqual([(h, [r[0] for r in rows]) for h, rows in out],
                         [("pit (2)", ["Bo", "Zed"]), ("practice field (0)", []), ("stands (1)", ["Amy"]),
                          ("garage (1)", ["Cy"]), ("unassigned (1)", ["Di"])])
        self.assertEqual([h for h, _ in here_groups(C, {})], ["students (4)", "parents (0)", "mentors (1)"])


class FitWidthTests(unittest.TestCase):
    class Font:
        @staticmethod
        def size(text):
            return (10 * len(text), 20)

    def ctx(self, names):
        c = type("C", (), {})()
        c.font_sm = self.Font
        c.size = (1920, 1080)
        c.mentors, c.parents = [], []
        c.students = [(n, 0, False, 0, "pit") for n in names]
        return c

    def test_width_follows_the_longest_name_between_floor_and_cap(self):
        opts = {"group_by": "location", "locations": ["pit"], "times": False}
        self.assertEqual(fit_width(self.ctx([]), opts, 560), 220)                       # floor
        self.assertEqual(fit_width(self.ctx(["a" * 30]), opts, 560), 340)               # 300 + 40
        self.assertEqual(fit_width(self.ctx(["a" * 80]), opts, 560), 560)               # cap
        self.assertEqual(fit_width(self.ctx(["a" * 30]), {**opts, "times": True}, 560), 550)  # + time columns

    def test_resolver_places_modules_after_a_fit_width_module(self):
        import types
        H = types.SimpleNamespace(fit_width=lambda ctx, opts, max_w, h: 300 if h == 50 else 0)  # h must be passed through
        layout = [{"id": "r", "handler": "h", "x": 24, "y": 0, "w": "fit", "max_w": 560, "h": 50},
                  {"handler": "h", "x": "r.right+24", "y": 0, "right": 600, "h": 10}]
        ctx = type("C", (), {"size": (1000, 100)})()
        self.assertEqual([r for _, r in resolve(layout, ctx, lambda n: H)], [(24, 0, 300, 50), (348, 0, 252, 10)])

    def test_team_and_event_come_from_opts_then_env(self):
        import unittest.mock as mock
        with mock.patch.dict(os.environ, {"TVGUI_TEAM": "447", "TVGUI_NEXUS_EVENT": "ev1"}):
            self.assertEqual((nexus.team({}), nexus.event_key({})), ("447", "ev1"))
            self.assertEqual((nexus.team({"team": "800"}), nexus.event_key({"event": "ev2"})), ("800", "ev2"))
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(nexus.team({}))


class SlackTests(unittest.TestCase):
    def setUp(self):
        slack._names.clear()

    def fake(self, history, users=None, calls=None):
        def get(method, **p):
            if calls is not None:
                calls.append(method)
            if method == "conversations.history":
                return {"messages": history}
            if users is None:
                raise RuntimeError("missing_scope")
            return {"user": {"real_name": users[p["user"]]}}
        return get

    def test_clean_mentions_links_and_entities(self):
        name = lambda u: {"U1": "Ana"}.get(u, "someone")
        self.assertEqual(slack.clean("hi <@U1> see <https://x.org/a/b|the doc> or <https://y.org/p> in <#C1|pit> <!here> a &amp; b &lt;3", name),
                         "hi @Ana see the doc or y.org in #pit @here a & b <3")

    def test_messages_filter_name_cache_and_fallbacks(self):
        hist = [
            {"ts": "100.0", "user": "U1", "text": "old"},
            {"ts": "300.0", "user": "U1", "text": "newest <@U2>"},
            {"ts": "250.0", "subtype": "channel_join", "user": "U2", "text": "joined"},
            {"ts": "260.0", "user": "U2", "text": "reply", "thread_ts": "100.0"},
            {"ts": "280.0", "user": "U2", "text": "", "files": [{}]},
            {"ts": "290.0", "bot_id": "B1", "username": "deploybot", "text": "", "attachments": [{"fallback": "build ok"}]},
        ]
        calls = []
        self.assertEqual(len(slack.messages(None, 15, self.fake(hist, {"U1": "Ana", "U2": "Bo"}))), 4)  # no channel needed offline
        slack._names.clear()
        out = slack.messages("C1", 15, self.fake(hist, {"U1": "Ana", "U2": "Bo"}, calls))
        self.assertEqual([(m["author"], m["text"]) for m in out],
                         [("Ana", "newest @Bo"), ("deploybot", "build ok"), ("Bo", "[file]"), ("Ana", "old")])
        self.assertEqual(calls.count("users.info"), 2)  # each user looked up once

    def test_missing_users_scope_degrades_and_api_errors_raise(self):
        out = slack.messages("C1", 15, self.fake([{"ts": "1", "user": "U1", "text": "x"}]))
        self.assertEqual(out[0]["author"], "someone")

        def bad(method, **p):
            raise RuntimeError("not_in_channel")
        with self.assertRaises(RuntimeError):
            slack.messages("C1", 15, bad)
        import unittest.mock as mock
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(RuntimeError, "TVGUI_SLACK_CHANNEL"):
            slack.messages(None, 15, slack.call)

    def test_wrap_text_and_border_state(self):
        class F:
            @staticmethod
            def size(t):
                return (10 * len(t), 20)
        self.assertEqual(wrap_text(F, "aaa bbb ccc dd", 70), ["aaa bbb", "ccc dd"])
        self.assertEqual(wrap_text(F, "x" * 25, 100), ["x" * 10, "x" * 10, "x" * 5])
        self.assertEqual([border_state(a) for a in (0, 59, 60, 299, 300, 9000)], ["flash", "flash", "red", "red", "idle", "idle"])

    def test_border_colors_by_age(self):
        try:
            import pygame
        except ImportError:
            self.skipTest("pygame not installed")
        import time
        from layout import Ctx, module_key
        from refresh import Fetched
        import handlers.slack_feed as feed
        from panels import BLUE, BORDER, LTRED

        pygame.font.init()
        f = pygame.font.Font(None, 24)
        rect = pygame.Rect(10, 10, 300, 200)

        def edge(age, beat=0):
            fetched = Fetched()
            fetched.value, fetched.at = [{"ts": 1000.0 - age, "author": "Ana", "text": "hello pit"}], time.time()
            ctx = Ctx((400, 300), (f, f, f), 1000, [], [], [], "", {}, [], [], ("ok", 3), {module_key("slack_feed", {}): fetched}, beat)
            surf = pygame.Surface((400, 300))
            feed.draw(surf, rect, ctx, {})
            return tuple(surf.get_at((rect.x + 1, rect.centery)))[:3], ctx.tick

        self.assertEqual(edge(30, 0), (BLUE, 0.5))
        self.assertEqual(edge(30, 1), (LTRED, 0.5))   # flashes
        self.assertEqual(edge(120), (LTRED, 1))
        self.assertEqual(edge(900), (BORDER, 0))


class ImageTests(unittest.TestCase):
    def test_fit_size_contain_and_cover(self):
        self.assertEqual(fit_size(400, 200, 100, 100), (100, 50))               # contain: whole picture inside
        self.assertEqual(fit_size(400, 200, 100, 100, "cover"), (200, 100))     # cover: fills the box, overflow cropped
        self.assertEqual(fit_size(1, 1000, 100, 10), (1, 10))                   # never collapses to zero

    def test_missing_asset_marks_only_its_own_box(self):
        try:
            import pygame
        except ImportError:
            self.skipTest("pygame not installed")
        from layout import Ctx, render as render_layout

        pygame.font.init()
        f = pygame.font.Font(None, 20)
        ctx = Ctx((400, 300), (f, f, f), 0, [], [], [], "", {}, [], [], ("ok", 3))
        layout = [{"handler": "image", "x": 0, "y": 0, "w": 100, "h": 100, "opts": {"file": "no-such-file.png"}}]
        self.assertEqual(render_layout(layout, ctx).get_size(), (400, 300))   # no exception


class RosterOverflowTests(unittest.TestCase):
    def people(self, n, loc="pit"):
        return [(f"Person {i:02d}", 0, i % 2 == 0, 3600, loc) for i in range(n)]

    def test_rows_height_halves_with_two_columns(self):
        gs = [("a (13)", self.people(13)), ("b (0)", [])]
        self.assertEqual(rows_height(gs, 1), (32 + 13 * 30 + 10) + (32 + 10))
        self.assertEqual(rows_height(gs, 2), (32 + 7 * 30 + 10) + (32 + 10))   # ceil(13/2) rows

    def test_wall_roster_goes_to_two_columns_only_when_one_does_not_fit(self):
        class F:
            @staticmethod
            def size(t):
                return (10 * len(t), 20)
        c = type("C", (), {})()
        c.font_sm, c.mentors, c.parents, c.students = F, [], [], self.people(8)
        opts = {"min_w": 456}
        self.assertEqual(fit_width(c, opts, 620, 788), 456)                    # 8 people fit one column
        c.students = self.people(26)
        self.assertEqual(fit_width(c, opts, 620, 788), 616)                    # 26 do not: 2 * COL_W + 16
        self.assertEqual(fit_width(c, {"min_w": 300, "times": False}, 620, 788), 300)  # flow mode never widens

    def test_many_people_render_in_both_modes(self):
        try:
            import pygame
        except ImportError:
            self.skipTest("pygame not installed")
        from panels import _draw_here, _draw_here_flow

        pygame.font.init()
        f = pygame.font.Font(None, 22)
        gs = [(f"s{k} ({len(g)})", g) for k, g in enumerate([self.people(13), self.people(7), self.people(6)])]
        for fn in (lambda s, r: _draw_here(s, r, gs, f, 1000, True, 2),
                   lambda s, r: _draw_here(s, r, gs, f, 1000, True, 1),      # too tall for 1 column: must not crash
                   lambda s, r: _draw_here_flow(s, r, gs, f)):
            surf = pygame.Surface((700, 800))
            fn(surf, pygame.Rect(10, 10, 616, 788))


class DeckTests(unittest.TestCase):
    def setUp(self):
        import queue
        import threading
        ADMIN.armed_until = ADMIN.open_until = 0
        PENDING.loc = None
        self.store = Store(":memory:")
        self.q = queue.Queue()
        self.state = {"lock": threading.Lock(), "mentors": [], "students": [], "parents": [], "blanked": False}
        self.deck = Deck(self.store, self.state, self.q, locations=["pit", "stands"])
        self.mentor = Member("Ann", "ann", "Ann", Role.MENTOR)
        self.kid = Member("Kid", "kid", "Kid", Role.STUDENT)
        for m in (self.mentor, self.kid):
            self.store.upsert(m)

    def events(self):
        out = []
        while not self.q.empty():
            out.append(self.q.get_nowait())
        return out

    def test_location_keys_show_counts_and_the_armed_one(self):
        self.state["students"] = [("a", 0, False, 0, "pit"), ("b", 0, False, 0, "pit"), ("c", 0, False, 0, "stands")]
        keys = self.deck.keys(1000)
        self.assertEqual([(k[0], k[1]) for k in keys[:2]], [("pit", "2"), ("stands", "1")])
        self.assertEqual(keys[14][0], "ADMIN")
        self.deck.press(1, 1000)
        self.assertEqual(self.deck.keys(1001)[1][1], "TAP BADGE")
        self.assertEqual(PENDING.peek(), "stands")

    def test_admin_opens_only_for_a_mentor_and_nobody_is_punched(self):
        self.deck.press(14, 1000)
        self.assertEqual(self.deck.keys(1001)[14][:2], ("TAP", "MENTOR"))
        self.assertEqual(tap(self.store, self.kid, 1002), (None, "Admin needs a mentor badge"))
        self.assertEqual(tap(self.store, Member("Who", "who", "Who", Role.MENTOR), 1003)[1], "Admin needs a mentor badge")  # unknown
        self.assertFalse(ADMIN.is_open(1003))
        punch, note = tap(self.store, self.mentor, 1004)
        self.assertEqual((punch, note), (None, "Admin unlocked by Ann"))
        self.assertEqual(self.store.who(), [])                                   # no punches at all
        self.assertEqual(self.deck.keys(1005)[14][0], "BACK")
        self.assertTrue(ADMIN.is_open(1004 + ADMIN.IDLE_SECS - 1))
        self.assertFalse(ADMIN.is_open(1004 + ADMIN.IDLE_SECS))                  # idle timeout
        ADMIN.arm(2000)
        self.assertFalse(ADMIN.waiting(2000 + ADMIN.ARM_SECS))                   # the mentor window expires too

    def open_admin(self, now=1000):
        ADMIN.unlock(now)

    def test_clock_out_all_needs_a_second_press_within_five_seconds(self):
        for i, m in enumerate((self.mentor, self.kid)):
            self.store.toggle(m, 100 + i, 0, location="pit")
        self.open_admin()
        self.deck.press(0, 1001)
        self.assertEqual(self.deck.keys(1002)[0][0], "CONFIRM?")
        self.assertEqual(len(self.store.who()), 2)                                # first press changes nothing
        self.deck.press(0, 1001 + 6)                                              # too late: asks again
        self.assertEqual(len(self.store.who()), 2)
        self.deck.press(0, 1008)
        self.assertEqual(self.store.who(), [])
        self.assertIn(("status", "2 people clocked out"), self.events())

    def test_another_key_cancels_the_confirmation(self):
        self.store.toggle(self.kid, 100, 0)
        self.open_admin()
        self.deck.press(0, 1001)
        self.deck.press(3, 1002)                                                  # audio test
        self.deck.press(0, 1003)                                                  # asks again, does not clock out
        self.assertEqual(len(self.store.who()), 1)

    def test_other_admin_keys_post_events_and_back_closes(self):
        self.open_admin()
        for i in (1, 3, 4):
            self.deck.press(i, 1001)
        self.deck.press(2, 1001)
        self.state["blanked"] = True
        self.deck.press(2, 1001)
        self.assertEqual(self.events(), [("layout_next",), ("speak", "Audio test"), ("info",), ("blank",), ("unblank",)])
        self.deck.press(6, 1001)
        self.assertEqual(self.deck.brightness, 100)
        self.deck.press(14, 1001)
        self.assertEqual(self.deck.keys(1002)[14][0], "ADMIN")                    # back on the main page

    def test_next_layout_wraps_and_layout_file_switches(self):
        import json, layout
        first = next_layout("nonexistent.json")
        seen = [first]
        for _ in range(10):
            nxt = next_layout(seen[-1])
            if nxt == first:
                break
            seen.append(nxt)
        self.assertGreaterEqual(len(seen), 2)                                      # wall + pit at least
        with tempfile.TemporaryDirectory() as d:
            lf = layout.LayoutFile(seen[0])
            bad = os.path.join(d, "bad.json")
            open(bad, "w").write("{nope")
            good = lf.get()
            self.assertEqual(lf.use(bad), good)                                    # a bad layout keeps the one showing
            self.assertEqual(lf.use(seen[1]), json.load(open(seen[1])))

    def test_location_keys_follow_the_displayed_layout(self):
        pit = [{"deck": {"pick": "location"}}, {"handler": "title"},
               {"handler": "here", "opts": {"group_by": "location", "locations": ["pit", "stands"]}}]
        wall = [{"handler": "here"}]
        shown = {"layout": wall}
        deck = Deck(self.store, self.state, self.q, lambda: shown["layout"])
        self.assertEqual(layout_locations(pit), ["pit", "stands"])
        self.assertEqual([k and k[0] for k in deck.keys(1000)[:3]], [None, None, None])   # shop layout: no locations
        self.assertEqual(deck.keys(1000)[14][0], "ADMIN")                                  # admin is still there
        deck.press(0, 1000)
        self.assertIsNone(PENDING.peek())
        shown["layout"] = pit                                                              # layout switched (admin key)
        self.assertEqual([k[0] for k in deck.keys(1001)[:2]], ["pit", "stands"])

    def test_stop_before_start_is_harmless(self):
        import deck as deckmod
        deckmod.stop()
        self.assertTrue(deckmod._stop.is_set())
        deckmod._stop.clear()

    def test_key_images_render(self):
        try:
            import PIL  # noqa: F401
        except ImportError:
            self.skipTest("PIL not installed")
        for spec in self.deck.keys(1000) + [("CLOCK OUT", "all 7", (1, 2, 3), (255, 255, 255))]:
            if spec:
                self.assertEqual(render_key(spec).size, (72, 72))


class WorkTests(unittest.TestCase):
    MS = [f"Milestone {i:02d}" for i in range(14)]

    def setUp(self):
        import queue
        import threading
        ADMIN.armed_until = ADMIN.open_until = 0
        PENDING.loc = WORK.loc = None
        self.store = Store(":memory:")
        self.q = queue.Queue()
        self.state = {"lock": threading.Lock(), "mentors": [], "students": [], "parents": [], "blanked": False}
        self.deck = Deck(self.store, self.state, self.q, lambda: [{"deck": {"pick": "work"}}])
        self.deck._work, self.deck._work_at = (self.MS, {"Milestone 01": ["Fix arm", "Wire it"]}), 1e12
        self.kid = Member("Kid", "kid", "Kid", Role.STUDENT)
        self.store.upsert(self.kid)

    def events(self):
        out = []
        while not self.q.empty():
            out.append(self.q.get_nowait())
        return out

    def test_store_keeps_work_on_the_open_clock_in_only(self):
        self.store.toggle(self.kid, 100, 0, work=("Shop Organization", "Organize Build Space"))
        m = self.store.who()[0][0]
        self.assertEqual((m.milestone, m.task), ("Shop Organization", "Organize Build Space"))
        self.store.set_work("kid", "Practice", None)
        m = self.store.who()[0][0]
        self.assertEqual((m.milestone, m.task), ("Practice", None))
        self.store.toggle(self.kid, 200, 0)                                      # out
        self.store.toggle(self.kid, 300, 0)                                      # in again, nothing picked
        m = self.store.who()[0][0]
        self.assertEqual((m.milestone, m.task), (None, None))

    def test_require_work_move_and_status_text(self):
        import unittest.mock as mock
        with mock.patch.dict(os.environ, {"TVGUI_REQUIRE_WORK": "1"}):
            self.assertEqual(tap(self.store, self.kid, 100), (None, "Pick a project first"))
            WORK.arm(("Shop Organization", "Organize Build Space"))
            punch, note = tap(self.store, self.kid, 101)
            self.assertEqual(punch_status(punch), "Kid badged in · Shop Organization › Organize Build Space")
            WORK.arm(("General", None))
            self.assertEqual(tap(self.store, self.kid, 200), (None, "Kid moved to General"))
            self.assertEqual(self.store.who()[0][0].milestone, "General")
            punch, _ = tap(self.store, self.kid, 300)                             # plain tap clocks out
            self.assertEqual(punch_status(punch), "Kid badged out")

    def test_milestone_keys_task_page_and_whole_milestone(self):
        self.state["students"] = [("a", 0, False, 0, None, "Milestone 01", "Fix arm", "A"), ("b", 0, False, 0, None, "Milestone 01", None, "B")]
        keys = self.deck.keys(1000)
        self.assertEqual((keys[0][0], keys[0][1]), ("Milestone 00", "0"))
        self.assertEqual((keys[1][0], keys[1][1]), ("Milestone 01", "2"))
        self.assertEqual(keys[12][:2], ("NEXT", "1/2"))                           # 14 milestones, 12 per page
        self.assertEqual((keys[13][0], keys[14][0]), ("GENERAL", "ADMIN"))
        self.deck.press(1, 1000)                                                  # has priority tasks: opens its page
        page = self.deck.keys(1001)
        self.assertEqual([k[0] for k in page[:2]], ["Fix arm", "Wire it"])
        self.assertEqual(page[2], None)
        self.assertEqual((page[13][0], page[14][0]), ("WHOLE", "BACK"))
        self.deck.press(1, 1002)                                                  # arm "Wire it"
        self.assertEqual(WORK.peek(), ("Milestone 01", "Wire it"))
        self.assertEqual(self.deck.keys(1003)[1][1], "TAP BADGE")                 # back on the main page, key armed
        self.deck.press(1, 1004)
        self.deck.press(13, 1005)                                                 # WHOLE milestone
        self.assertEqual(WORK.peek(), ("Milestone 01", None))
        self.deck.press(0, 1006)                                                  # no priority tasks: arms directly
        self.assertEqual(WORK.peek(), ("Milestone 00", None))
        self.deck.press(13, 1007)
        self.assertEqual(WORK.peek(), ("General", None))

    def test_long_press_shows_who_is_on_it_and_a_tap_still_arms(self):
        deck = Deck(self.store, self.state, self.q, lambda: [{"deck": {"pick": "work"}}])
        deck._work, deck._work_at = (["Alpha", "Beta"], {"Alpha": ["Fix arm"]}), 1e12
        self.state["students"] = [("Zed", 0, True, 0, None, "Alpha", "Fix arm", "Z"), ("Amy", 0, False, 0, None, "Alpha", None, "A"),
                                  ("Bo", 0, False, 0, None, "Beta", None, "B"), ("Cy", 0, False, 0, None, "General", None, "C")]
        deck.press(0, 1000, long=True)
        self.assertEqual(self.events(), [("overlay", "Alpha", [(None, [("Amy", False), ("Zed", True)])], 10)])
        self.assertIsNone(WORK.peek())                                          # a long press does not arm
        deck.press(13, 1001, long=True)                                         # GENERAL: overview of every milestone
        self.assertEqual(self.events()[0][1:3], ("Who's on what (4 here)",
                         [("Alpha (2)", [("Amy", False), ("Zed", True)]), ("Beta (1)", [("Bo", False)]), ("General (1)", [("Cy", False)])]))
        self.assertEqual(deck.keys(1001)[14][:2], ("CLOSE", "overlay"))        # the overlay can be dismissed from the deck
        deck.press(14, 1001)
        self.assertEqual(self.events(), [("overlay_close",)])
        self.assertEqual(deck.keys(1001)[14][0], "ADMIN")
        deck.press(0, 1001, long=True)
        self.events()
        self.assertEqual(deck.keys(1001 + 11)[14][0], "ADMIN")                 # or it expires on its own
        deck.press(0, 1002)                                                     # a tap on a milestone with tasks opens its page
        deck.press(0, 1003, long=True)
        self.assertEqual(self.events(), [("overlay", "Alpha › Fix arm", [(None, [("Zed", True)])], 10)])
        deck.press(14, 1004, long=True)                                         # overlay is up: the last key is CLOSE
        self.assertEqual(self.events(), [("overlay_close",)])
        deck.press(10, 1004, long=True)                                         # an empty slot is not a pick key
        self.assertEqual(self.events(), [])
        loc = Deck(self.store, self.state, self.q, locations=["pit"])
        self.state["students"] = [("Ann", 0, False, 0, "pit", None, None, "A")]
        loc.press(0, 1005, long=True)
        self.assertEqual(self.events(), [("overlay", "pit", [(None, [("Ann", False)])], 10)])

    def test_overlay_renders_with_few_many_and_no_people(self):
        try:
            import pygame
        except ImportError:
            self.skipTest("pygame not installed")
        from panels import draw_overlay

        pygame.font.init()
        f = pygame.font.Font(None, 30)
        for people in ([], [("Ann", True)], [(f"Person {i}", i % 2 == 0) for i in range(50)]):
            surf = pygame.Surface((1920, 1080))
            draw_overlay(surf, (1920, 1080), "Shop Organization", [(None, people)], f, f)
        many = [(f"Milestone {k} ({k})", [(f"Person {i}", i % 2 == 0) for i in range(k * 3)]) for k in range(12)]
        for groups in (many, [("Empty (0)", [])]):
            draw_overlay(pygame.Surface((1920, 1080)), (1920, 1080), "Who's on what", groups, f, f)     # sections, incl. empty and overflow

    def test_paging_and_task_page_timeout(self):
        self.deck.press(12, 1000)
        keys = self.deck.keys(1001)
        self.assertEqual(keys[0][0], "Milestone 12")
        self.assertEqual(keys[2], None)                                           # 14 milestones: 2 left on page 2
        self.deck.press(12, 1002)
        self.assertEqual(self.deck.keys(1003)[0][0], "Milestone 00")              # wraps to page 1
        self.deck.press(1, 1004)
        self.assertEqual(self.deck.keys(1004 + self.deck.VIEW_SECS + 1)[0][0], "Milestone 00")  # task page timed out

    def test_deck_entry_selects_the_mode_and_resolve_ignores_it(self):
        from layout import resolve
        layout = [{"deck": {"pick": "work"}}, {"handler": "here", "x": 0, "y": 0, "w": 10, "h": 10}]
        self.assertEqual(layout_pick(layout), "work")
        self.assertIsNone(layout_pick([{"handler": "here"}]))
        ctx = type("C", (), {"size": (100, 100)})()
        self.assertEqual([r for _, r in resolve(layout, ctx, lambda n: None)], [(0, 0, 10, 10)])

    def test_initials_from_full_names(self):
        self.assertEqual([initials_of(n) for n in ("Alex Kirby", "Mary Ann Smith", "Cher", "", "ryan m")], ["AK", "MS", "CH", "", "RM"])

    def test_sync_stores_initials_from_the_full_name(self):
        from sync_authentik import sync
        u = {"username": "akirby", "name": "Alex Kirby", "type": "internal", "groups_obj": [{"name": "Students"}], "attributes": {}}
        sync([u], self.store, verbose=False)
        self.assertEqual(self.store.get("akirby").initials, "AK")
        self.assertEqual(self.store.get("akirby").name, "Alex")                    # display name stays the short one

    def test_chips_and_gantt_count_render(self):
        try:
            import pygame
        except ImportError:
            self.skipTest("pygame not installed")
        from panels import _draw_chips, _draw_gantt, _draw_md

        pygame.font.init()
        f = pygame.font.Font(None, 22)
        surf = pygame.Surface((600, 200))
        rect = pygame.Rect(0, 0, 600, 200)
        self.assertEqual(_draw_chips(surf, f, rect, 10, []), 0)
        used = _draw_chips(surf, f, rect, 10, [("AK", True), ("DT", False)])
        self.assertTrue(0 < used < rect.width * 0.5)
        many = _draw_chips(surf, f, rect, 40, [(f"P{i}", False) for i in range(30)])
        self.assertLessEqual(many, rect.width * 0.5 + 20)                          # overflow becomes "+N", never runs off
        items = [("h", "Shop Organization"), ("wip", "Organize Build Space")]
        seen = []
        _draw_md(surf, rect, "priority", items, f, f, lambda kind, text, heading: seen.append((kind, text, heading)) or [("AK", True)])
        self.assertEqual(seen, [("h", "Shop Organization", "Shop Organization"), ("wip", "Organize Build Space", "Shop Organization")])
        plan = {"start": None, "weeks": 4, "title": "", "bars": [("new", "Shop Organization", 1, 3, None, None, 1, None)], "items": []}
        a, b = pygame.Surface((800, 200)), pygame.Surface((800, 200))
        _draw_gantt(a, pygame.Rect(0, 0, 800, 200), plan, f, f, 0)
        _draw_gantt(b, pygame.Rect(0, 0, 800, 200), plan, f, f, 0, {"Shop Organization": 3})
        self.assertNotEqual(pygame.image.tobytes(a, "RGB"), pygame.image.tobytes(b, "RGB"))  # the pill was drawn


if __name__ == "__main__":
    unittest.main()
