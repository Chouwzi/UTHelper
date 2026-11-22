"""Shared Moodle gradebook row parsing and rendering (used by CourseDetailPane and Grade popup)."""

from __future__ import annotations

from dataclasses import dataclass
import html
import re
from typing import Optional

from bs4 import BeautifulSoup
import flet as ft

from core.courses.models import CourseGradeItem
from core.html_compat import BS4_PARSER
from gui.core.theme import C

_SCORE_NUM_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?")
_RANGE_NUMS_RE = re.compile(r"([-+]?\d+(?:[.,]\d+)?)\s*[-–—]\s*([-+]?\d+(?:[.,]\d+)?)")
_EMPTY_GRADE_TOKENS = frozenset({"", "-", "–", "—", "trống", "n/a", "null", "none"})
_COURSE_TOTAL_PHRASES = (
    "tổng kết môn",
    "điểm tổng kết",
    "tổng cộng khóa học",
    "course total",
    "trung bình môn",
)


@dataclass(frozen=True, slots=True)
class ParsedGradeRow:
    item: CourseGradeItem
    clean_name: str
    is_course_total: bool
    has_score: bool
    score_value: Optional[float]
    max_range: float
    ratio_0_1: Optional[float]
    score_on_10: Optional[float]
    weight_pct: float
    weight_label: str
    range_label: str
    pct_label: str
    feedback_text: str


def _parse_float_token(raw: str) -> Optional[float]:
    if not raw:
        return None
    m = _SCORE_NUM_RE.search(raw.replace(" ", ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "."))
    except ValueError:
        return None


def analyze_grade_item(item: CourseGradeItem) -> ParsedGradeRow:
    raw_name = html.unescape(str(item.item_name or "")).strip()
    clean_name = re.sub(r"\s+", " ", raw_name) or "Điểm thành phần"
    lower_name = clean_name.casefold()
    is_total = item.item_module == "course" or any(p in lower_name for p in _COURSE_TOTAL_PHRASES)

    raw_grade = html.unescape(str(item.grade_formatted or "")).strip()
    has_score = raw_grade.casefold() not in _EMPTY_GRADE_TOKENS and bool(
        _SCORE_NUM_RE.search(raw_grade)
    )
    score_val = _parse_float_token(raw_grade) if has_score else None

    raw_range = html.unescape(str(item.range_formatted or "")).strip()
    range_label = "" if raw_range.casefold() in _EMPTY_GRADE_TOKENS else raw_range
    max_range = 10.0
    if range_label:
        m_rng = _RANGE_NUMS_RE.search(range_label)
        if m_rng:
            try:
                parsed_max = float(m_rng.group(2).replace(",", "."))
                if parsed_max > 0:
                    max_range = parsed_max
            except ValueError:
                pass

    raw_pct = html.unescape(str(item.percentage or "")).strip()
    pct_val = (
        _parse_float_token(raw_pct)
        if raw_pct.casefold() not in _EMPTY_GRADE_TOKENS and "%" in raw_pct
        else None
    )
    pct_label = ""
    if pct_val is not None:
        pct_label = f"{pct_val:.1f}%".replace(".0%", "%")

    ratio: Optional[float] = None
    score_10: Optional[float] = None
    if score_val is not None and max_range > 0:
        ratio = max(0.0, min(1.0, score_val / max_range))
        score_10 = round(ratio * 10.0, 2)
    elif pct_val is not None:
        ratio = max(0.0, min(1.0, pct_val / 100.0))
        score_10 = round(ratio * 10.0, 2)

    raw_weight = html.unescape(str(item.weight or "")).strip()
    w_val = (
        _parse_float_token(raw_weight)
        if raw_weight.casefold() not in _EMPTY_GRADE_TOKENS
        else None
    )
    weight_pct = w_val if (w_val is not None and w_val > 0.009) else 0.0
    if weight_pct <= 0.0:
        inline_w = re.search(r"\((\d{1,3}(?:[.,]\d+)?)\s*%", clean_name)
        if inline_w:
            try:
                parsed_inline = float(inline_w.group(1).replace(",", "."))
                if 0.0 < parsed_inline <= 100.0:
                    weight_pct = parsed_inline
            except ValueError:
                pass
    weight_label = (
        f"{weight_pct:.1f}%".replace(".0%", "%") if weight_pct > 0 else ""
    )

    fb_text = ""
    if item.feedback_html:
        raw_fb = BeautifulSoup(item.feedback_html, BS4_PARSER).get_text(" ", strip=True)
        cleaned_fb = re.sub(r"\s+", " ", raw_fb).strip()
        if cleaned_fb.casefold() not in _EMPTY_GRADE_TOKENS:
            fb_text = cleaned_fb

    return ParsedGradeRow(
        item=item,
        clean_name=clean_name,
        is_course_total=is_total,
        has_score=has_score,
        score_value=score_val,
        max_range=max_range,
        ratio_0_1=ratio,
        score_on_10=score_10,
        weight_pct=weight_pct,
        weight_label=weight_label,
        range_label=range_label,
        pct_label=pct_label,
        feedback_text=fb_text,
    )


