"""Unit tests for the Learning Roadmap core (stdlib unittest, no network, no Flet).

Run from ``src/``:  python -m unittest tests.test_learning_roadmap -v
"""
from __future__ import annotations

import gzip
import io
import json
import sys
import tempfile
import types
import unittest
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# `config` needs pydantic + OS dirs; the roadmap only needs these two names.
if "config" not in sys.modules:
    _cfg = types.ModuleType("config")
    _cfg.settings = types.SimpleNamespace(
        PORTAL_API_BASE="https://portal.test/api/v1",
        UTH_USERNAME="", UTH_PASSWORD="", MOODLE_USERNAME="", MOODLE_PASSWORD="",
    )
    _cfg.get_data_dir = lambda: Path(tempfile.gettempdir())
    sys.modules["config"] = _cfg

from core.learning_roadmap.builder import attempt_outcome, build_snapshot  # noqa: E402
from core.learning_roadmap.filtering import (  # noqa: E402
    FILTER_ACTIONABLE, FILTER_ALL, filter_semesters,
)
from core.learning_roadmap.models import CourseStatus, RequirementStatus  # noqa: E402
from core.learning_roadmap.parsers import fold, parse_curriculum  # noqa: E402
from core.learning_roadmap.service import (  # noqa: E402
    SOURCE_CACHE, SOURCE_NETWORK, SOURCE_NONE, SOURCE_OFFLINE_CACHE,
    LearningRoadmapService,
)
from core.academic_radar.models import CourseGradeItem  # noqa: E402
from core.portal_client import PortalAuthenticationError, PortalClient, PortalError  # noqa: E402


# ------------------------------------------------------------------ fixtures
def curriculum():
    return {"success": True, "body": [
        {"hocKy": 1, "monHocs": [
            {"maHocPhan": "0120005105", "tenMonHoc": "Triết học Mác - Lênin", "soTinChi": 3},
            {"maHocPhan": "0120121003", "tenMonHoc": "Hệ quản trị cơ sở dữ liệu", "soTinChi": 3},
            {"maHocPhan": "TT01", "tenMonHoc": "General English 1", "soTinChi": 0},
            {"maHocPhan": "006124", "tenMonHoc": "Tin học cơ bản", "soTinChi": 0},
        ]},
        {"hocKy": 2, "monHocs": [
            {"maHocPhan": "0120122010", "tenMonHoc": "XD phần mềm hướng đối tượng", "soTinChi": 3},
            {"maHocPhan": "0120121137", "tenMonHoc": "Quản trị doanh nghiệp CNTT", "soTinChi": 3},
            {"maHocPhan": "GDTC1", "tenMonHoc": "Giáo dục thể chất 1", "soTinChi": 0},
            {"maHocPhan": "GDQP1", "tenMonHoc": "Giáo dục quốc phòng và an ninh", "soTinChi": 0},
        ]},
        {"hocKy": 3, "monHocs": [
            {"maHocPhan": "0120999001", "tenMonHoc": "Lập trình Web", "soTinChi": 3},
        ]},
    ]}["body"]


def sem(sid, name, courses):
    return {"id": sid, "tenDot": name, "danhSachMonHoc": courses}


def course(code, name, credits, final=None, letter="", **extra):
    d = {"maHocPhan": code, "tenMonHoc": name, "soTinChi": credits}
    if final is not None:
        d["diemTongKet"] = final
    if letter:
        d["diemChu"] = letter
    d.update(extra)
    return d


def bundle(**over):
    b = {
        "bodies": {
            "curriculum": curriculum(),
            "credit_progress": {"canDat": 120, "hienTai": 61},
        },
        "current_semester": {},
        "transcript": [
            sem(74, "HK1 2025-2026", [
                course("0120005105", "Triết học Mác - Lênin", 3, 7.5, "B"),
                course("0120121003", "Hệ quản trị cơ sở dữ liệu", 3, 3.0, "F"),
                course("GDTC1", "Giáo dục thể chất 1", 0, 8.0, "B+"),
            ]),
        ],
        "errors": {},
    }
    for k, v in over.items():
        if k == "bodies":
            b["bodies"].update(v)
        else:
            b[k] = v
    return b


def by_code(snapshot):
    return {c.code: c for c in snapshot.all_courses()}


