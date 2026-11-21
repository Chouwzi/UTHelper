"""One-shot, explicit Moodle quiz attempt entry policy."""

from __future__ import annotations

import os
import sys
import threading
from http.cookiejar import Cookie

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import core.quiz_entry as quiz_entry
from core.moodle_sites import moodle_site_from_origin
from core.quiz_entry import QuizEntryError, prepare_quiz_entry
from core.quiz_player import (
    _begin_quiz_session_login,
    _end_quiz_session_login,
    acquire_quiz_session_lease,
    build_quiz_launch_target,
)
from core.quiz_web_session import QuizWebSessionError


def _session_cookie(host: str) -> Cookie:
    return Cookie(
        version=0,
        name="MoodleSession",
        value="private-session-value",
        port=None,
        port_specified=False,
        domain=host,
        domain_specified=False,
        domain_initial_dot=False,
        path="/",
        path_specified=True,
        secure=True,
        expires=None,
        discard=True,
        comment=None,
        comment_url=None,
        rest={"HttpOnly": None},
        rfc2109=False,
    )


def _overview(label: str, *, password: bool = False, secure: bool = False) -> str:
    secure_class = ' class="quizsecuremoderequired"' if secure else ""
    password_form = _password_preflight() if password else ""
    return f"""<html><body><div{secure_class}>
    <div class="quizstartbuttondiv"><form method="post" action="/mod/quiz/startattempt.php">
      <input type="hidden" name="cmid" value="52">
      <input type="hidden" name="sesskey" value="private-form-sesskey">
      <button type="submit">{label}</button>
    </form></div>{password_form}</div></body></html>"""


def _password_preflight() -> str:
    return """<form id="mod_quiz_preflight_form" method="post" action="/mod/quiz/startattempt.php">
      <input type="hidden" name="cmid" value="52">
      <input type="hidden" name="sesskey" value="private-form-sesskey">
      <input type="hidden" name="_qf__mod_quiz_preflight_form" value="1">
      <input type="password" name="quizpassword">
      <input type="submit" name="submitbutton" value="Start attempt">
      <input type="submit" name="cancel" value="Cancel">
    </form>"""


def _prepared(monkeypatch, overview: str, *, after_post=None):
    target = build_quiz_launch_target(
        "https://courses.ut.edu.vn/mod/quiz/view.php?id=52", "student-42"
    )
    lease = acquire_quiz_session_lease(target.session_key)
    operation_lease = acquire_quiz_session_lease(target.session_key)
    site = moodle_site_from_origin("https://courses.ut.edu.vn")
    assert site is not None
    from core.client import MoodleClient

    client = MoodleClient(site.origin)
    client._cookie_jar.set_cookie(_session_cookie("courses.ut.edu.vn"))
    calls = []
    current_overview = [overview]

    def fake_open_html(path, *, data=None):
        calls.append((path, data))
        if path == target.url and data is None:
            return target.url, current_overview[0]
        assert path == "/mod/quiz/startattempt.php"
        if after_post is not None:
            return after_post(data, calls, current_overview)
        return (
            f"{site.origin}/mod/quiz/attempt.php?attempt=70&page=0",
            "<html>Attempt</html>",
        )

    client._open_html = fake_open_html
    monkeypatch.setattr(
        quiz_entry,
        "_authenticated_quiz_client",
        lambda _target, _lease: (client, site, operation_lease),
    )
    prepared = prepare_quiz_entry(target, lease)
    return prepared, lease, operation_lease, calls, current_overview


def _release(prepared, lease):
    prepared.close()
    lease.release()


@pytest.mark.parametrize("start_label", ["Start attempt", "Thực hiện lại đề thi"])
def test_prepare_only_inspects_and_start_requires_explicit_confirmation(
    monkeypatch, start_label
):
    prepared, lease, operation_lease, calls, _ = _prepared(
        monkeypatch, _overview(start_label, secure=True)
    )
    try:
        assert prepared.mode == "start"
        assert prepared.secure_window is True
        assert prepared.password_required is False
        assert [call[0] for call in calls] == [
            "https://courses.ut.edu.vn/mod/quiz/view.php?id=52"
        ]
        with pytest.raises(QuizEntryError) as error:
            prepared.enter()
        assert error.value.reason == "confirmation_required"
        assert len(calls) == 1

        result = prepared.enter(confirmed=True)
        assert result.launch_url == (
            "https://courses.ut.edu.vn/mod/quiz/attempt.php?attempt=70&page=0"
        )
        assert result.secure_window is True
        assert result.to_native_cookies()[0]["name"] == "MoodleSession"
        assert calls[1] == (
            "/mod/quiz/startattempt.php",
            {"cmid": "52", "sesskey": "private-form-sesskey"},
        )
        assert prepared.enter(confirmed=True) is result
        assert len(calls) == 2
        assert not operation_lease._released
    finally:
        _release(prepared, lease)
    assert operation_lease._released


