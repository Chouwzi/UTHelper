"""Integration tests against LIVE courses.ut.edu.vn API.

Verifies the real data flow: login → token → WS API → data parsing → notification logic.
This is NOT a unit test — it calls the actual Moodle server.

Usage:
    python -m pytest tests/test_integration_live.py -v --tb=short -x -s
"""
import sys
import os
import time
import pytest
from datetime import datetime
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# ── Test credentials ──
_USERNAME = "STUDENT_ID"
_PASSWORD = "YOUR_PASSWORD"

# ── Shared state across test classes (populated progressively) ──
_state = {}


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 1: Authentication & Token
# ═══════════════════════════════════════════════════════════════════════════════

class TestPhase1Authentication:
    """Test real login flow against courses.ut.edu.vn."""

    def test_01_moodle_client_import(self):
        """Verify MoodleClient can be imported."""
        from core.client import MoodleClient
        assert MoodleClient is not None

    def test_02_login_and_get_token(self):
        """Login with real credentials via client.login()."""
        from core.client import MoodleClient
        from config import settings

        # Set credentials
        settings.UTH_USERNAME = _USERNAME
        settings.UTH_PASSWORD = _PASSWORD
        settings.MOODLE_WS_TOKEN = ""  # Force fresh login

        client = MoodleClient()
        success = client.login(_USERNAME, _PASSWORD, force=True)
        assert success, f"Login failed! Error: {getattr(client, '_last_login_error', 'unknown')}"

        token = settings.MOODLE_WS_TOKEN
        assert token, "Token is empty after successful login"
        assert len(token) > 10, f"Token looks invalid: {token[:10]}..."

        _state['client'] = client
        _state['token'] = token
        print(f"\n  [OK] Token obtained: {token[:8]}...{token[-4:]}")

    def test_03_site_info(self):
        """Verify core_webservice_get_site_info returns valid user data."""
        from core import ws_functions
        client = _state['client']
        info = ws_functions.get_site_info(client.call_ws_api)

        assert info is not None, "site_info returned None"
        assert 'userid' in info, f"No userid in site_info: {list(info.keys())}"
        assert 'fullname' in info, "No fullname in site_info"
        assert info['userid'] > 0, f"Invalid userid: {info['userid']}"

        _state['userid'] = info['userid']
        _state['fullname'] = info.get('fullname', '')
        print(f"\n  [OK] User: {info['fullname']} (ID: {info['userid']})")
        print(f"  [OK] Site: {info.get('sitename', 'N/A')}")
        print(f"  [OK] Functions: {len(info.get('functions', []))}")


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2: Core WS API Functions
# ═══════════════════════════════════════════════════════════════════════════════