# ------------------------------------------------------------------- parsers
class ParserTests(unittest.TestCase):
    def test_fold_strips_vietnamese_diacritics(self):
        self.assertEqual(fold("Hệ quản trị cơ sở dữ liệu"), "he quan tri co so du lieu")
        self.assertEqual(fold("Đại cương"), "dai cuong")

    def test_curriculum_inherits_semester_from_parent_group(self):
        courses = parse_curriculum(curriculum())
        sem_of = {c.code: c.semester_no for c in courses}
        self.assertEqual(sem_of["0120005105"], 1)
        self.assertEqual(sem_of["GDTC1"], 2)
        self.assertEqual(sem_of["0120999001"], 3)

    def test_curriculum_accepts_flat_list_with_semester_on_each_course(self):
        flat = [{"maMonHoc": "A1", "tenMonHoc": "A", "soTinChi": 2, "hocKyDuKien": "Học kỳ 4"}]
        (c,) = parse_curriculum(flat)
        self.assertEqual((c.code, c.semester_no, c.credits), ("A1", 4, 2.0))

    def test_curriculum_dedupes_and_ignores_garbage(self):
        self.assertEqual(parse_curriculum(None), [])
        self.assertEqual(parse_curriculum("oops"), [])
        dup = [{"maHocPhan": "X", "tenMonHoc": "X", "soTinChi": 1}] * 3
        self.assertEqual(len(parse_curriculum(dup)), 1)


# ----------------------------------------------------------- status classifier
class OutcomeTests(unittest.TestCase):
    def item(self, **kw):
        base = dict(course_id="1", course_code="C", course_name="N", credits=3.0)
        base.update(kw)
        return CourseGradeItem(**base)

    def test_outcomes(self):
        self.assertEqual(attempt_outcome(self.item(final_grade=4.0)), "pass")
        self.assertEqual(attempt_outcome(self.item(final_grade=3.9)), "fail")
        self.assertEqual(attempt_outcome(self.item(letter_grade="F")), "fail")
        self.assertEqual(attempt_outcome(self.item(letter_grade="Đạt")), "pass")
        self.assertEqual(attempt_outcome(self.item(letter_grade="Không đạt")), "fail")
        self.assertEqual(attempt_outcome(self.item(final_grade=9.0, is_passed=False)), "fail")
        self.assertEqual(attempt_outcome(self.item(process_grade=8.0)), "pending")


