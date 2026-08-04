from collections.abc import Callable
from typing import Literal

import flet as ft

from gui.core.theme import C


CrashConsentDecision = Literal["enabled", "disabled"]


class CrashConsentDialog:
    """One-time, UI-only first-run consent prompt.

    This component deliberately has no dependency on diagnostics SDKs,
    transports, spools, or application settings.
    """

    _presented_in_process = False

    def __init__(
        self,
        page: ft.Page,
        on_decision: Callable[[CrashConsentDecision], bool],
    ) -> None:
        self._page = page
        self._on_decision = on_decision
        self._presented = False
        self._decision_in_flight = False
        self.current_consent: Literal["not_asked", "enabled", "disabled"] = (
            "not_asked"
        )
        self._dialog: ft.AlertDialog | None = None

    def present_if_needed(self, current_consent: str) -> bool:
        self.current_consent = current_consent  # type: ignore[assignment]
        if (
            current_consent != "not_asked"
            or self._presented
            or type(self)._presented_in_process
        ):
            return False

        self._presented = True
        type(self)._presented_in_process = True

        def dismiss(_event) -> None:
            # Closing the window is an explicit deferral, not a decision.
            return None

        def defer(_event) -> None:
            # Keep ``not_asked`` so a later process may ask again.
            self._page.pop_dialog()

        def choose(decision: CrashConsentDecision):
            def handler(_event) -> None:
                if self._decision_in_flight:
                    return
                self._decision_in_flight = True
                try:
                    persisted = bool(self._on_decision(decision))
                except Exception:
                    persisted = False
                finally:
                    self._decision_in_flight = False
                if not persisted:
                    return
                self.current_consent = decision
                self._page.pop_dialog()

            return handler

        self._dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(
                "Gửi chẩn đoán sự cố?",
                color=C.TEXT_PRIMARY,
                weight=ft.FontWeight.BOLD,
            ),
            content=ft.Text(
                "Bạn có muốn cho phép UTHelper gửi thông tin chẩn đoán khi ứng dụng gặp sự cố không?",
                color=C.TEXT_SECONDARY,
            ),
            actions=[
                ft.TextButton("Để sau", on_click=defer),
                ft.TextButton("Từ chối", on_click=choose("disabled")),
                ft.TextButton("Bật", on_click=choose("enabled")),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
            on_dismiss=dismiss,
            bgcolor=C.BG,
        )
        self._page.show_dialog(self._dialog)
        return True
