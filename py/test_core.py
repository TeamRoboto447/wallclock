import datetime
import hashlib
import os
import re
import tempfile
import unittest

from attendance import IN, OUT, Store, year_start
from member import Member, Role
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
from nfc import _run_fake, split_here
from layout import LayoutFile, resolve
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
        self.assertEqual(students, [("Jane Doe", b + 10000, False, 3600)])
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
    """The default screen must not change unless py/golden/wall.png is regenerated on purpose:
    .venv/bin/python py/tvgui.py render py/golden/wall.png (pixels depend on this machine's FreeSans/pygame)."""

    def test_default_screen_matches_golden(self):
        try:
            import pygame
        except ImportError:
            self.skipTest("pygame not installed")
        from fixture import render

        golden = pygame.image.load(os.path.join(os.path.dirname(__file__), "golden", "wall.png"))
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
            self.assertEqual(LayoutFile(os.path.join(d, "missing.json")).get()[0]["handler"], "title")  # default

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


if __name__ == "__main__":
    unittest.main()