class StatusTests(unittest.TestCase):
    def test_all_four_statuses(self):
        b = bundle(current_semester={"id": 76, "name": "Hè", "courses": [
            {"maHocPhan": "0120122010", "tenMonHoc": "XD phần mềm hướng đối tượng", "soTinChi": 3},
        ]})
        s = by_code(build_snapshot(b))
        self.assertEqual(s["0120005105"].status, CourseStatus.COMPLETED)
        self.assertEqual(s["0120005105"].grade_text, "7.5 · B")
        self.assertEqual(s["0120121003"].status, CourseStatus.FAILED)
        self.assertEqual(s["0120122010"].status, CourseStatus.IN_PROGRESS)
        self.assertEqual(s["0120999001"].status, CourseStatus.NOT_TAKEN)

    def test_retake_that_passes_clears_the_fail(self):
        b = bundle()
        b["transcript"].append(sem(75, "HK2", [
            course("0120121003", "Hệ quản trị cơ sở dữ liệu", 3, 6.0, "C")]))
        c = by_code(build_snapshot(b))["0120121003"]
        self.assertEqual(c.status, CourseStatus.COMPLETED)
        self.assertEqual(c.note, "Học lại")

    def test_retake_in_progress_is_not_reported_as_failed(self):
        b = bundle(current_semester={"id": 76, "name": "Hè", "courses": [
            {"maHocPhan": "0120121003", "tenMonHoc": "Hệ quản trị cơ sở dữ liệu", "soTinChi": 3}]})
        b["transcript"].append(sem(76, "Hè", []))
        c = by_code(build_snapshot(b))["0120121003"]
        self.assertEqual(c.status, CourseStatus.IN_PROGRESS)
        self.assertEqual(c.note, "Học lại")

    def test_posted_fail_in_latest_semester_beats_still_registered(self):
        b = bundle(current_semester={"id": 74, "name": "HK1", "courses": [
            {"maHocPhan": "0120121003", "tenMonHoc": "Hệ quản trị cơ sở dữ liệu", "soTinChi": 3}]})
        c = by_code(build_snapshot(b))["0120121003"]
        self.assertEqual(c.status, CourseStatus.FAILED)

    def test_exemption_counts_as_completed(self):
        b = bundle(bodies={"exemptions": [
            {"maHocPhan": "0120999001", "tenMonHoc": "Lập trình Web", "soTinChi": 3}]})
        c = by_code(build_snapshot(b))["0120999001"]
        self.assertEqual(c.status, CourseStatus.COMPLETED)
        self.assertIn("Miễn", c.note)

    def test_pending_grade_in_latest_semester_is_in_progress(self):
        b = bundle()
        b["transcript"].append(sem(76, "Hè", [
            {"maHocPhan": "0120122010", "tenMonHoc": "XD phần mềm hướng đối tượng",
             "soTinChi": 3, "diemTBThuongKy": 8.0}]))
        self.assertEqual(by_code(build_snapshot(b))["0120122010"].status, CourseStatus.IN_PROGRESS)

    def test_stale_pending_in_old_semester_is_not_in_progress(self):
        b = bundle()
        b["transcript"].insert(0, sem(72, "Cũ", [
            {"maHocPhan": "0120122010", "tenMonHoc": "XD phần mềm hướng đối tượng",
             "soTinChi": 3, "diemTBThuongKy": 8.0}]))
        self.assertEqual(by_code(build_snapshot(b))["0120122010"].status, CourseStatus.NOT_TAKEN)

    def test_class_section_codes_still_match_framework(self):
        for tcode in ("0120005105.01", "251_0120005105_02"):
            b = bundle()
            b["transcript"] = [sem(74, "HK1", [course(tcode, "Triết học Mác - Lênin", 3, 8.0, "B+")])]
            self.assertEqual(by_code(build_snapshot(b))["0120005105"].status,
                             CourseStatus.COMPLETED, tcode)

    def test_name_fallback_when_codes_differ(self):
        b = bundle()
        b["transcript"] = [sem(74, "HK1", [course("OLD-CODE", "TRIET HOC MAC - LENIN", 3, 8.0, "B+")])]
        self.assertEqual(by_code(build_snapshot(b))["0120005105"].status, CourseStatus.COMPLETED)

    def test_transcript_courses_outside_framework_are_kept(self):
        b = bundle()
        b["transcript"][0]["danhSachMonHoc"].append(course("ZZ9", "Môn tự chọn lạ", 2, 9.0, "A"))
        snap = build_snapshot(b)
        extra = [s for s in snap.semesters if s.key == "extra"]
        self.assertEqual(len(extra), 1)
        self.assertEqual(extra[0].courses[0].code, "ZZ9")
        self.assertFalse(extra[0].courses[0].in_framework)

    def test_semesters_are_ordered_and_counts_add_up(self):
        snap = build_snapshot(bundle())
        self.assertEqual([s.key for s in snap.semesters], ["hk-1", "hk-2", "hk-3"])
        self.assertEqual(sum(snap.counts().values()), len(snap.all_courses()))

    def test_no_framework_degrades_to_transcript_only(self):
        b = bundle()
        b["bodies"]["curriculum"] = None
        b["errors"] = {"curriculum": "HTTP 500"}
        snap = build_snapshot(b)
        self.assertFalse(snap.framework_available)
        self.assertTrue(all(s.key == "extra" for s in snap.semesters))
        self.assertEqual(snap.endpoint_errors, {"curriculum": "HTTP 500"})

    def test_empty_bundle_does_not_crash(self):
        snap = build_snapshot({})
        self.assertEqual(snap.semesters, ())
        self.assertFalse(snap.framework_available)

    def test_credit_progress_from_tiendo_and_fallback(self):
        snap = build_snapshot(bundle())
        self.assertEqual((snap.credits.required, snap.credits.earned), (120.0, 61.0))
        self.assertAlmostEqual(snap.credits.ratio, 61 / 120)
        b = bundle()
        b["bodies"].pop("credit_progress")
        snap = build_snapshot(b)
        self.assertEqual(snap.credits.earned, 3.0)   # only Triết học is completed (3 TC)


