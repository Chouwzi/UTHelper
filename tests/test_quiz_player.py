"""Policy and session coordination tests for embedded Moodle quiz pages."""

import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core.quiz_player import (
    QuizLaunchPolicyError,
    acquire_quiz_session_lease,
    build_quiz_launch_target,
    quiz_session_key,
)


def test_builds_a_canonical_courses_quiz_target_from_cmid():
    target = build_quiz_launch_target(
        "https://courses.ut.edu.vn/mod/quiz/view.php?id=675077&attempt=991",
        "student-42",
    )

    assert target.site_identity == "courses"
    assert target.course_module_id == 675077
    assert target.url == "https://courses.ut.edu.vn/mod/quiz/view.php?id=675077"
    assert len(target.session_key) == 64
    assert "student-42" not in target.session_key
    assert target.secure_window is False


def test_builds_a_canonical_thnn_quiz_target():
    target = build_quiz_launch_target(
        "https://thnn.ut.edu.vn/mod/quiz/view.php?foo=bar&id=93",
        "student-42",
    )

    assert target.site_identity == "thnn"
    assert target.course_module_id == 93
    assert target.url == "https://thnn.ut.edu.vn/mod/quiz/view.php?id=93"


def test_session_keys_are_scoped_to_site_and_account():
    courses = quiz_session_key("courses", "Student-42")

    assert courses == quiz_session_key("courses", "student-42")
    assert courses != quiz_session_key("thnn", "student-42")
    assert courses != quiz_session_key("courses", "student-43")


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("https://courses.ut.edu.vn/mod/scorm/player.php?id=12", "not_quiz_overview"),
        ("https://courses.ut.edu.vn/mod/lesson/view.php?id=12", "not_quiz_overview"),
        ("https://courses.ut.edu.vn/mod/quiz/attempt.php?attempt=12", "not_quiz_overview"),
        ("http://courses.ut.edu.vn/mod/quiz/view.php?id=12", "unsupported_site"),
        ("https://courses.ut.edu.vn:443/mod/quiz/view.php?id=12", "unsupported_site"),
        ("https://evil.example/mod/quiz/view.php?id=12", "unsupported_site"),
        ("https://user@courses.ut.edu.vn/mod/quiz/view.php?id=12", "unsupported_site"),
        ("https://courses.ut.edu.vn/mod/quiz/view.php?id=0", "invalid_course_module_id"),
        ("https://courses.ut.edu.vn/mod/quiz/view.php?id=-12", "invalid_course_module_id"),
        ("https://courses.ut.edu.vn/mod/quiz/view.php?id=12&id=13", "invalid_course_module_id"),
        ("https://courses.ut.edu.vn/mod/quiz/view.php", "invalid_course_module_id"),
    ],
)
def test_rejects_non_quiz_or_unsafe_targets(url, reason):
    with pytest.raises(QuizLaunchPolicyError) as error:
        build_quiz_launch_target(url, "student-42")

    assert error.value.reason == reason


def test_requires_a_non_secret_account_identifier():
    with pytest.raises(QuizLaunchPolicyError) as error:
        build_quiz_launch_target(
            "https://courses.ut.edu.vn/mod/quiz/view.php?id=12", ""
        )

    assert error.value.reason == "missing_account"


def test_explicit_securewindow_is_allowed_and_reported():
    target = build_quiz_launch_target(
        "https://courses.ut.edu.vn/mod/quiz/view.php?id=12",
        "student-42",
        metadata={"browsersecurity": "securewindow"},
    )

    assert target.secure_window is True


def test_explicit_securewindow_access_rule_is_allowed():
    target = build_quiz_launch_target(
        "https://thnn.ut.edu.vn/mod/quiz/view.php?id=12",
        "student-42",
        metadata={"activerulenames": ["quizaccess_securewindow"]},
    )

    assert target.secure_window is True


@pytest.mark.parametrize(
    "metadata",
    [
        {"browsersecurity": "safebrowser"},
        {"activerulenames": ["quizaccess_seb"]},
        {"activerulenames": ["quizaccess_safebrowser"]},
    ],
)
def test_explicit_moodle_safe_exam_browser_rules_block_embedded_launch(metadata):
    with pytest.raises(QuizLaunchPolicyError) as error:
        build_quiz_launch_target(
            "https://courses.ut.edu.vn/mod/quiz/view.php?id=12",
            "student-42",
            metadata=metadata,
        )

    assert error.value.reason == "requires_safe_exam_browser"


def test_unknown_moodle_security_metadata_remains_a_moodle_page_decision():
    target = build_quiz_launch_target(
        "https://thnn.ut.edu.vn/mod/quiz/view.php?id=12",
        "student-42",
        metadata={"custom_access_rule": "institutional rule"},
    )

    assert target.site_identity == "thnn"
    assert target.secure_window is False


