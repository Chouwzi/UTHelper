from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from diagnostics.models import AppPhase, CrashConsent, DiagnosticContext, DiagnosticReport
from diagnostics.redaction import build_report, sanitize_log_text


SYNTHETIC_SECRETS = (
    "student@example.invalid",
    "sesskey=0123456789abcdef",
    "MoodleSession=synthetic-session",
    "Bearer eyJhbGciOiJIUzI1NiJ9.synthetic.signature",
    "https://courses.example.invalid/mod/assign/view.php?id=123&token=secret",
    r"C:\\Users\\Synthetic User\\Documents\\Private assignment.pdf",
    "/home/synthetic/private/assignment.txt",
    "Advanced Calculus Final Assignment",
)


def valid_report_dict() -> dict[str, object]:
    return {
        "schema_version": 1,
        "event_id": UUID("00112233-4455-6677-8899-aabbccddeeff"),
        "fingerprint": "a" * 64,
        "occurred_at": datetime(2026, 8, 4, 3, tzinfo=timezone.utc),
        "app_version": "2.2.0",
        "release_channel": "stable",
        "install_type": "msi",
        "os_family": "Windows",
        "os_version": "10",
        "architecture": "AMD64",
        "python_version": "3.11",
        "flet_version": "0.86.5",
        "flutter_version": None,
        "exception_type": "RuntimeError",
        "frames": (),
        "phase": "gui",
        "window_state": "foreground",
        "unclean_previous_exit": False,
    }


@pytest.fixture
def diagnostic_context(tmp_path: Path) -> DiagnosticContext:
    return DiagnosticContext(
        source_root=tmp_path / "application" / "src",
        app_version="2.2.0",
        release_channel="stable",
        install_type="msi",
        os_family="Windows",
        os_version="10",
        architecture="AMD64",
        python_version="3.11",
        flet_version="0.86.5",
        phase=AppPhase.GUI,
        window_state="foreground",
    )


def _captured_exception(
    message: str,
    *,
    filename: Path | None = None,
    module_name: str = "diagnostic_test_module",
) -> BaseException:
    namespace = {"__name__": module_name}
    source = "def trigger():\n    raise RuntimeError(message)\ntrigger()\n"
    namespace["message"] = message
    try:
        exec(compile(source, str(filename or __file__), "exec"), namespace)
    except RuntimeError as exc:
        return exc
    raise AssertionError("synthetic exception was not raised")


def _serialized_report(exc: BaseException, context: DiagnosticContext) -> str:
    return build_report(exc, context).model_dump_json()


def test_report_rejects_unknown_or_forbidden_fields():
    with pytest.raises(ValidationError):
        DiagnosticReport.model_validate(
            {
                **valid_report_dict(),
                "username": "student123",
            }
        )


def test_consent_is_tri_state():
    assert {item.value for item in CrashConsent} == {
        "not_asked",
        "enabled",
        "disabled",
    }


@pytest.mark.parametrize(
    "secret",
    SYNTHETIC_SECRETS,
    ids=[f"secret_case_{index}" for index in range(len(SYNTHETIC_SECRETS))],
)
def test_report_bytes_never_contain_exception_message_secret(
    secret: str,
    diagnostic_context: DiagnosticContext,
):
    payload = _serialized_report(
        _captured_exception(f"failed: {secret}"),
        diagnostic_context,
    )

    if secret in payload:
        pytest.fail("serialized report leaked a synthetic exception secret")
    assert "failed:" not in payload
    assert set(DiagnosticReport.model_validate_json(payload).model_dump()) == set(
        valid_report_dict()
    )


def test_source_frames_are_relative_and_external_frames_are_basename_only(
    diagnostic_context: DiagnosticContext,
):
    source_exception = _captured_exception(
        "safe",
        filename=diagnostic_context.source_root / "gui" / "controller.py",
    )
    external_exception = _captured_exception(
        "safe",
        filename=Path(r"C:\Users\Synthetic User\Documents\external_module.py"),
    )

    source_report = build_report(source_exception, diagnostic_context)
    external_report = build_report(external_exception, diagnostic_context)

    assert source_report.frames[-1].relative_path == "gui/controller.py"
    assert external_report.frames[-1].relative_path == "external_module.py"
    assert all("Users" not in frame.relative_path for frame in external_report.frames)
    assert all("\\" not in frame.relative_path for frame in external_report.frames)


