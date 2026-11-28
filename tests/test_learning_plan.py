"""Tests for the graduation planner, plan store and the plan tab of the view.

Run from the repo root:  python -m pytest tests/test_learning_plan.py
"""
from __future__ import annotations

import json
import tempfile
import types
import unittest
from datetime import date, datetime
from pathlib import Path

from tests._roadmap_stubs import ensure_config, ensure_flet  # noqa: E402

ensure_flet()
ensure_config()
from tests.test_learning_roadmap import course, sem  # noqa: E402

from core.learning_roadmap.builder import build_snapshot  # noqa: E402
from core.learning_roadmap.models import CourseStatus  # noqa: E402
from core.learning_roadmap.plan_store import PlanStore  # noqa: E402
from core.learning_roadmap.planner import (  # noqa: E402
    PlanLimits, PlanState, Slot, Term, build_plan, move_course, parse_term,
    set_limits, set_target, term_from_date,
)
from core.learning_roadmap.service import SOURCE_NETWORK, RoadmapLoadResult  # noqa: E402
from gui.components.learning_roadmap_view import LearningRoadmapView  # noqa: E402

TODAY = date(2026, 11, 25)          # -> HK1 2026-2027


# ------------------------------------------------------------------ fixtures
def big_bundle(n_courses=30, credits=3, running=0, electives=0):
    """``n_courses`` required courses spread over curriculum semesters 1-8,
    last transcript term = HK2 2025-2026 (all finished)."""
    groups = {}
    for i in range(n_courses):
        groups.setdefault(i * 8 // n_courses + 1, []).append(
            {"maHocPhan": f"C{i:03d}", "tenMonHoc": f"Mon {i}", "soTinChi": credits}
        )
    for j in range(electives):
        groups.setdefault(8, []).append(
            {"maHocPhan": f"E{j:03d}", "tenMonHoc": f"Tu chon {j}", "soTinChi": 2, "batBuoc": False}
        )
    curriculum = [{"hocKy": k, "monHocs": v} for k, v in sorted(groups.items())]
    current = {}
    transcript = [sem(75, "HK2 2025-2026", [])]
    if running:
        current = {"id": 76, "name": "HK1 2026-2027", "courses": [
            {"maHocPhan": f"C{i:03d}", "tenMonHoc": f"Mon {i}", "soTinChi": credits}
            for i in range(running)]}
        transcript.append(sem(76, "HK1 2026-2027", []))
    return {
        "bodies": {"curriculum": curriculum},
        "current_semester": current,
        "transcript": transcript,
        "errors": {},
    }


def snap(**kw):
    return build_snapshot(big_bundle(**kw))


def plan(snapshot, state=None, **kw):
    return build_plan(snapshot, state or PlanState(), today=TODAY, **kw)


def load(result):
    return {t.term.key: t.credits for t in result.terms}


# --------------------------------------------------------------------- terms
class TermTests(unittest.TestCase):
    def test_parse_common_portal_names(self):
        self.assertEqual(parse_term("HK1 2025-2026"), Term(2025, 1))
        self.assertEqual(parse_term("Học kỳ 2 - Năm học 2025-2026"), Term(2025, 2))
        self.assertEqual(parse_term("Học kỳ hè 2025-2026"), Term(2025, 3))
        self.assertEqual(parse_term("HK Hè 2025 - 2026"), Term(2025, 3))
        self.assertEqual(parse_term("Học kỳ 3 (2025-2026)"), Term(2025, 3))

    def test_parse_does_not_mistake_he_thong_for_summer(self):
        self.assertEqual(parse_term("Học kỳ 1 hệ chính quy 2025-2026"), Term(2025, 1))

    def test_parse_gives_up_instead_of_guessing(self):
        for text in ("", None, "Học kỳ 1", "2025-2026", "foo"):
            self.assertIsNone(parse_term(text), text)

    def test_order_and_next(self):
        self.assertEqual(Term(2025, 1).next(), Term(2025, 2))
        self.assertEqual(Term(2025, 2).next(), Term(2025, 3))      # summer follows HK2
        self.assertEqual(Term(2025, 3).next(), Term(2026, 1))
        self.assertLess(Term(2025, 3), Term(2026, 1))
        self.assertEqual(Term.from_key("2026-2"), Term(2026, 2))
        self.assertIsNone(Term.from_key("2026-9"))

    def test_calendar_fallback(self):
        self.assertEqual(term_from_date(date(2026, 11, 25)), Term(2026, 1))
        self.assertEqual(term_from_date(date(2027, 1, 10)), Term(2026, 1))
        self.assertEqual(term_from_date(date(2027, 3, 1)), Term(2026, 2))
        self.assertEqual(term_from_date(date(2027, 7, 15)), Term(2026, 3))


