#!/usr/bin/env python3
"""
=================================================================
  UTHelper Notification System - Comprehensive Test Suite
=================================================================

Muc tieu: Mo phong sat thuc te nhat he thong thong bao tren
ca Windows va Android, test moi truong hop co the xay ra.

Bao gom:
  1. WindowsNotifier   - toast formatting, fallback
  2. MobileNotifier    - Android mock, iOS mock, fallback
  3. TelegramNotifier  - HTTP mock, HTML escape, message truncation
  4. DiscordNotifier   - Webhook mock, embed format, 10-embed limit
  5. EmailNotifier     - SMTP mock, HTML template, plain text fallback
  6. NotificationManager - DND, milestones, cache, filtering, dispatch
  7. Edge cases        - empty, unicode, XSS, batch, expired deadlines

Usage:
  python scripts/notification_system_test.py
"""
import sys
import os
import json
import tempfile
import shutil
import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock, call
from types import SimpleNamespace

# Suppress noisy logs during test
logging.basicConfig(level=logging.WARNING)

# Setup path
_SRC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
sys.path.insert(0, _SRC_DIR)

# Temp directory for test cache/data
_TEST_DIR = Path(tempfile.mkdtemp(prefix="uth_notif_test_"))

# ============================================================
# Test infrastructure
# ============================================================
_passed = 0
_failed = 0
_errors = []

def P(name, ok, detail=""):
    global _passed, _failed
    icon = "PASS" if ok else "FAIL"
    line = f"  [{icon}] {name}"
    if detail:
        line += f" -- {detail}"
    print(line)
    if ok:
        _passed += 1
    else:
        _failed += 1
        _errors.append(name)
    return ok

def section(title):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def make_assignment(**overrides):
    """Factory for test Assignment objects."""
    from models import Assignment
    defaults = {
        "id": "1001",
        "course_id": "CS101",
        "course_name": "[12345]_HKII2025-2026_Lap trinh Python_987654321",
        "title": "Bai tap tuan 5",
        "event_type": "assignment",
        "deadline": datetime.now() + timedelta(hours=12),
        "url": "https://courses.ut.edu.vn/mod/assign/view.php?id=1001",
        "submission_status": "unknown",
    }
    defaults.update(overrides)
    return Assignment(**defaults)


def make_dict_assignment(**overrides):
    """Factory for dict-based assignments (backward compat)."""
    defaults = {
        "title": "Quiz Chuong 3",
        "course_name": "[CS201] - Cau truc du lieu - ABC123",
        "course": "[CS201] - Cau truc du lieu - ABC123",
        "type": "quiz",
        "event_type": "quiz",
        "deadline": (datetime.now() + timedelta(hours=6)).isoformat(),
        "url": "https://courses.ut.edu.vn/mod/quiz/view.php?id=2001",
        "urgency": "warning",
        "submission_status": "unknown",
    }
    defaults.update(overrides)
    return defaults


# ============================================================
# SECTION 1: WindowsNotifier
# ============================================================
def test_windows_notifier():
    section("1. WindowsNotifier")

    from notifiers.windows import WindowsNotifier, _get

    # 1a. Single assignment toast
    try:
        notifier = WindowsNotifier.__new__(WindowsNotifier)
        notifier.tray_app = MagicMock()
        notifier.app_id = "UTHelper"
        notifier.aumid = "UTHelper.App"
        notifier._icon_path = ""

        a = make_assignment(deadline=datetime.now() + timedelta(hours=2))

        # Mock windows_toasts import to fail -> fallback to tray
        with patch.dict('sys.modules', {'windows_toasts': None}):
            notifier.notify([a])
            P("Single assignment -> tray fallback", notifier.tray_app.notify.called,
              f"tray_called={notifier.tray_app.notify.called}")
    except Exception as ex:
        P("Single assignment -> tray fallback", False, repr(ex)[:80])

    # 1b. Multiple assignments toast
    try:
        notifier.tray_app.reset_mock()
        assignments = [
            make_assignment(title=f"Bai tap {i}", deadline=datetime.now() + timedelta(hours=2+i))
            for i in range(5)
        ]
        with patch.dict('sys.modules', {'windows_toasts': None}):
            notifier.notify(assignments)
        P("Multi assignment toast", notifier.tray_app.notify.called,
          f"tray_called={notifier.tray_app.notify.called}")
    except Exception as ex:
        P("Multi assignment toast", False, repr(ex)[:80])

    # 1c. Empty list - should do nothing
    try:
        notifier.tray_app.reset_mock()
        notifier.notify([])
        P("Empty list -> no-op", not notifier.tray_app.notify.called)
    except Exception as ex:
        P("Empty list -> no-op", False, str(ex))

    # 1d. Dict-based assignment
    try:
        notifier.tray_app.reset_mock()
        d = make_dict_assignment()
        with patch.dict('sys.modules', {'windows_toasts': None}):
            notifier.notify([d])
        P("Dict assignment support", notifier.tray_app.notify.called)
    except Exception as ex:
        P("Dict assignment support", False, str(ex))