@pytest.mark.parametrize(
    ("browser_security", "expected_secure_window", "expected_error"),
    [
        ("securewindow", True, None),
        ("safebrowser", None, "requires_safe_exam_browser"),
    ],
)
def test_catalog_security_metadata_reaches_quiz_cards(
    monkeypatch, browser_security, expected_secure_window, expected_error
):
    from config import settings
    from core.data_orchestrator import DataOrchestrator
    from unittest.mock import Mock

    monkeypatch.setattr(settings, "MOODLE_BASE_URL", "https://courses.ut.edu.vn")
    orchestrator = DataOrchestrator()
    orchestrator._courses_cache = [{"id": 22, "fullname": "Math"}]
    orchestrator.moodle_service = Mock()
    orchestrator.moodle_service.get_calendar_events.return_value = [
        {"modulename": "quiz", "courseid": 22}
    ]
    orchestrator.moodle_service.get_quizzes_by_courses.return_value = [
        {
            "id": 991,
            "coursemodule": 771,
            "course": 22,
            "name": "Quiz",
            "timeclose": 1_900_000_000,
            "browsersecurity": browser_security,
        }
    ]

    result = orchestrator._fetch_calendar_quizzes(
        [22], time_start=1_800_000_000, time_end=2_000_000_000
    )

    assert result[0]["quiz_security"] == {"browsersecurity": browser_security}
    if expected_error:
        with pytest.raises(QuizLaunchPolicyError) as error:
            build_quiz_launch_target(
                result[0]["url"], "student-42", metadata=result[0]["quiz_security"]
            )
        assert error.value.reason == expected_error
    else:
        target = build_quiz_launch_target(
            result[0]["url"], "student-42", metadata=result[0]["quiz_security"]
        )
        assert target.secure_window is expected_secure_window


def test_catalog_security_metadata_merges_onto_action_event_quiz_cards():
    from core.data_orchestrator import DataOrchestrator
    from unittest.mock import Mock

    orchestrator = DataOrchestrator()
    orchestrator._get_userid = lambda: 42
    orchestrator._should_sync_thnn = lambda _now: False
    orchestrator.moodle_service = Mock()
    orchestrator.moodle_service.get_action_events_by_timesort.return_value = {
        "events": [{"id": 1}],
        "moreevents": False,
    }
    orchestrator.moodle_service.get_user_courses.return_value = [{"id": 22}]
    action_card = {
        "url": "https://courses.ut.edu.vn/mod/quiz/view.php?id=771",
        "course_id": 22,
        "site_identity": "courses",
        "type": "quiz",
    }
    orchestrator.moodle_service.ws_events_to_assignments.return_value = [action_card]
    orchestrator._merge_all_assignments = lambda activities, **_kwargs: activities
    orchestrator._fetch_calendar_quizzes = lambda *_args: [
        {
            "url": action_card["url"],
            "quiz_id": 991,
            "quiz_security": {"browsersecurity": "safebrowser"},
        }
    ]

    result = orchestrator._fetch_via_ws_api()

    assert result == [action_card]
    assert action_card["quiz_security"] == {"browsersecurity": "safebrowser"}


def test_quiz_detail_path_retains_explicit_moodle_security_metadata():
    from core.data_orchestrator import DataOrchestrator
    from unittest.mock import Mock

    orchestrator = DataOrchestrator()
    orchestrator.moodle_service = Mock()
    security = {"browsersecurity": "safebrowser"}
    orchestrator.moodle_service.get_assign_details_via_ws.return_value = {
        "quiz_security": security,
        "quiz_info": [],
        "status_data": {},
    }

    result = orchestrator._fetch_detail_via_ws(
        {
            "url": "https://courses.ut.edu.vn/mod/quiz/view.php?id=77",
            "type": "quiz",
            "course_id": 12,
            "details": {},
        }
    )

    assert result["details"]["quiz_security"] == security


def test_quiz_web_service_detail_extracts_only_available_security_fields():
    from core.ws_functions import clear_all_caches, get_assign_details_via_ws

    quiz_security = {
        "browsersecurity": "securewindow",
        "activerulenames": ["quizaccess_securewindow"],
    }

    def call_api(function, **_params):
        if function == "mod_quiz_get_quizzes_by_courses":
            return {
                "quizzes": [
                    {
                        "id": 7,
                        "coursemodule": 77,
                        **quiz_security,
                    }
                ]
            }
        if function == "mod_quiz_get_user_attempts":
            return {"attempts": []}
        if function == "core_enrol_get_users_courses":
            return []
        return None

    clear_all_caches()
    details = get_assign_details_via_ws(
        call_api, cmid=77, course_id=12, modulename="quiz"
    )

    assert details["quiz_security"] == quiz_security


def test_session_lease_blocks_only_the_matching_site_and_account():
    thnn_target = build_quiz_launch_target(
        "https://thnn.ut.edu.vn/mod/quiz/view.php?id=12", "student-42"
    )
    courses_key = quiz_session_key("courses", "student-42")
    other_account_key = quiz_session_key("thnn", "student-43")

    lease = acquire_quiz_session_lease(thnn_target.session_key)
    try:
        from core.quiz_player import _begin_quiz_session_login, _end_quiz_session_login

        assert _begin_quiz_session_login(thnn_target.session_key) is False
        assert _begin_quiz_session_login(courses_key) is True
        _end_quiz_session_login(courses_key)
        assert _begin_quiz_session_login(other_account_key) is True
        _end_quiz_session_login(other_account_key)
    finally:
        lease.release()

    assert _begin_quiz_session_login(thnn_target.session_key) is True
    _end_quiz_session_login(thnn_target.session_key)


