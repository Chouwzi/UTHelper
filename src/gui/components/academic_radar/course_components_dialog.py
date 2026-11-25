from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
import logging
import re
from typing import Any, Awaitable, Callable, Optional

import flet as ft

from core.academic_radar.gpa_planner import PASS_POINT, course_point4
from core.academic_radar.models import CourseGradeItem
from core.courses.models import CourseGradeItem as CoreMoodleGradeItem
from gui.components.course_grade_table import grade_rows_to_moodle_items
from gui.components.courses._shared import safe_control_update
from gui.components.courses.grade_rows import (
    build_grouped_grade_card,
    build_pending_grades_tile,
    split_and_sort_grade_rows,
)
from gui.core.theme import C

logger = logging.getLogger(__name__)

_PORTAL_FIELD_LABELS: dict[str, str] = {
    "diemThi1": "Điểm thi lần 1",
    "diemThiLan1": "Điểm thi lần 1",
    "diemThi2": "Điểm thi lần 2",
    "diemThiLan2": "Điểm thi lần 2",
    "diemTongKet1": "Điểm tổng kết lần 1",
    "diemTongKet2": "Điểm tổng kết lần 2",
    "diemChuyenCan": "Điểm chuyên cần",
    "diemGiuaKy": "Điểm giữa kỳ",
    "diemThucHanh": "Điểm thực hành",
    "diemKiemTra": "Điểm kiểm tra",
    "diemTieuLuan": "Điểm tiểu luận",
    "diemCong": "Điểm cộng",
    "diemTichLuy": "Điểm tích lũy",
    "diemHocKy": "Điểm học kỳ",
    "xepLoai": "Xếp loại",
    "tenLopHocPhan": "Lớp học phần",
    "nhomHocPhan": "Nhóm học phần",
    "loaiHocPhan": "Loại học phần",
    "tinhChatMonHoc": "Tính chất môn học",
    "hinhThucThi": "Hình thức thi",
    "giangVien": "Giảng viên",
    "ghiChu": "Ghi chú",
    "lyDo": "Lý do",
    "isKhongTinhDiem": "Không tính điểm GPA",
    "isHocLai": "Học lại",
    "isCaiThien": "Học cải thiện",
    "isMien": "Miễn học",
    "isCamThi": "Cấm thi",
    "isVangThi": "Vắng thi",
    "soLanThi": "Số lần thi",
    "ngayThi": "Ngày thi",
    "phongThi": "Phòng thi",
}

_ELIGIBILITY_LABELS: dict[str, str] = {
    "academic": "Tính vào GPA",
    "conditional": "Học phần điều kiện",
    "transferred": "Chuyển điểm / Miễn",
    "unknown": "Chưa xác định",
}


@dataclass
class MoodleCourseGradesResult:
    """Resolved Moodle gradebook payload for a Portal course popup."""

    grades: list[CoreMoodleGradeItem] = field(default_factory=list)
    course_id: Optional[int] = None
    course_title: str = ""
    source: str = ""  # "courses" | "thnn" | "monitor"
    last_synced_at: int = 0
    needs_async_fetch: bool = False


def _humanize_portal_key(key: str) -> str:
    if key in _PORTAL_FIELD_LABELS:
        return _PORTAL_FIELD_LABELS[key]
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(key).replace("_", " ")).strip()
    return spaced[:1].upper() + spaced[1:] if spaced else str(key)


def _format_portal_value(value: Any) -> str:
    if isinstance(value, bool):
        return "Có" if value else "Không"
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:.2f}".rstrip("0").rstrip(".")
    if isinstance(value, (list, tuple)):
        parts = [_format_portal_value(v) for v in value if v not in (None, "")]
        return ", ".join(p for p in parts if p)
    if isinstance(value, dict):
        parts = [
            f"{_humanize_portal_key(str(k))}: {_format_portal_value(v)}"
            for k, v in value.items()
            if v not in (None, "")
        ]
        return "; ".join(parts)
    return str(value).strip()


