"""Explicit, one-shot entry into a trusted Moodle quiz attempt."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

from core.moodle_sites import MoodleSite
from core.quiz_player import QuizLaunchTarget, QuizSessionLease
from core.quiz_web_session import (
    QuizWebCookie,
    QuizWebSessionError,
    _authenticated_quiz_client,
    _export_moodle_session_cookies,
    _site_for_target,
)


class QuizEntryError(ValueError):
    """A short reason code safe to show or record in UI state."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass
class QuizEntryResult:
    """Resolved attempt destination and its isolated native WebView cookies."""

    launch_url: str
    mode: str
    secure_window: bool
    _cookies: tuple[QuizWebCookie, ...] = field(repr=False)
    _cleared: bool = field(default=False, init=False, repr=False)

    def to_native_cookies(self) -> list[dict[str, Any]]:
        """Return detached dictionaries for the native WebView bridge."""
        if self._cleared:
            raise QuizEntryError("session_closed")
        return [cookie.to_native_dict() for cookie in self._cookies]

    def clear(self) -> None:
        """Drop this result's cookie references after the UI has copied them."""
        self._cookies = ()
        self._cleared = True


@dataclass
class _QuizForm:
    method: str
    action: str
    form_id: str
    is_start_button: bool
    hidden_fields: dict[str, str] = field(repr=False)
    password_field: str | None
    submit_controls: list[tuple[str, str, str]] = field(repr=False)
    labels: list[str]
    text: str = field(repr=False)