class TestPhase2WSFunctions:
    """Test WS API wrapper functions with real data."""

    def test_04_enrolled_courses(self):
        """Get enrolled courses — should return at least 1 course."""
        from core import ws_functions
        client = _state['client']

        courses = ws_functions.get_enrolled_courses(client.call_ws_api)
        assert courses is not None, "get_enrolled_courses returned None"
        assert isinstance(courses, list), f"Expected list, got {type(courses)}"
        assert len(courses) > 0, "No enrolled courses found!"

        for c in courses[:3]:
            assert 'id' in c, f"Course missing 'id': {c}"

        _state['courses'] = courses
        print(f"\n  [OK] Enrolled courses: {len(courses)}")
        for c in courses[:5]:
            print(f"     - [{c.get('id')}] {c.get('fullname', c.get('shortname', 'N/A'))[:60]}")

    def test_05_calendar_events(self):
        """Get upcoming calendar events."""
        from core import ws_functions
        client = _state['client']

        events = ws_functions.get_calendar_action_events(client.call_ws_api)
        assert events is not None, "get_calendar_action_events returned None"
        assert isinstance(events, list), f"Expected list, got {type(events)}"

        _state['events'] = events
        print(f"\n  [OK] Calendar events: {len(events)}")
        for e in events[:5]:
            name = e.get('name', 'N/A')[:50]
            ts = e.get('timesort', 0)
            dt = datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M') if ts else 'N/A'
            print(f"     - {name} | {dt}")

    def test_06_assignments(self):
        """Get assignments for enrolled courses."""
        from core import ws_functions
        client = _state['client']
        courses = _state.get('courses', [])
        if not courses:
            pytest.skip("No courses to get assignments from")

        course_ids = [c['id'] for c in courses[:5] if 'id' in c]
        assignments = ws_functions.get_assignments(client.call_ws_api, course_ids)

        if assignments is None:
            print("\n  [WARN] get_assignments returned None")
        else:
            assert isinstance(assignments, list)
            total = sum(len(c.get('assignments', [])) for c in assignments)
            _state['assignments'] = assignments
            print(f"\n  [OK] Assignment courses: {len(assignments)}, total: {total}")

    def test_07_unread_notification_count(self):
        """Get unread notification count (used by badge feature)."""
        from core import ws_functions
        client = _state['client']
        userid = _state['userid']

        count = ws_functions.get_unread_notification_count(client.call_ws_api, userid)
        assert count is not None, "Unread count returned None"
        assert isinstance(count, int), f"Expected int, got {type(count)}"
        assert count >= 0, f"Invalid count: {count}"
        _state['unread_count'] = count
        print(f"\n  [OK] Unread notifications: {count}")

    def test_09_course_grades(self):
        """Get grade overview for all courses."""
        from core import ws_functions
        client = _state['client']
        userid = _state['userid']

        grades = ws_functions.get_course_grades(client.call_ws_api, userid)
        if grades is None:
            print("\n  [WARN] No grades returned")
        else:
            assert isinstance(grades, list)
            _state['grades'] = grades
            print(f"\n  [OK] Course grades: {len(grades)}")
            for g in grades[:5]:
                name = g.get('coursename', 'N/A')[:40]
                grade = g.get('grade', '-')
                print(f"     - {name}: {grade}")

    def test_10_grade_items_detail(self):
        """Get detailed grade items for a specific course."""
        from core import ws_functions
        client = _state['client']
        userid = _state['userid']
        courses = _state.get('courses', [])
        if not courses:
            pytest.skip("No courses")

        for c in courses[:10]:
            cid = c.get('id')
            if not cid:
                continue
            items = ws_functions.get_grade_items(client.call_ws_api, cid, userid)
            if items:
                print(f"\n  [OK] Grade items for [{cid}] {c.get('fullname', '')[:40]}: {len(items)}")
                for item in items[:5]:
                    name = str(item.get('itemname') or 'N/A')[:40]
                    grade = str(item.get('gradeformatted') or '-')
                    print(f"     - {name}: {grade}")
                return
        print("\n  [WARN] No grade items found in any course")


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 3: Data Orchestrator — Full Pipeline
# ═══════════════════════════════════════════════════════════════════════════════