def _format_sync_time(ts: int) -> str:
    if not ts or ts <= 0:
        return ""
    try:
        dt = datetime.fromtimestamp(ts)
    except (ValueError, OSError, OverflowError):
        return ""
    return f"Đồng bộ {dt.strftime('%H:%M %d/%m/%Y')}"


def _legacy_moodle_result(moodle_course: Optional[Any]) -> MoodleCourseGradesResult:
    report = getattr(moodle_course, "report", None)
    rows = getattr(report, "rows", None) or []
    grades = grade_rows_to_moodle_items(rows)
    return MoodleCourseGradesResult(
        grades=grades,
        course_id=getattr(moodle_course, "course_id", None),
        course_title=str(getattr(moodle_course, "name", "") or ""),
        source="monitor" if grades else "",
        needs_async_fetch=False,
    )


def _build_kv_row(
    label: str,
    value: str,
    *,
    value_color: Optional[str] = None,
    bold: bool = False,
    is_mobile: bool = False,
) -> ft.Container:
    return ft.Container(
        padding=ft.Padding.symmetric(
            horizontal=12 if is_mobile else 14,
            vertical=9,
        ),
        content=ft.Row(
            spacing=10,
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Text(
                    label,
                    size=12.5,
                    color=C.TEXT_SECONDARY,
                    expand=2,
                    max_lines=2,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                ft.Text(
                    value,
                    size=12.5,
                    weight=ft.FontWeight.W_700 if bold else ft.FontWeight.W_600,
                    color=value_color or C.TEXT_PRIMARY,
                    expand=3,
                    text_align=ft.TextAlign.RIGHT,
                    max_lines=3,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
            ],
        ),
    )


def _build_grouped_kv_card(rows: list[ft.Control], *, is_mobile: bool = False) -> ft.Container:
    children: list[ft.Control] = []
    for idx, row in enumerate(rows):
        if idx > 0:
            children.append(
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=12 if is_mobile else 14),
                    content=ft.Divider(height=1, thickness=0.6, color=C.BORDER),
                )
            )
        children.append(row)
    return ft.Container(
        bgcolor=C.SURFACE,
        border=ft.Border.all(1, C.BORDER),
        border_radius=12,
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        content=ft.Column(spacing=0, controls=children),
    )