class _OverviewParser(HTMLParser):
    """Read only the start form's controls, labels and relevant page signals."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[_QuizForm] = []
        self.page_text: list[str] = []
        self.secure_window = False
        self._current: dict[str, Any] | None = None
        self._button_depth = 0
        self._button_text: list[str] = []
        self._button_name = ""
        self._button_value = ""
        self._div_start_button_stack: list[bool] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name.casefold(): value or "" for name, value in attrs}
        classes = set(attributes.get("class", "").casefold().split())
        if "quizsecuremoderequired" in classes:
            self.secure_window = True
        if tag.casefold() == "div":
            self._div_start_button_stack.append("quizstartbuttondiv" in classes)

        if tag.casefold() == "form":
            self._current = {
                "method": attributes.get("method", "get").casefold(),
                "action": attributes.get("action", ""),
                "id": attributes.get("id", ""),
                "is_start_button": any(self._div_start_button_stack),
                "hidden": {},
                "password": None,
                "submits": [],
                "labels": [],
                "text": [],
            }
        elif tag.casefold() == "input" and self._current is not None:
            name = attributes.get("name", "")
            input_type = attributes.get("type", "text").casefold()
            value = attributes.get("value", "")
            if name and input_type == "hidden":
                self._current["hidden"][name] = value
            elif name and input_type == "password":
                self._current["password"] = name
            if input_type in {"submit", "button"} and value.strip():
                self._current["labels"].append(value.strip())
            if name and input_type in {"submit", "button"}:
                self._current["submits"].append((name, value, value.strip()))
        elif tag.casefold() == "button" and self._current is not None:
            self._button_depth = 1
            self._button_text = []
            self._button_name = attributes.get("name", "")
            self._button_value = attributes.get("value", "")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "button" and self._button_depth:
            label = " ".join(" ".join(self._button_text).split())
            if label and self._current is not None:
                self._current["labels"].append(label)
                if self._button_name:
                    self._current["submits"].append(
                        (self._button_name, self._button_value, label)
                    )
            self._button_depth = 0
            self._button_text = []
        if tag.casefold() == "form" and self._current is not None:
            current = self._current
            self.forms.append(
                _QuizForm(
                    method=current["method"],
                    action=current["action"],
                    form_id=current["id"],
                    is_start_button=current["is_start_button"],
                    hidden_fields=dict(current["hidden"]),
                    password_field=current["password"],
                    submit_controls=list(current["submits"]),
                    labels=list(current["labels"]),
                    text=" ".join(" ".join(current["text"]).split()),
                )
            )
            self._current = None
        if tag.casefold() == "div" and self._div_start_button_stack:
            self._div_start_button_stack.pop()

    def handle_data(self, data: str) -> None:
        if not data.strip():
            return
        self.page_text.append(data)
        if self._current is not None:
            self._current["text"].append(data)
        if self._button_depth:
            self._button_text.append(data)


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def _form_route(site: MoodleSite, action: str) -> str | None:
    action_url = urljoin(f"{site.origin}/mod/quiz/view.php", action or "")
    try:
        parsed = urlsplit(action_url)
        origin = f"{parsed.scheme.casefold()}://{parsed.netloc.casefold()}"
    except ValueError:
        return None
    if (
        origin != site.origin
        or parsed.path != "/mod/quiz/startattempt.php"
        or parsed.fragment
    ):
        return None
    return parsed.path + (f"?{parsed.query}" if parsed.query else "")


def _label_mode(labels: list[str]) -> str | None:
    normalized = [_normalized(label) for label in labels if label.strip()]
    resume_phrases = (
        "continue attempt",
        "continue the attempt",
        "continue your attempt",
        "continue the last attempt",
        "resume attempt",
        "tiếp tục",
        "làm tiếp",
    )
    start_phrases = (
        "attempt quiz now",
        "start attempt",
        "start a new attempt",
        "re-attempt quiz",
        "try another attempt",
        "bắt đầu",
        "làm bài",
        "thực hiện bài",
        "thực hiện lại đề thi",
    )
    resume = any(any(phrase in label for phrase in resume_phrases) for label in normalized)
    start = any(any(phrase in label for phrase in start_phrases) for label in normalized)
    if resume == start:
        return None
    return "resume" if resume else "start"


def _page_reason(text: str) -> str:
    normalized = _normalized(text)
    exhausted_phrases = (
        "no more attempts",
        "no attempts remaining",
        "maximum number of attempts reached",
        "max attempts reached",
        "không còn lần kiểm tra nào",
        "không còn lượt làm bài",
        "đã hết số lần",
    )
    if any(phrase in normalized for phrase in exhausted_phrases):
        return "attempts_exhausted"
    closed_phrases = (
        "quiz is closed",
        "quiz has closed",
        "quiz is not currently available",
        "quiz is not open",
        "the quiz is closed",
        "đã đóng",
        "đã kết thúc",
        "chưa mở",
    )
    if any(phrase in normalized for phrase in closed_phrases):
        return "quiz_closed"
    return "quiz_unavailable"


def _form_payload(form: _QuizForm) -> dict[str, str] | None:
    """Copy hidden preflight fields and the one activated submit control."""
    fields = dict(form.hidden_fields)
    controls = [control for control in form.submit_controls if control[0]]
    if len(controls) == 1:
        selected = controls[0]
    elif controls:
        selected_controls = [
            control for control in controls if _label_mode([control[2]]) == "start"
        ]
        if len(selected_controls) == 1:
            selected = selected_controls[0]
        else:
            non_cancel = [
                control
                for control in controls
                if control[0].casefold() != "cancel"
                and not any(
                    word in _normalized(control[2])
                    for word in ("cancel", "hủy", "huỷ")
                )
            ]
            if len(non_cancel) != 1:
                return None
            selected = non_cancel[0]
    else:
        selected = None
    if selected is not None:
        fields[selected[0]] = selected[1]
    return fields


def _inspect_page(
    html: str,
    target: QuizLaunchTarget,
    site: MoodleSite,
) -> tuple[str, bool, str | None, _QuizForm | None, bool]:
    parser = _OverviewParser()
    parser.feed(html)
    page_text = " ".join(parser.page_text)
    folded_page_text = page_text.casefold()
    if "safe exam browser" in folded_page_text or "quizaccess_seb" in html.casefold():
        return "unavailable", parser.secure_window, "requires_safe_exam_browser", None, False

    candidates = [
        form
        for form in parser.forms
        if form.method == "post" and _form_route(site, form.action) is not None
    ]
    start_forms = [form for form in candidates if form.is_start_button]
    if not start_forms:
        start_forms = [form for form in candidates if _label_mode(form.labels) is not None]
    start_form = start_forms[0] if len(start_forms) == 1 else None
    declared_preflight_forms = [
        form for form in parser.forms if form.form_id == "mod_quiz_preflight_form"
    ]
    preflight_forms = [
        form
        for form in candidates
        if form.form_id == "mod_quiz_preflight_form"
    ]
    if declared_preflight_forms:
        selected_form = (
            preflight_forms[0]
            if len(preflight_forms) == 1 and len(declared_preflight_forms) == 1
            else None
        )
    else:
        selected_form = start_form
    mode = _label_mode(start_form.labels) if start_form is not None else None
    payload = _form_payload(selected_form) if selected_form is not None else None
    if (
        selected_form is not None
        and mode is not None
        and payload is not None
        and payload.get("cmid") == str(target.course_module_id)
        and payload.get("sesskey")
    ):
        return (
            mode,
            parser.secure_window or target.secure_window,
            None,
            selected_form,
            selected_form.password_field is not None,
        )

    reason = _page_reason(page_text)
    if reason == "quiz_unavailable" and candidates:
        reason = "unsupported_action"
    return "unavailable", parser.secure_window or target.secure_window, reason, None, False


def _attempt_destination(url: str, site: MoodleSite) -> bool:
    try:
        parsed = urlsplit(url)
        origin = f"{parsed.scheme.casefold()}://{parsed.netloc.casefold()}"
        query = parse_qs(parsed.query, keep_blank_values=True)
    except ValueError:
        return False
    attempts = query.get("attempt", [])
    return (
        origin == site.origin
        and parsed.path in {"/mod/quiz/attempt.php", "/mod/quiz/summary.php"}
        and len(attempts) == 1
        and re.fullmatch(r"[1-9][0-9]*", attempts[0]) is not None
    )


def _overview_destination(url: str, site: MoodleSite) -> bool:
    try:
        parsed = urlsplit(url)
        origin = f"{parsed.scheme.casefold()}://{parsed.netloc.casefold()}"
    except ValueError:
        return False
    return origin == site.origin and parsed.path == "/mod/quiz/view.php"


def _wrong_password(text: str) -> bool:
    normalized = _normalized(text)
    return any(
        phrase in normalized
        for phrase in (
            "wrong password",
            "incorrect password",
            "password is incorrect",
            "sai mật khẩu",
            "mật khẩu không đúng",
            "mật khẩu không hợp lệ",
        )
    )


@dataclass
class QuizPreparedEntry:
    """An inspected quiz overview, ready for one explicit Moodle entry."""

    target: QuizLaunchTarget = field(repr=False)
    mode: str
    secure_window: bool
    password_required: bool
    reason: str | None
    _site: MoodleSite = field(repr=False)
    _client: Any = field(repr=False)
    _form_route: str | None = field(repr=False)
    _form_fields: dict[str, str] = field(repr=False)
    _password_field: str | None = field(repr=False)
    _lease: QuizSessionLease = field(repr=False)
    _operation_lease: QuizSessionLease = field(repr=False)
    _result: QuizEntryResult | None = field(default=None, init=False, repr=False)
    _busy: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _uncertain: bool = field(default=False, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    def _check_session(self) -> None:
        if self._closed:
            raise QuizEntryError("session_closed")
        if not isinstance(self._lease, QuizSessionLease) or not self._lease.is_active:
            raise QuizEntryError("session_lease_inactive")
        if not self._operation_lease.is_active:
            raise QuizEntryError("session_lease_inactive")
        if self._lease.session_key != self.target.session_key:
            raise QuizEntryError("session_site_mismatch")
        if not self._lease.is_ready:
            raise QuizEntryError("session_not_ready")

    def _read_overview(self) -> None:
        self._check_session()
        try:
            final_url, html = self._client._open_html(self.target.url)
        except Exception:
            raise QuizEntryError("overview_unavailable") from None
        if not _overview_destination(final_url, self._site):
            raise QuizEntryError("session_expired")
        mode, secure_window, reason, form, password_required = _inspect_page(
            html, self.target, self._site
        )
        self.mode = mode
        self.secure_window = secure_window
        self.reason = reason
        self.password_required = password_required
        if form is None:
            self._form_route = None
            self._form_fields.clear()
            self._password_field = None
        else:
            self._form_route = _form_route(self._site, form.action)
            payload = _form_payload(form)
            self._form_fields = payload or {}
            self._password_field = form.password_field

    def enter(
        self,
        password: str | None = None,
        confirmed: bool = False,
    ) -> QuizEntryResult:
        """Submit the discovered start form once and resolve its attempt URL.

        A new attempt requires ``confirmed=True``. Resume forms are submitted
        without that confirmation. Network ambiguity is reconciled with a GET
        of the overview before another submit can be attempted.
        """
        if not self._busy.acquire(blocking=False):
            raise QuizEntryError("entry_in_progress")
        try:
            self._check_session()
            if self._result is not None:
                return self._result
            if self._uncertain:
                self._read_overview()
                if self.mode == "unavailable":
                    raise QuizEntryError(self.reason or "quiz_unavailable")
                # The fresh overview is authoritative; a submit still requires
                # the caller's normal confirmation when it offers a new start.
                self._uncertain = False
            if self.mode == "unavailable" or self._form_route is None:
                raise QuizEntryError(self.reason or "quiz_unavailable")
            if self.mode == "start" and confirmed is not True:
                raise QuizEntryError("confirmation_required")
            if self.password_required and (not isinstance(password, str) or not password):
                raise QuizEntryError("password_required")

            self._check_session()
            fields = dict(self._form_fields)
            if self.password_required and self._password_field:
                fields[self._password_field] = password or ""
            try:
                final_url, html = self._client._open_html(self._form_route, data=fields)
            except Exception:
                self._uncertain = True
                raise QuizEntryError("entry_outcome_uncertain") from None

            page_text = " ".join(_OverviewParserText(html).text)
            if self.password_required and _wrong_password(page_text):
                raise QuizEntryError("wrong_password")
            if not _attempt_destination(final_url, self._site):
                # A same-site page can describe a closed quiz, a rejected
                # password, or a concurrently changed attempt state. Do not
                # guess and do not resubmit until a new overview GET reconciles it.
                self._uncertain = True
                raise QuizEntryError("entry_not_opened")

            # Moodle has accepted this POST and resolved it to an attempt.
            # If cookie export then fails, a later call must reconcile the
            # overview before it can submit the cached start form again.
            self._uncertain = True
            try:
                cookies = _export_moodle_session_cookies(
                    self._client, self._site, self.target
                )
            except QuizWebSessionError as error:
                raise QuizEntryError(error.reason) from None
            except Exception:
                raise QuizEntryError("session_cookie_missing") from None
            self._result = QuizEntryResult(
                launch_url=final_url,
                mode=self.mode,
                secure_window=self.secure_window,
                _cookies=cookies,
            )
            self._uncertain = False
            return self._result
        finally:
            self._busy.release()

    def close(self) -> None:
        """Release hidden form values and cookie references held by this entry."""
        with self._busy:
            if self._closed:
                return
            self._form_fields.clear()
            self._form_route = None
            self._password_field = None
            if self._result is not None:
                self._result.clear()
            client = self._client
            if client is not None:
                client._cookie_jar.clear()
                client._sesskey = ""
            self._operation_lease.release()
            self._client = None
            self._closed = True


class _OverviewParserText(HTMLParser):
    """Extract visible response text without retaining the response HTML."""

    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.feed(html)

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.text.append(data)


def prepare_quiz_entry(
    target: QuizLaunchTarget,
    lease: QuizSessionLease,
) -> QuizPreparedEntry:
    """Authenticate and inspect a quiz overview without starting an attempt."""
    try:
        site = _site_for_target(target)
        client, site, operation_lease = _authenticated_quiz_client(target, lease)
    except QuizWebSessionError as error:
        raise QuizEntryError(error.reason) from None

    try:
        try:
            final_url, html = client._open_html(target.url)
        except Exception:
            raise QuizEntryError("overview_unavailable") from None
        if not _overview_destination(final_url, site):
            raise QuizEntryError("session_expired")

        mode, secure_window, reason, form, password_required = _inspect_page(
            html, target, site
        )
        form_route = _form_route(site, form.action) if form is not None else None
        form_fields = _form_payload(form) if form is not None else {}
        password_field = form.password_field if form is not None else None

        # Fail before any attempt POST if the authenticated session cookie cannot
        # be handed to the WebView after Moodle resolves the entry.
        try:
            _export_moodle_session_cookies(client, site, target)
        except QuizWebSessionError as error:
            raise QuizEntryError(error.reason) from None

        return QuizPreparedEntry(
            target=target,
            mode=mode,
            secure_window=secure_window,
            password_required=password_required,
            reason=reason,
            _site=site,
            _client=client,
            _form_route=form_route,
            _form_fields=form_fields or {},
            _password_field=password_field,
            _lease=lease,
            _operation_lease=operation_lease,
        )
    except Exception:
        client._cookie_jar.clear()
        operation_lease.release()
        raise