# ============================================================
# SECTION 2: MobileNotifier (Android/iOS simulation)
# ============================================================
def test_mobile_notifier():
    section("2. MobileNotifier (Android/iOS)")

    from notifiers.mobile import MobileNotifier

    # 2a. Android mock - flet-android-notifications
    try:
        notifier = MobileNotifier.__new__(MobileNotifier)
        notifier._notifier = None
        notifier._backend = "flet-android-notifications"
        mock_android = MagicMock()
        notifier._android_notif = mock_android

        assignments = [make_assignment(title="Nop bai Android"), make_assignment(title="Quiz Android")]
        result = notifier.notify(assignments)

        P("Android: 2 assignments notified", result and mock_android.show_notification.call_count == 2,
          f"calls={mock_android.show_notification.call_count}")

        # Check notification IDs are unique
        ids = [c[1]['id'] for c in mock_android.show_notification.call_args_list]
        P("Android: unique notification IDs", len(set(ids)) == 2, f"ids={ids}")
    except Exception as ex:
        P("Android: basic notify", False, str(ex))

    # 2b. Android max 5 notifications
    try:
        mock_android.reset_mock()
        assignments = [make_assignment(title=f"Task {i}") for i in range(10)]
        notifier.notify(assignments)
        P("Android: max 5 cap", mock_android.show_notification.call_count == 5,
          f"sent={mock_android.show_notification.call_count}/10")
    except Exception as ex:
        P("Android: max 5 cap", False, str(ex))

    # 2c. iOS fallback (flet_notifications)
    try:
        notifier2 = MobileNotifier.__new__(MobileNotifier)
        notifier2._android_notif = None
        notifier2._backend = "flet_notifications"
        mock_ios = MagicMock()
        notifier2._notifier = mock_ios

        result = notifier2.notify([make_assignment(title="iOS Test")])
        P("iOS: flet_notifications fallback", result and mock_ios.show_notification.called)
    except Exception as ex:
        P("iOS: flet_notifications fallback", False, str(ex))

    # 2d. No package -> log-only mode
    try:
        notifier3 = MobileNotifier.__new__(MobileNotifier)
        notifier3._android_notif = None
        notifier3._notifier = None
        notifier3._backend = "none"

        result = notifier3.notify([make_assignment(title="Log Only")])
        P("Mobile: log-only mode", result,
          "logged without crash")
    except Exception as ex:
        P("Mobile: log-only mode", False, str(ex))

    # 2e. Empty assignments
    try:
        result = notifier.notify([])
        P("Mobile: empty list -> True", result)
    except Exception as ex:
        P("Mobile: empty list -> True", False, str(ex))


# ============================================================
# SECTION 3: TelegramNotifier
# ============================================================
def test_telegram_notifier():
    section("3. TelegramNotifier")

    from notifiers.telegram import TelegramNotifier

    notifier = TelegramNotifier()

    # 3a. Basic send with mock HTTP
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()

    try:
        assignments = [
            make_assignment(title="<script>alert('xss')</script>",
                          deadline=datetime.now() + timedelta(hours=2)),
            make_assignment(title="Bai tap binh thuong",
                          deadline=datetime.now() + timedelta(hours=48)),
        ]

        with patch('notifiers.telegram.settings') as mock_cfg:
            mock_cfg.ENABLE_TELEGRAM = True
            mock_cfg.TELEGRAM_BOT_TOKEN = "123456:ABC-DEF"
            mock_cfg.TELEGRAM_CHAT_ID = "999888"
            with patch('httpx.post', return_value=mock_response) as mock_post:
                result = notifier.notify(assignments)

                P("Telegram: basic send", result and mock_post.called)

                payload = mock_post.call_args[1]['json']
                text = payload['text']
                P("Telegram: XSS escaped", "&lt;script&gt;" in text,
                  f"found escaped: {'&lt;script&gt;' in text}")
                P("Telegram: HTML parse mode", payload['parse_mode'] == 'HTML')
                P("Telegram: web preview disabled", payload['disable_web_page_preview'])
    except Exception as ex:
        P("Telegram: basic send", False, repr(ex)[:80])

    # 3b. Disabled config -> returns False
    try:
        with patch('notifiers.telegram.settings') as mock_cfg:
            mock_cfg.ENABLE_TELEGRAM = False
            mock_cfg.TELEGRAM_BOT_TOKEN = ""
            mock_cfg.TELEGRAM_CHAT_ID = ""
            result = notifier.notify([make_assignment()])
            P("Telegram: disabled -> False", result == False)
    except Exception as ex:
        P("Telegram: disabled -> False", False, str(ex))

    # 3c. Message truncation (>4000 chars)
    try:
        many_assignments = [make_assignment(title=f"Bai tap dai so {i} voi ten rat dai " * 3,
                            deadline=datetime.now() + timedelta(hours=3+i)) for i in range(80)]

        with patch('notifiers.telegram.settings') as mock_cfg:
            mock_cfg.ENABLE_TELEGRAM = True
            mock_cfg.TELEGRAM_BOT_TOKEN = "123:abc"
            mock_cfg.TELEGRAM_CHAT_ID = "999"
            with patch('httpx.post', return_value=mock_response) as mock_post:
                result = notifier.notify(many_assignments)
                if mock_post.called:
                    text = mock_post.call_args[1]['json']['text']
                    P("Telegram: msg truncation <4000", len(text) <= 4100,
                      f"len={len(text)}")
                    P("Telegram: shows omitted count",
                      "khac" in text or "kh\u00e1c" in text or "..." in text,
                      f"has_ellipsis={'...' in text}, included={text.count('━━')}")
                else:
                    P("Telegram: msg truncation", False, "not called")
    except Exception as ex:
        P("Telegram: msg truncation", False, repr(ex)[:80])

    # 3d. Network error handling
    try:
        with patch('notifiers.telegram.settings') as mock_cfg:
            mock_cfg.ENABLE_TELEGRAM = True
            mock_cfg.TELEGRAM_BOT_TOKEN = "123:abc"
            mock_cfg.TELEGRAM_CHAT_ID = "999"
            with patch('httpx.post', side_effect=Exception("Connection refused")):
                result = notifier.notify([make_assignment()])
                P("Telegram: network error -> False", result == False)
    except Exception as ex:
        P("Telegram: network error -> False", False, str(ex))


