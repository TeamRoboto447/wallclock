import datetime
import hashlib
import os
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
    blocking_ids, is_priority, overdue, plan_moves, render_plan, render_today,
    roll_values, week_deadline,
)
from tvgui import fmt_total, plan_height, today_pages
from nfc import split_here
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

    def test_totals_reset_at_january_first(self):
        store = Store(":memory:")
        m = Member.new("Jane Doe", "jdoe")
        jan1 = year_start(datetime.datetime(2026, 6, 1).timestamp())
        self.assertEqual(jan1, int(datetime.datetime(2026, 1, 1).timestamp()))
        for ts in (jan1 - 3600, jan1 + 1800, jan1 + 5000, jan1 + 6000):
            store.toggle(m, ts)  # session across new year (1800s counts), then 1000s
        self.assertEqual(store.closed_secs(jan1), {"jdoe": 1800 + 1000})
        self.assertEqual(store.closed_secs(), {"jdoe": 5400 + 1000})

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

    def test_dependency_cycle_terminates(self):
        today = datetime.date(2026, 9, 30)
        ms = [{"id": 1, "type": "milestone", "status": 3, "editFrom": "2026-09-14", "editTo": "2026-09-20", "dependingTicketId": 2},
              {"id": 2, "type": "milestone", "status": 3, "editFrom": "2026-09-14", "editTo": "2026-09-20", "dependingTicketId": 1}]
        self.assertEqual(set(plan_moves(ms, today)), {1, 2})

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
    def test_today_pages_one_per_tag_and_paginate(self):
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

    def test_today_pages_empty(self):
        self.assertEqual(today_pages([], 5), [])

    def test_plan_height_scales_and_caps(self):
        bars = lambda n: {"bars": [("new", "x", 1, 1)] * n}
        self.assertEqual(plan_height(bars(2), 400), 48 + 2 * 32 + 12)
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


if __name__ == "__main__":
    unittest.main()
