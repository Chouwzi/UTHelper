from __future__ import annotations

from typing import Any, Callable, Optional
import flet as ft

from core.academic_radar.overall import OverallGpa, required_average
from gui.core.theme import C


def _num(text: str | None) -> float | None:
    if not text:
        return None
    try:
        return float(text.strip().replace(",", "."))
    except ValueError:
        return None


class ExpectedGradeDialog:
    """Form tính điểm trung bình cần đạt ở các tín chỉ sắp tới để đạt GPA mục tiêu."""

    @staticmethod
    def show(
        page: ft.Page,
        overall: OverallGpa | None,
        *,
        on_dismiss: Optional[Callable[[], Any]] = None,
    ) -> None:
        scale = {"value": "4"}

        def _field(label: str, value: str = "", hint: str = "") -> ft.TextField:
            return ft.TextField(
                label=label,
                value=value,
                hint_text=hint,
                dense=True,
                text_size=13,
                border_color=C.BORDER,
                keyboard_type=ft.KeyboardType.NUMBER,
            )

        def _initial_gpa(which: str) -> str:
            if overall is None:
                return ""
            v = overall.gpa_4 if which == "4" else overall.gpa_10
            return f"{v:.2f}" if v is not None else ""

        credits_default = ""
        if overall is not None and overall.total_credits > 0:
            tc = overall.total_credits
            credits_default = str(int(tc)) if float(tc).is_integer() else f"{tc:.1f}"

        current_gpa = _field("GPA hiện tại", _initial_gpa("4"))
        current_credits = _field("Tín chỉ đã tích lũy", credits_default)
        target_gpa = _field("GPA mục tiêu", hint="VD: 3.20")
        extra_credits = _field("Tín chỉ sẽ học thêm", hint="VD: 20")

        result = ft.Text("Nhập mục tiêu và số tín chỉ sắp học.", size=13, color=C.TEXT_SECONDARY)
        result_box = ft.Container(
            content=result,
            bgcolor=C.SURFACE,
            padding=ft.Padding.all(12),
            border_radius=10,
            border=ft.Border.all(1, C.BORDER),
        )

        def _recalculate(_=None):
            max_scale = 4.0 if scale["value"] == "4" else 10.0
            cur, cc = _num(current_gpa.value), _num(current_credits.value)
            tgt, add = _num(target_gpa.value), _num(extra_credits.value)
            if None in (cur, cc, tgt, add):
                result.value = "Nhập đủ 4 ô để tính."
                result.color = C.TEXT_SECONDARY
            elif add <= 0:
                result.value = "Số tín chỉ sắp học phải lớn hơn 0."
                result.color = C.WARNING
            elif not (0 <= tgt <= max_scale) or not (0 <= cur <= max_scale):
                result.value = f"GPA phải nằm trong khoảng 0 – {max_scale:g} (hệ {scale['value']})."
                result.color = C.WARNING
            else:
                need = required_average(cur, cc, tgt, add)
                if need is None:
                    result.value = "Không tính được."
                    result.color = C.WARNING
                elif need > max_scale + 1e-9:
                    best = (cur * cc + max_scale * add) / (cc + add)
                    result.value = (
                        f"Không khả thi: cần {need:.2f}/{max_scale:g}, vượt thang điểm. "
                        f"Tối đa đạt được {best:.2f}."
                    )
                    result.color = C.WARNING
                elif need <= 0:
                    result.value = "Bạn đã vượt mục tiêu, kể cả khi các tín chỉ sắp tới điểm thấp."
                    result.color = C.SAFE
                else:
                    result.value = (
                        f"Cần đạt trung bình {need:.2f}/{max_scale:g} "
                        f"ở {add:g} tín chỉ sắp tới."
                    )
                    result.color = C.SAFE
            try:
                result.update()
            except Exception:
                pass

        def _on_scale(e):
            scale["value"] = e.control.value
            current_gpa.value = _initial_gpa(scale["value"])
            try:
                current_gpa.update()
            except Exception:
                pass
            _recalculate()

        scale_dd = ft.Dropdown(
            options=[ft.dropdown.Option(key="4", text="Hệ 4"), ft.dropdown.Option(key="10", text="Hệ 10")],
            value="4",
            on_select=_on_scale,
            dense=True,
            text_size=13,
            border_color=C.BORDER,
        )
        for f in (current_gpa, current_credits, target_gpa, extra_credits):
            f.on_change = _recalculate

        used_overlay = False
        is_real_page = isinstance(page, ft.Page)

        def _notify_dismiss(_=None) -> None:
            if callable(on_dismiss):
                try:
                    on_dismiss()
                except Exception:
                    pass

        def _close(_=None):
            if not used_overlay:
                try:
                    page.pop_dialog()
                    if not is_real_page:
                        _notify_dismiss()
                    return
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
            _notify_dismiss()

        def _on_key_down(event) -> None:
            if getattr(event, "key", "") == "Escape":
                _close()

        try:
            page_w = float(getattr(page, "width", 0) or 0)
        except (TypeError, ValueError):
            page_w = 0.0
        try:
            page_h = float(getattr(page, "height", 0) or 0)
        except (TypeError, ValueError):
            page_h = 0.0
        is_narrow = 0 < page_w < 600
        dialog_w = max(240.0, min(360.0, page_w - 64.0)) if page_w > 0 else 360.0
        max_h = max(260.0, min(520.0, page_h * 0.65)) if page_h > 0 else None

        dialog = ft.AlertDialog(
            modal=True,
            semantics_label="Tính điểm kỳ vọng",
            title=ft.Text(
                "Tính điểm kỳ vọng",
                size=15,
                weight=ft.FontWeight.BOLD,
                color=C.TEXT_PRIMARY,
                semantics_label="Tính điểm kỳ vọng",
            ),
            content=ft.KeyboardListener(
                autofocus=False,
                on_key_down=_on_key_down,
                content=ft.Container(
                    content=ft.Column(
                        [scale_dd, current_gpa, current_credits, target_gpa, extra_credits, result_box],
                        spacing=10,
                        tight=True,
                        scroll=ft.ScrollMode.AUTO,
                    ),
                    width=dialog_w,
                    height=max_h,
                ),
            ),
            actions=[ft.TextButton("Đóng", height=44, on_click=_close)],
            on_dismiss=_notify_dismiss,
            inset_padding=ft.Padding.symmetric(horizontal=16 if is_narrow else 40, vertical=24),
            title_padding=ft.Padding.only(left=16 if is_narrow else 24, right=16 if is_narrow else 24, top=16, bottom=4),
            content_padding=ft.Padding.only(left=16 if is_narrow else 24, right=16 if is_narrow else 24, top=12, bottom=8),
            actions_padding=ft.Padding.only(left=16, right=16, top=0, bottom=8),
            bgcolor=C.BG,
            shape=ft.RoundedRectangleBorder(radius=16),
        )
        try:
            page.show_dialog(dialog)
        except Exception:
            used_overlay = True
            overlay = getattr(page, "overlay", None)
            if overlay is not None:
                overlay.append(dialog)
                dialog.open = True
                try:
                    page.update()
                except Exception:
                    pass
