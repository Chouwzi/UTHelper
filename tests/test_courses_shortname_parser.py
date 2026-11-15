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


def test_parse_rejects_single_four_digit_year_and_cross_field_semester_match():
    single_year = parse_course_identity(
        shortname="AI_2026",
        fullname="[HKI 2026] Nhập môn Trí tuệ nhân tạo",
    )
    assert single_year.semester_label == "Khác"
    assert single_year.semester_sort_key == (0, 0)

    cross_field = parse_course_identity(
        shortname="COURSE_HKI",
        fullname="2026-2027 Nhập môn Trí tuệ nhân tạo",
    )
    assert cross_field.semester_label == "Khác"
    assert cross_field.semester_sort_key == (0, 0)

    single_in_short_valid_in_full = parse_course_identity(
        shortname="[HKI 2026] CS",
        fullname="[HKI2627] - Hệ điều hành",
    )
    assert single_in_short_valid_in_full.semester_label == "HKI 2026-2027"
    assert single_in_short_valid_in_full.semester_sort_key == (2026, 3)

    single_year_2021 = parse_course_identity(
        shortname="AI_2021",
        fullname="[HKI 2021] Nhập môn AI",
    )
    assert single_year_2021.semester_label == "Khác"
    assert single_year_2021.semester_sort_key == (0, 0)

    dashed_20_21 = parse_course_identity(
        shortname="AI_2020",
        fullname="[HKI 20-21] Nhập môn AI",
    )
    assert dashed_20_21.semester_label == "HKI 2020-2021"
    assert dashed_20_21.semester_sort_key == (2020, 3)

    adjacent_tokens = parse_course_identity(
        shortname="HKI 2026_HKI2627",
        fullname="Môn học",
    )
    assert adjacent_tokens.semester_label == "HKI 2026-2027"
    assert adjacent_tokens.semester_sort_key == (2026, 3)

    adjacent_underscore_tokens = parse_course_identity(
        shortname="HKI_2026_HKI2627",
        fullname="Môn học",
    )
    assert adjacent_underscore_tokens.semester_label == "HKI 2026-2027"
    assert adjacent_underscore_tokens.semester_sort_key == (2026, 3)

    adjacent_long_tokens = parse_course_identity(
        shortname="HKI_2026-2029_HKI2026-2027",
        fullname="Môn học",
    )
    assert adjacent_long_tokens.semester_label == "HKI 2026-2027"
    assert adjacent_long_tokens.semester_sort_key == (2026, 3)


def test_parse_uth_shortname_handles_separated_semester_underscores_entities_and_dashes():
    spaced_sem = parse_course_identity(
        shortname="[TT]_HKI_2026-2027_Tin học_012012113707",
        fullname="[HKI2627] - [012012113707] - Tin học - CNS_CS1",
    )
    assert spaced_sem.semester_label == "HKI 2026-2027"
    assert spaced_sem.course_code == "012012113707"
    assert spaced_sem.display_name == "Tin học"

    underscored_title = parse_course_identity(
        shortname="[TT]_HKI2026-2027_Lập_trình_Python_012012113707",
        fullname="[HKI2627] - [012012113707] - Lập trình Python - CNS_CS1",
    )
    assert underscored_title.display_name == "Lập trình Python"

    html_entities = parse_course_identity(
        shortname="[TT]_HKI2026-2027_Cấu trúc dữ liệu &amp; Giải thuật_012012113707",
        fullname="[HKI2627] - [012012113707] - Cấu trúc dữ liệu &amp; Giải thuật - CNS_CS1",
    )
    assert html_entities.display_name == "Cấu trúc dữ liệu & Giải thuật"

    collapsed_dashes = parse_course_identity(
        shortname="012012113707",
        fullname="[HKI2627] - Môn học - [012012113707] - Nhóm 01 - CNS_CS1",
    )
    assert collapsed_dashes.display_name == "Môn học - Nhóm 01"


def test_extract_teacher_and_meet_info_multiline_html_and_inline_markers():
    multiline_fragments = [
        (
            "<p>Giảng viên: <strong>Võ Văn Bình</strong></p>"
            "<p>Email: binh.vo@ut.edu.vn</p>"
            "<p>SĐT: 0918 116 115</p>"
        ),
    ]
    info = extract_teacher_and_meet_info(multiline_fragments)
    assert info.teacher_name == "Võ Văn Bình"
    assert info.teacher_email == "binh.vo@ut.edu.vn"
    assert info.teacher_phone == "0918116115"
    assert info.meet_url == ""

    br_info = extract_teacher_and_meet_info(
        ["<p>GV phụ trách: Trần Thị Hoa<br/>Link học online: https://meet.google.com/zry-mvjo-dpe</p>"]
    )
    assert br_info.teacher_name == "Trần Thị Hoa"
    assert br_info.meet_url == "https://meet.google.com/zry-mvjo-dpe"

    inline_marker_info = extract_teacher_and_meet_info(
        ["<p>Giảng viên: Nguyễn Văn An - Email: an.nguyen@ut.edu.vn - SĐT: 0909.123.456</p>"]
    )
    assert inline_marker_info.teacher_name == "Nguyễn Văn An"
    assert inline_marker_info.teacher_email == "an.nguyen@ut.edu.vn"
    assert inline_marker_info.teacher_phone == "0909123456"


def test_extract_teacher_and_meet_info_mailto_zoom_subdomain_punctuation_and_non_url_phone():
    info = extract_teacher_and_meet_info(
        [
            '<p>Liên hệ GV: <a href="mailto:binh.vo@ut.edu.vn?subject=Help">Gửi email</a></p>',
            "<p>Link (https://uth.zoom.us/j/0912345678?pwd=xyz).</p>",
        ]
    )
    assert info.teacher_email == "binh.vo@ut.edu.vn"
    assert info.meet_url == "https://uth.zoom.us/j/0912345678?pwd=xyz"
    assert info.teacher_phone == ""


def test_extract_teacher_info_preserves_pretty_printed_table_and_wrapped_paragraphs():
    pretty_table_html = """
    <table>
      <tr>
        <td>Giảng viên:</td>
        <td>Võ Văn Bình</td>
      </tr>
      <tr>
        <td>Email:</td>
        <td>binh.vo@ut.edu.vn</td>
      </tr>
      <tr>
        <td>SĐT:</td>
        <td>028 3899 2862</td>
      </tr>
    </table>
    """
    table_info = extract_teacher_and_meet_info([pretty_table_html])
    assert table_info.teacher_name == "Võ Văn Bình"
    assert table_info.teacher_email == "binh.vo@ut.edu.vn"
    assert table_info.teacher_phone == "02838992862"

    wrapped_p_html = """
    <p>Giảng viên:
      <strong>Võ Văn Bình</strong>
    </p>
    <p>Email:
      binh.vo@ut.edu.vn</p>
    """
    wrapped_info = extract_teacher_and_meet_info([wrapped_p_html])
    assert wrapped_info.teacher_name == "Võ Văn Bình"
    assert wrapped_info.teacher_email == "binh.vo@ut.edu.vn"


def test_extract_teacher_phone_rejects_nine_digits_and_accepts_ten_to_eleven_digits():
    nine_digit_only = extract_teacher_and_meet_info(["<p>Mã: 012345678</p>"])
    assert nine_digit_only.teacher_phone == ""

    nine_then_ten = extract_teacher_and_meet_info(["<p>Mã: 012345678 - SĐT: 0918 116 115</p>"])
    assert nine_then_ten.teacher_phone == "0918116115"


