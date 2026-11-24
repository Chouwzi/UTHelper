from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from diagnostics.models import (
    AppPhase,
    CrashConsent,
    DiagnosticAction,
    DiagnosticBreadcrumb,
    DiagnosticContext,
    DiagnosticOutcome,
    DiagnosticScreen,
    DiagnosticSource,
)
from diagnostics.runtime import DiagnosticRuntime
from diagnostics.spool import DiagnosticSpool


class _NoopDelivery:
    def flush_once(self, consent: CrashConsent) -> object:
        return consent


def test_diagnostic_screen_supports_courses_course_grades_and_quiz(tmp_path: Path):
    assert DiagnosticScreen("courses") is DiagnosticScreen.COURSES
    assert DiagnosticScreen("course_grades") is DiagnosticScreen.COURSE_GRADES
    assert DiagnosticScreen("quiz") is DiagnosticScreen.QUIZ

    for value in ("courses", "course_grades", "quiz"):
        crumb = DiagnosticBreadcrumb(
            occurred_at=datetime.now(UTC),
            action=DiagnosticAction.NAVIGATION,
            outcome=DiagnosticOutcome.SUCCEEDED,
            source=DiagnosticSource.USER,
            screen=DiagnosticScreen(value),
        )
        assert crumb.screen.value == value

    def _ctx(phase: AppPhase, unclean: bool) -> DiagnosticContext:
        return DiagnosticContext(
            source_root=Path(__file__).resolve().parents[1] / "src",
            app_version="2.5.1",
            release_channel="exe_lts",
            install_type="unpackaged",
            os_family="windows",
            os_version="11",
            architecture="x86_64",
            python_version="3.14",
            flet_version="0.86.5",
            phase=phase,
            window_state="foreground",
            unclean_previous_exit=unclean,
        )

    runtime = DiagnosticRuntime(
        data_dir=tmp_path,
        spool=DiagnosticSpool(tmp_path / "diagnostics" / "spool"),
        delivery=_NoopDelivery(),
        context_provider=_ctx,
        consent_provider=lambda: CrashConsent.DISABLED,
        evidence_reader=lambda *_a, **_kw: (),
    )
    runtime._started = True
    try:
        for screen_value in ("courses", "course_grades", "quiz"):
            runtime.set_diagnostic_screen(screen_value)
            assert runtime._screen is DiagnosticScreen(screen_value)
            runtime.record_breadcrumb(
                DiagnosticAction.NAVIGATION,
                DiagnosticOutcome.SUCCEEDED,
                DiagnosticSource.USER,
                screen=screen_value,
            )
        assert [b.screen.value for b in runtime._breadcrumbs] == [
            "courses",
            "course_grades",
            "quiz",
        ]
    finally:
        runtime._started = False