# ------------------------------------------------------------------ planning
class PlannerTests(unittest.TestCase):
    def test_snapshot_exposes_anchor_and_history(self):
        s = snap(running=2)
        self.assertEqual(s.anchor_term, "2026-1")
        self.assertEqual(s.entry_year, 2025)
        # no HK1 transcript here: the finished main term is still listed (empty)
        self.assertEqual([h.label for h in s.history], ["Học kỳ 2 · 2025-2026"])
        self.assertEqual(s.history[0].courses, ())

    def test_no_target_means_everything_is_unplaced(self):
        _, r = plan(snap())
        self.assertFalse(r.has_target)
        self.assertEqual(len(r.unplaced), 30)
        self.assertEqual(r.outstanding_credits, 90)
        self.assertEqual(r.terms, ())

    def test_target_spreads_load_evenly_and_under_cap(self):
        st, r = plan(snap(), PlanState(target="2027-2"))       # HK1+HK2 2026 and HK1+HK2 2027
        self.assertTrue(r.feasible)
        self.assertEqual(r.unplaced, ())
        main = [t.credits for t in r.terms if not t.term.is_summer]
        self.assertEqual(len(main), 4)
        self.assertEqual(sum(t.credits for t in r.terms), 90)
        self.assertTrue(all(c <= 24 for c in main), main)
        self.assertLessEqual(max(main) - min(main), 3, main)      # balanced, not 24/24/24/18
        self.assertEqual(r.terms[2].courses, ())                  # the summer in between stays free

    def test_target_too_soon_reports_shortfall_and_earliest(self):
        _, r = plan(snap(), PlanState(target="2026-2"))        # 2 terms x 24 = 48 < 90
        self.assertFalse(r.feasible)
        self.assertGreater(r.shortfall_credits, 0)
        self.assertTrue(r.unplaced)
        self.assertIsNotNone(r.earliest_target)
        self.assertGreater(r.earliest_target, Term(2026, 2))
        # ...and that earliest target really is feasible.
        _, ok = plan(snap(), PlanState(target=r.earliest_target.key))
        self.assertTrue(ok.feasible)

    def test_summer_is_used_only_when_main_terms_are_not_enough(self):
        # 57 TC: two main terms (48) are not enough, so the target summer is used
        s = snap(n_courses=19)
        _, r = plan(s, PlanState(target="2026-3"))
        self.assertTrue(r.feasible)
        summer = next(t for t in r.terms if t.term.is_summer)
        self.assertTrue(summer.courses)
        self.assertLessEqual(summer.credits, 12)
        # a plan that fits in main terms leaves the intermediate summer empty
        _, r2 = plan(snap(n_courses=10), PlanState(target="2027-2"))
        mid_summer = next(t for t in r2.terms if t.term == Term(2026, 3))
        self.assertEqual(mid_summer.courses, ())

    def test_limits_are_respected_and_editable(self):
        st = set_target(PlanState(), "2027-2")
        st = set_limits(st, 15, 6)
        _, r = plan(snap(), st)
        self.assertTrue(all(t.credits <= 15 for t in r.terms))
        self.assertFalse(r.feasible)                           # 4 x 15 = 60 < 90

    def test_retakes_are_placed_before_new_courses(self):
        b = big_bundle()
        b["transcript"] = [sem(75, "HK2 2025-2026", [course("C029", "Mon 29", 3, 2.0, "F")])]
        _, r = plan(build_snapshot(b), PlanState(target="2027-2"))
        first = r.terms[0]
        self.assertIn("C029", [p.course.code for p in first.courses])

    def test_current_term_comes_first_and_is_locked(self):
        s = snap(running=3)
        _, r = plan(s, PlanState(target="2027-2"))
        cur = r.terms[0]
        self.assertTrue(cur.is_current)
        self.assertEqual(cur.term, Term(2026, 1))
        self.assertTrue(all(p.locked for p in cur.courses))
        self.assertEqual(len(cur.courses), 3)
        self.assertEqual(r.first_plan_term, Term(2026, 2))
        self.assertEqual([t.term for t in r.terms[1:]], [Term(2026, 2), Term(2026, 3), Term(2027, 1), Term(2027, 2)])
        # running courses are not planned again
        planned = [p.course.code for t in r.terms[1:] for p in t.courses]
        self.assertFalse({"C000", "C001", "C002"} & set(planned))

    def test_electives_and_met_requirements_are_not_auto_placed(self):
        s = snap(electives=3)
        _, r = plan(s, PlanState(target="2027-2"))
        self.assertEqual(len(r.optional), 3)
        placed = {p.course.code for p in r.planned_courses()}
        self.assertFalse(any(c.startswith("E") for c in placed))
        self.assertEqual(r.outstanding_credits, 90)             # electives excluded

    def test_target_before_the_first_plannable_term_is_dropped(self):
        st, r = plan(snap(), PlanState(target="2025-1"))
        self.assertFalse(r.has_target)
        self.assertIsNone(st.target)
        self.assertTrue(r.notes)

    def test_graduating_right_after_current_term(self):
        s = snap(n_courses=2, running=2)
        _, r = plan(s, PlanState(target="2026-1"))
        self.assertTrue(r.feasible)
        self.assertEqual(len(r.terms), 1)
        self.assertTrue(r.terms[0].is_final)