def test_resume_is_entered_without_new_attempt_confirmation(monkeypatch):
    prepared, lease, _, calls, _ = _prepared(
        monkeypatch, _overview("Tiếp tục lần kiểm tra cuối cùng")
    )
    try:
        assert prepared.mode == "resume"
        result = prepared.enter()
        assert result.mode == "resume"
        assert len(calls) == 2
        assert calls[1][1] == {"cmid": "52", "sesskey": "private-form-sesskey"}
    finally:
        _release(prepared, lease)


@pytest.mark.parametrize(
    "label,expected_mode",
    [
        ("Tiếp tục làm bài", "resume"),
        ("Attempt quiz", "start"),
        ("Thực hiện đề thi", "start"),
    ],
)
def test_moodle_default_labels_are_classified(label, expected_mode):
    assert quiz_entry._label_mode([label]) == expected_mode


def test_conflicting_start_and_resume_controls_remain_ambiguous():
    assert quiz_entry._label_mode(["Continue attempt", "Start attempt"]) is None


@pytest.mark.parametrize(
    "wrong_text",
    [
        "Sai mật khẩu",
        "Invalid password",
        "The password entered was incorrect",
        "Mật khẩu đã nhập không đúng",
        "Mật khẩu đã nhập không chính xác",
        "Mật khẩu không chính xác",
    ],
)
def test_password_is_required_and_wrong_password_can_be_retried(
    monkeypatch, wrong_text
):
    def respond(data, calls, _overview_state):
        if data.get("quizpassword") == "private-wrong-password":
            return "https://courses.ut.edu.vn/mod/quiz/view.php?id=52", wrong_text
        return (
            "https://courses.ut.edu.vn/mod/quiz/summary.php?attempt=81",
            "<html>Summary</html>",
        )

    prepared, lease, _, calls, _ = _prepared(
        monkeypatch, _overview("Start attempt", password=True), after_post=respond
    )
    try:
        assert prepared.password_required is True
        with pytest.raises(QuizEntryError) as error:
            prepared.enter(confirmed=True)
        assert error.value.reason == "password_required"
        assert len(calls) == 1

        with pytest.raises(QuizEntryError) as error:
            prepared.enter(password="private-wrong-password", confirmed=True)
        assert error.value.reason == "wrong_password"
        result = prepared.enter(password="private-correct-password", confirmed=True)
        assert result.launch_url.endswith("/mod/quiz/summary.php?attempt=81")
        assert calls[1][1] == {
            "cmid": "52",
            "sesskey": "private-form-sesskey",
            "_qf__mod_quiz_preflight_form": "1",
            "submitbutton": "Start attempt",
            "quizpassword": "private-wrong-password",
        }
        assert calls[2][1]["quizpassword"] == "private-correct-password"
    finally:
        _release(prepared, lease)


def test_exhaustion_is_distinct_from_unknown_action_and_never_posts(monkeypatch):
    prepared, lease, _, calls, _ = _prepared(
        monkeypatch, "<p>You have no attempts remaining.</p>"
    )
    try:
        assert prepared.mode == "unavailable"
        assert prepared.reason == "attempts_exhausted"
        with pytest.raises(QuizEntryError) as error:
            prepared.enter(confirmed=True)
        assert error.value.reason == "attempts_exhausted"
        assert len(calls) == 1
    finally:
        _release(prepared, lease)

    prepared, lease, _, calls, _ = _prepared(
        monkeypatch, _overview("Thực hiện kiểm tra ngay", secure=False)
    )
    try:
        # This label is intentionally not an affirmative supported start/resume
        # phrase. Unknown locale copy remains recoverable without a mutation.
        assert prepared.mode == "unavailable"
        assert prepared.reason == "unsupported_action"
        with pytest.raises(QuizEntryError) as error:
            prepared.enter(confirmed=True)
        assert error.value.reason == "unsupported_action"
        assert len(calls) == 1
    finally:
        _release(prepared, lease)


def test_ambiguous_post_requires_overview_reconciliation_before_next_post(monkeypatch):
    def respond(data, calls, overview_state):
        if len([call for call in calls if call[1] is not None]) == 1:
            raise TimeoutError("private transport detail")
        return (
            "https://courses.ut.edu.vn/mod/quiz/attempt.php?attempt=90",
            "<html>Attempt</html>",
        )

    prepared, lease, _, calls, overview_state = _prepared(
        monkeypatch, _overview("Start attempt"), after_post=respond
    )
    try:
        with pytest.raises(QuizEntryError) as error:
            prepared.enter(confirmed=True)
        assert error.value.reason == "entry_outcome_uncertain"
        assert len(calls) == 2

        overview_state[0] = _overview("Continue attempt")
        result = prepared.enter()
        assert result.mode == "resume"
        assert [call[1] is None for call in calls] == [True, False, True, False]
    finally:
        _release(prepared, lease)