def build_portal_tab_body(
    course: CourseGradeItem,
    *,
    is_mobile: bool = False,
) -> ft.Control:
    """Render Portal component breakdowns and omitted Portal metadata without duplicating QT/Thi/TK."""
    controls: list[ft.Control] = []

    controls.append(
        ft.Text(
            "ĐIỂM THÀNH PHẦN PORTAL",
            size=11,
            weight=ft.FontWeight.W_700,
            color=C.TEXT_SECONDARY,
        )
    )
    if course.components:
        comp_rows: list[ft.Control] = []
        for comp in course.components:
            weight_str = ""
            if comp.weight is not None:
                weight_str = (
                    f"{comp.weight:.0f}%"
                    if abs(comp.weight - round(comp.weight)) < 0.05
                    else f"{comp.weight:.1f}%"
                )
            score_str = f"{comp.score:.1f}" if comp.score is not None else "Chưa có"
            left_items: list[ft.Control] = [
                ft.Text(
                    comp.name,
                    size=13,
                    weight=ft.FontWeight.W_600,
                    color=C.TEXT_PRIMARY,
                    max_lines=2,
                    overflow=ft.TextOverflow.ELLIPSIS,
                )
            ]
            if weight_str:
                left_items.append(
                    ft.Text(
                        f"Trọng số {weight_str}",
                        size=11,
                        color=C.TEXT_SECONDARY,
                    )
                )
            comp_rows.append(
                ft.Container(
                    padding=ft.Padding.symmetric(
                        horizontal=12 if is_mobile else 14,
                        vertical=9,
                    ),
                    content=ft.Row(
                        spacing=10,
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Column(spacing=2, expand=True, controls=left_items),
                            ft.Container(
                                bgcolor=C.SURFACE_ELEVATED,
                                border=ft.Border.all(1, C.BORDER),
                                border_radius=8,
                                padding=ft.Padding.symmetric(horizontal=10, vertical=5),
                                content=ft.Text(
                                    score_str,
                                    size=13.5,
                                    weight=ft.FontWeight.W_700,
                                    color=C.TEXT_PRIMARY
                                    if comp.score is not None
                                    else C.TEXT_SECONDARY,
                                ),
                            ),
                        ],
                    ),
                )
            )
        controls.append(_build_grouped_kv_card(comp_rows, is_mobile=is_mobile))
    else:
        controls.append(
            ft.Container(
                bgcolor=C.SURFACE,
                border=ft.Border.all(1, C.BORDER),
                border_radius=12,
                padding=ft.Padding.symmetric(horizontal=12, vertical=10),
                content=ft.Text(
                    "Portal không tách cột điểm thành phần cho học phần này.",
                    size=12,
                    color=C.TEXT_SECONDARY,
                ),
            )
        )

    detail_rows: list[ft.Control] = []
    if course.credit_grade is not None:
        detail_rows.append(
            _build_kv_row(
                "Điểm hệ 4",
                f"{course.credit_grade:.2f}",
                bold=True,
                is_mobile=is_mobile,
            )
        )

    if course.pass_status is False:
        pass_label, pass_color = "Chưa đạt", C.CRITICAL
    elif course.pass_status is True:
        pass_label, pass_color = "Đạt", C.SAFE
    elif course.final_grade is not None:
        pt4 = course_point4(course)
        passed = (
            bool(course.is_passed)
            and pt4 is not None
            and pt4 >= PASS_POINT
        )
        pass_label, pass_color = (
            ("Đạt", C.SAFE) if passed else ("Chưa đạt", C.CRITICAL)
        )
    else:
        pass_label, pass_color = "Chưa có kết quả", C.TEXT_SECONDARY
    detail_rows.append(
        _build_kv_row("Kết quả", pass_label, value_color=pass_color, bold=True, is_mobile=is_mobile)
    )

    elig_status = course.eligibility.status
    elig_text = _ELIGIBILITY_LABELS.get(elig_status, elig_status)
    if course.is_transferred and elig_status != "transferred":
        elig_text = f"{elig_text} · Chuyển điểm"
    detail_rows.append(_build_kv_row("Phân loại GPA", elig_text, is_mobile=is_mobile))
    if course.eligibility.reason:
        detail_rows.append(
            _build_kv_row("Ghi chú phân loại", course.eligibility.reason, is_mobile=is_mobile)
        )

    if course.subject_code:
        detail_rows.append(
            _build_kv_row("Mã học phần", course.subject_code, is_mobile=is_mobile)
        )
    class_code = course.class_code or course.course_code
    if class_code and class_code != course.subject_code:
        detail_rows.append(
            _build_kv_row("Mã lớp học phần", class_code, is_mobile=is_mobile)
        )
    if course.course_id and course.course_id not in {class_code, course.subject_code}:
        detail_rows.append(
            _build_kv_row("ID bản ghi Portal", course.course_id, is_mobile=is_mobile)
        )
    detail_rows.append(
        _build_kv_row(
            "Số tín chỉ",
            f"{course.credits:g} TC",
            is_mobile=is_mobile,
        )
    )

    for key, value in (course.extra_portal_fields or {}).items():
        formatted = _format_portal_value(value)
        if not formatted:
            continue
        detail_rows.append(
            _build_kv_row(
                _humanize_portal_key(key),
                formatted,
                is_mobile=is_mobile,
            )
        )

    controls.append(
        ft.Text(
            "CHI TIẾT HỌC PHẦN (PORTAL)",
            size=11,
            weight=ft.FontWeight.W_700,
            color=C.TEXT_SECONDARY,
        )
    )
    controls.append(_build_grouped_kv_card(detail_rows, is_mobile=is_mobile))

    return ft.Column(
        controls=controls,
        spacing=10,
        scroll=ft.ScrollMode.AUTO,
    )