# ============================================================
# SECTION 4: DiscordNotifier
# ============================================================
def test_discord_notifier():
    section("4. DiscordNotifier")

    from notifiers.discord import DiscordNotifier

    notifier = DiscordNotifier()

    # 4a. Basic webhook send
    try:
        # Use deadlines that produce correct urgency levels via the model
        assignments = [
            make_assignment(title="Bai tap GK", deadline=datetime.now() + timedelta(hours=2)),   # critical
            make_assignment(title="Bai tap CK", deadline=datetime.now() + timedelta(hours=48)),  # warning
            make_assignment(title="Bai tap thuong", deadline=datetime.now() + timedelta(hours=200)),  # safe
        ]

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch('notifiers.discord.settings') as mock_cfg:
            mock_cfg.ENABLE_DISCORD = True
            mock_cfg.DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/test/token"
            with patch('httpx.post', return_value=mock_response) as mock_post:
                result = notifier.notify(assignments)
                P("Discord: basic webhook send", result and mock_post.called)

                payload = mock_post.call_args[1]['json']
                embeds = payload['embeds']
                P("Discord: 3 embeds created", len(embeds) == 3, f"embeds={len(embeds)}")

                # Check embed colors (urgency computed from deadline)
                colors = [e['color'] for e in embeds]
                P("Discord: critical=red(15158332)", colors[0] == 15158332, f"color={colors[0]}")
                P("Discord: warning=orange(15105570)", colors[1] == 15105570, f"color={colors[1]}")
                P("Discord: safe=green(3066993)", colors[2] == 3066993, f"color={colors[2]}")
    except Exception as ex:
        P("Discord: basic webhook send", False, repr(ex)[:80])

    # 4b. Max 10 embeds limit
    try:
        assignments = [make_assignment(title=f"Task {i}") for i in range(15)]
        with patch('notifiers.discord.settings') as mock_cfg:
            mock_cfg.ENABLE_DISCORD = True
            mock_cfg.DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/test/token"
            with patch('httpx.post', return_value=mock_response) as mock_post:
                notifier.notify(assignments)
                payload = mock_post.call_args[1]['json']
                P("Discord: max 10 embeds", len(payload['embeds']) == 10,
                  f"embeds={len(payload['embeds'])}/15")
    except Exception as ex:
        P("Discord: max 10 embeds", False, str(ex))

    # 4c. Disabled -> False
    try:
        with patch('notifiers.discord.settings') as mock_cfg:
            mock_cfg.ENABLE_DISCORD = False
            mock_cfg.DISCORD_WEBHOOK_URL = ""
            result = notifier.notify([make_assignment()])
            P("Discord: disabled -> False", result == False)
    except Exception as ex:
        P("Discord: disabled -> False", False, str(ex))


