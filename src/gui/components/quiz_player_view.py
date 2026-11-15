"""Full-window Moodle quiz surface backed by the native quiz extension."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from typing import Any

import flet as ft

import platform_utils
from gui.core.theme import (
    C,
    refresh_semantic_control_colors,
    semantic_palette,
)

logger = logging.getLogger(__name__)

_READY_TIMEOUT_SECONDS = 12
_LOGIN_WAIT_TIMEOUT_SECONDS = 15
_SESSION_PREPARE_TIMEOUT_SECONDS = 30


def _native_runtime_supported(page=None) -> bool:
    """Use native WebView only in the supported app runtimes."""
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


def _default_target_builder():
    from core.quiz_player import build_quiz_launch_target

    return build_quiz_launch_target


def _default_lease_provider():
    from core.quiz_player import acquire_quiz_session_lease

    return acquire_quiz_session_lease


def _default_session_provider():
    from core.quiz_web_session import prepare_quiz_web_session

    return prepare_quiz_web_session


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
        session_provider: Callable | None = None,
        account_id_provider: Callable | None = None,
        on_auth_required: Callable | None = None,
        ready_timeout_seconds: float = _READY_TIMEOUT_SECONDS,
    ):
        self._page = page
        self.on_closed = on_closed
        self._player_factory = player_factory
        self._target_builder = target_builder
        self._lease_provider = lease_provider
        self._session_provider = session_provider
        self._account_id_provider = account_id_provider
        self._on_auth_required = on_auth_required
        self._ready_timeout_seconds = max(0.1, float(ready_timeout_seconds))

        self._player = None
        self._lease = None
        self._target = None
        self._activity: dict[str, Any] | None = None
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
        self._saved_window_state: dict[str, Any] | None = None

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
        """Return a trusted target and a UI state for an actual quiz overview."""
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
            return target, "unsupported_runtime"
        if self._resolve_player_factory() is None:
            return target, "player_unavailable"
        return target, None

    def open_activity(self, target, activity: Mapping[str, Any]) -> bool:
        """Acquire the per-account session guard and mount one native player."""
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
        self._title.value = str(activity.get("title") or "Bài kiểm tra")
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
        self._status_text.value = ""
        self._loading.visible = True
        self._status_panel.visible = True
        self.visible = True
        self._enter_desktop_fullscreen()

        try:
            lease_provider = self._lease_provider or _default_lease_provider()
            lease = lease_provider(target.session_key)
            self._lease = lease
        except Exception as exc:
            logger.warning("Quiz session lease unavailable (%s)", type(exc).__name__)
            self._show_mount_error("player_unavailable")
            return False

        self._safe_update()
        if not self._run_task(
            self._prepare_session_and_mount, generation, factory
        ):
            self._show_mount_error("player_unavailable")
            return False
        return True

    def open_activity_for_account(
        self,
        target,
        activity: Mapping[str, Any],
        account_id: str,
    ) -> bool:
        self._account_id = str(account_id or "")
        return self.open_activity(target, activity)

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

    async def _prepare_session_and_mount(
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
            provider = self._session_provider or _default_session_provider()
            session_task = asyncio.create_task(
                asyncio.to_thread(provider, self._target, lease)
            )
            done, _ = await asyncio.wait(
                {session_task},
                timeout=_SESSION_PREPARE_TIMEOUT_SECONDS,
            )
            if not done:
                session_task.add_done_callback(self._clear_late_session_result)
                if (
                    generation == self._generation
                    and self.visible
                    and self._lease is lease
                ):
                    self._show_recovery_message("session_timeout")
                return
            session = session_task.result()
            cookies = session.to_native_cookies()
            del session
            if (
                generation != self._generation
                or not self.visible
                or self._lease is not lease
            ):
                self._clear_cookies(cookies)
                return
            if not cookies:
                if (
                    generation == self._generation
                    and self.visible
                    and self._lease is lease
                ):
                    self._show_recovery_message("session_cookie_missing")
                return
            self._pending_cookies = cookies
            self._bootstrap_started = False
            self._player = factory(
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
            self._safe_update()
            self._run_task(self._wait_for_ready, generation)
        except Exception as exc:
            reason = getattr(exc, "reason", "")
            logger.info("Quiz session preparation failed (%s)", type(exc).__name__)
            if (
                generation == self._generation
                and self.visible
                and self._lease is lease
            ):
                self._clear_pending_cookies()
                self._show_recovery_message(reason or "session_unavailable")

    @classmethod
    def _clear_late_session_result(cls, future):
        try:
            session = future.result()
            cookies = session.to_native_cookies()
            cls._clear_cookies(cookies)
            clear = getattr(session, "clear", None)
            if callable(clear):
                clear()
            del session
        except Exception:
            # Never surface an expired worker result or an exception that may
            # contain a server response or credential detail.
            return

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
        self._bootstrap_started = True
        self._pending_cookies = None
        try:
            bootstrapped = await player.bootstrap_session(cookies)
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

    def _show_mount_error(self, code: str):
        """Unwind only failures that happen before an active page is usable."""
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
        if not self._run_task(self._prepare_session_and_mount, generation, factory):
            self._show_mount_error("player_unavailable")

    async def _open_in_browser(self, _event=None):
        target = self._target
        if target is None:
            return
        try:
            await ft.UrlLauncher().launch_url(target.url)
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
        after_close = self._after_close
        self._after_close = None
        self._account_id = ""
        self._generation += 1
        self.visible = False
        self._player_host.content = None
        self._player = None
        self._target = None
        self._activity = None
        self._ready = False
        self._auth_request_active = False
        self._status_panel.visible = False
        self._error_actions.visible = False
        self._clear_pending_cookies()
        self._release_lease()
        self._restore_desktop_window()
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

    def _enter_desktop_fullscreen(self):
        if (
            not self.native_runtime_supported()
            or not platform_utils.IS_WINDOWS
            or self._saved_window_state is not None
        ):
            return
        window = getattr(self._page, "window", None)
        if window is None:
            return
        fields = (
            "full_screen",
            "width",
            "height",
            "min_width",
            "max_width",
            "min_height",
            "max_height",
            "resizable",
            "maximizable",
        )
        self._saved_window_state = {
            field: getattr(window, field, None) for field in fields
        }
        try:
            window.full_screen = True
            self._page.update()
        except Exception:
            self._saved_window_state = None

    def _restore_desktop_window(self):
        state = self._saved_window_state
        self._saved_window_state = None
        window = getattr(self._page, "window", None)
        if state is None or window is None:
            return
        try:
            window.full_screen = bool(state.get("full_screen", False))
            for field in (
                "width",
                "height",
                "min_width",
                "max_width",
                "min_height",
                "max_height",
                "resizable",
                "maximizable",
            ):
                setattr(window, field, state.get(field))
            self._page.update()
        except Exception:
            logger.debug("Could not restore window size after quiz close", exc_info=True)

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