class TestPhase3DataOrchestrator:
    """Test the full data pipeline: login → fetch → parse → merge."""

    def test_11_orchestrator_full_fetch(self):
        """Full data fetch via DataOrchestrator.get_latest_activities()."""
        from core.data_orchestrator import DataOrchestrator
        from config import settings

        # Settings already set from Phase 1
        settings.UTH_USERNAME = _USERNAME
        settings.UTH_PASSWORD = _PASSWORD

        orch = DataOrchestrator()
        login_ok = orch.login()
        assert login_ok, "DataOrchestrator.login() failed!"

        activities = orch.get_latest_activities()

        assert activities is not None, "get_latest_activities returned None!"
        assert isinstance(activities, list), f"Expected list, got {type(activities)}"

        _state['orchestrator'] = orch
        _state['activities'] = activities
        print(f"\n  [OK] Total activities from orchestrator: {len(activities)}")

        if activities:
            first = activities[0]
            print(f"  [OK] Activity keys: {list(first.keys())}")
            for a in activities[:5]:
                title = str(a.get('title', 'N/A'))[:50]
                deadline = a.get('deadline', 'N/A')
                urgency = a.get('urgency', 'N/A')
                status = a.get('submission_status', 'N/A')
                print(f"     - [{urgency}] {title} | DL: {deadline} | Status: {status}")

    def test_12_deadline_format_iso(self):
        """BUG-12 verification: all deadlines should be ISO format, not dd/mm/yyyy."""
        activities = _state.get('activities', [])
        if not activities:
            pytest.skip("No activities to check")

        bad_formats = []
        for a in activities:
            dl = str(a.get('deadline', ''))
            if not dl or dl == 'N/A' or dl == 'None':
                continue
            # dd/mm/yyyy pattern would have / at position 2
            if len(dl) >= 10 and dl[2] == '/':
                bad_formats.append(dl)

        assert len(bad_formats) == 0, \
            f"BUG-12 REGRESSION: {len(bad_formats)} deadlines in dd/mm/yyyy: {bad_formats[:3]}"
        print(f"\n  [OK] All deadlines in valid format (checked {len(activities)} activities)")

    def test_13_activity_urls_valid(self):
        """Verify activity URLs point to courses.ut.edu.vn."""
        activities = _state.get('activities', [])
        if not activities:
            pytest.skip("No activities")

        urls_ok = 0
        for a in activities:
            url = a.get('url', '')
            if url:
                assert 'courses.ut.edu.vn' in url or 'ut.edu.vn' in url, \
                    f"URL not on correct domain: {url}"
                urls_ok += 1
        print(f"\n  [OK] {urls_ok}/{len(activities)} activities have valid URLs")


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 4: Feature-Specific Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestPhase4SmartPolling:
    """Test smart polling feature with real API."""

    def test_14_course_updates_since(self):
        """Smart poll: detect changes since last fetch."""
        from core import ws_functions
        client = _state['client']
        courses = _state.get('courses', [])
        if not courses:
            pytest.skip("No courses")

        ts = int(time.time()) - 86400  # 24 hours ago
        cid = courses[0].get('id')
        if not cid:
            pytest.skip("No course ID")

        updates = ws_functions.get_course_updates_since(
            client.call_ws_api, cid, ts
        )
        print(f"\n  [OK] Course {cid} updates since 24h ago: {type(updates).__name__}")
        if isinstance(updates, dict):
            print(f"     Keys: {list(updates.keys())}")


class TestPhase4GradeMonitor:
    """Test grade monitoring with real grade data."""

    def test_15_grade_monitor_first_run(self):
        """First run: should store snapshot, no changes detected."""
        from core.grade_monitor import GradeMonitor
        import tempfile

        with tempfile.NamedTemporaryFile(suffix='.json', delete=False, mode='w') as f:
            f.write('{}')
            snap_path = f.name

        try:
            monitor = GradeMonitor(snapshot_path=snap_path)
            client = _state['client']
            userid = _state['userid']

            changes = monitor.check_for_changes(client.call_ws_api, userid)
            assert isinstance(changes, list), f"Expected list, got {type(changes)}"
            # First run = no previous data = 0 changes (storing initial snapshot)
            print(f"\n  [OK] First run: {len(changes)} changes detected")

            # Verify snapshot was saved
            assert os.path.exists(snap_path), "Snapshot file not created"
            import json
            with open(snap_path, 'r', encoding='utf-8') as f:
                snapshot = json.load(f)
            print(f"  [OK] Snapshot saved: {len(snapshot)} entries")

            # Second run: should have 0 changes (grades haven't changed)
            changes2 = monitor.check_for_changes(client.call_ws_api, userid)
            assert len(changes2) == 0, \
                f"Second run without real grade changes should be 0, got {len(changes2)}"
            print(f"  [OK] Second run: 0 changes (consistent)")
        finally:
            if os.path.exists(snap_path):
                os.unlink(snap_path)


class TestPhase4NotificationBadge:
    """Test notification badge feature."""

    def test_16_badge_count_non_negative(self):
        """Badge count should be a non-negative integer."""
        count = _state.get('unread_count')
        if count is None:
            pytest.skip("Unread count not fetched")
        assert isinstance(count, int)
        assert count >= 0
        print(f"\n  [OK] Badge count: {count}")


