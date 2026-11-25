"""Tests for core.grade_monitor — GradeMonitor change detection.

Tests verify:
- GradeChange dataclass creation
- Snapshot load/save persistence
- Change detection: new grades, changed grades, no changes
- First-run behavior (no snapshot = no alerts)
- Edge cases: empty API response, API errors

NOTE: ws_functions is imported *inside* check_for_changes via
`from core import ws_functions`. Per Python mocking best practices
(https://adamj.eu/tech/2020/12/09/how-to-mock-an-import/),
we patch the function at the SOURCE module: `core.ws_functions.<func>`.
"""
import json
import os
import sys
import tempfile
import pytest
from unittest.mock import Mock, patch, MagicMock

# Ensure src/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.grade_monitor import GradeMonitor, GradeChange


class TestGradeChange:
    """GradeChange dataclass."""

    def test_create_grade_change(self):
        gc = GradeChange(
            course_name="Lập trình Web",
            item_name="CK",
            old_grade="7.5",
            new_grade="8.0",
            timestamp="2026-06-23T10:00:00",
        )
        assert gc.course_name == "Lập trình Web"
        assert gc.item_name == "CK"
        assert gc.old_grade == "7.5"
        assert gc.new_grade == "8.0"

    def test_create_with_none_old_grade(self):
        gc = GradeChange(
            course_name="Math",
            item_name="Final",
            old_grade=None,
            new_grade="9.0",
            timestamp="2026-01-01T00:00:00",
        )
        assert gc.old_grade is None
        assert gc.new_grade == "9.0"