def build_course_tab_body(
    result: MoodleCourseGradesResult,
    *,
    loading: bool = False,
    is_mobile: bool = False,
) -> ft.Control:
    """Render Moodle course-level grades matching `Môn học -> Điểm môn`."""
    if loading:
        return ft.Container(
            padding=ft.Padding.symmetric(vertical=32, horizontal=12),
            alignment=ft.Alignment.CENTER,
            content=ft.Column(
                spacing=10,
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.ProgressRing(width=24, height=24, stroke_width=2.5, color=C.ACCENT),
                    ft.Text(
                        "Đang tải bảng điểm Moodle...",
                        size=12.5,
                        color=C.TEXT_SECONDARY,
                        text_align=ft.TextAlign.CENTER,
                    ),
                ],
            ),
        )

    if not result.grades:
        return ft.Container(
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.BORDER),
            border_radius=12,
            padding=ft.Padding.symmetric(horizontal=16, vertical=28),
            alignment=ft.Alignment.CENTER,
            content=ft.Column(
                spacing=8,
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Icon(
                        ft.Icons.ASSIGNMENT_OUTLINED,
                        size=30,
                        color=C.TEXT_SECONDARY,
                    ),
                    ft.Text(
                        "Chưa có dữ liệu điểm trên Moodle cho môn học này.",
                        size=13,
                        weight=ft.FontWeight.W_600,
                        color=C.TEXT_PRIMARY,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    ft.Text(
                        "Đồng bộ môn học ở tab Môn học để cập nhật bảng điểm chi tiết từ Moodle.",
                        size=11.5,
                        color=C.TEXT_SECONDARY,
                        text_align=ft.TextAlign.CENTER,
                    ),
                ],
            ),
        )

    parsed_all, graded_rows, pending_rows = split_and_sort_grade_rows(result.grades)
    controls: list[ft.Control] = []

    if result.course_title or result.course_id:
        meta_parts: list[str] = []
        if result.course_title:
            meta_parts.append(result.course_title)
        if result.course_id:
            meta_parts.append(f"ID {result.course_id}")
        sync_lbl = _format_sync_time(result.last_synced_at)
        if sync_lbl:
            meta_parts.append(sync_lbl)
        controls.append(
            ft.Text(
                " · ".join(meta_parts),
                size=11,
                color=C.TEXT_SECONDARY,
                max_lines=2,
                overflow=ft.TextOverflow.ELLIPSIS,
            )
        )

    if graded_rows:
        controls.append(
            ft.Text(
                "ĐIỂM ĐÃ CẬP NHẬT",
                size=11,
                weight=ft.FontWeight.W_700,
                color=C.TEXT_SECONDARY,
            )
        )
        controls.append(build_grouped_grade_card(graded_rows, is_mobile=is_mobile))

    if pending_rows and graded_rows:
        controls.append(build_pending_grades_tile(pending_rows, is_mobile=is_mobile))
    elif pending_rows:
        controls.append(build_grouped_grade_card(pending_rows, is_mobile=is_mobile))

    scored_items = [r for r in graded_rows if not r.is_course_total]
    regular_total = [r for r in parsed_all if not r.is_course_total]
    total_count = len(regular_total) or len(parsed_all)
    scored_count = len(scored_items) if regular_total else len(graded_rows)
    source_label = (
        "Nguồn: Moodle (Môn học)"
        if result.source in {"courses", "thnn"}
        else "Nguồn: Moodle"
    )
    controls.append(
        ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            controls=[
                ft.Text(source_label, size=10.5, color=C.TEXT_SECONDARY),
                ft.Text(
                    f"{scored_count}/{total_count} mục đã có điểm",
                    size=10.5,
                    color=C.TEXT_SECONDARY,
                ),
            ],
        )
    )

    return ft.Column(
        controls=controls,
        spacing=10,
        scroll=ft.ScrollMode.AUTO,
    )


def build_course_components_body(
    course: CourseGradeItem,
    moodle_course: Optional[Any],
    width: float,
    max_height: float = 0.0,
    *,
    moodle_result: Optional[MoodleCourseGradesResult] = None,
    is_mobile: bool = False,
) -> ft.Control:
    """Build body for legacy callers (defaults to Portal or Course summary container)."""
    resolved = moodle_result if moodle_result is not None else _legacy_moodle_result(moodle_course)
    body = (
        build_course_tab_body(resolved, is_mobile=is_mobile)
        if resolved.grades
        else build_portal_tab_body(course, is_mobile=is_mobile)
    )
    return ft.Container(
        content=body,
        width=width,
        height=max_height if max_height > 0 else None,
    )


