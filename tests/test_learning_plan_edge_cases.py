"""Edge-case / regression tests for the graduation planner and plan store.

Complements ``test_learning_plan.py`` (happy paths + view). Everything here is
pure logic: no network, no Flet rendering, no real user data directory.

Run from the repo root:  python -m pytest tests/test_learning_plan_edge_cases.py
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from tests._roadmap_stubs import ensure_config, ensure_flet

ensure_flet()
ensure_config()

from core.learning_roadmap.plan_store import PLAN_FILE_NAME, PlanStore  # noqa: E402
from core.learning_roadmap.planner import (  # noqa: E402
    DEFAULT_MAIN_MAX, DEFAULT_SUMMER_MAX, PLAN_SCHEMA, PlanLimits, PlanState, Slot, Term,
    build_plan, move_course, set_limits, set_target, term_from_date, term_range,
)
from tests.test_learning_plan import TODAY, snap  # noqa: E402


# --------------------------------------------------------------------- terms
class TermEdgeTests(unittest.TestCase):
    def test_next_walks_hk1_hk2_summer_then_rolls_the_year(self):
        t = Term(2025, 1)
        chain = [t.key]
        for _ in range(4):
            t = t.next()
            chain.append(t.key)
        self.assertEqual(chain, ["2025-1", "2025-2", "2025-3", "2026-1", "2026-2"])

    def test_ordering_and_ordinal_are_strictly_chronological(self):
        terms = term_range(Term(2024, 1), Term(2026, 3))
        self.assertEqual(terms, sorted(terms))
        ordinals = [t.ordinal for t in terms]
        self.assertEqual(ordinals, list(range(ordinals[0], ordinals[0] + len(terms))))

    def test_from_key_accepts_padding_and_rejects_everything_else(self):
        self.assertEqual(Term.from_key(" 2025-2 "), Term(2025, 2))
        for bad in (None, "", "2025", "2025-0", "2025-4", "25-1", "2025-1-1", "abcd-1", 20251, [], {}):
            with self.subTest(bad=bad):
                self.assertIsNone(Term.from_key(bad))

    def test_labels(self):
        self.assertEqual(Term(2025, 1).short_label, "HK1 2025-2026")
        self.assertEqual(Term(2025, 3).short_label, "Hè 2025-2026")
        self.assertTrue(Term(2025, 3).is_summer)
        self.assertFalse(Term(2025, 2).is_summer)
        self.assertIn("2025-2026", Term(2025, 2).label)


class TermFromDateBoundaryTests(unittest.TestCase):
    CASES = (
        (date(2026, 1, 1), Term(2025, 1)),    # January still belongs to HK1 of the previous year
        (date(2026, 1, 31), Term(2025, 1)),
        (date(2026, 2, 1), Term(2025, 2)),
        (date(2026, 6, 30), Term(2025, 2)),
        (date(2026, 7, 1), Term(2025, 3)),
        (date(2026, 8, 31), Term(2025, 3)),
        (date(2026, 9, 1), Term(2026, 1)),
        (date(2026, 12, 31), Term(2026, 1)),
    )

    def test_calendar_fallback_boundaries(self):
        for day, expected in self.CASES:
            with self.subTest(day=day.isoformat()):
                self.assertEqual(term_from_date(day), expected)


class TermRangeTests(unittest.TestCase):
    def test_inclusive_single_and_reversed(self):
        self.assertEqual([t.key for t in term_range(Term(2025, 2), Term(2026, 1))],
                         ["2025-2", "2025-3", "2026-1"])
        self.assertEqual(term_range(Term(2025, 1), Term(2025, 1)), [Term(2025, 1)])
        self.assertEqual(term_range(Term(2026, 1), Term(2025, 1)), [])


# ------------------------------------------------------------------- limits
class LimitsTests(unittest.TestCase):
    def test_cap_depends_on_term_kind(self):
        lim = PlanLimits(main_max=20, summer_max=9)
        self.assertEqual(lim.cap(Term(2025, 1)), 20)
        self.assertEqual(lim.cap(Term(2025, 2)), 20)
        self.assertEqual(lim.cap(Term(2025, 3)), 9)

    def test_set_limits_rejects_nonsense_and_keeps_defaults(self):
        st = PlanState()
        for bad in (0, -5, 61, 1e9, float("nan"), None, "abc"):
            with self.subTest(bad=bad):
                out = set_limits(st, bad, bad)
                self.assertEqual(out.limits, PlanLimits(DEFAULT_MAIN_MAX, DEFAULT_SUMMER_MAX))

    def test_set_limits_accepts_boundaries_and_numeric_strings(self):
        self.assertEqual(set_limits(PlanState(), 60, 0.5).limits, PlanLimits(60.0, 0.5))
        self.assertEqual(set_limits(PlanState(), "18", "6").limits, PlanLimits(18.0, 6.0))

    def test_setters_never_mutate_their_input(self):
        st = PlanState(target="2027-2", assignments={"a": Slot("2026-2", True)})
        set_limits(st, 10, 5)
        set_target(st, None)
        self.assertEqual(st.limits, PlanLimits())
        self.assertEqual(st.target, "2027-2")
        self.assertEqual(st.assignments, {"a": Slot("2026-2", True)})

    def test_set_target_validates(self):
        st = PlanState()
        self.assertEqual(set_target(st, "2027-1").target, "2027-1")
        for bad in (None, "", "soon", "2027-9"):
            with self.subTest(bad=bad):
                self.assertIsNone(set_target(set_target(st, "2027-1"), bad).target)


# ------------------------------------------------------- state (de)serialise
class PlanStateJsonTests(unittest.TestCase):
    def test_round_trip_is_lossless(self):
        st = PlanState(
            target="2027-2",
            limits=PlanLimits(18, 8),
            assignments={"a": Slot("2026-2", True), "b": Slot(None, True), "c": Slot("2027-1", False)},
        )
        back = PlanState.from_json(json.loads(json.dumps(st.to_json())))
        self.assertEqual(back, st)
        self.assertEqual(st.to_json()["schema"], PLAN_SCHEMA)

    def test_foreign_or_garbage_documents_become_an_empty_plan(self):
        for doc in (None, [], "x", 5, {}, {"schema": PLAN_SCHEMA + 1}, {"schema": "1"}, {"target": "2027-1"}):
            with self.subTest(doc=doc):
                self.assertEqual(PlanState.from_json(doc), PlanState())

    def test_bad_fields_are_dropped_individually_not_all_or_nothing(self):
        out = PlanState.from_json({
            "schema": PLAN_SCHEMA,
            "target": "not-a-term",
            "limits": {"main_max": -1, "summer_max": "7"},
            "assignments": {
                "ok": {"term": "2026-2", "manual": 1},
                "parked": {"term": None, "manual": True},
                "bad_term": {"term": "2026-7", "manual": True},
                "not_a_dict": "2026-2",
                "also_bad": None,
            },
        })
        self.assertIsNone(out.target)
        self.assertEqual(out.limits, PlanLimits(DEFAULT_MAIN_MAX, 7.0))
        self.assertEqual(out.assignments, {"ok": Slot("2026-2", True), "parked": Slot(None, True)})

    def test_non_dict_limits_and_assignments_are_tolerated(self):
        out = PlanState.from_json({"schema": PLAN_SCHEMA, "limits": [1, 2], "assignments": [1]})
        self.assertEqual(out, PlanState())

    def test_copy_is_independent(self):
        a = PlanState(assignments={"x": Slot("2026-2", True)})
        b = a.copy()
        b.assignments["y"] = Slot(None, True)
        b.target = "2027-1"
        self.assertEqual(set(a.assignments), {"x"})
        self.assertIsNone(a.target)


# -------------------------------------------------------------------- store
class PlanStoreEdgeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_missing_file_loads_an_empty_plan(self):
        self.assertEqual(PlanStore(self.dir, owner="sv").load(), PlanState())

    def test_save_writes_one_file_and_leaves_no_temp_files(self):
        store = PlanStore(self.dir, owner="sv")
        self.assertTrue(store.save(PlanState(target="2027-2")))
        self.assertTrue(store.save(PlanState(target="2027-1")))
        self.assertEqual([p.name for p in self.dir.iterdir()], [PLAN_FILE_NAME])
        self.assertEqual(store.load().target, "2027-1")

    def test_saved_file_records_owner_lowercased_and_trimmed(self):
        PlanStore(self.dir, owner="  SV001 ").save(PlanState(target="2027-2"))
        data = json.loads((self.dir / PLAN_FILE_NAME).read_text(encoding="utf-8"))
        self.assertEqual(data["owner"], "sv001")

    def test_set_owner_switches_account_without_leaking(self):
        store = PlanStore(self.dir, owner="a")
        store.save(PlanState(target="2027-2"))
        store.set_owner("b")
        self.assertEqual(store.load(), PlanState())
        store.set_owner("A ")
        self.assertEqual(store.load().target, "2027-2")

    def test_save_failure_is_reported_not_raised(self):
        store = PlanStore(self.dir, owner="sv")
        with mock.patch("core.learning_roadmap.plan_store.SafeFileIO.write_json_atomic",
                        side_effect=OSError("disk full")):
            self.assertFalse(store.save(PlanState()))
        with mock.patch("core.learning_roadmap.plan_store.SafeFileIO.write_json_atomic",
                        return_value=False):
            self.assertFalse(store.save(PlanState()))

    def test_failed_save_does_not_destroy_the_previous_plan(self):
        store = PlanStore(self.dir, owner="sv")
        store.save(PlanState(target="2027-2"))
        with mock.patch("core.learning_roadmap.plan_store.SafeFileIO.write_json_atomic",
                        side_effect=OSError("boom")):
            store.save(PlanState(target="2028-1"))
        self.assertEqual(store.load().target, "2027-2")

    def test_empty_owner_only_matches_files_saved_without_owner(self):
        PlanStore(self.dir, owner="").save(PlanState(target="2027-2"))
        self.assertEqual(PlanStore(self.dir, owner="").load().target, "2027-2")
        self.assertEqual(PlanStore(self.dir, owner="someone").load(), PlanState())


# ------------------------------------------------------ planner invariants
class PlannerInvariantTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = snap()

    def plan(self, state):
        return build_plan(self.snapshot, state, today=TODAY)

    def test_planning_is_deterministic(self):
        st = set_target(PlanState(), "2028-2")
        first = self.plan(st)
        second = self.plan(st)
        self.assertEqual(first[0], second[0])
        self.assertEqual([(t.term.key, t.credits) for t in first[1].terms],
                         [(t.term.key, t.credits) for t in second[1].terms])

    def test_replanning_its_own_output_is_a_fixed_point(self):
        st = set_target(PlanState(), "2028-2")
        planned, _ = self.plan(st)
        again, _ = self.plan(planned)
        self.assertEqual(again.assignments, planned.assignments)

    def test_no_planned_term_ever_exceeds_its_cap_unless_flagged(self):
        st = set_limits(set_target(PlanState(), "2028-2"), 15, 6)
        _, result = self.plan(st)
        for t in result.terms:
            if not t.over_limit:
                self.assertLessEqual(t.credits, st.limits.cap(t.term) + 1e-9, t.term.key)

    def test_every_required_course_is_placed_at_most_once(self):
        st = set_target(PlanState(), "2028-2")
        _, result = self.plan(st)
        keys = [pc.key for pc in result.planned_courses()]
        self.assertEqual(len(keys), len(set(keys)))

    def test_input_state_is_not_mutated_by_planning(self):
        st = set_target(PlanState(), "2028-2")
        before = st.to_json()
        self.plan(st)
        self.assertEqual(st.to_json(), before)


class MoveCourseEdgeTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = snap()
        self.state, self.result = build_plan(
            self.snapshot, set_target(PlanState(), "2028-2"), today=TODAY)
        self.key = self.result.planned_courses()[0].key

    def move(self, key, term_key, state=None):
        return move_course(self.snapshot, state or self.state, key, term_key, today=TODAY)

    def test_unknown_course_is_rejected_and_state_returned_unchanged(self):
        new, err = self.move("does-not-exist", "2028-1")
        self.assertTrue(err)
        self.assertIs(new, self.state)

    def test_malformed_term_is_rejected(self):
        for bad in ("soon", "2028-9", ""):
            with self.subTest(bad=bad):
                new, err = self.move(self.key, bad)
                self.assertTrue(err)
                self.assertIs(new, self.state)

    def test_none_parks_the_course_manually(self):
        new, err = self.move(self.key, None)
        self.assertIsNone(err)
        self.assertEqual(new.assignments[self.key], Slot(None, manual=True))

    def test_parking_twice_is_idempotent(self):
        once, _ = self.move(self.key, None)
        twice, err = self.move(self.key, None, state=once)
        self.assertIsNone(err)
        self.assertEqual(twice.assignments, once.assignments)

    def test_a_rejected_move_never_changes_the_saved_assignments(self):
        before = dict(self.state.assignments)
        self.move(self.key, "1999-1")
        self.assertEqual(self.state.assignments, before)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
