"""Direct Moodle quiz attempt surface backed by the native quiz extension."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

import flet as ft

import platform_utils
from platform_utils import quiz_client_bootstrap
from gui.core.theme import (
    C,
    refresh_semantic_control_colors,
    semantic_palette,
)

logger = logging.getLogger(__name__)

_READY_TIMEOUT_SECONDS = 12
_LOGIN_WAIT_TIMEOUT_SECONDS = 15
_ENTRY_PREPARE_TIMEOUT_SECONDS = 30
_ENTRY_ENTER_TIMEOUT_SECONDS = 30


def _native_runtime_supported(page=None) -> bool:
    """Use native WebView only in the supported app runtimes."""
    if quiz_client_bootstrap.source_quiz_client_unavailable():
        return False
    if page is not None and bool(getattr(page, "web", False)):
        return False
    return bool(
        platform_utils.IS_WINDOWS
        or platform_utils.IS_ANDROID
        or platform_utils.IS_IOS
    )


def _default_player_factory():
    from flet_uth_quiz import UthQuizPlayer

    return UthQuizPlayer


def _default_session_cleaner_factory():
    from flet_uth_quiz import UthQuizSessionCleaner

    return UthQuizSessionCleaner


def _default_target_builder():
    from core.quiz_player import build_quiz_launch_target

    return build_quiz_launch_target


def _default_lease_provider():
    from core.quiz_player import acquire_quiz_session_lease

    return acquire_quiz_session_lease


def _default_entry_provider():
    from core.quiz_entry import prepare_quiz_entry

    return prepare_quiz_entry


class QuizPlayerView(ft.Container):
    """A dismissible full-screen player that leaves quiz work to Moodle."""

    def __init__(
        self,
        page: ft.Page,
        *,
        on_closed: Callable[[dict[str, Any] | None], Any] | None = None,
        player_factory: Callable | None = None,
        target_builder: Callable | None = None,
        lease_provider: Callable | None = None,
        entry_provider: Callable | None = None,
        account_id_provider: Callable | None = None,
        client_provider: Callable[[str], Any] | None = None,
        on_auth_required: Callable | None = None,
        on_entry_unavailable: Callable | None = None,
        on_telemetry_quiz_action: Callable[..., Any] | None = None,
        ready_timeout_seconds: float = _READY_TIMEOUT_SECONDS,
    ):
        self._page = page
        self.on_closed = on_closed
        self._player_factory = player_factory
        self._target_builder = target_builder
        self._lease_provider = lease_provider
        self._entry_provider = entry_provider
        self._account_id_provider = account_id_provider
        self._client_provider = client_provider
        self._on_auth_required = on_auth_required
        self._on_entry_unavailable = on_entry_unavailable
        self._on_telemetry_quiz_action = on_telemetry_quiz_action
        self._ready_timeout_seconds = max(0.1, float(ready_timeout_seconds))
        self._telemetry_started_mono: float | None = None
        self._telemetry_open_mono: float | None = None

        self._player = None
        self._lease = None
        self._target = None
        self._activity: dict[str, Any] | None = None
        self._launch_url: str | None = None
        self._prepared_entry = None
        self._entry_mode = ""
        self._entry_entering = False
        self._attempt_may_be_open = False
        self._entry_unavailable_callback = None
        self._entry_dialog = None
        self._entry_password_field = None
        self._entry_prompt_error = None
        self._entry_actions: list[Any] = []
        self._entry_action_label = ""
        self._entry_prompt_open = False
        self._entry_prepare_task = None
        self._entry_operation_task = None
        self._deferred_close: dict[str, Any] | None = None
        self._account_id = ""
        self._pending_cookies: list[dict[str, Any]] | None = None
        self._bootstrap_started = False
        self._auth_request_active = False
        self._generation = 0
        self._ready = False
        self._close_pending = False
        self._after_close: Callable | None = None
        self._close_title = "Đóng bài?"
        self._close_action_label = "Đóng bài"
        self._session_cleaner = None
        self._ensure_session_cleaner()

        self._title = ft.Text(
            "",
            size=16,
            weight=ft.FontWeight.W_600,
            color=C.TEXT_PRIMARY,
            max_lines=2,
            overflow=ft.TextOverflow.ELLIPSIS,
            expand=True,
        )
        self._back_button = ft.TextButton(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.ARROW_BACK_ROUNDED, size=18),
                    ft.Text("Quay lại", size=13),
                ],
                spacing=5,
                tight=True,
            ),
            on_click=self._on_close_button,
            tooltip="Quay lại chi tiết",
        )
        self._player_host = ft.Container(expand=True, bgcolor=C.BG)
        self._loading = ft.ProgressRing(width=22, height=22, stroke_width=2, color=C.ACCENT)
        self._status_text = ft.Text(
            "",
            expand=True,
            size=13,
            color=C.TEXT_SECONDARY,
            text_align=ft.TextAlign.CENTER,
            max_lines=3,
        )
        self._status_panel = ft.Container(
            content=ft.Row(
                controls=[self._loading, self._status_text],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=10,
            ),
            left=12,
            right=12,
            top=12,
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.BORDER),
            border_radius=8,
            ignore_interactions=True,
            visible=False,
        )
        self._browser_button = ft.Button(
            "Mở trong trình duyệt",
            icon=ft.Icons.OPEN_IN_BROWSER_ROUNDED,
            on_click=self._open_in_browser,
            bgcolor=C.SURFACE,
            color=C.ACCENT,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=8),
                side=ft.BorderSide(1, C.ACCENT),
                padding=ft.Padding.symmetric(horizontal=16, vertical=10),
            ),
            visible=False,
        )
        self._auth_button = ft.TextButton(
            "Đăng nhập lại",
            icon=ft.Icons.LOGIN_ROUNDED,
            on_click=self._request_authentication,
            visible=False,
        )
        self._error_actions = ft.Container(
            content=ft.Column(
                controls=[self._auth_button, self._browser_button],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.only(top=10),
            visible=False,
        )
        self._external_notice = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.INFO_OUTLINE_ROUNDED, size=15, color=C.WARNING),
                    ft.Text(
                        "Liên kết ngoài chưa được mở.",
                        size=12,
                        color=C.WARNING,
                    ),
                ],
                spacing=7,
            ),
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.BORDER),
            border_radius=6,
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            visible=False,
        )
        self._external_notice_row = ft.Container(
            content=self._external_notice,
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            visible=False,
        )
        self._body = ft.Column(
            controls=[
                ft.Container(
                    content=ft.Row(
                        controls=[self._back_button, self._title],
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    bgcolor=C.BG,
                    border=ft.Border.only(bottom=ft.BorderSide(1, C.BORDER)),
                    padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                ),
                self._external_notice_row,
                ft.Stack(
                    controls=[self._player_host, self._status_panel],
                    expand=True,
                ),
                self._error_actions,
            ],
            spacing=0,
            expand=True,
        )
        self._close_dialog = ft.AlertDialog(
            modal=True,
            bgcolor=C.SURFACE,
            title=ft.Text(self._close_title, size=16, color=C.TEXT_PRIMARY),
            content=ft.Text(
                "Bạn có thể mất phần trả lời chưa lưu.",
                size=13,
                color=C.TEXT_SECONDARY,
            ),
            actions=[
                ft.TextButton(
                    "Tiếp tục làm bài",
                    on_click=self._cancel_close,
                    style=ft.ButtonStyle(color=C.TEXT_SECONDARY),
                ),
                ft.Button(
                    self._close_action_label,
                    on_click=self._confirm_close,
                    bgcolor=C.CRITICAL,
                    color=C.ON_CRITICAL,
                    style=ft.ButtonStyle(
                        shape=ft.RoundedRectangleBorder(radius=8),
                    ),
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
            on_dismiss=self._on_dialog_dismiss,
        )

        super().__init__(
            content=self._body,
            expand=True,
            visible=False,
            bgcolor=C.BG,
            left=0,
            right=0,
            top=0,
            bottom=0,
        )
        self._rendered_palette = semantic_palette()

    def native_runtime_supported(self) -> bool:
        return _native_runtime_supported(self._page)

    def _resolve_player_factory(self):
        if self._player_factory is not None:
            return self._player_factory
        try:
            self._player_factory = _default_player_factory()
        except Exception as exc:
            logger.info("Quiz player package unavailable (%s)", type(exc).__name__)
            return None
        return self._player_factory

    def runtime_ready_to_try(self) -> bool:
        """Whether Python and the host platform can attempt native mounting."""
        return self.native_runtime_supported() and self._resolve_player_factory() is not None

    def prepare_activity(
        self, activity: Mapping[str, Any], account_id: str
    ) -> tuple[object | None, str | None]:
        """Return a trusted quiz target and the current UI availability state."""
        if not isinstance(activity, Mapping) or str(activity.get("type", "")).casefold() != "quiz":
            return None, None
        url = activity.get("url", "")
        detail_metadata = activity.get("details")
        metadata: dict[str, Any] = {}
        if isinstance(detail_metadata, Mapping):
            metadata.update(detail_metadata)
            nested_security = detail_metadata.get("quiz_security")
            if isinstance(nested_security, Mapping):
                metadata.update(nested_security)
        quiz_security = activity.get("quiz_security")
        if isinstance(quiz_security, Mapping):
            metadata.update(quiz_security)
        for key in (
            "browsersecurity",
            "browser_security",
            "activerulenames",
            "active_rule_names",
        ):
            if key in activity and key not in metadata:
                metadata[key] = activity[key]
        try:
            builder = self._target_builder or _default_target_builder()
            target = builder(url, account_id, metadata=metadata)
        except Exception as exc:
            reason = getattr(exc, "reason", "")
            return None, reason or None
        if not self.native_runtime_supported():
            if quiz_client_bootstrap.source_quiz_client_unavailable():
                return target, "player_unavailable"
            return target, "unsupported_runtime"
        if self._resolve_player_factory() is None:
            return target, "player_unavailable"
        return target, None

    def open_activity(
        self,
        target,
        activity: Mapping[str, Any],
        *,
        on_entry_unavailable: Callable | None = None,
    ) -> bool:
        """Prepare an attempt, then mount only Moodle's returned attempt URL."""
        if self.visible or self._player is not None or self._lease is not None:
            return False
        if not self.native_runtime_supported():
            self._show_mount_error("unsupported_runtime")
            return False
        factory = self._resolve_player_factory()
        if factory is None:
            self._show_mount_error("player_unavailable")
            return False

        self._generation += 1
        generation = self._generation
        self._target = target
        self._activity = dict(activity) if isinstance(activity, Mapping) else None
        self._telemetry_open_mono = time.monotonic()
        self._telemetry_started_mono = None
        self._launch_url = None
        self._title.value = str(
            self._activity.get("title") if self._activity else ""
        ) or "Bài kiểm tra"
        self._prepared_entry = None
        self._entry_mode = ""
        self._entry_entering = False
        self._attempt_may_be_open = False
        self._entry_unavailable_callback = (
            on_entry_unavailable or self._on_entry_unavailable
        )
        self._entry_dialog = None
        self._entry_password_field = None
        self._entry_prompt_error = None
        self._entry_actions = []
        self._entry_action_label = ""
        self._entry_prompt_open = False
        self._entry_prepare_task = None
        self._entry_operation_task = None
        self._deferred_close = None
        self._ready = False
        self._close_pending = False
        self._after_close = None
        self._auth_request_active = False
        self._bootstrap_started = False
        self._clear_pending_cookies()
        self._external_notice.visible = False
        self._external_notice_row.visible = False
        self._error_actions.visible = False
        self._browser_button.visible = False
        self._status_text.value = "Đang kết nối với Moodle..."
        self._loading.visible = True
        self._status_panel.visible = True
        self.visible = True

        try:
            lease_provider = self._lease_provider or _default_lease_provider()
            lease = lease_provider(target.session_key)
            self._lease = lease
        except Exception as exc:
            logger.warning("Quiz session lease unavailable (%s)", type(exc).__name__)
            self._show_mount_error("player_unavailable")
            return False

        self._safe_update()
        if self._session_cleaner is not None and callable(
            getattr(self._session_cleaner, "prewarm", None)
        ):
            self._run_task(self._prewarm_session, target.session_key)
        if not self._run_task(
            self._prepare_entry_and_prompt, generation, factory
        ):
            self._show_mount_error("player_unavailable")
            return False
        return True

    async def _prewarm_session(self, session_key: str) -> None:
        cleaner = self._session_cleaner
        if cleaner is None:
            return
        prewarm_fn = getattr(cleaner, "prewarm", None)
        if not callable(prewarm_fn):
            return
        try:
            await prewarm_fn(session_key)
        except Exception:
            pass

    def open_activity_for_account(
        self,
        target,
        activity: Mapping[str, Any],
        account_id: str,
        *,
        on_entry_unavailable: Callable | None = None,
    ) -> bool:
        self._account_id = str(account_id or "")
        return self.open_activity(
            target,
            activity,
            on_entry_unavailable=on_entry_unavailable,
        )

    def _run_task(self, coroutine, *args):
        runner = getattr(self._page, "run_task", None)
        if callable(runner):
            try:
                runner(coroutine, *args)
                return True
            except Exception as exc:
                logger.debug(
                    "Quiz task scheduling failed (%s)", type(exc).__name__
                )
        try:
            asyncio.get_running_loop().create_task(coroutine(*args))
            return True
        except RuntimeError:
            logger.debug("Quiz player task could not be scheduled")
            return False

    async def _prepare_entry_and_prompt(
        self, generation: int, factory: Callable
    ):
        lease = self._lease
        if lease is None:
            return
        if not getattr(lease, "is_ready", True):
            ready = await asyncio.to_thread(
                lease.wait_until_ready,
                _LOGIN_WAIT_TIMEOUT_SECONDS,
            )
            if not ready:
                if (
                    generation == self._generation
                    and self.visible
                    and self._lease is lease
                ):
                    self._show_mount_error("player_unavailable")
                return
        if generation != self._generation or not self.visible or self._lease is not lease:
            return

        try:
            provider = self._entry_provider or _default_entry_provider()
            existing_client = None
            if callable(self._client_provider) and self._target is not None:
                try:
                    existing_client = self._client_provider(
                        getattr(self._target, "site_identity", "")
                    )
                except Exception:
                    existing_client = None
            if existing_client is not None and self._provider_accepts_existing_client(
                provider
            ):
                prepare_task = asyncio.create_task(
                    asyncio.to_thread(
                        provider,
                        self._target,
                        lease,
                        existing_client=existing_client,
                    )
                )
            else:
                prepare_task = asyncio.create_task(
                    asyncio.to_thread(provider, self._target, lease)
                )
            self._entry_prepare_task = prepare_task
            done, _ = await asyncio.wait(
                {prepare_task},
                timeout=_ENTRY_PREPARE_TIMEOUT_SECONDS,
            )
            if not done:
                prepare_task.add_done_callback(self._close_late_prepared_entry)
                if (
                    generation == self._generation
                    and self.visible
                    and self._lease is lease
                ):
                    self._show_recovery_message("session_timeout")
                return
            prepared = prepare_task.result()
            if (
                generation != self._generation
                or not self.visible
                or self._lease is not lease
            ):
                self._close_prepared_entry(prepared)
                return
            self._prepared_entry = prepared
            mode = self._entry_mode_value(prepared)
            self._entry_mode = mode
            if mode == "unavailable":
                reason = self._safe_entry_reason(prepared)
                await self._finish_entry_unavailable(
                    reason, generation=generation, lease=lease
                )
                return
            if mode not in {"resume", "start"}:
                await self._finish_entry_unavailable(
                    "quiz_unavailable", generation=generation, lease=lease
                )
                return

            if mode == "start" or bool(
                getattr(prepared, "password_required", False)
            ):
                self._show_entry_prompt(prepared, mode)
                return

            await self._enter_prepared_entry(
                generation,
                factory,
                password=None,
                confirmed=False,
            )
        except Exception as exc:
            reason = self._exception_reason(exc)
            logger.info("Quiz entry preparation failed (%s)", type(exc).__name__)
            if (
                generation == self._generation
                and self.visible
                and self._lease is lease
            ):
                if reason in {
                    "attempts_exhausted",
                    "quiz_closed",
                    "quiz_unavailable",
                    "requires_safe_exam_browser",
                    "unsupported_action",
                    "entry_in_progress",
                    "entry_not_opened",
                }:
                    await self._finish_entry_unavailable(
                        reason, generation=generation, lease=lease
                    )
                else:
                    self._show_recovery_message(reason or "session_unavailable")
        finally:
            task = locals().get("prepare_task")
            if task is not None and task.done() and self._entry_prepare_task is task:
                self._entry_prepare_task = None

    @staticmethod
    def _provider_accepts_existing_client(provider: Callable) -> bool:
        try:
            params = inspect.signature(provider).parameters
        except (TypeError, ValueError):
            return False
        if "existing_client" in params:
            return True
        return any(
            param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values()
        )

    @classmethod
    def _close_late_prepared_entry(cls, future):
        try:
            prepared = future.result()
            cls._close_prepared_entry_later(prepared)
        except Exception:
            # Late worker failures may contain private server response data.
            return

    @classmethod
    def _clear_late_entry_result(cls, future, prepared):
        try:
            result = future.result()
            cls._clear_entry_result(result)
        except Exception:
            pass
        finally:
            cls._close_prepared_entry_later(prepared)

    @classmethod
    def _close_prepared_entry_later(cls, prepared):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            cls._close_prepared_entry(prepared)
            return
        loop.create_task(asyncio.to_thread(cls._close_prepared_entry, prepared))

    @staticmethod
    def _close_prepared_entry(prepared):
        close = getattr(prepared, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    @staticmethod
    def _clear_entry_result(result):
        clear = getattr(result, "clear", None)
        if callable(clear):
            try:
                clear()
            except Exception:
                pass

    @staticmethod
    def _entry_mode_value(entry) -> str:
        mode = getattr(entry, "mode", "")
        return str(getattr(mode, "value", mode) or "").casefold()

    @staticmethod
    def _exception_reason(exc) -> str:
        reason = getattr(exc, "reason", "")
        return reason if isinstance(reason, str) else ""

    @classmethod
    def _safe_entry_reason(cls, entry) -> str:
        reason = getattr(entry, "reason", "")
        return reason if isinstance(reason, str) else ""

    def _show_entry_prompt(self, prepared, mode: str):
        self._loading.visible = False
        self._status_panel.visible = False
        password_required = bool(getattr(prepared, "password_required", False))
        password_field = None
        prompt_error = ft.Text(
            "",
            size=12,
            color=C.CRITICAL,
            visible=False,
        )
        content_controls = []
        if mode == "start":
            content_controls.append(
                ft.Text("Bắt đầu làm bài?", size=13, color=C.TEXT_PRIMARY)
            )
        if password_required:
            password_field = ft.TextField(
                label="Mật khẩu bài kiểm tra",
                password=True,
                can_reveal_password=True,
                autofocus=True,
                text_size=13,
                border_color=C.BORDER,
                focused_border_color=C.ACCENT,
                color=C.TEXT_PRIMARY,
                label_style=ft.TextStyle(color=C.TEXT_SECONDARY),
                bgcolor=C.BG,
                on_submit=self._confirm_entry_prompt,
                on_change=self._on_entry_password_change,
            )
            content_controls.append(password_field)
        content_controls.append(prompt_error)

        cancel_button = ft.TextButton(
            "Hủy",
            on_click=self._cancel_entry_prompt,
            style=ft.ButtonStyle(color=C.TEXT_SECONDARY),
        )
        action_label = "Bắt đầu" if mode == "start" else "Tiếp tục"
        self._entry_action_label = action_label
        confirm_button = ft.Button(
            action_label,
            on_click=self._confirm_entry_prompt,
            bgcolor=C.ACCENT,
            color=C.ON_ACCENT,
            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
        )
        dialog = ft.AlertDialog(
            modal=True,
            bgcolor=C.SURFACE,
            title=ft.Text(self._title.value or "Bài kiểm tra", size=16, color=C.TEXT_PRIMARY),
            content=ft.Column(controls=content_controls, spacing=12, tight=True),
            actions=[cancel_button, confirm_button],
            actions_alignment=ft.MainAxisAlignment.END,
            on_dismiss=self._on_entry_prompt_dismiss,
        )
        self._entry_dialog = dialog
        self._entry_password_field = password_field
        self._entry_prompt_error = prompt_error
        self._entry_actions = [cancel_button, confirm_button]
        self._entry_prompt_open = True
        try:
            self._page.show_dialog(dialog)
        except Exception:
            dialog.open = True
        self._safe_update()

    def _on_entry_password_change(self, _event=None):
        if self._entry_prompt_error is not None:
            self._entry_prompt_error.value = ""
            self._entry_prompt_error.visible = False
            self._safe_update()

    async def _on_entry_prompt_dismiss(self, _event=None):
        if self._entry_prompt_open and not self._entry_entering:
            self._entry_prompt_open = False
            await self._discard_pending_entry(True)

    async def _cancel_entry_prompt(self, _event=None):
        if self._entry_entering or not self.visible:
            return
        self._entry_prompt_open = False
        if self._entry_dialog is not None:
            self._entry_dialog.open = False
        if self._entry_password_field is not None:
            self._entry_password_field.value = ""
        try:
            self._page.pop_dialog()
        except Exception:
            pass
        await self._discard_pending_entry(notify_closed=True)

    async def _confirm_entry_prompt(self, _event=None):
        if self._entry_entering or not self._entry_prompt_open:
            return
        password = None
        if self._entry_password_field is not None:
            password = self._entry_password_field.value or None
        await self._enter_prepared_entry(
            self._generation,
            self._resolve_player_factory(),
            password=password,
            confirmed=self._entry_mode == "start",
        )

    def _set_entry_prompt_busy(self, busy: bool):
        for action in self._entry_actions:
            action.disabled = busy
        if len(self._entry_actions) > 1:
            self._entry_actions[1].content = (
                "Đang mở bài..." if busy else self._entry_action_label
            )
        if self._entry_password_field is not None:
            self._entry_password_field.disabled = busy
        if self._entry_prompt_error is not None:
            if busy:
                self._entry_prompt_error.value = "Đang mở bài kiểm tra..."
                self._entry_prompt_error.color = C.TEXT_SECONDARY
                self._entry_prompt_error.visible = True
            elif self._entry_prompt_error.value == "Đang mở bài kiểm tra...":
                self._entry_prompt_error.value = ""
                self._entry_prompt_error.visible = False
                self._entry_prompt_error.color = C.CRITICAL
        self._safe_update()

    async def _enter_prepared_entry(
        self,
        generation: int,
        factory: Callable | None,
        *,
        password: str | None,
        confirmed: bool,
    ):
        prepared = self._prepared_entry
        lease = self._lease
        if (
            prepared is None
            or lease is None
            or factory is None
            or self._entry_entering
            or generation != self._generation
        ):
            if factory is None and self.visible:
                self._close_entry_prompt()
                self._prepared_entry = None
                if prepared is not None:
                    await asyncio.to_thread(self._close_prepared_entry, prepared)
                self._show_mount_error("player_unavailable")
            return

        self._entry_entering = True
        if self._entry_mode == "start":
            self._attempt_may_be_open = True
        if not self._entry_prompt_open:
            self._status_text.value = "Đang mở bài kiểm tra..."
            self._loading.visible = True
            self._status_panel.visible = True
        self._set_entry_prompt_busy(True)
        enter_task = asyncio.create_task(
            asyncio.to_thread(
                prepared.enter,
                password=password,
                confirmed=confirmed,
            )
        )
        self._entry_operation_task = enter_task
        try:
            done, _ = await asyncio.wait(
                {enter_task}, timeout=_ENTRY_ENTER_TIMEOUT_SECONDS
            )
            if not done:
                enter_task.add_done_callback(
                    lambda future: self._clear_late_entry_result(future, prepared)
                )
                if (
                    generation == self._generation
                    and self.visible
                    and self._lease is lease
                ):
                    self._prepared_entry = None
                    self._entry_entering = False
                    self._attempt_may_be_open = True
                    self._close_entry_prompt()
                    self._show_recovery_message("entry_outcome_uncertain")
                return

            result = enter_task.result()
            if (
                generation != self._generation
                or not self.visible
                or self._lease is not lease
            ):
                self._clear_entry_result(result)
                await asyncio.to_thread(self._close_prepared_entry, prepared)
                return

            launch_url = getattr(result, "launch_url", None)
            if not isinstance(launch_url, str) or not launch_url.strip():
                self._clear_entry_result(result)
                self._prepared_entry = None
                await asyncio.to_thread(self._close_prepared_entry, prepared)
                self._entry_entering = False
                self._close_entry_prompt()
                await self._finish_entry_unavailable(
                    "entry_not_opened", generation=generation, lease=lease
                )
                return

            secure_window = bool(
                getattr(result, "secure_window", False)
                or getattr(prepared, "secure_window", False)
            )
            if secure_window:
                logger.debug("Opening securewindow quiz in normal app window")
            source_cookies = None
            try:
                source_cookies = result.to_native_cookies()
                cookies = [
                    dict(cookie)
                    for cookie in source_cookies
                    if isinstance(cookie, Mapping)
                ]
            finally:
                self._clear_cookies(source_cookies)
                self._clear_entry_result(result)
            self._prepared_entry = None
            await asyncio.to_thread(self._close_prepared_entry, prepared)
            if not cookies:
                self._entry_entering = False
                self._close_entry_prompt()
                self._show_recovery_message("session_cookie_missing")
                return

            self._entry_entering = False
            self._attempt_may_be_open = True
            self._pending_cookies = cookies
            self._launch_url = launch_url
            self._bootstrap_started = False
            self._close_entry_prompt()
            self._player = factory(
                # The direct attempt URL travels only through the native
                # player's private session DataChannel in bootstrap_session.
                url=self._target.url,
                session_key=self._target.session_key,
                require_session=True,
                on_ready=self._bootstrap_callbacks(generation),
                on_close=lambda event=None: self._on_player_close(
                    generation, event
                ),
                on_error=lambda event=None: self._on_player_error(
                    generation, event
                ),
                on_session_expired=lambda event=None: self._on_session_expired(
                    generation, event
                ),
                on_external_link=lambda event=None: self._on_external_link(
                    generation, event
                ),
            )
            self._player_host.content = self._player
            prep_elapsed = (
                max(0.0, time.monotonic() - self._telemetry_open_mono)
                if self._telemetry_open_mono is not None
                else None
            )
            self._telemetry_started_mono = time.monotonic()
            self._emit_quiz_telemetry(
                "player_started",
                self._activity,
                duration_seconds=prep_elapsed,
            )
            self._safe_update()
            self._run_task(self._wait_for_ready, generation)
            deferred_close = self._deferred_close
            self._deferred_close = None
            if deferred_close is not None:
                self.request_close(**deferred_close)
        except Exception as exc:
            reason = self._exception_reason(exc) or "session_unavailable"
            logger.info("Quiz entry failed (%s)", type(exc).__name__)
            if generation != self._generation or self._lease is not lease:
                self._entry_entering = False
                return
            self._entry_entering = False
            if self._entry_password_field is not None:
                self._entry_password_field.value = ""
            if reason in {"wrong_password", "password_required"}:
                self._attempt_may_be_open = self._entry_mode == "resume"
                if self._entry_prompt_error is not None:
                    self._entry_prompt_error.value = (
                        "Mật khẩu không đúng. Thử lại."
                        if reason == "wrong_password"
                        else "Hãy nhập mật khẩu bài kiểm tra."
                    )
                    self._entry_prompt_error.color = C.CRITICAL
                    self._entry_prompt_error.visible = True
                self._set_entry_prompt_busy(False)
                if self._entry_password_field is not None:
                    try:
                        await self._entry_password_field.focus()
                    except Exception:
                        pass
                deferred_close = self._deferred_close
                if deferred_close is not None:
                    self._deferred_close = None
                    await self._discard_pending_entry(
                        True, deferred_close.get("after_close")
                    )
                return
            self._close_entry_prompt()
            if reason in {
                "attempts_exhausted",
                "quiz_closed",
                "quiz_unavailable",
                "requires_safe_exam_browser",
                "unsupported_action",
                "entry_in_progress",
                "entry_not_opened",
            }:
                self._prepared_entry = None
                await asyncio.to_thread(self._close_prepared_entry, prepared)
                await self._finish_entry_unavailable(
                    reason, generation=generation, lease=lease
                )
                return
            if reason in {
                "missing_credentials",
                "account_mismatch",
                "site_credentials_unavailable",
                "login_failed",
                "session_cookie_missing",
                "session_lease_inactive",
            }:
                self._prepared_entry = None
                await asyncio.to_thread(self._close_prepared_entry, prepared)
            elif reason == "entry_outcome_uncertain":
                self._attempt_may_be_open = True
                self._prepared_entry = None
                await asyncio.to_thread(self._close_prepared_entry, prepared)
            else:
                self._prepared_entry = None
                await asyncio.to_thread(self._close_prepared_entry, prepared)
            self._show_recovery_message(reason)
            deferred_close = self._deferred_close
            self._deferred_close = None
            if deferred_close is not None:
                if self._attempt_may_be_open:
                    self.request_close(**deferred_close)
                else:
                    await self._discard_pending_entry(
                        True, deferred_close.get("after_close")
                    )
        finally:
            if enter_task.done() and self._entry_operation_task is enter_task:
                self._entry_operation_task = None

    def _close_entry_prompt(self):
        if not self._entry_prompt_open:
            return
        self._entry_prompt_open = False
        if self._entry_dialog is not None:
            self._entry_dialog.open = False
        if self._entry_password_field is not None:
            self._entry_password_field.value = ""
        try:
            self._page.pop_dialog()
        except Exception:
            pass

    async def _discard_pending_entry(
        self,
        notify_closed: bool,
        after_close: Callable | None = None,
    ):
        activity = self._activity
        callback = self.on_closed if notify_closed else None
        self._generation += 1
        self.visible = False
        self._player_host.content = None
        self._player = None
        self._target = None
        self._activity = None
        self._launch_url = None
        self._ready = False
        self._entry_entering = False
        self._attempt_may_be_open = False
        if self._entry_password_field is not None:
            self._entry_password_field.value = ""
        prepared = self._prepared_entry
        self._prepared_entry = None
        self._clear_pending_cookies()
        if prepared is not None:
            await asyncio.to_thread(self._close_prepared_entry, prepared)
        prepare_task = self._entry_prepare_task
        lease = self._lease
        if lease is not None and prepare_task is not None and not prepare_task.done():
            self._lease = None
            prepare_task.add_done_callback(
                lambda _future, held_lease=lease: self._release_lease_object(
                    held_lease
                )
            )
        else:
            self._release_lease()
        self._status_panel.visible = False
        self._error_actions.visible = False
        self._safe_update()
        if callable(callback):
            await self._call_maybe_async(callback, activity)
        if callable(after_close):
            await self._call_maybe_async(after_close)

    @staticmethod
    def _release_lease_object(lease):
        try:
            lease.release()
        except Exception:
            pass

    async def _finish_entry_unavailable(
        self,
        reason: str,
        *,
        generation: int,
        lease,
    ):
        if (
            generation != self._generation
            or not self.visible
            or self._lease is not lease
        ):
            return
        activity = self._activity
        callback = self._entry_unavailable_callback
        if reason == "requires_safe_exam_browser":
            self._emit_quiz_telemetry("player_blocked_seb", activity)
        elif reason in {"quiz_unavailable", "unsupported_action", "entry_not_opened"}:
            self._emit_quiz_telemetry("player_unavailable", activity)
        self._telemetry_started_mono = None
        self._telemetry_open_mono = None
        self._close_entry_prompt()
        prepared = self._prepared_entry
        self._prepared_entry = None
        self._entry_entering = False
        self._attempt_may_be_open = False
        self._generation += 1
        self.visible = False
        self._player_host.content = None
        self._player = None
        self._target = None
        self._activity = None
        self._launch_url = None
        self._status_panel.visible = False
        self._error_actions.visible = False
        self._clear_pending_cookies()
        self._safe_update()
        if prepared is not None:
            await asyncio.to_thread(self._close_prepared_entry, prepared)
        self._release_lease()
        if callable(callback):
            await self._call_maybe_async(callback, activity, reason)
        deferred_close = self._deferred_close
        self._deferred_close = None
        if deferred_close is not None and callable(deferred_close.get("after_close")):
            await self._call_maybe_async(deferred_close["after_close"])

    @staticmethod
    def _clear_cookies(cookies):
        if not isinstance(cookies, list):
            return
        for cookie in cookies:
            if isinstance(cookie, dict):
                cookie.clear()
        cookies.clear()

    def _clear_pending_cookies(self):
        cookies = self._pending_cookies
        self._pending_cookies = None
        if cookies is not None:
            self._clear_cookies(cookies)

    def _bootstrap_callbacks(self, generation: int):
        async def on_ready(event=None):
            await self._on_player_ready(generation, event)

        return on_ready

    async def _wait_for_ready(self, generation: int):
        await asyncio.sleep(self._ready_timeout_seconds)
        if (
            generation != self._generation
            or self._ready
            or not self.visible
            or self._player is None
        ):
            return
        self._show_recovery_message("player_unavailable")

    async def _on_player_ready(self, generation: int, _event=None):
        if generation != self._generation or not self.visible:
            return
        if self._bootstrap_started:
            return
        player = self._player
        cookies = self._pending_cookies
        if player is None or cookies is None:
            return
        launch_url = self._launch_url
        if not isinstance(launch_url, str) or not launch_url:
            self._show_recovery_message("entry_not_opened")
            return
        self._bootstrap_started = True
        self._pending_cookies = None
        try:
            bootstrapped = await player.bootstrap_session(
                cookies,
                launch_url=launch_url,
            )
            if generation != self._generation or not self.visible:
                return
            if bootstrapped is False:
                self._show_recovery_message("login_failed")
                return
            self._ready = True
            self._status_panel.visible = False
            self._loading.visible = False
            self._error_actions.visible = False
            self._auth_button.visible = False
            self._safe_update()
        except Exception as exc:
            logger.info("Quiz session bootstrap failed (%s)", type(exc).__name__)
            if generation == self._generation and self.visible:
                self._show_recovery_message("login_failed")
        finally:
            self._clear_cookies(cookies)

    def _on_player_close(self, generation: int, _event=None):
        if generation != self._generation or not self.visible:
            return
        # The native event is a close request from Moodle's page. Keep the
        # widget mounted until the user confirms leaving the attempt.
        self.request_close()

    def _on_player_error(self, generation: int, event=None):
        if generation != self._generation or not self.visible:
            return
        code = str(getattr(event, "data", ""))
        if code == "webview_unavailable" and not self._ready:
            self._show_mount_error(code)
        else:
            self._show_recovery_message(code)

    def _on_session_expired(self, generation: int, _event=None):
        if generation != self._generation or not self.visible:
            return
        self._show_recovery_message("session_expired")

    def _on_external_link(self, generation: int, _event=None):
        if generation != self._generation or not self.visible:
            return
        self._external_notice.visible = True
        self._external_notice_row.visible = True
        self._safe_update()

    def _emit_quiz_telemetry(
        self,
        action: str,
        activity: Mapping[str, Any] | None = None,
        *,
        duration_seconds: float | None = None,
    ) -> None:
        cb = getattr(self, "_on_telemetry_quiz_action", None)
        if not callable(cb):
            return
        source_site = "courses"
        if isinstance(activity, Mapping):
            site_id = str(activity.get("site_identity") or "").strip().casefold()
            if site_id == "thnn" or str(activity.get("source") or "") == "thnn_sync":
                source_site = "thnn"
            else:
                raw_url = str(activity.get("url") or "").strip()
                if raw_url and (urlsplit(raw_url).hostname or "").casefold() == "thnn.ut.edu.vn":
                    source_site = "thnn"
        elif self._target is not None:
            if str(getattr(self._target, "site_identity", "") or "").strip().casefold() == "thnn":
                source_site = "thnn"
        try:
            cb(action, source_site, duration_seconds)
        except Exception:
            pass

    def _show_mount_error(self, code: str):
        """Unwind only failures that happen before an active page is usable."""
        self._emit_quiz_telemetry("player_unavailable", self._activity)
        self._telemetry_started_mono = None
        self._telemetry_open_mono = None
        messages = {
            "webview_unavailable": "Trình xem bài chưa sẵn sàng. Hãy mở bằng trình duyệt.",
            "player_unavailable": "Trình xem bài chưa sẵn sàng. Hãy mở bằng trình duyệt.",
            "unsupported_runtime": "Hãy mở bài bằng trình duyệt.",
        }
        self._status_text.value = messages.get(
            code,
            "Không thể mở bài trong ứng dụng. Hãy mở bằng trình duyệt.",
        )
        self._loading.visible = False
        self._status_panel.visible = True
        self._error_actions.visible = True
        self._browser_button.visible = True
        self._auth_button.visible = False
        player = self._player
        self._player = None
        self._player_host.content = None
        self._ready = False
        self._clear_pending_cookies()
        self._release_lease()
        if player is not None:
            self._generation += 1
        self._safe_update()

    def _show_recovery_message(self, code: str):
        """Keep an existing WebView and its lease alive after page errors."""
        messages = {
            "webview_unavailable": "Trình xem bài đã mất kết nối. Bạn có thể thử lại hoặc mở bằng trình duyệt.",
            "load_timeout": "Trang không phản hồi. Hãy kiểm tra kết nối hoặc mở bằng trình duyệt.",
            "navigation_error": "Không thể tải trang. Hãy kiểm tra kết nối hoặc mở bằng trình duyệt.",
            "player_unavailable": "Trình xem bài chưa sẵn sàng. Hãy mở bằng trình duyệt.",
            "session_timeout": "Chưa lấy được phiên Moodle. Thử lại hoặc mở bằng trình duyệt.",
            "session_unavailable": "Không thể đăng nhập Moodle trong ứng dụng. Đăng nhập lại hoặc mở bằng trình duyệt.",
            "session_lease_inactive": "Phiên Moodle đã hết hạn. Đăng nhập lại hoặc mở bằng trình duyệt.",
            "session_site_mismatch": "Không thể dùng phiên Moodle cho trang này. Đăng nhập lại hoặc mở bằng trình duyệt.",
            "session_not_ready": "Đang chờ phiên Moodle. Thử lại sau hoặc mở bằng trình duyệt.",
            "missing_credentials": "Hãy đăng nhập UTHelper để làm bài trong ứng dụng.",
            "account_mismatch": "Tài khoản Moodle đã thay đổi. Đăng nhập lại để làm bài.",
            "site_credentials_unavailable": "Chưa có thông tin đăng nhập cho trang Moodle này.",
            "login_failed": "Không đăng nhập được Moodle. Kiểm tra tài khoản trong UTHelper.",
            "session_cookie_missing": "Không nhận được phiên Moodle. Đăng nhập lại hoặc mở bằng trình duyệt.",
            "session_expired": "Phiên Moodle hết hạn. Đăng nhập lại sau khi đóng bài.",
            "entry_outcome_uncertain": "Không xác nhận được trạng thái mở bài. Hãy kiểm tra Moodle trong trình duyệt.",
            "entry_not_opened": "Không thể mở lượt làm bài. Hãy mở bằng trình duyệt.",
        }
        self._status_text.value = messages.get(
            code,
            "Không thể tải trang. Hãy kiểm tra kết nối hoặc mở bằng trình duyệt.",
        )
        self._loading.visible = False
        self._status_panel.visible = True
        auth_reasons = {
            "missing_credentials",
            "account_mismatch",
            "site_credentials_unavailable",
            "login_failed",
            "session_cookie_missing",
            "session_lease_inactive",
        }
        needs_auth = code in auth_reasons
        can_offer_auth = callable(self._on_auth_required) and code != "session_expired"
        self._error_actions.visible = code in {
            "player_unavailable",
            "session_timeout",
            "session_unavailable",
            *auth_reasons,
            "session_expired",
            "entry_outcome_uncertain",
            "entry_not_opened",
        }
        self._browser_button.visible = self._error_actions.visible
        self._auth_button.visible = needs_auth and can_offer_auth
        self._safe_update()

    async def _request_authentication(self, _event=None):
        if (
            not callable(self._on_auth_required)
            or not self.visible
            or self._auth_request_active
            or self._ready
        ):
            return
        self._auth_request_active = True
        try:
            await self._call_maybe_async(
                self._on_auth_required, self._retry_after_auth
            )
        finally:
            self._auth_request_active = False

    async def request_app_auth(self, retry_callback: Callable):
        """Open the app credential dialog for a quiz without a player yet."""
        if callable(self._on_auth_required):
            await self._call_maybe_async(self._on_auth_required, retry_callback)

    async def _retry_after_auth(self):
        if (
            not self.visible
            or self._activity is None
            or self._lease is None
            or self._ready
        ):
            return
        # Invalidate every callback from the failed pre-bootstrap widget before
        # replacing it. Active quiz pages never use this retry path.
        self._generation += 1
        generation = self._generation
        self._clear_pending_cookies()
        self._player_host.content = None
        self._player = None
        self._bootstrap_started = False
        self._status_text.value = "Đang kết nối với Moodle..."
        self._loading.visible = True
        self._status_panel.visible = True
        self._auth_button.visible = False
        self._browser_button.visible = False
        self._error_actions.visible = False

        account_id = self._account_id
        if callable(self._account_id_provider):
            try:
                account_id = self._account_id_provider() or ""
            except Exception:
                account_id = ""
        if not account_id:
            self._show_recovery_message("missing_credentials")
            return
        if account_id.casefold() != self._account_id.casefold():
            try:
                target, state = self.prepare_activity(self._activity, account_id)
                if target is None or state is not None:
                    self._show_recovery_message(state or "session_unavailable")
                    return
                lease_provider = self._lease_provider or _default_lease_provider()
                new_lease = lease_provider(target.session_key)
            except Exception as exc:
                self._show_recovery_message(
                    getattr(exc, "reason", "session_unavailable")
                )
                return
            self._release_lease()
            self._target = target
            self._lease = new_lease
            self._account_id = account_id

        factory = self._resolve_player_factory()
        if factory is None:
            self._show_mount_error("player_unavailable")
            return
        prepared = self._prepared_entry
        self._prepared_entry = None
        if prepared is not None:
            await asyncio.to_thread(self._close_prepared_entry, prepared)
        if generation != self._generation or not self.visible:
            return
        if not self._run_task(self._prepare_entry_and_prompt, generation, factory):
            self._show_mount_error("player_unavailable")

    async def _open_in_browser(self, _event=None):
        launch_url = self._launch_url
        target = self._target
        url = launch_url or (getattr(target, "url", None) if target is not None else None)
        if not isinstance(url, str) or not url:
            return
        try:
            await ft.UrlLauncher().launch_url(url)
        except Exception:
            self._status_text.value = "Không mở được trình duyệt. Hãy quay lại thử lại sau."
            self._status_panel.visible = True
            self._safe_update()

    def request_close(
        self,
        *,
        after_close: Callable | None = None,
        title: str = "Đóng bài?",
        action_label: str = "Đóng bài",
    ) -> bool:
        """Ask before leaving; a canceled request keeps the WebView mounted."""
        if not self.visible:
            return False
        if self._entry_entering:
            self._deferred_close = {
                "after_close": after_close,
                "title": title,
                "action_label": action_label,
            }
            return True
        if self._player is None and not self._attempt_may_be_open:
            self._close_entry_prompt()
            self._run_task(
                self._discard_pending_entry,
                True,
                after_close,
            )
            return True
        if self._close_dialog.open:
            if after_close is not None:
                self._after_close = after_close
                self._close_title = title
                self._close_action_label = action_label
                self._close_dialog.title.value = title
                if len(self._close_dialog.actions) > 1:
                    self._close_dialog.actions[1].content = action_label
                self._safe_update()
            return True
        self._after_close = after_close
        self._close_title = title
        self._close_action_label = action_label
        self._close_dialog.title.value = title
        if len(self._close_dialog.actions) > 1:
            self._close_dialog.actions[1].content = action_label
        self._close_pending = True
        try:
            self._page.show_dialog(self._close_dialog)
        except Exception:
            self._close_dialog.open = True
        self._safe_update()
        return True

    def handle_back(self) -> bool:
        """Handle app/system Back before the enclosing activity detail."""
        if not self.visible:
            return False
        if self._close_dialog.open:
            self._cancel_close()
        else:
            self.request_close()
        return True

    def _on_close_button(self, _event=None):
        self.request_close()

    def _cancel_close(self, _event=None):
        self._close_pending = False
        self._after_close = None
        self._close_dialog.open = False
        try:
            self._page.pop_dialog()
        except Exception:
            pass
        self._safe_update()

    def _on_dialog_dismiss(self, _event=None):
        # Escape and barrier dismissal are both cancellations.
        self._close_pending = False
        self._after_close = None

    async def _confirm_close(self, _event=None):
        if not self.visible or not self._close_pending:
            return
        self._close_pending = False
        self._close_dialog.open = False
        try:
            self._page.pop_dialog()
        except Exception:
            pass

        activity = self._activity
        started_mono = getattr(self, "_telemetry_started_mono", None)
        self._telemetry_started_mono = None
        self._telemetry_open_mono = None
        if started_mono is not None:
            self._emit_quiz_telemetry(
                "player_closed",
                activity,
                duration_seconds=max(0.0, time.monotonic() - started_mono),
            )
        after_close = self._after_close
        self._after_close = None
        self._account_id = ""
        self._generation += 1
        self.visible = False
        self._player_host.content = None
        self._player = None
        self._target = None
        self._activity = None
        self._launch_url = None
        self._ready = False
        self._auth_request_active = False
        self._entry_entering = False
        self._attempt_may_be_open = False
        self._status_panel.visible = False
        self._error_actions.visible = False
        self._clear_pending_cookies()
        if self._entry_password_field is not None:
            self._entry_password_field.value = ""
        prepared = self._prepared_entry
        self._prepared_entry = None
        operation_task = self._entry_operation_task
        lease = self._lease
        if lease is not None and operation_task is not None and not operation_task.done():
            self._lease = None
            if prepared is not None:
                operation_task.add_done_callback(
                    lambda _future, pending=prepared: self._close_prepared_entry_later(
                        pending
                    )
                )
            operation_task.add_done_callback(
                lambda _future, held_lease=lease: self._release_lease_object(
                    held_lease
                )
            )
        else:
            if prepared is not None:
                await asyncio.to_thread(self._close_prepared_entry, prepared)
            self._release_lease()
        self._safe_update()

        try:
            if callable(self.on_closed):
                await self._call_maybe_async(self.on_closed, activity)
        finally:
            if callable(after_close):
                await self._call_maybe_async(after_close)

    def _release_lease(self):
        lease = self._lease
        self._lease = None
        if lease is not None:
            try:
                lease.release()
            except Exception as exc:
                logger.warning("Quiz session lease release failed (%s)", type(exc).__name__)

    def update_theme(self):
        """Refresh this detached or mounted quiz surface from semantic colors."""
        current_palette = semantic_palette()
        refresh_semantic_control_colors(
            [
                self,
                *(
                    value
                    for value in vars(self).values()
                    if isinstance(value, ft.BaseControl)
                ),
            ],
            self._rendered_palette,
            current_palette,
        )
        self._rendered_palette = current_palette

        self.bgcolor = C.BG
        self._player_host.bgcolor = C.BG
        self._loading.color = C.ACCENT
        self._status_panel.bgcolor = C.SURFACE
        self._status_panel.border = ft.Border.all(1, C.BORDER)
        self._external_notice.bgcolor = C.SURFACE
        self._external_notice.border = ft.Border.all(1, C.BORDER)
        self._browser_button.bgcolor = C.SURFACE
        self._browser_button.color = C.ACCENT
        self._browser_button.style = ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=8),
            side=ft.BorderSide(1, C.ACCENT),
            padding=ft.Padding.symmetric(horizontal=16, vertical=10),
        )

        self._close_dialog.bgcolor = C.SURFACE
        self._close_dialog.title.color = C.TEXT_PRIMARY
        self._close_dialog.content.color = C.TEXT_SECONDARY
        self._close_dialog.actions[0].style = ft.ButtonStyle(
            color=C.TEXT_SECONDARY
        )
        self._close_dialog.actions[1].bgcolor = C.CRITICAL
        self._close_dialog.actions[1].color = C.ON_CRITICAL
        if self._entry_dialog is not None:
            self._entry_dialog.bgcolor = C.SURFACE
            self._entry_dialog.title.color = C.TEXT_PRIMARY
            for action in self._entry_actions:
                if isinstance(action, ft.TextButton):
                    action.style = ft.ButtonStyle(color=C.TEXT_SECONDARY)
                else:
                    action.bgcolor = C.ACCENT
                    action.color = C.ON_ACCENT
            if self._entry_password_field is not None:
                self._entry_password_field.bgcolor = C.BG
                self._entry_password_field.color = C.TEXT_PRIMARY
                self._entry_password_field.border_color = C.BORDER
                self._entry_password_field.focused_border_color = C.ACCENT

    @staticmethod
    async def _call_maybe_async(callback: Callable, *args):
        result = callback(*args)
        if asyncio.iscoroutine(result):
            await result

    def _safe_update(self):
        try:
            self._page.update()
        except Exception:
            pass

    def _ensure_session_cleaner(self):
        if self._session_cleaner is not None or self._player_factory is not None:
            return self._session_cleaner
        if not self.native_runtime_supported():
            return None
        services = getattr(self._page, "services", None)
        if not isinstance(services, list):
            return None
        try:
            cleaner_cls = _default_session_cleaner_factory()
            cleaner = cleaner_cls()
        except Exception:
            return None
        services.append(cleaner)
        self._session_cleaner = cleaner
        return cleaner

    async def clear_persisted_sessions(self) -> bool:
        """Purge stored Windows WebView2 profiles or mobile cookies/storage."""
        if self.visible or self._player is not None:
            return False
        cleaner = self._session_cleaner
        if cleaner is None:
            cleaner = self._ensure_session_cleaner()
            if cleaner is None:
                return False
            self._safe_update()
        clear_method = getattr(cleaner, "clear_sessions", None)
        if not callable(clear_method):
            return False
        try:
            return bool(await clear_method())
        except Exception:
            return False