def test_cookie_export_failure_after_accepted_start_reconciles_before_retry(monkeypatch):
    def respond(_data, calls, overview_state):
        post_count = len([call for call in calls if call[1] is not None])
        if post_count == 1:
            # The server accepted the first start; the next overview now offers
            # the existing attempt as a resume action.
            overview_state[0] = _overview("Continue attempt")
        return (
            "https://courses.ut.edu.vn/mod/quiz/attempt.php?attempt=111",
            "<html>Attempt</html>",
        )

    prepared, lease, _, calls, _ = _prepared(
        monkeypatch, _overview("Start attempt"), after_post=respond
    )
    original_export = quiz_entry._export_moodle_session_cookies
    export_count = 0

    def fail_after_first_accepted_entry(client, site, target):
        nonlocal export_count
        export_count += 1
        if export_count == 1:  # The first entry export fails after its POST.
            raise QuizWebSessionError("session_cookie_missing")
        return original_export(client, site, target)

    monkeypatch.setattr(
        quiz_entry, "_export_moodle_session_cookies", fail_after_first_accepted_entry
    )
    try:
        with pytest.raises(QuizEntryError) as error:
            prepared.enter(confirmed=True)
        assert error.value.reason == "session_cookie_missing"
        assert [call[0] for call in calls] == [
            "https://courses.ut.edu.vn/mod/quiz/view.php?id=52",
            "/mod/quiz/startattempt.php",
        ]

        result = prepared.enter(confirmed=True)
        assert result.mode == "resume"
        assert result.launch_url.endswith("attempt=111")
        assert [call[0] for call in calls] == [
            "https://courses.ut.edu.vn/mod/quiz/view.php?id=52",
            "/mod/quiz/startattempt.php",
            "https://courses.ut.edu.vn/mod/quiz/view.php?id=52",
            "/mod/quiz/startattempt.php",
        ]
        assert export_count == 2
    finally:
        _release(prepared, lease)


def test_rejects_non_attempt_redirect_and_clears_result_cookie_copy(monkeypatch):
    prepared, lease, _, calls, _ = _prepared(
        monkeypatch,
        _overview("Start attempt"),
        after_post=lambda _data, _calls, _state: (
            "https://courses.ut.edu.vn/course/view.php?id=4",
            "<html>Course</html>",
        ),
    )
    try:
        with pytest.raises(QuizEntryError) as error:
            prepared.enter(confirmed=True)
        assert error.value.reason == "entry_not_opened"
        assert len(calls) == 2
    finally:
        _release(prepared, lease)


def test_concurrent_entry_is_blocked_and_operation_lease_cleans_up(monkeypatch):
    post_started = threading.Event()
    finish_post = threading.Event()

    def blocked_post(_data, _calls, _overview_state):
        post_started.set()
        assert finish_post.wait(timeout=2)
        return (
            "https://courses.ut.edu.vn/mod/quiz/attempt.php?attempt=92",
            "<html>Attempt</html>",
        )

    prepared, lease, operation_lease, _, _ = _prepared(
        monkeypatch, _overview("Start attempt"), after_post=blocked_post
    )
    target_key = lease.session_key
    result = []
    errors = []

    def enter_once():
        try:
            result.append(prepared.enter(confirmed=True))
        except Exception as error:  # Keep worker-thread failures visible to pytest.
            errors.append(error)

    worker = threading.Thread(target=enter_once)
    worker.start()
    try:
        assert post_started.wait(timeout=2)
        with pytest.raises(QuizEntryError) as error:
            prepared.enter(confirmed=True)
        assert error.value.reason == "entry_in_progress"

        # Simulate the UI releasing its own hold while the POST is running.
        lease.release()
        assert operation_lease.is_active
        assert _begin_quiz_session_login(target_key) is False
        finish_post.set()
        worker.join(timeout=2)
        assert not worker.is_alive()
        assert errors == []
        assert len(result) == 1
        assert result[0].to_native_cookies()[0]["name"] == "MoodleSession"
        assert _begin_quiz_session_login(target_key) is False

        prepared.close()
        assert operation_lease.is_active is False
        with pytest.raises(QuizEntryError) as error:
            result[0].to_native_cookies()
        assert error.value.reason == "session_closed"
        assert _begin_quiz_session_login(target_key) is True
        _end_quiz_session_login(target_key)
    finally:
        finish_post.set()
        worker.join(timeout=2)
        prepared.close()
        lease.release()