# ------------------------------------------------------- graduation requirements
class RequirementTests(unittest.TestCase):
    def req(self, snap):
        return {r.key: r for r in snap.requirements}

    def test_course_evidence(self):
        b = bundle(current_semester={"id": 76, "name": "Hè", "courses": [
            {"maHocPhan": "TT01", "tenMonHoc": "General English 1", "soTinChi": 0}]})
        r = self.req(build_snapshot(b))
        self.assertEqual(r["pe"].status, RequirementStatus.MET)                 # GDTC1 passed
        self.assertEqual(r["english"].status, RequirementStatus.IN_PROGRESS)    # TT01 running
        self.assertEqual(r["it"].status, RequirementStatus.NOT_MET)             # 006124 untouched
        self.assertEqual(r["defense"].status, RequirementStatus.NOT_MET)        # GDQP1 untouched

    def test_certificate_satisfies_requirement(self):
        b = bundle(bodies={"certificates": [
            {"tenChungChi": "IELTS 6.5", "ngayCap": "2025-01-01"},
            {"tenChungChi": "MOS Excel", "isDat": True},
            {"tenChungChi": "Chứng chỉ Tin học khác", "isDat": False},
        ]})
        r = self.req(build_snapshot(b))
        self.assertEqual(r["english"].status, RequirementStatus.MET)
        self.assertIn("IELTS", r["english"].detail)
        self.assertEqual(r["it"].status, RequirementStatus.MET)

    def test_invalid_certificate_is_ignored(self):
        b = bundle(bodies={"certificates": [{"tenChungChi": "MOS", "isDat": False}]})
        self.assertNotEqual(self.req(build_snapshot(b))["it"].detail[:9], "Chứng chỉ")

    def test_portal_flag_overrides_everything(self):
        b = bundle(bodies={
            "certificates": [{"tenChungChi": "IELTS 7.0"}],
            "graduation_info": {"chuanDauRaNgoaiNgu": "Chưa đạt", "gdqp": True},
        })
        r = self.req(build_snapshot(b))
        self.assertEqual(r["english"].status, RequirementStatus.NOT_MET)  # Portal beats the cert
        self.assertEqual(r["defense"].status, RequirementStatus.MET)

    def test_no_evidence_is_unknown_not_a_guess(self):
        b = bundle()
        b["bodies"]["curriculum"] = [{"hocKy": 1, "monHocs": [
            {"maHocPhan": "X1", "tenMonHoc": "Toán", "soTinChi": 3}]}]
        b["transcript"] = []
        r = self.req(build_snapshot(b))
        for key in ("english", "it", "defense", "pe"):
            self.assertEqual(r[key].status, RequirementStatus.UNKNOWN, key)

    def test_portal_verdict_extracted(self):
        b = bundle(bodies={"graduation_info": {"duDieuKien": False, "lyDo": "Thiếu 59 tín chỉ"}})
        snap = build_snapshot(b)
        self.assertIs(snap.portal_verdict, False)
        self.assertIn("Thiếu", snap.portal_message)

    def test_all_four_requirements_always_present_in_order(self):
        snap = build_snapshot({})
        self.assertEqual([r.key for r in snap.requirements], ["english", "it", "defense", "pe"])


# ------------------------------------------- x/N progress (English, GDQP)
def _fw_course(code, name, credits, sem_no=1):
    return {"maHocPhan": code, "tenMonHoc": name, "soTinChi": credits, "hocKy": sem_no}


GDQP_4 = (
    ("0120007201", "Đường lối quốc phòng và an ninh của đảng cộng sản VN", 3),
    ("0120007202", "Công tác quốc phòng và an ninh", 2),
    ("0120007203", "Quân sự chung", 1),
    ("0120007204", "Kỹ thuật chiến đấu bộ binh và chiến thuật", 2),
)


def _progress_bundle(passed_english=(), passed_gdqp=(), running_english=()):
    fw = [{"hocKy": 1, "monHocs": [
        *[{"maHocPhan": f"TT0{n}", "tenMonHoc": f"General English {n}", "soTinChi": 0}
          for n in (1, 2, 3, 4)],
        *[{"maHocPhan": c, "tenMonHoc": n, "soTinChi": t} for c, n, t in GDQP_4],
    ]}]
    done = [course(f"TT0{n}", f"General English {n}", 0, 8.0, "B") for n in passed_english]
    done += [course(c, n, t, 8.0, "B") for c, n, t in GDQP_4 if c in passed_gdqp]
    cur = {"id": 76, "name": "HK1 2026-2027", "courses": [
        {"maHocPhan": f"TT0{n}", "tenMonHoc": f"General English {n}", "soTinChi": 0}
        for n in running_english]}
    return bundle(
        bodies={"curriculum": fw},
        transcript=[sem(74, "HK1 2025-2026", done)] if done else [],
        current_semester=cur,
    )


