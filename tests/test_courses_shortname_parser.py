from dataclasses import FrozenInstanceError

import pytest

from core.courses import (
    CourseAttachment,
    CourseGradeItem,
    CourseModuleItem,
    CourseSection,
    CourseSource,
    CourseSummary,
    OpenStrategy,
    PreviewKind,
    RecentMaterialEntry,
)
from core.courses.shortname_parser import (
    ParsedCourseIdentity,
    TeacherContactInfo,
    extract_teacher_and_meet_info,
    parse_course_identity,
)


def test_parse_uth_shortname_extracts_semester_code_and_clean_name():
    parsed = parse_course_identity(
        shortname="[TT]_HKI2026-2027_Quản trị doanh nghiệp CNTT_012012113707",
        fullname="[HKI2627] - [012012113707] - Quản trị doanh nghiệp CNTT - CNS_CS1",
    )
    assert isinstance(parsed, ParsedCourseIdentity)
    assert parsed.program_tag == "TT"
    assert parsed.semester_label == "HKI 2026-2027"
    assert parsed.semester_sort_key == (2026, 3)
    assert parsed.course_code == "012012113707"
    assert parsed.display_name == "Quản trị doanh nghiệp CNTT"


def test_parse_thnn_and_event_course_names_gracefully():
    parsed_thnn = parse_course_identity(
        shortname="TA4_K24_NHOM_03",
        fullname="[HKII 25-26] Tiếng Anh 4 - Nhóm 03",
    )
    assert parsed_thnn.semester_label == "HKII 2025-2026"
    assert parsed_thnn.semester_sort_key == (2025, 2)
    assert "Tiếng Anh 4" in parsed_thnn.display_name

    parsed_thnn_long = parse_course_identity(
        shortname="TA4_K24_NHOM_03",
        fullname="[HKII 2025-2026] Tiếng Anh 4 - Nhóm 03",
    )
    assert parsed_thnn_long.semester_label == "HKII 2025-2026"
    assert parsed_thnn_long.semester_sort_key == (2025, 2)
    assert parsed_thnn_long.display_name == "Tiếng Anh 4 - Nhóm 03"

    parsed_event = parse_course_identity(
        shortname="CSTT_2026",
        fullname="Chuỗi sự kiện truyền thông 2026",
    )
    assert parsed_event.semester_label == "Khác"
    assert parsed_event.semester_sort_key == (0, 0)
    assert parsed_event.display_name == "Chuỗi sự kiện truyền thông 2026"


def test_parse_summer_semester_variants_and_empty_fallback():
    parsed_summer = parse_course_identity(
        shortname="[CLC]_HKH2025-2026_Lập trình Python_012012999901",
        fullname="[HKHE 25-26] - Lập trình Python - CS2",
    )
    assert parsed_summer.program_tag == "CLC"
    assert parsed_summer.semester_label == "HK Hè 2025-2026"
    assert parsed_summer.semester_sort_key == (2025, 1)
    assert parsed_summer.course_code == "012012999901"
    assert parsed_summer.display_name == "Lập trình Python"

    parsed_compact_summer = parse_course_identity(
        shortname="CS_EVENT",
        fullname="[HK3 25-26] - Seminar chuyên ngành - HTHN_CS1",
    )
    assert parsed_compact_summer.semester_label == "HK Hè 2025-2026"
    assert parsed_compact_summer.semester_sort_key == (2025, 1)
    assert parsed_compact_summer.display_name == "Seminar chuyên ngành"

    parsed_blank = parse_course_identity(shortname="  ", fullname="")
    assert parsed_blank.display_name == ""
    assert parsed_blank.semester_label == "Khác"
    assert parsed_blank.semester_sort_key == (0, 0)


def test_extract_teacher_and_meet_info_from_moodle_fragments():
    fragments = [
        "<p>Giảng viên: Võ Văn Bình (binh.vo@ut.edu.vn); DĐ 0918116115</p>",
        '<div>Link học online: <a href="https://meet.google.com/zry-mvjo-dpe?authuser=0">Meet</a></div>',
    ]
    info = extract_teacher_and_meet_info(fragments)
    assert isinstance(info, TeacherContactInfo)
    assert info.teacher_name == "Võ Văn Bình"
    assert info.teacher_email == "binh.vo@ut.edu.vn"
    assert info.teacher_phone == "0918116115"
    assert info.meet_url == "https://meet.google.com/zry-mvjo-dpe?authuser=0"


def test_extract_teacher_and_meet_info_handles_empty_and_text_links():
    info = extract_teacher_and_meet_info(
        [
            "",
            "<p>GV phụ trách - Nguyễn Thị Lan</p>",
            "<p>Phòng học Zoom: https://zoom.us/j/1234567890</p>",
        ]
    )
    assert info.teacher_name == "Nguyễn Thị Lan"
    assert info.teacher_email == ""
    assert info.teacher_phone == ""
    assert info.meet_url == "https://zoom.us/j/1234567890"


def test_course_models_defaults_immutability_and_key_property():
    summary = CourseSummary(
        id=31564,
        source="courses",
        fullname="Test",
        shortname="T",
        display_name="Test",
        course_code="0120",
        semester_label="HKI 2026-2027",
        semester_sort_key=(2026, 3),
    )
    assert summary.course_key == "courses:31564"
    assert summary.is_pinned is False
    assert summary.has_unseen_updates is False
    assert summary.progress is None

    module = CourseModuleItem(
        cmid=10,
        instance_id=20,
        modname="resource",
        name="Slide 1",
        section_id=100,
    )
    assert module.attachments == ()
    assert module.open_strategy == "web_fallback"

    attachment = CourseAttachment(file_name="a.pdf", file_url="https://example.com/a.pdf")
    assert attachment.preview_kind == "unsupported"
    assert attachment.is_archive is False

    section = CourseSection(
        id=100,
        course_id=31564,
        source="thnn",
        section_num=1,
        name="Topic 1",
    )
    assert section.modules == ()
    assert section.visible is True

    grade = CourseGradeItem(id=1, item_name="Giữa kỳ")
    assert grade.grade_formatted == "-"

    recent = RecentMaterialEntry(
        course_key=summary.course_key,
        course_name=summary.display_name,
        cmid=10,
        title="Slide 1",
        attachment_url="https://example.com/a.pdf",
        file_name="a.pdf",
        opened_at=1700000000,
    )
    assert recent.source == "courses"

    # Exported type aliases are importable
    source_val: CourseSource = "courses"
    strategy_val: OpenStrategy = "preview_file"
    preview_val: PreviewKind = "pdf"
    assert source_val == "courses"
    assert strategy_val == "preview_file"
    assert preview_val == "pdf"

    with pytest.raises(FrozenInstanceError):
        summary.is_pinned = True  # type: ignore[misc]
