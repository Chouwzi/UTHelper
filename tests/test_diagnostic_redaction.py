from datetime import datetime, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from diagnostics.models import CrashConsent, DiagnosticReport


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