class ProgressRequirementTests(unittest.TestCase):
    def req(self, snap, key):
        return {r.key: r for r in snap.requirements}[key]

    def test_english_counts_levels_out_of_four(self):
        r = self.req(build_snapshot(_progress_bundle(passed_english=(1, 2, 3))), "english")
        self.assertEqual((r.progress_done, r.progress_total), (3, 4))
        self.assertEqual(r.progress_text, "3/4")
        self.assertEqual(r.status, RequirementStatus.NOT_MET)

    def test_english_met_only_when_all_four_levels_done(self):
        r = self.req(build_snapshot(_progress_bundle(passed_english=(1, 2, 3, 4))), "english")
        self.assertEqual(r.progress_text, "4/4")
        self.assertEqual(r.status, RequirementStatus.MET)

    def test_english_running_level_is_in_progress(self):
        snap = build_snapshot(_progress_bundle(passed_english=(1,), running_english=(2,)))
        r = self.req(snap, "english")
        self.assertEqual(r.progress_text, "2/4")   # studying level 2 = level 2 reached
        self.assertEqual(r.status, RequirementStatus.IN_PROGRESS)

    def test_currently_in_general_english_3_is_3_of_4(self):
        # Student placed into / currently studying level 3 (1-2 not in the data).
        b = _progress_bundle(running_english=(3,))
        r = self.req(build_snapshot(b), "english")
        self.assertEqual(r.progress_text, "3/4")
        self.assertEqual(r.status, RequirementStatus.IN_PROGRESS)
        self.assertIn("General English 3", r.detail)

    def test_english_met_only_when_level_4_completed(self):
        r = self.req(build_snapshot(_progress_bundle(passed_english=(1, 2, 3))), "english")
        self.assertNotEqual(r.status, RequirementStatus.MET)
        r = self.req(build_snapshot(_progress_bundle(passed_english=(4,))), "english")
        self.assertEqual((r.progress_text, r.status), ("4/4", RequirementStatus.MET))

    def test_all_past_terms_listed_when_current_term_is_not_in_transcript(self):
        # Year 3 HK1 running (id 77, not in transcript); transcript holds the
        # earlier terms. The newest finished term must not be dropped.
        b = bundle(
            transcript=[
                sem(71, "HK1 2024-2025", [course("A1", "Môn A", 3, 8.0, "B+")]),
                sem(72, "HK2 2024-2025", [course("A2", "Môn B", 3, 7.0, "B")]),
                sem(74, "HK1 2025-2026", [course("A3", "Môn C", 3, 9.0, "A")]),
                sem(75, "HK2 2025-2026", [course("A4", "Môn D", 3, 8.5, "A")]),
            ],
            current_semester={"id": 77, "name": "Học kỳ 1 năm học 2026-2027", "courses": [
                {"maHocPhan": "TT03", "tenMonHoc": "General English 3", "soTinChi": 0}]},
        )
        snap = build_snapshot(b)
        labels = [h.label for h in snap.history]
        self.assertEqual(labels, ["HK2 2025-2026", "HK1 2025-2026",
                                  "HK2 2024-2025", "HK1 2024-2025"])

    def test_empty_main_term_between_is_listed_summer_is_not(self):
        b = bundle(
            transcript=[
                sem(71, "HK1 2024-2025", [course("A1", "Môn A", 3, 8.0, "B+")]),
                sem(74, "HK1 2025-2026", [course("A3", "Môn C", 3, 9.0, "A")]),
            ],
            current_semester={"id": 76, "name": "Học kỳ 2 năm học 2025-2026", "courses": [
                {"maHocPhan": "T9", "tenMonHoc": "Môn Z", "soTinChi": 3}]},
        )
        labels = [h.label for h in build_snapshot(b).history]
        self.assertEqual(labels, ["HK1 2025-2026", "Học kỳ 2 · 2024-2025", "HK1 2024-2025"])

    def test_english_level_outside_1_to_4_is_not_counted(self):
        s = build_snapshot(_progress_bundle(passed_english=(1,)))
        # a hypothetical "General English 5" must not push the count past 4
        from core.learning_roadmap.builder import _english_level
        from core.learning_roadmap.models import RoadmapCourse
        c = RoadmapCourse("X", "General English 5", 0, CourseStatus.COMPLETED)
        self.assertIsNone(_english_level(c))
        self.assertEqual(self.req(s, "english").progress_text, "1/4")

    def test_defense_is_the_four_courses(self):
        passed = ("0120007201", "0120007203")
        r = self.req(build_snapshot(_progress_bundle(passed_gdqp=passed)), "defense")
        self.assertEqual(r.progress_text, "2/4")
        self.assertEqual(r.status, RequirementStatus.NOT_MET)

    def test_defense_met_with_all_four(self):
        r = self.req(build_snapshot(_progress_bundle(
            passed_gdqp=tuple(c for c, _, _ in GDQP_4))), "defense")
        self.assertEqual(r.progress_text, "4/4")
        self.assertEqual(r.status, RequirementStatus.MET)

    def test_defense_missing_course_counts_as_not_done(self):
        b = _progress_bundle(passed_gdqp=("0120007201",))
        b["bodies"]["curriculum"][0]["monHocs"] = [
            m for m in b["bodies"]["curriculum"][0]["monHocs"] if m["maHocPhan"] != "0120007204"]
        r = self.req(build_snapshot(b), "defense")
        self.assertEqual(r.progress_text, "1/4")           # total stays 4

    def test_portal_flag_true_fills_the_bar(self):
        b = _progress_bundle(passed_gdqp=("0120007201",))
        b["bodies"]["graduation_info"] = {"gdqp": True}
        r = self.req(build_snapshot(b), "defense")
        self.assertEqual((r.status, r.progress_text), (RequirementStatus.MET, "4/4"))

    def test_certificate_fills_english_bar(self):
        b = _progress_bundle()
        b["bodies"]["certificates"] = [{"tenChungChi": "IELTS 6.5"}]
        r = self.req(build_snapshot(b), "english")
        self.assertEqual((r.status, r.progress_text), (RequirementStatus.MET, "4/4"))

    def test_program_certificate_does_not_fake_english_4_of_4(self):
        b = _progress_bundle(passed_english=(1, 2, 3))
        b["bodies"]["certificates"] = [{"tenChungChi": "Chương trình Tiếng Anh", "isDat": True}]
        r = self.req(build_snapshot(b), "english")
        self.assertEqual(r.progress_text, "3/4")
        self.assertNotEqual(r.status, RequirementStatus.MET)

    def test_english_counted_even_when_rows_do_not_match_the_framework(self):
        # Transcript rows use class codes / different wording and the framework
        # lists no English at all: 3 passed levels must still read 3/4.
        b = bundle(
            bodies={"curriculum": [{"hocKy": 1, "monHocs": [
                {"maHocPhan": "X1", "tenMonHoc": "Toán", "soTinChi": 3}]}]},
            transcript=[sem(74, "HK1 2025-2026", [
                course("251_TT01_01", "General English 1", 0, 8.0, "B"),
                course("251_TT02_01", "General English Level 2", 0, 7.0, "B"),
                course("251_TT03_01", "General English III", 0, 9.0, "A"),
            ])],
        )
        r = self.req(build_snapshot(b), "english")
        self.assertEqual(r.progress_text, "3/4")
        self.assertEqual(r.status, RequirementStatus.NOT_MET)

    def test_exempted_english_levels_count(self):
        b = _progress_bundle(passed_english=(3,))
        b["bodies"]["exemptions"] = [
            {"maHocPhan": "TT01", "tenMonHoc": "General English 1"},
            {"maHocPhan": "TT02", "tenMonHoc": "General English 2"}]
        self.assertEqual(self.req(build_snapshot(b), "english").progress_text, "3/4")

    def test_it_and_pe_have_no_progress_count(self):
        snap = build_snapshot(_progress_bundle())
        self.assertFalse(self.req(snap, "it").has_progress)
        self.assertFalse(self.req(snap, "pe").has_progress)