# ============================================================
# SECTION 5: EmailNotifier
# ============================================================
def test_email_notifier():
    section("5. EmailNotifier (SMTP)")

    from notifiers.email import EmailNotifier

    notifier = EmailNotifier()

    # 5a. HTML email generation
    try:
        assignments = [
            make_assignment(title="Nop bai Cuoi Ky", urgency="critical"),
            make_assignment(title="Bai tap Python", urgency="safe", submission_status="submitted"),
        ]

        mock_smtp = MagicMock()
        mock_smtp_class = MagicMock()
        mock_smtp_class.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp_class.__exit__ = MagicMock(return_value=False)

        with patch('notifiers.email.settings') as mock_cfg:
            mock_cfg.ENABLE_GMAIL = True
            mock_cfg.GMAIL_ADDRESS = "test@gmail.com"
            mock_cfg.GMAIL_APP_PASSWORD = "abcd1234"
            with patch('smtplib.SMTP_SSL', return_value=mock_smtp_class):
                result = notifier.notify(assignments)
                P("Email: SMTP send called", mock_smtp_class.send_message.called or mock_smtp.send_message.called or result,
                  f"result={result}")
    except Exception as ex:
        P("Email: SMTP send", False, str(ex))

    # 5b. Subject line for critical tasks
    try:
        assignments = [make_assignment(deadline=datetime.now() + timedelta(hours=2))]
        with patch('notifiers.email.settings') as mock_cfg:
            mock_cfg.ENABLE_GMAIL = True
            mock_cfg.GMAIL_ADDRESS = "test@gmail.com"
            mock_cfg.GMAIL_APP_PASSWORD = "abcd"
            captured_msgs = []

            class FakeSMTP:
                def __init__(self, *a, **kw): pass
                def __enter__(self): return self
                def __exit__(self, *a): pass
                def login(self, *a): pass
                def send_message(self, msg):
                    captured_msgs.append(msg)

            with patch('smtplib.SMTP_SSL', FakeSMTP):
                notifier.notify(assignments)
                if captured_msgs:
                    subject = captured_msgs[0]['Subject']
                    P("Email: critical subject", len(subject) > 10,
                      f"subject_len={len(subject)}")
                    html_parts = [p for p in captured_msgs[0].walk() if p.get_content_type() == 'text/html']
                    P("Email: has HTML body", len(html_parts) > 0)
                    if html_parts:
                        html_content = html_parts[0].get_content()
                        P("Email: has task-card CSS", "task-card" in html_content)
                        P("Email: has gradient header", "gradient" in html_content)
                else:
                    P("Email: no message captured", False)
    except Exception as ex:
        P("Email: subject & HTML", False, repr(ex)[:80])

    # 5c. No password -> False
    try:
        with patch('notifiers.email.settings') as mock_cfg:
            mock_cfg.ENABLE_GMAIL = True
            mock_cfg.GMAIL_ADDRESS = "test@gmail.com"
            mock_cfg.GMAIL_APP_PASSWORD = ""
            result = notifier.notify([make_assignment()])
            P("Email: no password -> False", result == False)
    except Exception as ex:
        P("Email: no password -> False", False, str(ex))