def test_frame_fields_use_only_bounded_allow_list_characters(
    diagnostic_context: DiagnosticContext,
):
    exc = _captured_exception(
        "safe",
        filename=diagnostic_context.source_root / ("nested-" + "x" * 260) / "worker.py",
        module_name="module.student@example.invalid/unsafe",
    )

    report = build_report(exc, diagnostic_context)

    assert report.frames
    for frame in report.frames:
        assert len(frame.module) <= 128
        assert len(frame.function) <= 128
        assert len(frame.relative_path) <= 240
        assert re.fullmatch(r"[A-Za-z0-9_.-]+", frame.module)
        assert re.fullmatch(r"[A-Za-z0-9_.<>-]+", frame.function)
        assert re.fullmatch(r"[A-Za-z0-9_./-]+", frame.relative_path)


def test_report_keeps_only_the_last_forty_frames(
    diagnostic_context: DiagnosticContext,
):
    def recurse(remaining: int) -> None:
        if remaining:
            recurse(remaining - 1)
        raise RuntimeError("safe")

    try:
        recurse(55)
    except RuntimeError as exc:
        report = build_report(exc, diagnostic_context)

    assert len(report.frames) == 40
    assert report.frames[-1].function == "recurse"


def test_exception_type_is_normalized_to_a_bounded_identifier(
    diagnostic_context: DiagnosticContext,
):
    unsafe_type = type("Student Email@example.invalid/" + "x" * 200, (Exception,), {})

    report = build_report(unsafe_type("private message"), diagnostic_context)

    assert len(report.exception_type) <= 128
    assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", report.exception_type)
    assert "@" not in report.exception_type
    assert "/" not in report.exception_type


def test_fingerprint_excludes_event_id_time_and_exception_messages(
    diagnostic_context: DiagnosticContext,
):
    first = _captured_exception("first private message")
    second = _captured_exception("second unrelated private message")
    first_report = build_report(
        first,
        diagnostic_context,
        occurred_at=datetime(2026, 8, 4, 3, tzinfo=timezone.utc),
    )
    second_report = build_report(
        second,
        diagnostic_context,
        occurred_at=datetime(2026, 8, 5, 3, tzinfo=timezone.utc),
    )

    assert first_report.event_id != second_report.event_id
    assert first_report.occurred_at != second_report.occurred_at
    assert first_report.fingerprint == second_report.fingerprint


def test_fingerprint_changes_with_phase_or_normalized_call_site(
    diagnostic_context: DiagnosticContext,
):
    exc = _captured_exception("safe")
    other_phase = diagnostic_context.model_copy(update={"phase": AppPhase.UPDATE})
    other_site = _captured_exception(
        "safe",
        filename=diagnostic_context.source_root / "update" / "installer.py",
    )

    baseline = build_report(exc, diagnostic_context)

    assert build_report(exc, other_phase).fingerprint != baseline.fingerprint
    assert build_report(other_site, diagnostic_context).fingerprint != baseline.fingerprint


def test_exception_chain_messages_and_tracebacks_are_not_serialized(
    diagnostic_context: DiagnosticContext,
):
    try:
        try:
            raise ValueError(SYNTHETIC_SECRETS[0])
        except ValueError as cause:
            raise RuntimeError(SYNTHETIC_SECRETS[1]) from cause
    except RuntimeError as exc:
        report = build_report(exc, diagnostic_context)

    payload = report.model_dump_json()
    for secret in SYNTHETIC_SECRETS[:2]:
        if secret in payload:
            pytest.fail("serialized report leaked a synthetic exception-chain secret")
    assert report.exception_type == "RuntimeError"
    assert 0 < len(report.frames) <= 40


@pytest.mark.parametrize(
    ("raw", "forbidden"),
    [
        ("email student@example.invalid", "student@example.invalid"),
        ("password=hunter2", "hunter2"),
        ("sesskey: 0123456789abcdef", "0123456789abcdef"),
        ("Authorization: Bearer synthetic.header.payload", "synthetic.header.payload"),
        ("GET https://courses.example.invalid/path?token=secret", "courses.example.invalid"),
        (r"open C:\Users\Synthetic User\private\file.txt", "Synthetic User"),
        ("open /home/synthetic/private/file.txt", "/home/synthetic"),
    ],
    ids=[
        "email",
        "password",
        "sesskey",
        "authorization",
        "url",
        "windows_home",
        "unix_home",
    ],
)
def test_sanitize_log_text_removes_sensitive_patterns(raw: str, forbidden: str):
    sanitized = sanitize_log_text(raw)

    if forbidden in sanitized:
        pytest.fail("sanitized log retained a synthetic sensitive value")
    assert "[redacted]" in sanitized


