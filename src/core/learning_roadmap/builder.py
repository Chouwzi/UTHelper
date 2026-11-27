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
from typing import Any

from core.academic_radar.eligibility_resolver import (
    KNOWN_CONDITIONAL_CODES,
    KNOWN_CONDITIONAL_PREFIXES,
)
from core.academic_radar.models import CourseGradeItem, SemesterGradeData
from core.academic_radar.normalizer import SemesterNormalizer
from core.learning_roadmap import parsers as P
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


REQUIREMENT_SPECS: tuple[_ReqSpec, ...] = (
    _ReqSpec(
        "english", "Chuẩn đầu ra tiếng Anh",
        (r"tieng anh", r"\bielts\b", r"\btoeic\b", r"\btoefl\b", r"\bvstep\b", r"\baptis\b",
         r"\bcambridge\b", r"\benglish\b", r"ngoai ngu", r"\b(a2|b1|b2|c1)\b"),
        ("ngoaingu", "tienganh", "anhvan", "english"),
        r"^TT0\d$",
        (r"general english", r"\bchuan dau ra tieng anh\b"),
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
        r"^GDQP",
        (r"quoc phong",),
    ),
    _ReqSpec(
        "pe", "Giáo dục thể chất",
        (r"the chat", r"\bgdtc\b"),
        ("thechat", "gdtc"),
        r"^GDTC",
        (r"giao duc the chat",),
    ),
)


def _matches_spec(spec: _ReqSpec, course: RoadmapCourse) -> bool:
    if spec.code_pattern and re.search(spec.code_pattern, course.code.strip().upper()):
        return True
    folded = P.fold(course.name)
    return any(re.search(p, folded) for p in spec.name_patterns)


def evaluate_requirements(
    courses: list[RoadmapCourse],
    certificates: list[P.Certificate],
    graduation_info: Any,
) -> tuple[GraduationRequirement, ...]:
    """Precedence: Portal's explicit flag > certificate > course evidence > unknown."""
    out: list[GraduationRequirement] = []
    valid_certs = [c for c in certificates if c.valid]
    for spec in REQUIREMENT_SPECS:
        # 1. Portal is the authority when it states a verdict.
        flag = P.find_flag(graduation_info, spec.flag_tokens) if graduation_info else None
        if flag is not None:
            out.append(GraduationRequirement(
                spec.key, spec.label,
                RequirementStatus.MET if flag else RequirementStatus.NOT_MET,
                "Theo Portal: " + ("đạt" if flag else "chưa đạt"),
            ))
            continue
        # 2. A matching certificate.
        cert = next(
            (c for c in valid_certs
             if any(re.search(p, c.folded) for p in spec.cert_patterns)),
            None,
        )
        if cert is not None:
            out.append(GraduationRequirement(
                spec.key, spec.label, RequirementStatus.MET, f"Chứng chỉ: {cert.label}"
            ))
            continue
        # 3. Evidence from the courses themselves.
        related = [c for c in courses if _matches_spec(spec, c)]
        if not related:
            out.append(GraduationRequirement(
                spec.key, spec.label, RequirementStatus.UNKNOWN,
                "Chưa có dữ liệu để xác định",
            ))
            continue
        done = sum(1 for c in related if c.status is CourseStatus.COMPLETED)
        running = sum(1 for c in related if c.status is CourseStatus.IN_PROGRESS)
        total = len(related)
        if done == total:
            status = RequirementStatus.MET
        elif running:
            status = RequirementStatus.IN_PROGRESS
        else:
            status = RequirementStatus.NOT_MET
        out.append(GraduationRequirement(
            spec.key, spec.label, status, f"Hoàn thành {done}/{total} học phần"
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


def build_snapshot(bundle: dict[str, Any], *, now: datetime | None = None) -> RoadmapSnapshot:
    bodies: dict[str, Any] = bundle.get("bodies") or {}
    framework = P.parse_curriculum(bodies.get("curriculum"))
    index = _CourseIndex(framework)

    transcript = SemesterNormalizer().normalize_transcript(bundle.get("transcript") or [])
    ranks = [_semester_rank(s, i, len(transcript)) for i, s in enumerate(transcript)]
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
    requirements = evaluate_requirements(all_courses, certs, grad_info)

    verdict = P.find_flag(grad_info, ("dudieukien", "dattotnghiep", "detotnghiep")) if grad_info else None
    message = P.find_message(grad_info, ("lydo", "ghichu", "thongbao")) if grad_info else ""

    return RoadmapSnapshot(
        semesters=tuple(semesters),
        requirements=requirements,
        credits=CreditProgress(required=required, earned=earned),
        framework_available=bool(framework),
        portal_verdict=verdict,
        portal_message=message,
        endpoint_errors=dict(bundle.get("errors") or {}),
        generated_at=now or datetime.now(),
    )