def score_semantic_color(score_on_10: Optional[float]) -> str:
    if score_on_10 is None:
        return C.TEXT_SECONDARY
    if score_on_10 >= 8.0:
        return C.SAFE
    if score_on_10 >= 6.5:
        return C.ACCENT
    if score_on_10 >= 5.0:
        return C.WARNING
    return C.CRITICAL


def score_rank_label(score_on_10: Optional[float]) -> str:
    if score_on_10 is None:
        return "Đang cập nhật"
    if score_on_10 >= 8.5:
        return "Xuất sắc"
    if score_on_10 >= 7.0:
        return "Khá · Tốt"
    if score_on_10 >= 5.0:
        return "Đạt yêu cầu"
    return "Cần củng cố"


def split_and_sort_grade_rows(
    grades: list[CourseGradeItem],
) -> tuple[list[ParsedGradeRow], list[ParsedGradeRow], list[ParsedGradeRow]]:
    parsed_all = [analyze_grade_item(item) for item in grades]
    graded_rows = sorted(
        [r for r in parsed_all if r.has_score],
        key=lambda r: (
            0 if r.is_course_total else 1,
            0 if r.weight_pct > 0 else 1,
            -r.weight_pct,
        ),
    )
    pending_rows = [r for r in parsed_all if not r.has_score]
    return parsed_all, graded_rows, pending_rows


