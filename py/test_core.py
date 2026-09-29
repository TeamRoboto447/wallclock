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
    is_priority, overdue, render_plan, render_today, roll_values, week_deadline,
)
from tvgui import fmt_total, plan_height, today_pages
from nfc import split_here


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
                ("done", "Gear box 5:1", 1, 2),
                ("new", "Battery Holder", 2, 4),
                ("new", "Quest Mount", 3, 3),
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
            {"headline": "Event", "editFrom": "2026-10-23 19:00:00", "editTo": "2026-10-24 19:00:00"},
            {"headline": "Week A", "editFrom": "2026-09-28 19:00:00", "editTo": "2026-10-04 19:00:00"},
        ]
        self.assertEqual(
            render_plan(ms), "# [ ] Week of 9/28 - Week A\n\n[ ] 10/23 - 10/24 --- Event\n"
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
                         "# [x] Week of 9/21 - Old\n")
        self.assertEqual(render_plan([done], today=datetime.date(2026, 9, 28)), "")
        self.assertEqual(render_plan([event], today=datetime.date(2026, 10, 1)),
                         "[!] 10/23 - 10/24 --- Ev\n")

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

    def test_roll_values_shifts_and_keeps_fields(self):
        m = {"id": 5, "headline": "h", "type": "milestone", "status": 3, "tags": "#1f77b4",
             "dateToFinish": "0000-00-00 00:00:00", "editFrom": "2026-09-14 19:00:00",
             "editTo": "2026-09-20 19:00:00", "timeFrom": None, "description": "d"}
        v = roll_values(m, datetime.date(2026, 9, 30))  # Wed; week ends Sun 10/4
        self.assertEqual((v["editFrom"], v["editTo"]), ("2026-09-28", "2026-10-04"))
        self.assertEqual((v["tags"], v["description"], v["dateToFinish"], v["timeFrom"]), ("#1f77b4", "d", "", ""))
        ev = dict(m, editFrom="2026-09-14 19:00:00", editTo="2026-09-15 19:00:00")  # 1-day event
        v = roll_values(ev, datetime.date(2026, 9, 30))
        self.assertEqual((v["editFrom"], v["editTo"]), ("2026-10-03", "2026-10-04"))

    def test_md_statuses(self):
        text = "- [ ] a\n- [~] b\n- [!] c\n- [x] d\n"
        self.assertEqual([k for k, _ in parse_md(text)], ["todo", "wip", "blocked", "done"])

    def test_today_groups_and_status(self):
        ts = [
            {"id": 2, "type": "task", "status": 0, "tags": "B", "headline": "two"},
            {"id": 1, "type": "task", "status": 3, "tags": "", "headline": "one"},
            {"id": 3, "type": "task", "status": -1, "tags": "B", "headline": "archived"},
            {"id": 4, "type": "milestone", "status": 3, "tags": "", "headline": "ms"},
        ]
        self.assertEqual(render_today(ts), "# Other\n- [ ] one\n\n# B\n- [x] two\n")


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


if __name__ == "__main__":
    unittest.main()
