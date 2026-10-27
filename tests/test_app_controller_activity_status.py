import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from config import settings
from gui.app_controller import AppController


@pytest.mark.parametrize("status", ["submitted", "Đã nộp", "Submitted for grading"])
def test_settings_filter_hides_all_submitted_status_variants(monkeypatch, status):
    controller = AppController.__new__(AppController)
    monkeypatch.setattr(settings, "INCLUDE_SUBMITTED", False)
    monkeypatch.setattr(settings, "INCLUDE_GRADED", True)

    assert controller._apply_settings_filter(
        [{"submission_status": status, "details": {}}]
    ) == []


@pytest.mark.parametrize("status", ["graded", "Đã chấm", "Đã chấm điểm"])
def test_settings_filter_hides_all_graded_status_variants(monkeypatch, status):
    controller = AppController.__new__(AppController)
    monkeypatch.setattr(settings, "INCLUDE_SUBMITTED", True)
    monkeypatch.setattr(settings, "INCLUDE_GRADED", False)

    assert controller._apply_settings_filter(
        [{"submission_status": status, "details": {}}]
    ) == []


def test_settings_filter_does_not_treat_not_submitted_as_submitted(monkeypatch):
    controller = AppController.__new__(AppController)
    monkeypatch.setattr(settings, "INCLUDE_SUBMITTED", False)
    monkeypatch.setattr(settings, "INCLUDE_GRADED", False)
    activity = {
        "submission_status": "unknown",
        "details": {"status_data": {"Submission status": "Not submitted"}},
    }

    assert controller._apply_settings_filter([activity]) == [activity]