def build_grade_row(parsed: ParsedGradeRow, *, is_mobile: bool = False) -> ft.Control:
    meta_chips: list[ft.Control] = []
    if parsed.is_course_total:
        meta_chips.append(
            ft.Container(
                bgcolor=ft.Colors.with_opacity(0.10, C.ACCENT),
                border_radius=5,
                padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                content=ft.Text(
                    "Tổng kết",
                    size=10,
                    weight=ft.FontWeight.W_700,
                    color=C.ACCENT,
                ),
            )
        )
    if parsed.weight_label:
        meta_chips.append(
            ft.Container(
                bgcolor=C.SURFACE_ELEVATED,
                border=ft.Border.all(1, C.BORDER),
                border_radius=5,
                padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                content=ft.Text(
                    f"Trọng số {parsed.weight_label}",
                    size=10,
                    weight=ft.FontWeight.W_600,
                    color=C.TEXT_SECONDARY,
                ),
            )
        )

    sub_tokens = [
        p
        for p in (
            f"Thang {parsed.range_label}" if parsed.range_label else "",
            f"Đạt {parsed.pct_label}" if parsed.pct_label else "",
        )
        if p
    ]
    if sub_tokens:
        meta_chips.append(
            ft.Text(" • ".join(sub_tokens), size=11, color=C.TEXT_SECONDARY)
        )

    left_col_items: list[ft.Control] = [
        ft.Text(
            parsed.clean_name,
            size=13.5,
            weight=ft.FontWeight.W_700 if parsed.is_course_total else ft.FontWeight.W_600,
            color=C.TEXT_PRIMARY,
            max_lines=2,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
    ]
    if meta_chips:
        left_col_items.append(
            ft.Row(
                wrap=True,
                spacing=6,
                run_spacing=4,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=meta_chips,
            )
        )
    if parsed.feedback_text:
        left_col_items.append(
            ft.Container(
                bgcolor=C.BG,
                border=ft.Border.all(1, C.BORDER),
                border_radius=7,
                padding=ft.Padding.symmetric(horizontal=9, vertical=5),
                content=ft.Row(
                    spacing=6,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                    controls=[
                        ft.Icon(
                            ft.Icons.CHAT_BUBBLE_OUTLINE_ROUNDED,
                            size=12,
                            color=C.TEXT_SECONDARY,
                        ),
                        ft.Text(
                            parsed.feedback_text,
                            size=11,
                            color=C.TEXT_SECONDARY,
                            expand=True,
                            max_lines=3,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                    ],
                ),
            )
        )

    if parsed.has_score:
        is_low_score = parsed.score_on_10 is not None and parsed.score_on_10 < 5.0
        score_text_color = (
            C.CRITICAL
            if is_low_score
            else (C.ACCENT if parsed.is_course_total else C.TEXT_PRIMARY)
        )
        score_bg = (
            ft.Colors.with_opacity(0.09, C.CRITICAL)
            if is_low_score
            else (
                ft.Colors.with_opacity(0.09, C.ACCENT)
                if parsed.is_course_total
                else C.SURFACE_ELEVATED
            )
        )
        score_border = (
            None
            if (is_low_score or parsed.is_course_total)
            else ft.Border.all(1, C.BORDER)
        )
        grade_str = str(parsed.item.grade_formatted or "")
        trailing_score: ft.Control = ft.Container(
            bgcolor=score_bg,
            border=score_border,
            border_radius=9,
            width=104 if (is_mobile and len(grade_str) > 8) else None,
            padding=ft.Padding.symmetric(horizontal=10 if is_mobile else 12, vertical=6),
            alignment=ft.Alignment.CENTER,
            content=ft.Text(
                grade_str,
                size=15,
                weight=ft.FontWeight.W_700,
                color=score_text_color,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
                text_align=ft.TextAlign.CENTER,
            ),
        )
    else:
        trailing_score = ft.Text(
            "Chưa chấm",
            size=12,
            weight=ft.FontWeight.W_500,
            color=C.TEXT_SECONDARY,
            max_lines=1,
        )

    return ft.Container(
        padding=ft.Padding.symmetric(
            horizontal=12 if is_mobile else 16,
            vertical=10 if is_mobile else 12,
        ),
        bgcolor=ft.Colors.with_opacity(0.04, C.ACCENT) if parsed.is_course_total else None,
        content=ft.Row(
            spacing=10 if is_mobile else 14,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Column(expand=True, spacing=4, controls=left_col_items),
                trailing_score,
            ],
        ),
    )


def build_grouped_grade_card(
    rows: list[ParsedGradeRow], *, is_mobile: bool = False
) -> ft.Container:
    children: list[ft.Control] = []
    for idx, row_data in enumerate(rows):
        if idx > 0:
            children.append(
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=12 if is_mobile else 16),
                    content=ft.Divider(height=1, thickness=0.6, color=C.BORDER),
                )
            )
        children.append(build_grade_row(row_data, is_mobile=is_mobile))
    return ft.Container(
        bgcolor=C.SURFACE,
        border=ft.Border.all(1, C.BORDER),
        border_radius=14,
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        content=ft.Column(spacing=0, controls=children),
    )


def build_pending_grades_tile(
    pending_rows: list[ParsedGradeRow], *, is_mobile: bool = False
) -> ft.Container:
    return ft.Container(
        bgcolor=C.SURFACE,
        border=ft.Border.all(1, C.BORDER),
        border_radius=14,
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        content=ft.ExpansionTile(
            title=ft.Text(
                "Chưa có điểm / Bài tự luyện",
                size=13,
                weight=ft.FontWeight.W_600,
                color=C.TEXT_SECONDARY,
            ),
            subtitle=ft.Text(
                f"{len(pending_rows)} hạng mục chưa chấm",
                size=11,
                color=C.TEXT_SECONDARY,
            ),
            expanded=False,
            bgcolor=C.SURFACE,
            collapsed_bgcolor=C.SURFACE,
            icon_color=C.ACCENT,
            collapsed_icon_color=C.TEXT_SECONDARY,
            shape=ft.RoundedRectangleBorder(
                radius=14,
                side=ft.BorderSide(0, C.SURFACE, style=ft.BorderStyle.NONE),
            ),
            collapsed_shape=ft.RoundedRectangleBorder(
                radius=14,
                side=ft.BorderSide(0, C.SURFACE, style=ft.BorderStyle.NONE),
            ),
            controls_padding=ft.Padding.only(left=4, right=4, bottom=8),
            controls=[build_grouped_grade_card(pending_rows, is_mobile=is_mobile)],
        ),
    )