# ------------------------------------------------------------------- filtering
class FilterTests(unittest.TestCase):
    def setUp(self):
        self.snap = build_snapshot(bundle())

    def codes(self, sems):
        return [c.code for s in sems for c in s.courses]

    def test_accent_and_case_insensitive_search(self):
        for q in ("co so du lieu", "CƠ SỞ DỮ LIỆU", "quan tri"):
            self.assertIn("0120121003", self.codes(filter_semesters(self.snap.semesters, q)), q)

    def test_search_by_code_and_multiple_terms(self):
        self.assertEqual(self.codes(filter_semesters(self.snap.semesters, "0120999001")), ["0120999001"])
        self.assertEqual(self.codes(filter_semesters(self.snap.semesters, "triet mac")), ["0120005105"])
        self.assertEqual(self.codes(filter_semesters(self.snap.semesters, "triet zzz")), [])

    def test_actionable_is_failed_plus_not_taken(self):
        got = {c.status for s in filter_semesters(self.snap.semesters, "", FILTER_ACTIONABLE) for c in s.courses}
        self.assertEqual(got, {CourseStatus.FAILED, CourseStatus.NOT_TAKEN})

    def test_single_status_and_combination_with_query(self):
        done = filter_semesters(self.snap.semesters, "", CourseStatus.COMPLETED.value)
        self.assertTrue(all(c.status is CourseStatus.COMPLETED for s in done for c in s.courses))
        self.assertEqual(self.codes(filter_semesters(self.snap.semesters, "triet", CourseStatus.FAILED.value)), [])

    def test_empty_semesters_are_dropped_and_all_is_identity(self):
        self.assertEqual(filter_semesters(self.snap.semesters, "", FILTER_ALL), list(self.snap.semesters))
        self.assertEqual(filter_semesters(self.snap.semesters, "khong ton tai"), [])