# ------------------------------------------------------------------- editing
class EditingTests(unittest.TestCase):
    def setUp(self):
        self.s = snap()
        self.st, self.r = plan(self.s, PlanState(target="2027-2"))

    def test_manual_move_is_kept_across_replans(self):
        key = self.r.terms[0].courses[0].key
        new, err = move_course(self.s, self.st, key, "2027-2", today=TODAY)
        self.assertIsNone(err)
        st2, r2 = plan(self.s, new, reset_auto=True)           # rebalance everything auto
        self.assertEqual(st2.assignments[key], Slot("2027-2", True))
        self.assertIn(key, [p.key for p in r2.terms[-1].courses])
        self.assertTrue(next(p for p in r2.terms[-1].courses if p.key == key).manual)

    def test_move_over_the_cap_is_refused_with_a_message(self):
        # fill HK1 2026 to the brim by hand, then try one more
        st = PlanState(target="2027-2")
        keys = [p.key for t in self.r.terms for p in t.courses]
        for k in keys[:8]:                                       # 8 x 3 = 24
            st, err = move_course(self.s, st, k, "2026-1", today=TODAY)
            self.assertIsNone(err)
        st9, err = move_course(self.s, st, keys[8], "2026-1", today=TODAY)
        self.assertIn("vượt giới hạn 24", err)
        self.assertIs(st9, st)                                   # unchanged

    def test_cannot_move_into_the_past_or_past_the_target(self):
        key = self.r.terms[0].courses[0].key
        self.assertIn("đã qua", move_course(self.s, self.st, key, "2025-2", today=TODAY)[1])
        self.assertIn("mốc ra trường", move_course(self.s, self.st, key, "2028-1", today=TODAY)[1])
        self.assertIn("Học kỳ không hợp lệ", move_course(self.s, self.st, key, "zzz", today=TODAY)[1])

    def test_parked_course_is_not_auto_placed_again(self):
        key = self.r.terms[0].courses[0].key
        parked, err = move_course(self.s, self.st, key, None, today=TODAY)
        self.assertIsNone(err)
        st2, r2 = plan(self.s, parked, reset_auto=True)
        self.assertIn(key, [p.key for p in r2.unplaced])
        self.assertNotIn(key, [p.key for p in r2.planned_courses()])
        self.assertFalse(r2.feasible)                            # something is still unplaced

    def test_changing_target_earlier_releases_and_replans(self):
        s = snap(n_courses=24)                                  # 72 TC = exactly 3 main terms
        st0, r0 = plan(s, PlanState(target="2027-2"))
        key = r0.terms[0].courses[0].key
        st, err = move_course(s, st0, key, "2027-2", today=TODAY)
        self.assertIsNone(err)
        earlier = set_target(st, "2027-1")
        st2, r2 = plan(s, earlier, reset_auto=True)
        self.assertEqual(r2.target, Term(2027, 1))
        self.assertTrue(r2.feasible)
        self.assertLessEqual(Term.from_key(st2.assignments[key].term), Term(2027, 1))
        self.assertFalse(st2.assignments[key].manual)           # the pin was released
        self.assertTrue(any("sau mốc" in n for n in r2.notes))

    def test_changing_target_later_lightens_the_load(self):
        later = set_target(self.st, "2028-2")
        _, r = plan(self.s, later, reset_auto=True)
        self.assertLess(max(t.credits for t in r.terms), max(t.credits for t in self.r.terms))

    def test_missed_registration_rolls_course_back_into_the_plan(self):
        # plan made earlier put C000 in HK2 2025-2026; that term is over and it was never taken
        key = "c000"
        old = PlanState(target="2027-2", assignments={key: Slot("2025-2", manual=True)})
        st, r = plan(self.s, old)
        self.assertNotEqual(st.assignments[key].term, "2025-2")
        placed = next(p for p in r.planned_courses() if p.key == key)
        self.assertIn("Trượt từ HK2 2025-2026", placed.note)
        self.assertTrue(any("chưa được học" in n for n in r.notes))
        # the roll is persisted into the state, so the note is shown once
        _, r2 = plan(self.s, st)
        self.assertEqual(r2.notes, ())

    def test_course_that_gets_registered_leaves_the_plan(self):
        running = snap(running=1)                              # C000 is now being taken
        st = PlanState(target="2027-2", assignments={"c000": Slot("2026-2", False)})
        st2, r = plan(running, st)
        self.assertNotIn("c000", st2.assignments)
        self.assertNotIn("c000", [p.key for t in r.terms[1:] for p in t.courses])

    def test_completed_course_cannot_be_moved(self):
        b = big_bundle()
        b["transcript"] = [sem(75, "HK2 2025-2026", [course("C000", "Mon 0", 3, 8.0, "B+")])]
        s = build_snapshot(b)
        _, err = move_course(s, PlanState(target="2027-2"), "c000", "2026-1", today=TODAY)
        self.assertIn("không còn cần xếp", err)

    def test_reset_all_forgets_manual_choices(self):
        key = self.r.terms[0].courses[0].key
        st, _ = move_course(self.s, self.st, key, "2027-2", today=TODAY)
        st2, _ = plan(self.s, st, reset_auto=True, reset_all=True)
        self.assertFalse(st2.assignments[key].manual)


