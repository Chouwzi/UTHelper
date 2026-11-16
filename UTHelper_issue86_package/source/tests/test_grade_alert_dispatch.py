"""Tests for notifiers.manager — dispatch_grade_alert and DND integration.

Tests verify:
- dispatch_grade_alert sends to all registered notifiers
- dispatch_grade_alert respects DND mode
- dispatch_grade_alert handles empty list (no-op)
- dispatch_grade_alert handles notifier errors gracefully
"""
import asyncio
import os
import sys
import pytest
from unittest.mock import Mock, patch, MagicMock
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


@dataclass
class _FakeGradeChange:
    """Minimal GradeChange for testing."""
    course_name: str
    item_name: str
    old_grade: str
    new_grade: str


class TestDispatchGradeAlert:
    """NotificationManager.dispatch_grade_alert() tests."""

    def _make_manager(self, dnd_active=False):
        """Create a minimal NotificationManager with mocked internals."""
        with patch("notifiers.manager.NotificationManager.__init__", return_value=None):
            from notifiers.manager import NotificationManager
            mgr = NotificationManager.__new__(NotificationManager)
            mgr.notifiers = []
            mgr._is_in_dnd = Mock(return_value=dnd_active)
            return mgr

    def test_empty_changes_is_noop(self):
        mgr = self._make_manager()
        mock_notifier = Mock()
        mgr.notifiers.append(mock_notifier)
        asyncio.run(mgr.dispatch_grade_alert([]))
        mock_notifier.notify.assert_not_called()

    def test_sends_to_all_notifiers(self):
        mgr = self._make_manager()
        n1 = Mock()
        n2 = Mock()
        mgr.notifiers = [n1, n2]
        changes = [
            _FakeGradeChange("Web", "CK", "7.0", "8.0"),
        ]
        asyncio.run(mgr.dispatch_grade_alert(changes))
        assert n1.notify.call_count == 1
        assert n2.notify.call_count == 1

    def test_dnd_active_skips(self):
        mgr = self._make_manager(dnd_active=True)
        mock_notifier = Mock()
        mgr.notifiers.append(mock_notifier)
        changes = [_FakeGradeChange("Math", "GK", "5.0", "6.0")]
        asyncio.run(mgr.dispatch_grade_alert(changes))
        mock_notifier.notify.assert_not_called()

    def test_notifier_error_doesnt_crash(self):
        mgr = self._make_manager()
        bad_notifier = Mock()
        bad_notifier.notify.side_effect = Exception("Boom")
        mgr.notifiers = [bad_notifier]
        changes = [_FakeGradeChange("AI", "Lab", "3.0", "4.0")]
        # Should not raise
        asyncio.run(mgr.dispatch_grade_alert(changes))

    def test_multiple_changes_dispatch(self):
        mgr = self._make_manager()
        mock_notifier = Mock()
        mgr.notifiers = [mock_notifier]
        changes = [
            _FakeGradeChange("Web", "CK", "7.0", "8.0"),
            _FakeGradeChange("Math", "GK", "5.0", "6.0"),
        ]
        asyncio.run(mgr.dispatch_grade_alert(changes))
        # Grade changes are batched so each channel receives one I/O operation.
        assert mock_notifier.notify.call_count == 1
        assert len(mock_notifier.notify.call_args.args[0]) == 2
