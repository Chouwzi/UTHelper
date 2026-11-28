"""Builds a RoadmapSnapshot from raw Portal bodies + normalized transcript.

Pure functions, no I/O, no Flet: everything here is unit-testable.

Status resolution per curriculum course (first match wins):
  1. exempted / credit-transferred (``diemMien``)      -> COMPLETED
  2. any passing attempt in the transcript              -> COMPLETED
  3. registered in the latest semester / pending there  -> IN_PROGRESS
  4. any failing attempt                                -> FAILED (outstanding)
  5. otherwise                                          -> NOT_TAKEN
A retake that passes therefore clears an earlier fail, and a retake in
progress shows as IN_PROGRESS rather than FAILED.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from core.academic_radar.eligibility_resolver import (
    KNOWN_CONDITIONAL_CODES,
    KNOWN_CONDITIONAL_PREFIXES,
)
from core.academic_radar.models import CourseGradeItem, SemesterGradeData
from core.academic_radar.normalizer import SemesterNormalizer
from core.learning_roadmap import parsers as P
from core.learning_roadmap.planner import Term, parse_term, term_range
from core.learning_roadmap.models import (
    CourseStatus,
    CreditProgress,
    FrameworkCourse,
    GraduationRequirement,
    RequirementStatus,
    RoadmapCourse,
    RoadmapSemester,
    RoadmapSnapshot,
)

PASS_THRESHOLD_10 = 4.0   # UTH: D (4.0/10) is the lowest passing grade
_PASS_WORDS = {"dat", "p", "pass", "passed"}
_FAIL_WORDS = {"khong dat", "chua dat", "f", "fail", "failed", "r"}
_MIN_SUBSTRING_CODE = 6   # for "251_0120121003_01"-style class codes


# ------------------------------------------------------------------ attempts
@dataclass(frozen=True)
class _Attempt:
    order: int                 # chronological rank of the semester
    is_latest_semester: bool
    outcome: str               # "pass" | "fail" | "pending"
    item: CourseGradeItem


def attempt_outcome(item: CourseGradeItem) -> str:
    letter = P.fold(item.letter_grade)
    if not item.is_passed or letter in _FAIL_WORDS:
        return "fail"
    if item.final_grade is not None:
        return "pass" if item.final_grade >= PASS_THRESHOLD_10 else "fail"
    if letter in _PASS_WORDS or (letter and letter[0] in "abcd"):
        return "pass"
    return "pending"


def _semester_rank(sem: SemesterGradeData, position: int, total: int) -> int:
    """Portal ids ascend with time (71..76); fall back to reversed list order
    (the transcript list is newest-first)."""
    sid = P.to_int_prefix(sem.id)
    return sid if sid else (total - position)


# ----------------------------------------------------------------- matching
class _CourseIndex:
    """Resolve a (code, name) pair to a framework course index."""

    def __init__(self, courses: list[FrameworkCourse]) -> None:
        self._by_code: dict[str, int] = {}
        self._by_name: dict[str, int] = {}
        for i, c in enumerate(courses):
            if c.code:
                self._by_code.setdefault(P.fold_key(c.code), i)
            if c.name:
                self._by_name.setdefault(P.fold(c.name), i)

    def find(self, code: str, name: str) -> int | None:
        raw = P.fold_key(code)
        if raw and raw in self._by_code:
            return self._by_code[raw]
        base = P.fold_key(P.base_code(code))
        if base and base in self._by_code:
            return self._by_code[base]
        if raw:
            for fcode, idx in self._by_code.items():
                if len(fcode) >= _MIN_SUBSTRING_CODE and fcode in raw:
                    return idx
        fname = P.fold(name)
        return self._by_name.get(fname) if fname else None


# ------------------------------------------------------------- conditionals
def is_conditional(code: str, name: str, credits: float) -> bool:
    c = (code or "").strip()
    return (
        credits == 0
        or c in KNOWN_CONDITIONAL_CODES
        or any(c.startswith(p) for p in KNOWN_CONDITIONAL_PREFIXES)
    )


def _grade_text(item: CourseGradeItem) -> str:
    parts = []
    if item.final_grade is not None:
        parts.append(f"{item.final_grade:.1f}")
    if item.letter_grade:
        parts.append(item.letter_grade)
    return " · ".join(parts)


# ---------------------------------------------------------------- requirements
@dataclass(frozen=True)
class _ReqSpec:
    key: str
    label: str
    cert_patterns: tuple[str, ...]      # regex over folded certificate text
    flag_tokens: tuple[str, ...]        # tokens in folded Portal-info keys
    code_pattern: str | None            # regex over upper-case raw code
    name_patterns: tuple[str, ...]      # regex over folded course name
    unit_label: str = ""                # "cấp" / "môn": enables an "x/N" progress count


REQUIREMENT_SPECS: tuple[_ReqSpec, ...] = (
    _ReqSpec(
        "english", "Chuẩn đầu ra tiếng Anh",
        # Only real external English certificates exempt the General English
        # courses; generic words ("Tiếng Anh", "English") also match program
        # certificates and would wrongly fill the 4/4 bar.
        (r"\bielts\b", r"\btoeic\b", r"\btoefl\b", r"\bvstep\b", r"\baptis\b",
         r"\bcambridge\b", r"\b(a2|b1|b2|c1)\b"),
        ("ngoaingu", "tienganh", "anhvan", "english"),
        r"^TT0\d$",
        (r"general english", r"\bchuan dau ra tieng anh\b"),
        unit_label="cấp",
    ),
    _ReqSpec(
        "it", "Chuẩn tin học",
        (r"tin hoc", r"\bmos\b", r"\bic3\b", r"\bcntt\b", r"information technology"),
        ("tinhoc", "cntt"),
        r"^006124$",
        (r"tin hoc co ban",),
    ),
    _ReqSpec(
        "defense", "Giáo dục quốc phòng - an ninh",
        (r"quoc phong", r"\bgdqp\b"),
        ("quocphong", "gdqp"),
        r"^GDQP|^012000720[1-4]$",
        (r"quoc phong",),
        unit_label="môn",
    ),
    _ReqSpec(
        "pe", "Giáo dục thể chất",
        (r"the chat", r"\bgdtc\b"),
        ("thechat", "gdtc"),
        r"^GDTC",
        (r"giao duc the chat",),
    ),
)


# ---- fixed-size requirements shown as "x/N" ---------------------------------
ENGLISH_LEVELS = 4                                   # General English 1..4
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4}
# over the folded name: "general english 3", "general english level 3", "... iii"
_ENGLISH_NAME = re.compile(
    r"\b(?:general english|tieng anh tong quat|anh van tong quat)(?: level| cap do| lop)? ?(\d+|iv|iii|ii|i)\b"
)
# over the upper-case code; also finds it inside class codes like 251_TT03_01
_ENGLISH_CODE = re.compile(r"(?<![A-Z0-9])TT0(\d)(?![0-9])")

# The 4 Giáo dục quốc phòng - an ninh courses: (unit id, code, folded-name regex)
DEFENSE_UNITS: tuple[tuple[str, str, str], ...] = (
    ("1", "0120007201", r"^duong loi quoc phong va an ninh"),
    ("2", "0120007202", r"^cong tac quoc phong va an ninh"),
    ("3", "0120007203", r"^quan su chung$"),
    ("4", "0120007204", r"^ky thuat chien dau bo binh va chien thuat"),
)


def _english_level(course: RoadmapCourse) -> str | None:
    m = _ENGLISH_NAME.search(P.fold(course.name))
    raw = m.group(1) if m else None
    if raw is None:
        mc = _ENGLISH_CODE.search(course.code.strip().upper())
        raw = mc.group(1) if mc else None
    if raw is None:
        return None
    n = _ROMAN.get(raw) or int(raw)
    return str(n) if 1 <= n <= ENGLISH_LEVELS else None


def _defense_unit(course: RoadmapCourse) -> str | None:
    code = course.code.strip().upper()
    folded = P.fold(course.name)
    for unit, ucode, name_re in DEFENSE_UNITS:
        if ucode in code or re.search(name_re, folded):
            return unit
    return None


_UNIT_MATCHERS = {
    "english": (_english_level, ENGLISH_LEVELS),
    "defense": (_defense_unit, len(DEFENSE_UNITS)),
}
_STATUS_RANK = {
    CourseStatus.COMPLETED: 3, CourseStatus.IN_PROGRESS: 2,
    CourseStatus.FAILED: 1, CourseStatus.NOT_TAKEN: 0,
}


@dataclass(frozen=True)
class _UnitProgress:
    done: int          # shown as "done/total"
    running: int
    total: int
    seen: int          # how many distinct units appear in the data at all
    met: bool          # requirement fully satisfied
    detail: str


def _unit_progress(spec: _ReqSpec, courses: Iterable[RoadmapCourse]) -> _UnitProgress | None:
    """Progress out of a fixed total, so a missing course counts as not done.

    * defense: number of the 4 courses completed.
    * english: General English levels run in sequence, so being in (or past)
      level N means levels 1..N-1 are behind you -> progress = highest level
      reached (completed or in progress); met only when level 4 is completed.
    """
    matcher = _UNIT_MATCHERS.get(spec.key)
    if matcher is None:
        return None
    fn, total = matcher
    best: dict[str, CourseStatus] = {}
    for c in courses:
        unit = fn(c)
        if unit is None:
            continue
        if unit not in best or _STATUS_RANK[c.status] > _STATUS_RANK[best[unit]]:
            best[unit] = c.status
    completed = {int(u) for u, st in best.items() if st is CourseStatus.COMPLETED}
    running = {int(u) for u, st in best.items() if st is CourseStatus.IN_PROGRESS}
    if spec.key == "english":
        reached = max(completed | running, default=0)
        met = total in completed
        if met:
            detail = f"Đã hoàn thành General English {total}"
        elif running:
            detail = f"Đang học General English {max(running)} · cần hoàn thành cấp {total}"
        elif completed:
            detail = f"Đã hoàn thành đến General English {max(completed)} · cần cấp {total}"
        else:
            detail = f"Chưa học General English · cần hoàn thành cấp {total}"
        return _UnitProgress(reached, len(running), total, len(best), met, detail)
    done = len(completed)
    detail = f"Hoàn thành {done}/{total} {spec.unit_label}"
    return _UnitProgress(done, len(running), total, len(best), done == total, detail)


def _matches_spec(spec: _ReqSpec, course: RoadmapCourse) -> bool:
    if spec.code_pattern and re.search(spec.code_pattern, course.code.strip().upper()):
        return True
    folded = P.fold(course.name)
    return any(re.search(p, folded) for p in spec.name_patterns)


def evaluate_requirements(
    courses: list[RoadmapCourse],
    certificates: list[P.Certificate],
    graduation_info: Any,
    evidence: Iterable[RoadmapCourse] = (),
) -> tuple[GraduationRequirement, ...]:
    """Precedence: Portal's explicit flag > certificate > course evidence > unknown.

    English (General English 1-4) and GDQP (4 courses) also carry an ``x/N``
    progress count taken from the courses themselves. ``evidence`` is raw
    transcript / exemption / registration rows, so the count does not depend on
    how well a row matched the curriculum framework."""
    out: list[GraduationRequirement] = []
    valid_certs = [c for c in certificates if c.valid]
    for spec in REQUIREMENT_SPECS:
        units = _unit_progress(spec, [*courses, *evidence])
        total = units.total if units else None

        def make(status, detail, done=None):
            if units is None:
                return GraduationRequirement(spec.key, spec.label, status, detail)
            return GraduationRequirement(
                spec.key, spec.label, status, detail,
                progress_done=units.done if done is None else done,
                progress_total=units.total,
            )

        # 1. Portal is the authority when it states a verdict.
        flag = P.find_flag(graduation_info, spec.flag_tokens) if graduation_info else None
        if flag is not None:
            out.append(make(
                RequirementStatus.MET if flag else RequirementStatus.NOT_MET,
                "Theo Portal: " + ("đạt" if flag else "chưa đạt"),
                done=total if flag else None,
            ))
            continue
        # 2. A matching certificate.
        cert = next(
            (c for c in valid_certs
             if any(re.search(p, c.folded) for p in spec.cert_patterns)),
            None,
        )
        if cert is not None:
            out.append(make(RequirementStatus.MET, f"Chứng chỉ: {cert.label}", done=total))
            continue
        # 3. Evidence from the courses themselves.
        if units is not None and units.seen:
            if units.met:
                status = RequirementStatus.MET
            elif units.running:
                status = RequirementStatus.IN_PROGRESS
            else:
                status = RequirementStatus.NOT_MET
            out.append(make(status, units.detail))
            continue
        related = [c for c in courses if _matches_spec(spec, c)]
        if not related:
            out.append(GraduationRequirement(
                spec.key, spec.label, RequirementStatus.UNKNOWN,
                "Chưa có dữ liệu để xác định",
            ))
            continue
        done = sum(1 for c in related if c.status is CourseStatus.COMPLETED)
        running = sum(1 for c in related if c.status is CourseStatus.IN_PROGRESS)
        count = len(related)
        if done == count:
            status = RequirementStatus.MET
        elif running:
            status = RequirementStatus.IN_PROGRESS
        else:
            status = RequirementStatus.NOT_MET
        out.append(GraduationRequirement(
            spec.key, spec.label, status, f"Hoàn thành {done}/{count} học phần"
        ))
    return tuple(out)


# -------------------------------------------------------------------- builder
@dataclass
class _Resolved:
    status: CourseStatus
    grade_text: str = ""
    note: str = ""


def _resolve(
    exempt: bool,
    attempts: list[_Attempt],
    registered_now: bool,
) -> _Resolved:
    if exempt:
        return _Resolved(CourseStatus.COMPLETED, "", "Miễn / chuyển điểm")
    passes = [a for a in attempts if a.outcome == "pass"]
    if passes:
        best = max(passes, key=lambda a: a.order)
        note = "Học lại" if len(attempts) > 1 else ""
        return _Resolved(CourseStatus.COMPLETED, _grade_text(best.item), note)
    fails = [a for a in attempts if a.outcome == "fail"]
    pending_now = any(a.outcome == "pending" and a.is_latest_semester for a in attempts)
    # A posted result in the latest semester beats "still listed as registered".
    decided_now = any(a.outcome == "fail" and a.is_latest_semester for a in attempts)
    if (registered_now and not decided_now) or pending_now:
        return _Resolved(
            CourseStatus.IN_PROGRESS, "",
            "Học lại" if fails else "Đang học / chờ điểm",
        )
    if fails:
        last = max(fails, key=lambda a: a.order)
        n = len(fails)
        return _Resolved(
            CourseStatus.FAILED, _grade_text(last.item),
            "Chưa đạt" + (f" ({n} lần)" if n > 1 else ""),
        )
    return _Resolved(CourseStatus.NOT_TAKEN)


def _make_course(
    code: str, name: str, credits: float, res: _Resolved,
    required: bool | None, in_framework: bool,
) -> RoadmapCourse:
    return RoadmapCourse(
        code=code, name=name, credits=credits, status=res.status,
        grade_text=res.grade_text, note=res.note,
        is_conditional=is_conditional(code, name, credits),
        required=required, in_framework=in_framework,
        search_text=P.fold(f"{code} {name}"),
    )


_OUTCOME_STATUS = {
    "pass": CourseStatus.COMPLETED,
    "fail": CourseStatus.FAILED,
    "pending": CourseStatus.IN_PROGRESS,
}


def _build_history(
    transcript: list[SemesterGradeData],
    ranks: list[int],
    current_rank: int | None,
    anchor: Any = None,
    has_running: bool = False,
) -> tuple[RoadmapSemester, ...]:
    """Every finished term, newest first, plus empty main terms in between
    (a gap in the transcript is shown, not silently skipped). The term that is
    in progress is left out; it is shown as the current term."""
    out: list[RoadmapSemester] = []
    seen_terms: set = set()
    for sem, rank in sorted(zip(transcript, ranks), key=lambda p: -p[1]):
        if has_running and current_rank is not None and rank == current_rank:
            continue
        courses = tuple(
            _make_course(
                it.course_code, it.course_name, it.credits,
                _Resolved(_OUTCOME_STATUS[attempt_outcome(it)], _grade_text(it)),
                None, True,
            )
            for it in sem.courses
        )
        term = parse_term(sem.name)
        if term is not None:
            seen_terms.add(term)
        if courses:
            out.append(RoadmapSemester(f"past-{sem.id}", sem.name, rank, courses))

    # Fill main terms (HK1/HK2) with no record between the first term and now.
    if seen_terms:
        first = min(seen_terms)
        last = max(seen_terms)
        if anchor is not None and has_running:
            # everything strictly before the running term
            prev = [t for t in term_range(first, anchor) if t < anchor]
            end_terms = prev
        else:
            end_terms = term_range(first, last)
        have = {parse_term(h.label) for h in out}
        for t in end_terms:
            if t.is_summer or t in have:
                continue
            out.append(RoadmapSemester(f"past-gap-{t.key}", t.label, -t.ordinal, ()))
    out.sort(key=lambda h: (parse_term(h.label) or Term(0, 1)).ordinal, reverse=True)
    return tuple(out)


def _requirement_evidence(
    transcript: list[SemesterGradeData], exemptions: Any, registered: Any,
) -> list[RoadmapCourse]:
    """Raw rows (transcript, exemptions, current registrations) as lightweight
    courses, used only to count English levels / GDQP courses."""
    out: list[RoadmapCourse] = []

    def add(code: str, name: str, credits: float, status: CourseStatus) -> None:
        out.append(RoadmapCourse(code=code or "", name=name or "", credits=credits, status=status))

    for sem in transcript:
        for it in sem.courses:
            status = _OUTCOME_STATUS[attempt_outcome(it)]
            for code in {it.course_code, it.subject_code}:
                add(code, it.course_name, it.credits, status)
    for ref in P.parse_course_refs(exemptions):
        add(ref.code, ref.name, ref.credits, CourseStatus.COMPLETED)
    for ref in P.parse_course_refs(registered):
        add(ref.code, ref.name, ref.credits, CourseStatus.IN_PROGRESS)
    return out


def build_snapshot(bundle: dict[str, Any], *, now: datetime | None = None) -> RoadmapSnapshot:
    bodies: dict[str, Any] = bundle.get("bodies") or {}
    framework = P.parse_curriculum(bodies.get("curriculum"))
    index = _CourseIndex(framework)

    transcript = SemesterNormalizer().normalize_transcript(bundle.get("transcript") or [])
    ranks = [_semester_rank(s, i, len(transcript)) for i, s in enumerate(transcript)]
    # The in-progress term is Portal's *current* semester. Only when that is
    # unknown do we fall back to the newest term in the transcript.
    current_pre = bundle.get("current_semester") or {}
    cur_id = P.to_int_prefix(current_pre.get("id")) if current_pre.get("id") is not None else 0
    if cur_id:
        latest_rank = cur_id if cur_id in ranks else None
    else:
        latest_rank = max(ranks) if ranks else None

    # ---- attempts, keyed by framework index (or by identity for extras)
    attempts: dict[int, list[_Attempt]] = {}
    extra_attempts: dict[str, list[_Attempt]] = {}
    extra_meta: dict[str, tuple[str, str, float]] = {}
    for sem, rank in zip(transcript, ranks):
        for item in sem.courses:
            att = _Attempt(rank, rank == latest_rank, attempt_outcome(item), item)
            idx = index.find(item.course_code, item.course_name)
            if idx is not None:
                attempts.setdefault(idx, []).append(att)
            else:
                ident = P.fold_key(P.base_code(item.course_code)) or P.fold_key(item.course_name)
                extra_attempts.setdefault(ident, []).append(att)
                extra_meta.setdefault(
                    ident, (item.course_code, item.course_name, item.credits)
                )

    # ---- exemptions & current-semester registrations
    exempt_idx: set[int] = set()
    extra_exempt: dict[str, P.CourseRef] = {}
    for ref in P.parse_course_refs(bodies.get("exemptions")):
        idx = index.find(ref.code, ref.name)
        if idx is not None:
            exempt_idx.add(idx)
        else:
            ident = P.fold_key(P.base_code(ref.code)) or P.fold_key(ref.name)
            if ident:
                extra_exempt[ident] = ref

    current = bundle.get("current_semester") or {}
    registered_idx: set[int] = set()
    extra_registered: dict[str, P.CourseRef] = {}
    for ref in P.parse_course_refs(current.get("courses")):
        idx = index.find(ref.code, ref.name)
        if idx is not None:
            registered_idx.add(idx)
        else:
            ident = P.fold_key(P.base_code(ref.code)) or P.fold_key(ref.name)
            if ident:
                extra_registered[ident] = ref

    # ---- framework courses grouped by semester number
    groups: dict[int | None, list[RoadmapCourse]] = {}
    for i, fc in enumerate(framework):
        res = _resolve(i in exempt_idx, attempts.get(i, []), i in registered_idx)
        groups.setdefault(fc.semester_no, []).append(
            _make_course(fc.code, fc.name, fc.credits, res, fc.required, True)
        )

    # ---- courses outside the framework (so nothing in the transcript is lost)
    extras: list[RoadmapCourse] = []
    idents = list(extra_attempts) + [
        i for i in list(extra_exempt) + list(extra_registered) if i not in extra_attempts
    ]
    for ident in dict.fromkeys(idents):
        code, name, credits = extra_meta.get(ident, ("", "", 0.0))
        ref = extra_exempt.get(ident) or extra_registered.get(ident)
        if ref is not None and not name:
            code, name, credits = ref.code, ref.name, ref.credits
        res = _resolve(
            ident in extra_exempt,
            extra_attempts.get(ident, []),
            ident in extra_registered,
        )
        extras.append(_make_course(code, name or code, credits, res, None, False))

    semesters: list[RoadmapSemester] = []
    for sem_no in sorted(k for k in groups if k is not None):
        semesters.append(RoadmapSemester(f"hk-{sem_no}", f"Học kỳ {sem_no}", sem_no, tuple(groups[sem_no])))
    if None in groups:
        semesters.append(RoadmapSemester("unscheduled", "Chưa xếp học kỳ", 10_000, tuple(groups[None])))
    if extras:
        semesters.append(RoadmapSemester("extra", "Ngoài chương trình khung", 20_000, tuple(extras)))

    all_courses = [c for s in semesters for c in s.courses]

    # ---- credits: Portal's tiendo is authoritative; else sum what we know
    required, earned = P.parse_credit_progress(bodies.get("credit_progress"))
    if earned is None and all_courses:
        earned = sum(c.credits for c in all_courses if c.status is CourseStatus.COMPLETED)
    if required is None and framework:
        required = sum(fc.credits for fc in framework) or None

    certs = P.parse_certificates(bodies.get("certificates")) + P.parse_certificates(
        bodies.get("graduation_certificates")
    )
    grad_info = bodies.get("graduation_info")
    evidence = _requirement_evidence(
        transcript, bodies.get("exemptions"), current.get("courses")
    )
    requirements = evaluate_requirements(all_courses, certs, grad_info, evidence)

    verdict = P.find_flag(grad_info, ("dudieukien", "dattotnghiep", "detotnghiep")) if grad_info else None
    message = P.find_message(grad_info, ("lydo", "ghichu", "thongbao")) if grad_info else ""

    # ---- calendar anchor + history (feeds the graduation planner)
    anchor_name = str(current.get("name") or "")
    anchor = parse_term(anchor_name)
    if anchor is None and ranks:
        latest_sem = transcript[ranks.index(max(ranks))]
        anchor_name = latest_sem.name
        anchor = parse_term(latest_sem.name)
    years = [t.year for t in (parse_term(s.name) for s in transcript) if t is not None]
    if anchor is not None:
        years.append(anchor.year)
    has_running = any(c.status is CourseStatus.IN_PROGRESS for c in all_courses)
    history = _build_history(transcript, ranks, latest_rank, anchor, has_running)

    return RoadmapSnapshot(
        semesters=tuple(semesters),
        requirements=requirements,
        credits=CreditProgress(required=required, earned=earned),
        framework_available=bool(framework),
        portal_verdict=verdict,
        portal_message=message,
        endpoint_errors=dict(bundle.get("errors") or {}),
        generated_at=now or datetime.now(),
        anchor_term=anchor.key if anchor else None,
        anchor_name=anchor_name,
        entry_year=min(years) if years else None,
        history=history,
    )