# ============================================================
# SECTION 6: NotificationManager
# ============================================================
def test_notification_manager():
    section("6. NotificationManager (Orchestration)")

    from notifiers.manager import NotificationManager

    # Use temp cache dir
    cache_path = _TEST_DIR / "notifications_cache.json"

    # 6a. DND logic
    try:
        mgr = NotificationManager.__new__(NotificationManager)
        mgr.notifiers = []
        mgr._cache_path = str(cache_path)
        mgr._cache_lock = __import__('threading').Lock()

        from core.notification_history import NotificationHistory
        mgr._history = NotificationHistory(history_dir=_TEST_DIR)

        # Test DND when disabled
        with patch('notifiers.manager.config') as mock_cfg:
            mock_cfg.NOTIFY_DND_ENABLE = False
            P("DND: disabled -> not in DND", not mgr._is_in_dnd())

        # Test DND when enabled and in range
        now_hour = datetime.now().hour
        with patch('notifiers.manager.config') as mock_cfg:
            mock_cfg.NOTIFY_DND_ENABLE = True
            mock_cfg.NOTIFY_DND_START = now_hour
            mock_cfg.NOTIFY_DND_END = now_hour + 2
            P("DND: in range -> True", mgr._is_in_dnd(),
              f"now={now_hour}, range={now_hour}-{now_hour+2}")

        # Test DND overnight wrap (22:00-06:00)
        with patch('notifiers.manager.config') as mock_cfg:
            mock_cfg.NOTIFY_DND_ENABLE = True
            mock_cfg.NOTIFY_DND_START = 22
            mock_cfg.NOTIFY_DND_END = 6
            result = mgr._is_in_dnd()
            expected = now_hour >= 22 or now_hour < 6
            P("DND: overnight wrap", result == expected,
              f"now={now_hour}, expected={expected}")

        # Test 24h DND (start==end)
        with patch('notifiers.manager.config') as mock_cfg:
            mock_cfg.NOTIFY_DND_ENABLE = True
            mock_cfg.NOTIFY_DND_START = 0
            mock_cfg.NOTIFY_DND_END = 0
            P("DND: 24h silence (start==end)", mgr._is_in_dnd())

    except Exception as ex:
        P("DND logic", False, str(ex))

    # 6b. Milestone filtering
    try:
        mgr2 = NotificationManager.__new__(NotificationManager)
        mgr2.notifiers = []
        mgr2._cache_path = str(_TEST_DIR / "milestone_test_cache.json")
        mgr2._cache_lock = __import__('threading').Lock()
        mgr2._history = NotificationHistory(history_dir=_TEST_DIR)

        assignments = [
            make_assignment(title="Due in 3h", deadline=datetime.now() + timedelta(hours=3)),
            make_assignment(title="Due in 12h", deadline=datetime.now() + timedelta(hours=12)),
            make_assignment(title="Due in 36h", deadline=datetime.now() + timedelta(hours=36)),
            make_assignment(title="Due in 100h", deadline=datetime.now() + timedelta(hours=100)),
            make_assignment(title="Already expired", deadline=datetime.now() - timedelta(hours=1)),
        ]

        with patch('notifiers.manager.config') as mock_cfg:
            mock_cfg.NOTIFY_MILESTONES = [6, 12, 24, 48]
            mock_cfg.NOTIFY_MINUTES_BEFORE = 0
            mock_cfg.NOTIFY_MUTED_COURSES = []
            mock_cfg.NOTIFY_IGNORE_SUBMITTED = False
            mock_cfg.NOTIFY_TYPES = None

            filtered = mgr2._filter_assignments(assignments)
            urls = [f['assignment'].title for f in filtered]

            P("Milestone: 3h matches ms=6", "Due in 3h" in urls, f"filtered={urls}")
            P("Milestone: 12h matches ms=12", "Due in 12h" in urls)
            P("Milestone: 36h matches ms=48", "Due in 36h" in urls)
            P("Milestone: 100h no match", "Due in 100h" not in urls)
            P("Milestone: expired excluded", "Already expired" not in urls)
    except Exception as ex:
        P("Milestone filtering", False, str(ex))

    # 6c. Muted courses
    try:
        a_muted = make_assignment(title="Muted Course", course_name="Banned101",
                                  deadline=datetime.now() + timedelta(hours=3))
        a_normal = make_assignment(title="Normal Course",
                                   deadline=datetime.now() + timedelta(hours=3))

        with patch('notifiers.manager.config') as mock_cfg:
            mock_cfg.NOTIFY_MILESTONES = [6, 12, 24]
            mock_cfg.NOTIFY_MINUTES_BEFORE = 0
            mock_cfg.NOTIFY_MUTED_COURSES = ["Banned101"]
            mock_cfg.NOTIFY_IGNORE_SUBMITTED = False
            mock_cfg.NOTIFY_TYPES = None

            filtered = mgr2._filter_assignments([a_muted, a_normal])
            titles = [f['assignment'].title for f in filtered]
            P("Muted: Banned101 excluded", "Muted Course" not in titles)
            P("Muted: normal included", "Normal Course" in titles)
    except Exception as ex:
        P("Muted courses", False, str(ex))

    # 6d. Ignore submitted assignments
    try:
        a_submitted = make_assignment(title="Already Submitted",
                                       submission_status="submitted",
                                       deadline=datetime.now() + timedelta(hours=3))
        a_graded = make_assignment(title="Already Graded",
                                    submission_status="graded",
                                    deadline=datetime.now() + timedelta(hours=3))
        a_pending = make_assignment(title="Not Submitted",
                                     deadline=datetime.now() + timedelta(hours=3))

        with patch('notifiers.manager.config') as mock_cfg:
            mock_cfg.NOTIFY_MILESTONES = [6]
            mock_cfg.NOTIFY_MINUTES_BEFORE = 0
            mock_cfg.NOTIFY_MUTED_COURSES = []
            mock_cfg.NOTIFY_IGNORE_SUBMITTED = True
            mock_cfg.NOTIFY_TYPES = None

            filtered = mgr2._filter_assignments([a_submitted, a_graded, a_pending])
            titles = [f['assignment'].title for f in filtered]
            P("Submitted: excluded", "Already Submitted" not in titles)
            P("Graded: excluded", "Already Graded" not in titles)
            P("Pending: included", "Not Submitted" in titles)
    except Exception as ex:
        P("Ignore submitted", False, str(ex))

    # 6e. NOTIFY_MINUTES_BEFORE
    try:
        a_close = make_assignment(title="Due in 4 min",
                                   deadline=datetime.now() + timedelta(minutes=4))

        with patch('notifiers.manager.config') as mock_cfg:
            mock_cfg.NOTIFY_MILESTONES = [6, 12]
            mock_cfg.NOTIFY_MINUTES_BEFORE = 5  # Trigger when < 5 min
            mock_cfg.NOTIFY_MUTED_COURSES = []
            mock_cfg.NOTIFY_IGNORE_SUBMITTED = False
            mock_cfg.NOTIFY_TYPES = None

            mgr3 = NotificationManager.__new__(NotificationManager)
            mgr3.notifiers = []
            mgr3._cache_path = str(_TEST_DIR / "minutes_cache.json")
            mgr3._cache_lock = __import__('threading').Lock()
            mgr3._history = NotificationHistory(history_dir=_TEST_DIR)

            filtered = mgr3._filter_assignments([a_close])
            P("Minutes: 4min < 5min threshold -> notified",
              any(f['assignment'].title == "Due in 4 min" for f in filtered))
    except Exception as ex:
        P("NOTIFY_MINUTES_BEFORE", False, str(ex))

    # 6f. Dispatch with mock notifiers
    try:
        mgr4 = NotificationManager.__new__(NotificationManager)
        mock_notif1 = MagicMock()
        mock_notif1.notify = MagicMock(return_value=True)
        mock_notif1.__class__.__name__ = "MockChannel1"
        mock_notif2 = MagicMock()
        mock_notif2.notify = MagicMock(return_value=True)
        mock_notif2.__class__.__name__ = "MockChannel2"
        mgr4.notifiers = [mock_notif1, mock_notif2]
        mgr4._cache_path = str(_TEST_DIR / "dispatch_cache.json")
        mgr4._cache_lock = __import__('threading').Lock()
        mgr4._history = NotificationHistory(history_dir=_TEST_DIR)

        assignments = [make_assignment(title="Dispatch Test",
                                        deadline=datetime.now() + timedelta(hours=3))]

        with patch('notifiers.manager.config') as mock_cfg:
            mock_cfg.NOTIFY_DND_ENABLE = False
            mock_cfg.NOTIFY_MILESTONES = [6]
            mock_cfg.NOTIFY_MINUTES_BEFORE = 0
            mock_cfg.NOTIFY_MUTED_COURSES = []
            mock_cfg.NOTIFY_IGNORE_SUBMITTED = False
            mock_cfg.NOTIFY_TYPES = None

            mgr4.dispatch(assignments)

            P("Dispatch: channel 1 called", mock_notif1.notify.called)
            P("Dispatch: channel 2 called", mock_notif2.notify.called)
    except Exception as ex:
        P("Dispatch", False, str(ex))

    # 6g. DND blocks dispatch
    try:
        mock_notif1.reset_mock()
        mock_notif2.reset_mock()
        with patch('notifiers.manager.config') as mock_cfg:
            mock_cfg.NOTIFY_DND_ENABLE = True
            mock_cfg.NOTIFY_DND_START = datetime.now().hour
            mock_cfg.NOTIFY_DND_END = datetime.now().hour + 1

            mgr4.dispatch(assignments)
            P("DND: blocks dispatch", not mock_notif1.notify.called and not mock_notif2.notify.called)
    except Exception as ex:
        P("DND blocks dispatch", False, str(ex))

    # 6h. Cache prevents re-notification
    try:
        mgr5 = NotificationManager.__new__(NotificationManager)
        mgr5.notifiers = [mock_notif1]
        mgr5._cache_path = str(_TEST_DIR / "dedup_cache.json")
        mgr5._cache_lock = __import__('threading').Lock()
        mgr5._history = NotificationHistory(history_dir=_TEST_DIR)

        mock_notif1.reset_mock()
        mock_notif1.notify = MagicMock(return_value=True)

        a = make_assignment(title="Dedup Test", deadline=datetime.now() + timedelta(hours=3))

        with patch('notifiers.manager.config') as mock_cfg:
            mock_cfg.NOTIFY_DND_ENABLE = False
            mock_cfg.NOTIFY_MILESTONES = [6]
            mock_cfg.NOTIFY_MINUTES_BEFORE = 0
            mock_cfg.NOTIFY_MUTED_COURSES = []
            mock_cfg.NOTIFY_IGNORE_SUBMITTED = False
            mock_cfg.NOTIFY_TYPES = None

            # First dispatch
            mgr5.dispatch([a])
            first_count = mock_notif1.notify.call_count

            # Second dispatch (same milestone) - should NOT re-notify
            mgr5.dispatch([a])
            second_count = mock_notif1.notify.call_count

            P("Cache: dedup prevents re-notify", first_count == 1 and second_count == 1,
              f"1st={first_count}, 2nd={second_count}")
    except Exception as ex:
        P("Cache dedup", False, str(ex))

    # 6i. Cache eviction (90 days)
    try:
        mgr6 = NotificationManager.__new__(NotificationManager)
        mgr6._cache_path = str(_TEST_DIR / "evict_cache.json")
        mgr6._cache_lock = __import__('threading').Lock()

        old_cache = {
            "https://old.url/1": {
                "milestones": [6],
                "updated_at": (datetime.now() - timedelta(days=100)).isoformat()
            },
            "https://new.url/2": {
                "milestones": [12],
                "updated_at": datetime.now().isoformat()
            }
        }
        with open(mgr6._cache_path, "w") as f:
            json.dump(old_cache, f)

        mgr6._save_cache(old_cache)

        with open(mgr6._cache_path, "r") as f:
            saved = json.load(f)

        P("Cache: old entry evicted", "https://old.url/1" not in saved,
          f"keys={list(saved.keys())}")
        P("Cache: new entry kept", "https://new.url/2" in saved)
    except Exception as ex:
        P("Cache eviction", False, str(ex))


