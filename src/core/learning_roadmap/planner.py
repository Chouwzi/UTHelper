"""Graduation planner: turns "when do I want to graduate" into a per-term plan.

Pure functions, no I/O, no Flet. The flow is:

1. The user picks a *target term* (last term in which they still study - a main
   semester or a summer term). ``PlanState.target`` stores it.
2. ``build_plan`` reconciles the saved plan with the freshest ``RoadmapSnapshot``
   (finished / in-progress courses leave the plan, terms that already passed
   without the course being taken roll the course back into the pool), then
   auto-fills whatever is still unplaced, balancing load across the terms that
   remain and respecting the per-term credit caps.
3. Anything the user moves by hand is marked ``manual`` and survives every
   later re-plan; "auto" placements are recomputed when the target or limits
   change. A course the user explicitly removes from the plan is "parked" and
   never auto-placed again until they put it somewhere.

Terms
-----
An academic year ``Y`` (``Y-Y+1``) has three terms: ``Y-1`` (HK1), ``Y-2``
(HK2) and ``Y-3`` (summer, taken after HK2). Keys sort chronologically via
``Term`` ordering.

Limits (UTH "Quy chế đào tạo tín chỉ", Điều 10): at most 24 credits per main
term (14 while the student is academically weak) and at least 12 (8 when weak),
except the final term. The regulation sets no minimum for summer terms and no
explicit summer maximum, so the summer cap here is a configurable default.
Both caps are editable by the user and stored with the plan.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Optional

from core.learning_roadmap.models import (
    CourseStatus,
    RequirementStatus,
    RoadmapCourse,
    RoadmapSnapshot,
)
from core.learning_roadmap.parsers import base_code, fold, fold_key

SLOT_HK1, SLOT_HK2, SLOT_SUMMER = 1, 2, 3

DEFAULT_MAIN_MAX = 24.0
DEFAULT_SUMMER_MAX = 12.0
MIN_MAIN_CREDITS = 12.0          # soft minimum per main term (not the last one)
MAX_TARGET_HORIZON = 12          # how many terms ahead a target may be chosen
PLAN_SCHEMA = 1


# ===================================================================== terms
@dataclass(frozen=True, order=True)
class Term:
    year: int    # start year of the academic year: 2025 for "2025-2026"
    slot: int    # 1 = HK1, 2 = HK2, 3 = summer

    @property
    def key(self) -> str:
        return f"{self.year}-{self.slot}"

    @property
    def is_summer(self) -> bool:
        return self.slot == SLOT_SUMMER

    @property
    def ordinal(self) -> int:
        return self.year * 3 + (self.slot - 1)

    @property
    def year_span(self) -> str:
        return f"{self.year}-{self.year + 1}"

    @property
    def label(self) -> str:
        name = "Học kỳ hè" if self.is_summer else f"Học kỳ {self.slot}"
        return f"{name} · {self.year_span}"

    @property
    def short_label(self) -> str:
        name = "Hè" if self.is_summer else f"HK{self.slot}"
        return f"{name} {self.year_span}"

    def next(self) -> "Term":
        if self.slot >= SLOT_SUMMER:
            return Term(self.year + 1, SLOT_HK1)
        return Term(self.year, self.slot + 1)

    @staticmethod
    def from_key(key: Any) -> Optional["Term"]:
        m = re.fullmatch(r"\s*(\d{4})-([123])\s*", str(key or ""))
        return Term(int(m.group(1)), int(m.group(2))) if m else None


def term_from_date(today: date) -> Term:
    """Calendar fallback: HK1 Sep-Jan, HK2 Feb-Jun, summer Jul-Aug."""
    m, y = today.month, today.year
    if m >= 9:
        return Term(y, SLOT_HK1)
    if m == 1:
        return Term(y - 1, SLOT_HK1)
    if m <= 6:
        return Term(y - 1, SLOT_HK2)
    return Term(y - 1, SLOT_SUMMER)


def parse_term(text: Any) -> Optional[Term]:
    """Read a Portal semester name ("HK1 2025-2026", "Học kỳ 2 - Năm học
    2025-2026", "Học kỳ hè 2025-2026") into a ``Term``; ``None`` if unclear."""
    f = fold(text)
    ym = re.search(r"\b(20\d{2}) (20\d{2})\b", f)
    if not ym:
        return None
    year = int(ym.group(1))
    if re.search(r"\b(?:hoc ky|ky|hk) ?(?:he|phu)\b|summer|\bhe 20\d{2}", f):
        return Term(year, SLOT_SUMMER)
    sm = re.search(r"\b(?:hoc ky|ky|hk) ?([123])\b", f)
    return Term(year, int(sm.group(1))) if sm else None


def term_range(start: Term, end: Term) -> list[Term]:
    out: list[Term] = []
    t = start
    while t <= end:
        out.append(t)
        t = t.next()
    return out


# ============================================================= state (saved)
@dataclass(frozen=True)
class PlanLimits:
    main_max: float = DEFAULT_MAIN_MAX
    summer_max: float = DEFAULT_SUMMER_MAX

    def cap(self, term: Term) -> float:
        return self.summer_max if term.is_summer else self.main_max


@dataclass(frozen=True)
class Slot:
    """Where one course sits in the plan. ``term=None`` means "parked": the
    user took it out of the plan and it must not be auto-placed."""

    term: Optional[str]
    manual: bool = False


@dataclass
class PlanState:
    target: Optional[str] = None
    limits: PlanLimits = field(default_factory=PlanLimits)
    assignments: dict[str, Slot] = field(default_factory=dict)

    def copy(self) -> "PlanState":
        return PlanState(self.target, self.limits, dict(self.assignments))

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": PLAN_SCHEMA,
            "target": self.target,
            "limits": {"main_max": self.limits.main_max, "summer_max": self.limits.summer_max},
            "assignments": {
                k: {"term": s.term, "manual": s.manual} for k, s in self.assignments.items()
            },
        }

    @staticmethod
    def from_json(data: Any) -> "PlanState":
        """Tolerant: corrupt or foreign data yields an empty plan, never raises."""
        if not isinstance(data, dict) or data.get("schema") != PLAN_SCHEMA:
            return PlanState()
        target = data.get("target")
        target = target if Term.from_key(target) else None
        lim = data.get("limits") if isinstance(data.get("limits"), dict) else {}
        limits = PlanLimits(
            main_max=_positive(lim.get("main_max"), DEFAULT_MAIN_MAX),
            summer_max=_positive(lim.get("summer_max"), DEFAULT_SUMMER_MAX),
        )
        out: dict[str, Slot] = {}
        raw = data.get("assignments")
        for key, val in (raw.items() if isinstance(raw, dict) else []):
            if not isinstance(key, str) or not isinstance(val, dict):
                continue
            term = val.get("term")
            if term is not None and Term.from_key(term) is None:
                continue
            out[key] = Slot(term, bool(val.get("manual")))
        return PlanState(target, limits, out)