class TestGradeMonitorSnapshot:
    """GradeMonitor snapshot load/save."""

    def test_load_empty_when_no_file(self, tmp_path):
        path = str(tmp_path / "nonexistent.json")
        monitor = GradeMonitor(snapshot_path=path)
        assert monitor._snapshot == {}

    def test_load_existing_snapshot(self, tmp_path):
        path = tmp_path / "grades.json"
        path.write_text(json.dumps({"101": {"_overall": "8.0"}}), encoding="utf-8")
        monitor = GradeMonitor(snapshot_path=str(path))
        assert monitor._snapshot == {"101": {"_overall": "8.0"}}

    def test_save_snapshot(self, tmp_path):
        path = str(tmp_path / "grades.json")
        monitor = GradeMonitor(snapshot_path=path)
        monitor._snapshot = {"42": {"_overall": "7.0", "CK": "6.5"}}
        monitor._save_snapshot()
        with open(path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        assert saved == {"42": {"_overall": "7.0", "CK": "6.5"}}

    def test_load_corrupted_file_returns_empty(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not valid json!!!", encoding="utf-8")
        monitor = GradeMonitor(snapshot_path=str(path))
        assert monitor._snapshot == {}


class TestGradeMonitorCheckForChanges:
    """GradeMonitor.check_for_changes() logic.
    
    Patches `core.ws_functions` at the source module level
    since grade_monitor imports it inside the method body.
    """

    def _make_monitor(self, tmp_path, initial_snapshot=None):
        path = str(tmp_path / "snapshot.json")
        if initial_snapshot:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(initial_snapshot, f)
        return GradeMonitor(snapshot_path=path)

    @patch("core.ws_functions.get_grade_items", return_value=[])
    @patch("core.ws_functions.get_course_grades")
    def test_first_run_no_changes(self, mock_grades, mock_items, tmp_path):
        """First run with empty snapshot should NOT detect changes (baseline)."""
        monitor = self._make_monitor(tmp_path)
        mock_grades.return_value = [
            {"courseid": 1, "coursename": "Web", "grade": "8.0"}
        ]
        changes = monitor.check_for_changes(Mock(), userid=123)
        assert changes == []  # first time = baseline, no alert

    @patch("core.ws_functions.get_grade_items", return_value=[])
    @patch("core.ws_functions.get_course_grades")
    def test_grade_changed_detected(self, mock_grades, mock_items, tmp_path):
        """Grade changing from 7.0 to 8.0 should be detected."""
        monitor = self._make_monitor(tmp_path, {"1": {"_overall": "7.0"}})
        mock_grades.return_value = [
            {"courseid": 1, "coursename": "Web", "grade": "8.0"}
        ]
        changes = monitor.check_for_changes(Mock(), userid=123)
        assert len(changes) == 1
        assert changes[0].course_name == "Web"
        assert changes[0].old_grade == "7.0"
        assert changes[0].new_grade == "8.0"

    @patch("core.ws_functions.get_grade_items", return_value=[])
    @patch("core.ws_functions.get_course_grades")
    def test_no_change_no_detection(self, mock_grades, mock_items, tmp_path):
        """Same grade = no detection."""
        monitor = self._make_monitor(tmp_path, {"1": {"_overall": "8.0"}})
        mock_grades.return_value = [
            {"courseid": 1, "coursename": "Web", "grade": "8.0"}
        ]
        changes = monitor.check_for_changes(Mock(), userid=123)
        assert changes == []

    @patch("core.ws_functions.get_grade_items", return_value=[])
    @patch("core.ws_functions.get_course_grades")
    def test_empty_api_response_returns_empty(self, mock_grades, mock_items, tmp_path):
        """API returning empty courses_grades should return empty list."""
        monitor = self._make_monitor(tmp_path, {"1": {"_overall": "7.0"}})
        mock_grades.return_value = []
        changes = monitor.check_for_changes(Mock(), userid=123)
        assert changes == []

    @patch("core.ws_functions.get_grade_items", return_value=[])
    @patch("core.ws_functions.get_course_grades")
    def test_none_api_response_returns_empty(self, mock_grades, mock_items, tmp_path):
        """API returning None should return empty list."""
        monitor = self._make_monitor(tmp_path)
        mock_grades.return_value = None
        changes = monitor.check_for_changes(Mock(), userid=123)
        assert changes == []

    @patch("core.ws_functions.get_grade_items")
    @patch("core.ws_functions.get_course_grades")
    def test_grade_item_change_detected(self, mock_grades, mock_items, tmp_path):
        """Per-item grade change detection (requires overall grade change to trigger)."""
        # PERF-OPT: grade_items only fetched when overall grade changes
        monitor = self._make_monitor(tmp_path, {"1": {"_overall": "7.0", "CK": "6.0"}})
        mock_grades.return_value = [
            {"courseid": 1, "coursename": "Math", "grade": "7.5"}  # overall changed 7.0→7.5
        ]
        mock_items.return_value = [
            {"itemname": "CK", "gradeformatted": "8.0"},
        ]
        changes = monitor.check_for_changes(Mock(), userid=123)
        # Should detect both overall change and item change
        assert len(changes) >= 1
        item_changes = [c for c in changes if c.item_name == "CK"]
        assert len(item_changes) == 1
        assert item_changes[0].old_grade == "6.0"
        assert item_changes[0].new_grade == "8.0"

    @patch("core.ws_functions.get_grade_items", return_value=[])
    @patch("core.ws_functions.get_course_grades")
    def test_skip_dash_grades(self, mock_grades, mock_items, tmp_path):
        """Grades that are '-' should be skipped."""
        monitor = self._make_monitor(tmp_path, {"1": {"_overall": "7.0"}})
        mock_grades.return_value = [
            {"courseid": 1, "coursename": "Web", "grade": "-"}
        ]
        changes = monitor.check_for_changes(Mock(), userid=123)
        assert changes == []

    @patch("core.ws_functions.get_course_grades")
    def test_api_exception_returns_empty(self, mock_grades, tmp_path):
        """API throwing exception should be handled gracefully."""
        monitor = self._make_monitor(tmp_path)
        mock_grades.side_effect = Exception("Network error")
        changes = monitor.check_for_changes(Mock(), userid=123)
        assert changes == []

    @patch("core.ws_functions.get_grade_items", return_value=[])
    @patch("core.ws_functions.get_course_grades")
    def test_snapshot_persisted_after_check(self, mock_grades, mock_items, tmp_path):
        """After check_for_changes, snapshot should be saved to disk."""
        path = str(tmp_path / "snapshot.json")
        monitor = GradeMonitor(snapshot_path=path)
        mock_grades.return_value = [
            {"courseid": 42, "coursename": "AI", "grade": "9.0"}
        ]
        monitor.check_for_changes(Mock(), userid=1)
        with open(path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        assert saved["42"]["_overall"] == "9.0"


def test_grade_monitor_namespaces_by_account_and_reset_detaches(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "_USER_DATA_DIR", tmp_path)
    monitor = GradeMonitor(namespace="https://courses.ut.edu.vn|studentA")

    svc_a = Mock()
    svc_a.get_course_grades.return_value = [{"courseid": 10, "coursename": "OS", "grade": "9.50"}]
    svc_a.get_grade_items.return_value = [{"itemname": "GK", "gradeformatted": "9.00"}]
    assert monitor.check_for_changes(svc_a, userid=1) == []

    # Switch to studentB on same shared courseid=10 -> must NOT emit studentA's 9.50 as old_grade
    monitor.set_namespace("https://courses.ut.edu.vn|studentB")
    svc_b = Mock()
    svc_b.get_course_grades.return_value = [{"courseid": 10, "coursename": "OS", "grade": "6.00"}]
    svc_b.get_grade_items.return_value = [{"itemname": "GK", "gradeformatted": "6.00"}]
    assert monitor.check_for_changes(svc_b, userid=2) == []

    # Logout reset clears memory without deleting studentA's persisted baseline
    monitor.reset()
    assert monitor._snapshot == {}
    monitor.set_namespace("https://courses.ut.edu.vn|studentA")
    assert monitor._snapshot["10"]["_overall"] == "9.50"


def test_grade_monitor_baselines_grade_items_on_initial_snapshot_and_detects_first_component_change(tmp_path):
    monitor = GradeMonitor(snapshot_path=str(tmp_path / "grades.json"))
    svc = Mock()

    # Run 1: Initial baseline captures GK=7.0 and marks course items as baselined (CK is '-')
    svc.get_course_grades.return_value = [{"courseid": 5, "coursename": "DB", "grade": "7.0"}]
    svc.get_grade_items.return_value = [
        {"itemname": "GK", "gradeformatted": "7.0"},
        {"itemname": "CK", "gradeformatted": "-"},
    ]
    assert monitor.check_for_changes(svc, userid=10) == []

    # Run 2: GK updates 7.0 -> 8.0 and CK is newly graded '-' -> 9.0
    svc.get_course_grades.return_value = [{"courseid": 5, "coursename": "DB", "grade": "8.6"}]
    svc.get_grade_items.return_value = [
        {"itemname": "GK", "gradeformatted": "8.0"},
        {"itemname": "CK", "gradeformatted": "9.0"},
    ]
    changes = monitor.check_for_changes(svc, userid=10)
    by_item = {c.item_name: c for c in changes}

    assert by_item["Tổng kết"].old_grade == "7.0"
    assert by_item["Tổng kết"].new_grade == "8.6"
    assert by_item["GK"].old_grade == "7.0"
    assert by_item["GK"].new_grade == "8.0"
    assert by_item["CK"].old_grade is None
    assert by_item["CK"].new_grade == "9.0"


def test_reset_and_set_namespace_do_not_block_during_network_io_and_discard_stale_grades(
    tmp_path, monkeypatch
):
    import threading
    import time
    import config

    monkeypatch.setattr(config, "_USER_DATA_DIR", tmp_path)
    monitor = GradeMonitor(namespace="https://courses.ut.edu.vn|studentA")
    # Seed baseline for studentA so overall change would normally trigger alerts & item fetch
    svc_seed = Mock()
    svc_seed.get_course_grades.return_value = [{"courseid": 10, "coursename": "OS", "grade": "7.0"}]
    svc_seed.get_grade_items.return_value = [{"itemname": "GK", "gradeformatted": "7.0"}]
    assert monitor.check_for_changes(svc_seed, userid=1) == []

    started = threading.Event()
    release = threading.Event()
    svc_slow = Mock()

    def slow_course_grades(_userid):
        started.set()
        assert release.wait(2.0)
        return [{"courseid": 10, "coursename": "OS", "grade": "9.5"}]

    svc_slow.get_course_grades.side_effect = slow_course_grades
    svc_slow.get_grade_items.return_value = [{"itemname": "GK", "gradeformatted": "9.5"}]

    out: list = []
    worker = threading.Thread(target=lambda: out.append(monitor.check_for_changes(svc_slow, userid=1)))
    worker.start()
    assert started.wait(1.0)

    # Account teardown + switch to studentB must return immediately without blocking on HTTP
    t0 = time.perf_counter()
    monitor.reset()
    monitor.set_namespace("https://courses.ut.edu.vn|studentB")
    elapsed = time.perf_counter() - t0

    release.set()
    worker.join(timeout=2.0)
    assert not worker.is_alive()

    assert elapsed < 0.15
    assert out == [[]]
    assert monitor._snapshot == {}
    assert not os.path.exists(monitor._snapshot_path)
    svc_slow.get_grade_items.assert_not_called()