def test_multiple_leases_keep_the_session_held_until_the_last_release():
    target = build_quiz_launch_target(
        "https://thnn.ut.edu.vn/mod/quiz/view.php?id=12", "student-42"
    )
    first = acquire_quiz_session_lease(target.session_key)
    second = acquire_quiz_session_lease(target.session_key)
    try:
        first.release()
        from core.quiz_player import _begin_quiz_session_login

        assert _begin_quiz_session_login(target.session_key) is False
    finally:
        second.release()

    from core.quiz_player import _begin_quiz_session_login, _end_quiz_session_login

    assert _begin_quiz_session_login(target.session_key) is True
    _end_quiz_session_login(target.session_key)


def test_lease_waits_for_a_login_that_started_before_player_open():
    target = build_quiz_launch_target(
        "https://thnn.ut.edu.vn/mod/quiz/view.php?id=12", "student-42"
    )
    from core.quiz_player import _begin_quiz_session_login, _end_quiz_session_login

    assert _begin_quiz_session_login(target.session_key) is True
    lease = acquire_quiz_session_lease(target.session_key)
    try:
        assert lease.is_ready is False
        releaser = threading.Thread(
            target=_end_quiz_session_login, args=(target.session_key,)
        )
        releaser.start()
        assert lease.wait_until_ready(timeout=1) is True
        releaser.join(timeout=1)
        assert not releaser.is_alive()
    finally:
        lease.release()


def test_moodle_client_defers_cookie_clearing_login_during_player_lease(monkeypatch):
    from core.client import MoodleClient
    from config import settings

    monkeypatch.setattr(settings, "UTH_USERNAME", "student-42")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "example-password")
    target = build_quiz_launch_target(
        "https://thnn.ut.edu.vn/mod/quiz/view.php?id=12", "student-42"
    )
    client = MoodleClient("https://thnn.ut.edu.vn")
    client._sesskey = "preserved-sesskey"
    lease = acquire_quiz_session_lease(target.session_key)
    try:
        from unittest.mock import Mock

        client._open_html = Mock()
        assert client.login_session(force=True) is False
        assert client._last_login_error == "quiz_session_active"
        assert client._sesskey == "preserved-sesskey"
        client._open_html.assert_not_called()
    finally:
        lease.release()

    from unittest.mock import Mock

    client._open_html = Mock(
        side_effect=[
            ("https://thnn.ut.edu.vn/login/index.php", '<input name="logintoken" value="login-token">'),
            ("https://thnn.ut.edu.vn/my/", ""),
            ("https://thnn.ut.edu.vn/my/", '"sesskey":"freshsesskey"'),
        ]
    )
    assert client.login_session(force=True) is True
    assert client._sesskey == "freshsesskey"


def test_thnn_player_lease_keeps_the_normal_courses_refresh_and_cached_thnn_data(
    monkeypatch,
):
    from config import settings
    from core.data_orchestrator import DataOrchestrator
    from unittest.mock import Mock

    monkeypatch.setattr(settings, "UTH_USERNAME", "student-42")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "example-password")
    orchestrator = DataOrchestrator()
    target = build_quiz_launch_target(
        "https://thnn.ut.edu.vn/mod/quiz/view.php?id=12", "student-42"
    )
    cached_thnn_activity = {
        "id": "thnn-cached",
        "site_identity": "thnn",
        "type": "quiz",
    }
    courses_activity = {
        "id": "courses-current",
        "site_identity": "courses",
        "type": "assignment",
    }
    orchestrator.thnn_client._sesskey = ""
    orchestrator._thnn_activities_cache = [cached_thnn_activity]
    orchestrator._last_thnn_fetch_ts = 1234.0
    orchestrator._get_userid = lambda: 42
    orchestrator._should_sync_thnn = lambda _now: True
    orchestrator._merge_all_assignments = lambda activities, **_kwargs: activities
    orchestrator._fetch_calendar_quizzes = lambda *_args: []
    orchestrator.moodle_service = Mock()
    orchestrator.moodle_service.get_action_events_by_timesort.return_value = {
        "events": [{"id": 1}],
        "moreevents": False,
    }
    orchestrator.moodle_service.get_user_courses.return_value = []
    orchestrator.moodle_service.ws_events_to_assignments.return_value = [
        courses_activity
    ]
    orchestrator.thnn_client._open_html = lambda *_args, **_kwargs: pytest.fail(
        "THNN login should be deferred while the quiz is open"
    )

    lease = acquire_quiz_session_lease(target.session_key)
    try:
        activities = orchestrator.get_latest_activities()
    finally:
        lease.release()

    assert activities == [courses_activity, cached_thnn_activity]
    assert orchestrator._last_thnn_fetch_ts == 1234.0