def test_sanitize_log_text_is_total_removes_controls_and_bounds_output():
    class BrokenString:
        def __str__(self) -> str:
            raise RuntimeError("must not escape")

    assert sanitize_log_text(BrokenString()) == "[unprintable]"
    sanitized = sanitize_log_text("prefix\x00\r\n" + "x" * 5000)
    assert "\x00" not in sanitized
    assert "\r" not in sanitized
    assert "\n" not in sanitized
    assert len(sanitized) <= 4096


@pytest.mark.parametrize(
    ("boundary_fragment", "continuation"),
    [
        ("crossb", "oundary@example.invalid"),
        ("Bear", "er synthetic.header.payload"),
        ("htt", "ps://courses.example.invalid/private?token=secret"),
        ("passw", "ord=synthetic-private-value"),
        ("C:\\Use", "rs\\Synthetic\\private\\file.txt"),
        ("/home/", "synthetic/private/file.txt"),
    ],
    ids=[
        "email_crosses_cutoff",
        "bearer_crosses_cutoff",
        "url_crosses_cutoff",
        "key_value_crosses_cutoff",
        "windows_user_path_crosses_cutoff",
        "unix_user_path_crosses_cutoff",
    ],
)
def test_sanitize_log_text_drops_sensitive_fragment_crossing_output_cutoff(
    boundary_fragment: str,
    continuation: str,
):
    raw = "." * (4096 - len(boundary_fragment))
    raw += boundary_fragment + continuation

    sanitized = sanitize_log_text(raw)

    if boundary_fragment in sanitized:
        pytest.fail("sanitized log retained a synthetic cross-boundary fragment")
    assert len(sanitized) <= 4096


def test_sanitize_log_text_bounds_work_for_oversized_cross_boundary_value():
    raw = "." * 4080 + "crossboundary" + "x" * 1_000_000 + "@example.invalid"

    sanitized = sanitize_log_text(raw)

    if "crossboundary" in sanitized:
        pytest.fail("sanitized log retained an oversized cross-boundary fragment")
    assert len(sanitized) <= 4096


@pytest.mark.parametrize(
    ("sensitive_value", "forbidden_fragment"),
    [
        (
            "boundary_value_marker" + "x" * 100 + "@example.invalid",
            "boundary_value_marker",
        ),
        ("Bearer boundary_value_marker" + "x" * 180, "boundary_value_marker"),
        (
            "https://example.invalid/boundary_value_marker/" + "x" * 180,
            "boundary_value_marker",
        ),
        (
            'password="boundary_value_marker private_tail_marker '
            + "x" * 1200,
            "private_tail_marker",
        ),
        (
            r"C:\Users\Synthetic User\boundary_value_marker" + "x" * 180,
            "boundary_value_marker",
        ),
        (
            "/home/synthetic user/private_tail_marker/" + "x" * 180,
            "private_tail_marker",
        ),
    ],
    ids=[
        "email_value_crosses_cutoff",
        "bearer_value_crosses_cutoff",
        "url_value_crosses_cutoff",
        "key_value_body_crosses_cutoff",
        "windows_user_path_value_crosses_cutoff",
        "unix_user_path_value_crosses_cutoff",
    ],
)
def test_sanitize_log_text_redacts_variable_length_family_across_cutoff(
    sensitive_value: str,
    forbidden_fragment: str,
):
    raw = "." * 4000 + sensitive_value

    sanitized = sanitize_log_text(raw)

    if forbidden_fragment in sanitized:
        pytest.fail("sanitized log retained a synthetic variable-length value")
    assert len(sanitized) <= 4096


def test_build_report_normalizes_naive_time_to_utc(
    diagnostic_context: DiagnosticContext,
):
    report = build_report(
        RuntimeError("safe"),
        diagnostic_context,
        occurred_at=datetime(2026, 8, 4, 3),
    )

    assert report.occurred_at.utcoffset() == timedelta(0)