# --------------------------------------------------------------- persistence
class StoreTests(unittest.TestCase):
    def test_round_trip_and_owner_isolation(self):
        with tempfile.TemporaryDirectory() as d:
            store = PlanStore(Path(d), owner="SV001")
            st = PlanState(target="2027-2", limits=PlanLimits(20, 9),
                           assignments={"c1": Slot("2026-2", True), "c2": Slot(None, True)})
            self.assertTrue(store.save(st))
            back = store.load()
            self.assertEqual(back.target, "2027-2")
            self.assertEqual(back.limits, PlanLimits(20, 9))
            self.assertEqual(back.assignments, st.assignments)
            self.assertEqual(PlanStore(Path(d), owner="sv001").load().target, "2027-2")   # case-insens.
            self.assertIsNone(PlanStore(Path(d), owner="other").load().target)

    def test_corrupt_or_foreign_files_yield_an_empty_plan(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "learning_plan.json"
            for junk in ("not json", "[]", json.dumps({"schema": 99}),
                         json.dumps({"schema": 1, "owner": "", "target": "bad",
                                     "limits": {"main_max": -5, "summer_max": "x"},
                                     "assignments": {"a": {"term": "zz"}, "b": 3,
                                                     "c": {"term": "2026-1", "manual": 1}}})):
                f.write_text(junk, encoding="utf-8")
                st = PlanStore(Path(d)).load()
                self.assertIsNone(st.target)
                self.assertEqual(st.limits, PlanLimits())
                self.assertLessEqual(set(st.assignments), {"c"})


# ---------------------------------------------------------------------- view
class PlanViewTests(unittest.TestCase):
    def make(self, **bundle_kw):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = PlanStore(Path(self.tmp.name), owner="sv")
        self.view = LearningRoadmapView(plan_store=self.store, today=lambda: TODAY)
        res = RoadmapLoadResult(snap(**bundle_kw), SOURCE_NETWORK, datetime(2026, 11, 25, 9, 0))
        self.view.show_result(res)
        return self.view

    def test_prompt_when_no_target_then_target_builds_and_saves_plan(self):
        v = self.make()
        self.assertEqual(v._tab, "plan")
        self.assertFalse(v._plan.has_target)
        v._apply_target("2027-2")
        self.assertTrue(v._plan.feasible)
        self.assertEqual(len(v._plan.terms), 5)                # 4 main terms + the summer between
        self.assertEqual(self.store.load().target, "2027-2")           # persisted
        self.assertTrue(self.store.load().assignments)

    def test_plan_survives_reopening_the_view(self):
        v = self.make()
        v._apply_target("2027-2")
        key = v._plan.terms[0].courses[0].key
        self.assertEqual(v._move(key, "2027-2"), "")
        v2 = LearningRoadmapView(plan_store=self.store, today=lambda: TODAY)
        v2.show_result(RoadmapLoadResult(snap(), SOURCE_NETWORK, datetime.now()))
        self.assertEqual(v2._plan.target, Term(2027, 2))
        self.assertIn(key, [p.key for p in v2._plan.terms[-1].courses])

    def test_current_term_is_rendered_first(self):
        v = self.make(running=3)
        v._apply_target("2027-2")
        self.assertTrue(v._plan.terms[0].is_current)
        self.assertEqual(v._plan.terms[0].term, Term(2026, 1))
        # goal card, then the current-term tile before any upcoming term
        self.assertGreaterEqual(len(v._plan_box.controls), 6)

    def test_rejected_move_shows_a_notice_and_changes_nothing(self):
        v = self.make()
        v._apply_target("2027-2")
        v._apply_limits(3, 3)                                  # tiny caps: one course per term
        first, other = v._plan.terms[0], v._plan.terms[1]
        self.assertEqual(len(first.courses), 1)
        saved = self.store.load().to_json()
        err = v._move(other.courses[0].key, first.term.key)
        self.assertIn("vượt giới hạn", err)
        self.assertIn("vượt giới hạn", v._plan_notice)
        self.assertEqual(self.store.load().to_json(), saved)   # nothing changed on disk

    def test_limits_change_replans_and_persists(self):
        v = self.make()
        v._apply_target("2028-2")
        v._apply_limits(15, 6)
        self.assertEqual(self.store.load().limits, PlanLimits(15, 6))
        self.assertTrue(all(t.credits <= 15 for t in v._plan.terms))

    def test_tabs_toggle_visibility_and_keep_framework_list(self):
        v = self.make()
        self.assertTrue(v._plan_box.visible and not v._framework_box.visible)
        v._set_tab("framework")
        self.assertTrue(v._framework_box.visible and not v._plan_box.visible)
        self.assertEqual(len(v._chips_row.controls), 6)

    def test_account_switch_does_not_leak_plans(self):
        v = self.make()
        v._apply_target("2027-2")
        v.set_plan_owner("someone-else")
        v.show_result(RoadmapLoadResult(snap(), SOURCE_NETWORK, datetime.now()))
        self.assertFalse(v._plan.has_target)

    def test_dialogs_open_without_a_page(self):
        v = self.make()
        v._open_target_dialog()
        v._apply_target("2027-2")
        v._open_limits_dialog()
        v._open_add_dialog("2026-1")
        v._close_dialog()

    def test_history_rendered_after_the_plan(self):
        b = big_bundle(running=2)
        b["transcript"].insert(0, sem(74, "HK1 2025-2026",
                                      [course("X1", "Mon da hoc", 3, 8.0, "B+")]))
        v = LearningRoadmapView(plan_store=PlanStore(Path(tempfile.mkdtemp()), "a"), today=lambda: TODAY)
        v.show_result(RoadmapLoadResult(build_snapshot(b), SOURCE_NETWORK, datetime.now()))
        # HK1 has courses; HK2 2025-2026 had none but is still listed
        self.assertEqual([h.label for h in v._snapshot.history],
                         ["Học kỳ 2 · 2025-2026", "HK1 2025-2026"])


if __name__ == "__main__":
    unittest.main()