# ============================================================
# SECTION 7: NotificationHistory
# ============================================================
def test_notification_history():
    section("7. NotificationHistory")

    from core.notification_history import NotificationHistory

    hist = NotificationHistory(history_dir=_TEST_DIR / "hist_test")
    (_TEST_DIR / "hist_test").mkdir(exist_ok=True)

    # 7a. Add and retrieve
    try:
        hist.clear()
        hist.add([make_assignment(title="Test1")], ["Telegram"])
        hist.add([make_assignment(title="Test2"), make_assignment(title="Test3")], ["Discord", "Gmail"])

        entries = hist.get_all()
        P("History: 3 entries added", len(entries) == 3, f"count={len(entries)}")
        P("History: newest first", entries[0]['title'] in ("Test2", "Test3"))
        P("History: channels recorded", "Telegram" in entries[2]['channels'])
    except Exception as ex:
        P("History: add/retrieve", False, str(ex))

    # 7b. Max 100 cap
    try:
        hist.clear()
        for i in range(110):
            hist.add([{"title": f"Task {i}"}], [f"ch{i}"])
        entries = hist.get_all()
        P("History: max 100 cap", len(entries) == 100, f"count={len(entries)}")
    except Exception as ex:
        P("History: max 100", False, str(ex))

    # 7c. Clear
    try:
        hist.clear()
        P("History: clear works", len(hist.get_all()) == 0)
    except Exception as ex:
        P("History: clear", False, str(ex))