def show_course_components_dialog(
    page: ft.Page | None,
    course: CourseGradeItem,
    moodle_course: Optional[Any] = None,
    *,
    moodle_result: Optional[MoodleCourseGradesResult] = None,
    async_loader: Optional[Callable[[CourseGradeItem], Awaitable[MoodleCourseGradesResult]]] = None,
    on_dismiss: Optional[Callable[[], Any]] = None,
    on_closing: Optional[Callable[[], Any]] = None,
) -> Optional[Callable[[], None]]:
    """Mở popup 2 tab (Portal | Course) cho một học phần."""
    if page is None:
        logger.error("Course components dialog: no page available")
        return None
    try:
        page_w = float(getattr(page, "width", None) or 0)
    except (TypeError, ValueError):
        page_w = 0.0
    try:
        page_h = float(getattr(page, "height", None) or 0)
    except (TypeError, ValueError):
        page_h = 0.0

    is_narrow = 0 < page_w < 600
    body_w = max(240.0, min(500.0, page_w - 64.0)) if page_w else 480.0
    max_h = max(240.0, min(520.0, page_h * 0.65)) if page_h else 460.0

    initial_result = (
        moodle_result if moodle_result is not None else _legacy_moodle_result(moodle_course)
    )
    should_fetch_async = bool(
        initial_result.needs_async_fetch and callable(async_loader)
    )

    state: dict[str, Any] = {
        "tab_index": 0,  # 0 = Portal, 1 = Course
        "result": initial_result,
        "loading": should_fetch_async,
        "alive": True,
    }

    tab_titles = ("Portal", "Course")
    tab_labels = [
        ft.Text(
            title,
            size=12.5,
            weight=ft.FontWeight.W_600 if i == 0 else ft.FontWeight.W_500,
            color=C.TEXT_PRIMARY if i == 0 else C.TEXT_SECONDARY,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
            text_align=ft.TextAlign.CENTER,
        )
        for i, title in enumerate(tab_titles)
    ]
    tab_semantics = [
        ft.Semantics(
            label=title,
            button=True,
            selected=(i == 0),
            exclude_semantics=True,
            content=tab_labels[i],
        )
        for i, title in enumerate(tab_titles)
    ]

    body_host = ft.Container(
        expand=True,
        content=build_portal_tab_body(course, is_mobile=is_narrow),
    )

    def _render_active_tab() -> ft.Control:
        if state["tab_index"] == 0:
            return build_portal_tab_body(course, is_mobile=is_narrow)
        return build_course_tab_body(
            state["result"],
            loading=bool(state["loading"]),
            is_mobile=is_narrow,
        )

    def _sync_tab_visuals() -> None:
        idx = state["tab_index"]
        for i, pill in enumerate(tab_pills):
            active = i == idx
            pill.bgcolor = C.SURFACE if active else None
            pill.border = ft.Border.all(1, C.BORDER) if active else None
            tab_labels[i].weight = ft.FontWeight.W_600 if active else ft.FontWeight.W_500
            tab_labels[i].color = C.TEXT_PRIMARY if active else C.TEXT_SECONDARY
            tab_semantics[i].selected = active

    def _select_tab(idx: int) -> None:
        if idx not in (0, 1) or state["tab_index"] == idx:
            return
        state["tab_index"] = idx
        _sync_tab_visuals()
        body_host.content = _render_active_tab()
        safe_control_update(tab_track)
        safe_control_update(body_host)

    tab_pills: list[ft.Container] = [
        ft.Container(
            expand=True,
            height=34,
            padding=ft.Padding.symmetric(horizontal=6),
            alignment=ft.Alignment.CENTER,
            border_radius=8,
            bgcolor=C.SURFACE if i == 0 else None,
            border=ft.Border.all(1, C.BORDER) if i == 0 else None,
            ink=True,
            on_click=lambda _e, idx=i: _select_tab(idx),
            content=tab_semantics[i],
        )
        for i in range(2)
    ]
    tab_track = ft.Container(
        bgcolor=C.SURFACE_ELEVATED,
        border=ft.Border.all(1, C.BORDER),
        border_radius=11,
        padding=ft.Padding.all(3),
        content=ft.Row(spacing=4, controls=tab_pills),
    )

    used_overlay = False
    is_real_page = isinstance(page, ft.Page)

    def _notify_dismiss(_event=None) -> None:
        state["alive"] = False
        if callable(on_dismiss):
            try:
                on_dismiss()
            except Exception:
                logger.debug("Course components dialog on_dismiss failed", exc_info=True)

    def _on_close(_event=None) -> None:
        state["alive"] = False
        if callable(on_closing):
            try:
                on_closing()
            except Exception:
                logger.debug("Course components dialog on_closing failed", exc_info=True)
        popped = _close(page, dialog, used_overlay=used_overlay)
        if not popped or not is_real_page:
            _notify_dismiss()

    def _on_key_down(event) -> None:
        if getattr(event, "key", "") == "Escape":
            _on_close()

    dialog_content = ft.Container(
        width=body_w,
        height=max_h,
        content=ft.Column(
            spacing=10,
            expand=True,
            controls=[tab_track, body_host],
        ),
    )

    dialog = ft.AlertDialog(
        modal=True,
        semantics_label=f"Bảng điểm {course.course_name}",
        title=ft.Text(
            course.course_name,
            size=15,
            weight=ft.FontWeight.W_700,
            color=C.TEXT_PRIMARY,
            max_lines=2,
            overflow=ft.TextOverflow.ELLIPSIS,
            semantics_label=course.course_name,
        ),
        content=ft.KeyboardListener(
            autofocus=False,
            on_key_down=_on_key_down,
            content=dialog_content,
        ),
        inset_padding=ft.Padding.symmetric(horizontal=16 if is_narrow else 40, vertical=24),
        title_padding=ft.Padding.only(
            left=16 if is_narrow else 24,
            right=16 if is_narrow else 24,
            top=16,
            bottom=4,
        ),
        content_padding=ft.Padding.only(
            left=16 if is_narrow else 24,
            right=16 if is_narrow else 24,
            top=12,
            bottom=8,
        ),
        actions_padding=ft.Padding.only(left=16, right=16, top=0, bottom=8),
        actions=[ft.TextButton("Đóng", height=44, on_click=_on_close)],
        on_dismiss=_notify_dismiss,
        bgcolor=C.BG,
        shape=ft.RoundedRectangleBorder(radius=16),
    )
    try:
        page.show_dialog(dialog)
    except Exception:
        logger.warning("page.show_dialog failed; falling back to overlay", exc_info=True)
        used_overlay = True
        page.overlay.append(dialog)
        dialog.open = True
        page.update()

    if should_fetch_async:
        async def _load_async() -> None:
            try:
                loaded = await async_loader(course)
            except Exception:
                logger.debug("On-demand Moodle grade lookup failed", exc_info=True)
                loaded = MoodleCourseGradesResult(needs_async_fetch=False)
            if not state["alive"]:
                return
            state["result"] = loaded
            state["loading"] = False
            if state["tab_index"] == 1:
                body_host.content = _render_active_tab()
                safe_control_update(body_host)

        run_task = getattr(page, "run_task", None)
        scheduled = False
        if callable(run_task):
            try:
                run_task(_load_async)
                scheduled = True
            except Exception:
                logger.debug("page.run_task for Moodle grades failed", exc_info=True)
        if not scheduled:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                state["loading"] = False
            else:
                loop.create_task(_load_async())

    return _on_close


def _close(page: ft.Page, dialog: ft.AlertDialog, *, used_overlay: bool = False) -> bool:
    if not used_overlay:
        try:
            page.pop_dialog()
            return True
        except Exception:
            pass
    dialog.open = False
    try:
        dialog.update()
    except Exception:
        try:
            page.update()
        except Exception:
            pass
    return False