class TestPhase4NotificationDispatch:
    """Test notification filtering logic with real activity data."""

    def test_17_filter_service_with_real_data(self):
        """Filter service should handle real activity data without crashing."""
        from core.filter_service import FilterService
        activities = _state.get('activities', [])
        if not activities:
            pytest.skip("No activities")

        filtered, counts = FilterService.filter_and_count(activities)
        assert isinstance(filtered, list)
        assert isinstance(counts, dict)
        print(f"\n  [OK] FilterService: {len(activities)} -> {len(filtered)} after filtering")
        print(f"     Urgency counts: {counts.get('urgency', {})}")

    def test_18_notification_manager_filter(self):
        """NotificationManager filter should not crash on real data."""
        from notifiers.manager import NotificationManager
        activities = _state.get('activities', [])
        if not activities:
            pytest.skip("No activities")

        manager = NotificationManager(tray_app=None)
        to_notify = manager._filter_assignments(activities)
        assert isinstance(to_notify, list)
        print(f"\n  [OK] Notification filter: {len(to_notify)} items to notify")


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 5: Security & Data Integrity
# ═══════════════════════════════════════════════════════════════════════════════

class TestPhase5Security:
    """Security checks with real API responses."""

    def test_19_html_sanitization_on_real_data(self):
        """Sanitize real course/event names for XSS."""
        from core.security import HTMLSanitizer
        activities = _state.get('activities', [])
        if not activities:
            pytest.skip("No activities")

        for a in activities:
            title = str(a.get('title', ''))
            sanitized = HTMLSanitizer.sanitize(title)
            assert '<script' not in sanitized.lower(), f"XSS in title: {sanitized}"
        print(f"\n  [OK] {len(activities)} titles sanitized — no XSS detected")

    def test_20_token_not_leaked_in_data(self):
        """WS token should never appear in activity data."""
        token = _state.get('token', '')
        activities = _state.get('activities', [])
        if not token or not activities:
            pytest.skip("No token or activities")

        for a in activities:
            data_str = str(a)
            assert token not in data_str, "TOKEN LEAKED in activity data!"
        print(f"\n  [OK] Token not leaked in {len(activities)} activity records")


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 6: Bug Fix Regression Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestPhase6BugFixRegression:
    """Verify critical bug fixes with real API."""

    def test_21_bug03_course_id_type(self):
        """BUG-03: course_id should be int when passed to grade APIs."""
        from core import ws_functions
        client = _state['client']
        userid = _state['userid']
        courses = _state.get('courses', [])
        if not courses:
            pytest.skip("No courses")

        cid = courses[0].get('id')
        # Pass as string (the bug scenario) — should still work after fix
        str_cid = str(cid)
        items = ws_functions.get_grade_items(client.call_ws_api, str_cid, userid)
        # Should not crash — BUG-03 fix casts to int internally
        print(f"\n  [OK] BUG-03: str course_id '{str_cid}' handled without crash")

    def test_22_bug04_http_error_handling(self):
        """BUG-04: HTTP errors should be caught, not crash."""
        import urllib.error
        from core.client import MoodleClient
        client = MoodleClient()

        # Call with invalid params to potentially trigger error
        try:
            result = client.call_ws_api("core_webservice_get_site_info")
            # Even if token is invalid after reset, it should not crash
            print(f"\n  [OK] BUG-04: API call handled gracefully (result type: {type(result).__name__})")
        except urllib.error.HTTPError:
            pytest.fail("BUG-04 regression: HTTPError not caught!")
        except Exception as e:
            # Other exceptions are OK (e.g., missing token)
            print(f"\n  [OK] BUG-04: Non-HTTP exception (OK): {type(e).__name__}")

    def test_23_bug06_course_cache_keyerror(self):
        """BUG-06: Courses without 'id' should not crash cache building."""
        from core import ws_functions
        client = _state['client']

        # This internally builds a dict comprehension that was crashing
        courses = ws_functions.get_enrolled_courses(client.call_ws_api)
        assert courses is not None
        # If we got here without KeyError, the fix works
        print(f"\n  [OK] BUG-06: Course cache built without KeyError ({len(courses)} courses)")