def _positive(value: Any, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if 0 < v <= 60 else default


def course_key(course: RoadmapCourse) -> str:
    """Stable identity of a course across refreshes."""
    return fold_key(base_code(course.code)) or fold_key(course.name)


# ================================================================= the result
@dataclass(frozen=True)
class PlannedCourse:
    course: RoadmapCourse
    key: str
    manual: bool = False
    locked: bool = False          # in progress right now: cannot be moved
    note: str = ""                # e.g. "Trượt từ HK1 2026-2027"


@dataclass(frozen=True)
class PlanTerm:
    term: Term
    is_current: bool
    max_credits: float
    courses: tuple[PlannedCourse, ...] = ()
    is_final: bool = False        # the target term
    below_min: bool = False

    @property
    def credits(self) -> float:
        return sum(p.course.credits for p in self.courses)

    @property
    def over_limit(self) -> bool:
        return self.credits > self.max_credits + 1e-9

    @property
    def free_credits(self) -> float:
        return max(0.0, self.max_credits - self.credits)


@dataclass(frozen=True)
class PlanResult:
    terms: tuple[PlanTerm, ...]                    # current first, then chronological
    unplaced: tuple[PlannedCourse, ...]            # required, not in any term
    optional: tuple[PlannedCourse, ...]            # electives, never auto-placed
    target: Optional[Term]
    target_options: tuple[Term, ...]
    first_plan_term: Term
    outstanding_credits: float                     # required credits still to take
    shortfall_credits: float                       # of those, not placeable by the target
    feasible: bool
    earliest_target: Optional[Term]
    entry_year: Optional[int]
    notes: tuple[str, ...] = ()

    @property
    def has_target(self) -> bool:
        return self.target is not None

    def year_no(self, term: Term) -> Optional[int]:
        """1-based study year of ``term`` (summer counts with its year)."""
        return term.year - self.entry_year + 1 if self.entry_year else None

    def target_label(self, term: Term) -> str:
        n = self.year_no(term)
        return term.label + (f" · năm {n}" if n and n >= 1 else "")

    def planned_courses(self) -> list[PlannedCourse]:
        return [p for t in self.terms for p in t.courses]


# =============================================================== candidate set
def _auto_exempt_keys(snapshot: RoadmapSnapshot, courses: Iterable[RoadmapCourse]) -> set[str]:
    """Conditional courses (English / IT / GDQP / GDTC) whose graduation
    requirement is already met by a certificate or Portal verdict are not
    something the student still has to schedule."""
    from core.learning_roadmap.builder import REQUIREMENT_SPECS, _matches_spec

    met = {r.key for r in snapshot.requirements if r.status is RequirementStatus.MET}
    out: set[str] = set()
    for c in courses:
        if not c.is_conditional:
            continue
        if any(spec.key in met and _matches_spec(spec, c) for spec in REQUIREMENT_SPECS):
            out.add(course_key(c))
    return out


@dataclass(frozen=True)
class _Cand:
    key: str
    course: RoadmapCourse
    order: tuple            # sort key: retakes first, then curriculum order
    required: bool


def _candidates(snapshot: RoadmapSnapshot) -> tuple[list[_Cand], list[RoadmapCourse]]:
    """(outstanding courses, in-progress courses)."""
    seen: set[str] = set()
    cands: list[_Cand] = []
    running: list[RoadmapCourse] = []
    index = 0
    all_courses = snapshot.all_courses()
    skip = _auto_exempt_keys(snapshot, all_courses)
    for sem in snapshot.semesters:
        for c in sem.courses:
            index += 1
            key = course_key(c)
            if not key or key in seen:
                continue
            if c.status is CourseStatus.IN_PROGRESS:
                seen.add(key)
                running.append(c)
            elif c.status in (CourseStatus.FAILED, CourseStatus.NOT_TAKEN):
                if key in skip:
                    continue
                seen.add(key)
                sem_no = sem.order if sem.order < 10_000 else 999
                cands.append(_Cand(
                    key, c,
                    (0 if c.status is CourseStatus.FAILED else 1, sem_no, index),
                    required=c.required is not False and c.in_framework,
                ))
    return cands, running


# ================================================================== auto-fill
def _autofill(
    cands: list[_Cand],
    terms: list[Term],
    limits: PlanLimits,
    loads: dict[str, float],
) -> dict[str, str]:
    """Place ``cands`` across ``terms``; returns {course key: term key}.

    Loads are balanced (each term aims at the average of what is left) and
    never exceed the term's cap. ``loads`` holds credits already committed per
    term key and is not mutated.
    """
    placed: dict[str, str] = {}
    if not terms:
        return placed
    pending = sorted(cands, key=lambda c: c.order)
    cur = dict(loads)
    n = len(terms)
    for i, term in enumerate(terms):
        remaining = sum(c.course.credits for c in pending)
        committed = sum(cur.get(t.key, 0.0) for t in terms[i:])
        left = n - i
        average = (remaining + committed) / left
        cap = limits.cap(term)
        load = cur.get(term.key, 0.0)
        still: list[_Cand] = []
        for c in pending:
            cr = c.course.credits
            # Round to the nearest course: may overshoot the average by up to
            # half a course, never the cap. The last term takes up to its cap.
            ceiling = cap if left == 1 else min(cap, average + cr / 2)
            if cr <= 1e-9 or load + cr <= ceiling + 1e-9:
                placed[c.key] = term.key
                load += cr
            else:
                still.append(c)
        cur[term.key] = load
        pending = still
        if not pending:
            break
    # Top-up: whatever did not fit its turn goes to the lightest term with room.
    for c in pending:
        room = [t for t in terms if cur.get(t.key, 0.0) + c.course.credits <= limits.cap(t) + 1e-9]
        if not room:
            continue
        best = min(room, key=lambda t: (cur.get(t.key, 0.0), t))
        placed[c.key] = best.key
        cur[best.key] = cur.get(best.key, 0.0) + c.course.credits
    return placed


def _fill_with_summers_last(
    cands: list[_Cand],
    terms: list[Term],
    target: Term,
    limits: PlanLimits,
    loads: dict[str, float],
) -> tuple[dict[str, str], bool]:
    """Pass 1: main terms (+ the target if it is a summer). Pass 2: also the
    other summers - only if pass 1 could not place everything."""
    first_pool = [t for t in terms if not t.is_summer or t == target]
    placed = _autofill(cands, first_pool, limits, loads)
    if len(placed) == len(cands):
        return placed, True
    placed = _autofill(cands, terms, limits, loads)
    return placed, len(placed) == len(cands)


# ================================================================ main entry
def _timeline(snapshot: RoadmapSnapshot, running: list[RoadmapCourse], today: date):
    anchor = Term.from_key(snapshot.anchor_term) or term_from_date(today)
    has_current = bool(running)
    first_plan = anchor.next()
    if not has_current:
        first_plan = max(first_plan, term_from_date(today))
    min_target = anchor if has_current else first_plan
    return anchor, has_current, first_plan, min_target


def build_plan(
    snapshot: RoadmapSnapshot,
    state: PlanState,
    *,
    today: Optional[date] = None,
    reset_auto: bool = False,
    reset_all: bool = False,
) -> tuple[PlanState, PlanResult]:
    """Reconcile ``state`` with ``snapshot`` and auto-fill the rest.

    Returns the (possibly changed) state - the caller persists it when it
    differs - and the renderable result. ``reset_auto`` forgets auto
    placements first (use after changing target/limits); ``reset_all`` also
    forgets the user's manual choices.
    """
    today = today or date.today()
    cands, running = _candidates(snapshot)
    anchor, has_current, first_plan, min_target = _timeline(snapshot, running, today)
    st = state.copy()
    notes: list[str] = []

    by_key = {c.key: c for c in cands}
    if reset_all:
        st.assignments = {}
    elif reset_auto:
        st.assignments = {k: s for k, s in st.assignments.items() if s.manual}

    # ---- target must be a still-reachable term
    target = Term.from_key(st.target)
    if target is not None and target < min_target:
        notes.append(
            f"Mốc ra trường {target.short_label} đã qua - hãy chọn lại mốc mới."
        )
        target = None
        st.target = None

    # ---- reconcile existing assignments
    rolled: dict[str, str] = {}
    released = 0
    for key, slot in list(st.assignments.items()):
        if key not in by_key:                       # done / running / not in curriculum anymore
            del st.assignments[key]
            continue
        if slot.term is None:
            continue
        t = Term.from_key(slot.term)
        if t is None or t < first_plan:             # term passed and the course was not taken
            rolled[key] = t.short_label if t else ""
            del st.assignments[key]
        elif target is not None and t > target:     # target moved earlier
            released += 1
            del st.assignments[key]
    if rolled:
        notes.append(
            f"{len(rolled)} môn chưa được học trong kỳ đã qua - đã đưa về lại kế hoạch."
        )
    if released:
        notes.append(f"{released} môn nằm sau mốc ra trường mới - đã xếp lại.")

    # ---- auto-fill whatever is required, unassigned and not parked
    limits = st.limits
    terms = term_range(first_plan, target) if target is not None else []
    loads: dict[str, float] = {}
    for key, slot in st.assignments.items():
        if slot.term is not None:
            loads[slot.term] = loads.get(slot.term, 0.0) + by_key[key].course.credits
    todo = [c for c in cands if c.required and c.key not in st.assignments]
    if target is not None and todo:
        placed, _ok = _fill_with_summers_last(todo, terms, target, limits, loads)
        for key, tkey in placed.items():
            st.assignments[key] = Slot(tkey, manual=False)

    # ---- who is still unplaced
    unplaced = [
        c for c in cands
        if c.required and (c.key not in st.assignments or st.assignments[c.key].term is None)
    ]
    optional = [
        c for c in cands
        if not c.required and (c.key not in st.assignments or st.assignments[c.key].term is None)
    ]
    outstanding = sum(c.course.credits for c in cands if c.required)
    shortfall = sum(c.course.credits for c in unplaced) if target is not None else 0.0
    feasible = not unplaced

    # ---- views
    def pc(c: _Cand, **kw) -> PlannedCourse:
        slot = st.assignments.get(c.key)
        return PlannedCourse(
            c.course, c.key, manual=bool(slot and slot.manual),
            note=("Trượt từ " + rolled[c.key]) if rolled.get(c.key) else "", **kw,
        )

    term_views: list[PlanTerm] = []
    if has_current:
        term_views.append(PlanTerm(
            anchor, True, limits.cap(anchor),
            tuple(PlannedCourse(c, course_key(c), locked=True) for c in running),
            is_final=(target == anchor),
        ))
    shown = term_range(first_plan, target) if target is not None else []
    for t in shown:
        items = tuple(
            pc(by_key[k]) for k, s in st.assignments.items() if s.term == t.key and k in by_key
        )
        items = tuple(sorted(items, key=lambda p: by_key[p.key].order))
        load_t = sum(p.course.credits for p in items)
        below = not t.is_summer and t != target and 0 < load_t < MIN_MAIN_CREDITS
        term_views.append(PlanTerm(t, False, limits.cap(t), items, t == target, below))

    options = tuple(term_range(min_target, _advance(min_target, MAX_TARGET_HORIZON)))
    earliest = _earliest_target(cands, first_plan, min_target, limits)

    entry = snapshot.entry_year
    result = PlanResult(
        terms=tuple(term_views),
        unplaced=tuple(pc(c) for c in unplaced),
        optional=tuple(pc(c) for c in optional),
        target=target,
        target_options=options,
        first_plan_term=first_plan,
        outstanding_credits=outstanding,
        shortfall_credits=shortfall,
        feasible=feasible,
        earliest_target=earliest,
        entry_year=entry,
        notes=tuple(notes),
    )
    return st, result


def _advance(t: Term, n: int) -> Term:
    for _ in range(n):
        t = t.next()
    return t


def _earliest_target(
    cands: list[_Cand], first_plan: Term, min_target: Term, limits: PlanLimits
) -> Optional[Term]:
    """Earliest term by which every required course could be placed (summers
    allowed), ignoring what the user already arranged."""
    req = [c for c in cands if c.required]
    t = min_target
    for _ in range(MAX_TARGET_HORIZON * 2):
        if t >= first_plan:
            terms = term_range(first_plan, t)
            placed = _autofill(req, terms, limits, {})
            if len(placed) == len(req):
                return t
        elif not req:
            return t
        t = t.next()
    return None


# ================================================================ user actions
def _credit_map(snapshot: RoadmapSnapshot) -> dict[str, float]:
    cands, _ = _candidates(snapshot)
    return {c.key: c.course.credits for c in cands}


def move_course(
    snapshot: RoadmapSnapshot,
    state: PlanState,
    key: str,
    term_key: Optional[str],
    *,
    today: Optional[date] = None,
) -> tuple[PlanState, Optional[str]]:
    """Put course ``key`` in ``term_key`` (``None`` = take it out of the plan).

    Returns ``(new_state, error)``; on error the state is unchanged and
    ``error`` is a Vietnamese message safe to show the user.
    """
    st, result = build_plan(snapshot, state, today=today)
    credits = _credit_map(snapshot)
    if key not in credits:
        return state, "Môn này không còn cần xếp (đã học xong hoặc đang học)."
    new = st.copy()
    if term_key is None:
        new.assignments[key] = Slot(None, manual=True)
        return new, None
    term = Term.from_key(term_key)
    if term is None:
        return state, "Học kỳ không hợp lệ."
    if term < result.first_plan_term:
        return state, "Không thể xếp vào học kỳ đã qua."
    if result.target is not None and term > result.target:
        return state, f"Sau mốc ra trường ({result.target.short_label}) - hãy dời mốc ra trường trước."
    load = sum(
        credits.get(k, 0.0) for k, s in st.assignments.items()
        if s.term == term.key and k != key
    )
    cap = st.limits.cap(term)
    if load + credits[key] > cap + 1e-9:
        return state, (
            f"{term.short_label}: vượt giới hạn {cap:g} TC "
            f"(đang có {load:g}, môn này {credits[key]:g})."
        )
    new.assignments[key] = Slot(term.key, manual=True)
    return new, None


def set_target(state: PlanState, term_key: Optional[str]) -> PlanState:
    new = state.copy()
    new.target = term_key if Term.from_key(term_key) else None
    return new


def set_limits(state: PlanState, main_max: float, summer_max: float) -> PlanState:
    new = state.copy()
    new.limits = PlanLimits(_positive(main_max, DEFAULT_MAIN_MAX),
                            _positive(summer_max, DEFAULT_SUMMER_MAX))
    return new