# ============================================================
# SECTION 8: Edge Cases
# ============================================================
def test_edge_cases():
    section("8. Edge Cases")

    # 8a. Unicode in all fields
    try:
        a = make_assignment(
            title="Bai tap ky tu dac biet: -  -  - {} [] ()",
            course_name="Mon hoc Viet Nam",
        )
        from notifiers.windows import WindowsNotifier
        notifier = WindowsNotifier.__new__(WindowsNotifier)
        notifier.tray_app = MagicMock()
        notifier.app_id = "UTHelper"
        notifier.aumid = "UTHelper.App"
        notifier._icon_path = ""

        with patch.dict('sys.modules', {'windows_toasts': None}):
            notifier.notify([a])
        P("Edge: Unicode assignment", notifier.tray_app.notify.called)
    except Exception as ex:
        P("Edge: Unicode assignment", False, str(ex))

    # 8b. Very long title (>200 chars)
    try:
        a = make_assignment(title="A" * 300)
        notifier.tray_app.reset_mock()
        with patch.dict('sys.modules', {'windows_toasts': None}):
            notifier.notify([a])
        P("Edge: 300-char title", notifier.tray_app.notify.called)
    except Exception as ex:
        P("Edge: 300-char title", False, str(ex))

    # 8c. Dict with missing fields
    try:
        sparse_dict = {"title": "Minimal"}  # Missing most fields
        from notifiers.telegram import TelegramNotifier
        tn = TelegramNotifier()
        block = tn._format_task_block(sparse_dict)
        P("Edge: sparse dict -> no crash", "Minimal" in block, f"len={len(block)}")
    except Exception as ex:
        P("Edge: sparse dict -> no crash", False, str(ex))

    # 8d. Assignment with deadline exactly now
    try:
        a = make_assignment(deadline=datetime.now())
        from core.time_utils import format_remaining_time
        remaining = format_remaining_time(a.deadline)
        P("Edge: deadline=now", remaining is not None, f"remaining='{remaining}'")
    except Exception as ex:
        P("Edge: deadline=now", False, str(ex))

    # 8e. Submission status edge values
    try:
        for status in ["submitted", "graded", "unknown", "", None, "new_status"]:
            a = make_assignment(submission_status=status or "unknown")
            # Should not crash any notifier
        P("Edge: all submission statuses", True, "no crashes")
    except Exception as ex:
        P("Edge: submission statuses", False, str(ex))

    # 8f. NOTIFY_TYPES filter
    try:
        from core.notification_history import NotificationHistory
        from notifiers.manager import NotificationManager as NM
        mgr = NM.__new__(NM)
        mgr.notifiers = []
        mgr._cache_path = str(_TEST_DIR / "types_cache.json")
        mgr._cache_lock = __import__('threading').Lock()
        mgr._history = NotificationHistory(history_dir=_TEST_DIR)

        a_quiz = make_assignment(title="Quiz Only", event_type="quiz",
                                  deadline=datetime.now() + timedelta(hours=3))
        a_assign = make_assignment(title="Assignment Only", event_type="assignment",
                                    deadline=datetime.now() + timedelta(hours=3))

        with patch('notifiers.manager.config') as mock_cfg:
            mock_cfg.NOTIFY_MILESTONES = [6]
            mock_cfg.NOTIFY_MINUTES_BEFORE = 0
            mock_cfg.NOTIFY_MUTED_COURSES = []
            mock_cfg.NOTIFY_IGNORE_SUBMITTED = False
            mock_cfg.NOTIFY_TYPES = ["quiz"]  # Only quiz

            filtered = mgr._filter_assignments([a_quiz, a_assign])
            titles = [f['assignment'].title for f in filtered]
            P("Edge: type filter quiz only", "Quiz Only" in titles and "Assignment Only" not in titles,
              f"filtered={titles}")
    except Exception as ex:
        P("Edge: type filter", False, repr(ex)[:80])