# --------------------------------------------------------------------- service
class FakeClient:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, 0

    def fetch_roadmap_bundle(self, u, p):
        self.calls += 1
        if self.error:
            raise self.error
        return json.loads(json.dumps(self.result))


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.now = datetime(2026, 11, 25, 9, 0)

    def svc(self, client, ttl=12.0):
        return LearningRoadmapService(client, self.dir, ttl_hours=ttl, clock=lambda: self.now)

    def test_network_then_fresh_cache_skips_network(self):
        client = FakeClient(bundle())
        s = self.svc(client)
        r1 = s.load("u", "p")
        self.assertEqual((r1.source, client.calls), (SOURCE_NETWORK, 1))
        self.now += timedelta(hours=1)
        r2 = s.load("u", "p")
        self.assertEqual((r2.source, client.calls), (SOURCE_CACHE, 1))
        self.assertEqual(len(r2.snapshot.all_courses()), len(r1.snapshot.all_courses()))

    def test_force_refresh_and_expired_cache_hit_network(self):
        client = FakeClient(bundle())
        s = self.svc(client)
        s.load("u", "p")
        s.load("u", "p", force_refresh=True)
        self.now += timedelta(hours=13)
        s.load("u", "p")
        self.assertEqual(client.calls, 3)

    def test_offline_serves_stale_cache_and_says_so(self):
        s = self.svc(FakeClient(bundle()))
        s.load("u", "p")
        self.now += timedelta(days=3)
        offline = self.svc(FakeClient(error=PortalError("Portal network request failed")))
        r = offline.load("u", "p")
        self.assertEqual(r.source, SOURCE_OFFLINE_CACHE)
        self.assertTrue(r.is_offline)
        self.assertIsNotNone(r.snapshot)
        self.assertIn("network", r.error)
        self.assertEqual(r.fetched_at, datetime(2026, 11, 25, 9, 0))

    def test_offline_without_cache_is_none(self):
        r = self.svc(FakeClient(error=PortalError("down"))).load("u", "p")
        self.assertEqual((r.source, r.snapshot), (SOURCE_NONE, None))

    def test_no_credentials_uses_cache_else_none(self):
        self.assertEqual(self.svc(None).load().source, SOURCE_NONE)
        self.svc(FakeClient(bundle())).load("u", "p")
        self.now += timedelta(days=2)
        r = self.svc(None).load()
        self.assertEqual(r.source, SOURCE_OFFLINE_CACHE)

    def test_cache_contains_no_credentials_or_token(self):
        self.svc(FakeClient(bundle())).load("student123", "s3cret-pass")
        text = (self.dir / "learning_roadmap_cache.json").read_text(encoding="utf-8")
        self.assertNotIn("s3cret-pass", text)
        self.assertNotIn("student123", text)

    def test_corrupt_or_wrong_schema_cache_is_ignored(self):
        (self.dir / "learning_roadmap_cache.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(self.svc(FakeClient(error=PortalError("x"))).load("u", "p").source, SOURCE_NONE)
        (self.dir / "learning_roadmap_cache.json").write_text(
            json.dumps({"schema": 999, "bundle": {}, "fetched_at": self.now.isoformat()}), encoding="utf-8")
        self.assertEqual(self.svc(FakeClient(error=PortalError("x"))).load("u", "p").source, SOURCE_NONE)

    def test_partial_failure_keeps_previously_cached_body(self):
        s = self.svc(FakeClient(bundle()))
        s.load("u", "p")
        partial = bundle()
        partial["bodies"].pop("curriculum")
        partial["errors"] = {"curriculum": "HTTP 500"}
        self.now += timedelta(days=1)
        r = self.svc(FakeClient(partial)).load("u", "p")
        self.assertEqual(r.source, SOURCE_NETWORK)
        self.assertTrue(r.snapshot.framework_available)           # curriculum carried over
        self.assertIn("curriculum", r.snapshot.endpoint_errors)    # but flagged as not refreshed
        self.assertTrue(self.svc(None).load().snapshot.framework_available)


# ---------------------------------------------------------------- portal client
class _Resp:
    def __init__(self, payload, status=200):
        self._raw = gzip.compress(json.dumps(payload).encode())
        self.headers = {"Content-Encoding": "gzip"}
        self.status = status

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakePortal:
    """Routes by URL path. `overrides[path]` may be a payload, an int (HTTP status),
    or an Exception to raise; a list is consumed one item per call."""

    def __init__(self, overrides=None, expire_first_token=False):
        self.calls: list[str] = []
        self.logins = 0
        self.overrides = overrides or {}
        self.expire = expire_first_token

    def __call__(self, request, timeout=None):
        path = request.full_url.split("/api/v1/")[1].split("?")[0]
        self.calls.append(path)
        if path == "user/login":
            self.logins += 1
            return _Resp({"success": True, "status": 200, "token": f"tok{self.logins}"})
        if self.expire and request.get_header("Authorization") == "Bearer tok1":
            raise urllib.error.HTTPError(request.full_url, 401, "x", {}, io.BytesIO(b""))
        ov = self.overrides.get(path)
        if isinstance(ov, list):
            ov = ov.pop(0)
        if isinstance(ov, Exception):
            raise ov
        if isinstance(ov, int):
            raise urllib.error.HTTPError(request.full_url, ov, "x", {}, io.BytesIO(b""))
        if ov is not None:
            return _Resp(ov)
        defaults = {
            "hoctap/chuongtrinhkhung": {"success": True, "body": curriculum()},
            "hoctap/tiendo": {"success": True, "body": {"canDat": 120, "hienTai": 61}},
            "hoctap/hocky": {"success": True, "body": [{"id": 75, "tenDot": "HK2"}, {"id": 76, "tenDot": "Hè"}]},
            "hoctap/montheoky/76": {"success": True, "body": [
                {"id": 1, "maHocPhan": "0120122010", "tenMonHoc": "XD phần mềm hướng đối tượng", "soTinChi": 3}]},
            "hoctap/bangdiem2": {"success": True, "body": [sem(74, "HK1", [
                course("0120005105", "Triết học Mác - Lênin", 3, 7.5, "B")])]},
        }
        return _Resp(defaults.get(path, {"success": True, "body": []}))


class PortalClientRoadmapTests(unittest.TestCase):
    def client(self, portal):
        return PortalClient("https://portal.test/api/v1", opener=portal)

    def test_bundle_collects_everything_and_picks_latest_semester(self):
        portal = FakePortal()
        b = self.client(portal).fetch_roadmap_bundle("u", "p")
        self.assertEqual(b["errors"], {})
        self.assertEqual(b["current_semester"]["id"], 76)       # max id, regardless of order
        self.assertIn("hoctap/montheoky/76", portal.calls)
        self.assertEqual(portal.logins, 1)                       # one session for ~10 calls
        for key in ("curriculum", "exemptions", "certificates", "graduation_certificates",
                    "graduation_info", "credit_progress"):
            self.assertIn(key, b["bodies"])
        self.assertNotIn("tok1", json.dumps(b))                  # token never leaks into the bundle
        snap = build_snapshot(b)
        self.assertEqual(by_code(snap)["0120122010"].status, CourseStatus.IN_PROGRESS)
        self.assertEqual(by_code(snap)["0120005105"].status, CourseStatus.COMPLETED)

    def test_single_endpoint_http_error_degrades_only_that_endpoint(self):
        b = self.client(FakePortal({"hoctap/diemMien": 500})).fetch_roadmap_bundle("u", "p")
        self.assertEqual(b["errors"], {"exemptions": "HTTP 500"})
        self.assertIn("curriculum", b["bodies"])

    def test_expired_token_relogs_in_once_and_continues(self):
        portal = FakePortal(expire_first_token=True)
        b = self.client(portal).fetch_roadmap_bundle("u", "p")
        self.assertEqual(b["errors"], {})
        self.assertGreaterEqual(portal.logins, 2)

    def test_network_failure_aborts_instead_of_hammering(self):
        portal = FakePortal({"hoctap/chuongtrinhkhung": urllib.error.URLError("offline")})
        with self.assertRaises(PortalError):
            self.client(portal).fetch_roadmap_bundle("u", "p")
        self.assertLess(len(portal.calls), 5)                    # stopped early

    def test_rejected_login_raises_auth_error(self):
        def opener(request, timeout=None):
            return _Resp({"success": False, "status": 401})
        with self.assertRaises(PortalAuthenticationError):
            PortalClient("https://portal.test/api/v1", opener=opener).fetch_roadmap_bundle("u", "bad")

    def test_missing_credentials_raise_before_any_request(self):
        portal = FakePortal()
        with self.assertRaises(PortalAuthenticationError):
            self.client(portal).fetch_roadmap_bundle("", "")
        self.assertEqual(portal.calls, [])


if __name__ == "__main__":
    unittest.main()