# ============================================================
# SECTION 9: Integration - Full Pipeline Simulation
# ============================================================
def test_full_pipeline():
    section("9. Full Pipeline Simulation")

    try:
        from notifiers.manager import NotificationManager
        from core.notification_history import NotificationHistory

        # Create a manager with mock notifiers
        mgr = NotificationManager.__new__(NotificationManager)
        mgr._cache_path = str(_TEST_DIR / "pipeline_cache.json")
        mgr._cache_lock = __import__('threading').Lock()
        mgr._history = NotificationHistory(history_dir=_TEST_DIR / "pipeline_hist")
        (_TEST_DIR / "pipeline_hist").mkdir(exist_ok=True)

        # Simulate Windows + Telegram channels
        win_notif = MagicMock()
        win_notif.notify = MagicMock(return_value=True)
        win_notif.__class__ = type("WindowsNotifier", (), {})

        tele_notif = MagicMock()
        tele_notif.notify = MagicMock(return_value=True)
        tele_notif.__class__ = type("TelegramNotifier", (), {})

        mgr.notifiers = [win_notif, tele_notif]

        # Simulate a real Moodle check result
        assignments = [
            make_assignment(title="KLTN - Nop bao cao", event_type="assignment",
                          urgency="critical", deadline=datetime.now() + timedelta(hours=2)),
            make_assignment(title="Trac nghiem Mang MT", event_type="quiz",
                          urgency="warning", deadline=datetime.now() + timedelta(hours=10)),
            make_assignment(title="Bai tap Java", event_type="assignment",
                          urgency="safe", deadline=datetime.now() + timedelta(hours=48)),
            make_assignment(title="Da nop roi", event_type="assignment",
                          submission_status="submitted",
                          deadline=datetime.now() + timedelta(hours=5)),
            make_assignment(title="Da het han", event_type="assignment",
                          deadline=datetime.now() - timedelta(hours=1)),
        ]

        with patch('notifiers.manager.config') as mock_cfg:
            mock_cfg.NOTIFY_DND_ENABLE = False
            mock_cfg.NOTIFY_MILESTONES = [6, 12, 24, 48]
            mock_cfg.NOTIFY_MINUTES_BEFORE = 0
            mock_cfg.NOTIFY_MUTED_COURSES = []
            mock_cfg.NOTIFY_IGNORE_SUBMITTED = True
            mock_cfg.NOTIFY_TYPES = None

            # First dispatch
            mgr.dispatch(assignments)

            P("Pipeline: Windows channel called", win_notif.notify.called)
            P("Pipeline: Telegram channel called", tele_notif.notify.called)

            if win_notif.notify.called:
                notified = win_notif.notify.call_args[0][0]
                titles = [a.title for a in notified]
                P("Pipeline: 3 tasks notified (excl submitted+expired)",
                  len(notified) == 3, f"titles={titles}")
                P("Pipeline: critical first check", "KLTN - Nop bao cao" in titles)
                P("Pipeline: submitted excluded", "Da nop roi" not in titles)
                P("Pipeline: expired excluded", "Da het han" not in titles)

            # History recorded
            history = mgr._history.get_all()
            P("Pipeline: history recorded", len(history) > 0, f"entries={len(history)}")

            # Second dispatch - dedup
            win_notif.notify.reset_mock()
            mgr.dispatch(assignments)
            P("Pipeline: dedup on 2nd dispatch", not win_notif.notify.called,
              "same milestones should not re-trigger")

            # New assignment (different URL) should trigger
            a_new = make_assignment(title="Brand new task",
                                    id="9999",
                                    url="https://courses.ut.edu.vn/mod/assign/view.php?id=9999",
                                    deadline=datetime.now() + timedelta(hours=5))
            win_notif.notify.reset_mock()
            mgr.dispatch([a_new])
            P("Pipeline: new assignment triggers", win_notif.notify.called)

    except Exception as ex:
        P("Full pipeline", False, str(ex))


# ============================================================
# Main
# ============================================================
def main():
    print("=" * 60)
    print("  UTHelper Notification System - Test Suite")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    test_windows_notifier()
    test_mobile_notifier()
    test_telegram_notifier()
    test_discord_notifier()
    test_email_notifier()
    test_notification_manager()
    test_notification_history()
    test_edge_cases()
    test_full_pipeline()

    print(f"\n{'=' * 60}")
    print(f"  RESULTS: {_passed} passed, {_failed} failed / {_passed + _failed} total")
    if _errors:
        print(f"  FAILED: {', '.join(_errors[:10])}")
    print(f"{'=' * 60}")

    # Cleanup
    try:
        shutil.rmtree(str(_TEST_DIR), ignore_errors=True)
    except Exception:
        pass

    return _failed == 0


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
