import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))
import os
import pytest
from datetime import datetime, timedelta
from notifiers.manager import NotificationManager
from config import settings as config

@pytest.fixture
def mock_config(monkeypatch):
    monkeypatch.setattr(config, "NOTIFY_MILESTONES", [72, 24, 3])
    monkeypatch.setattr(config, "NOTIFY_MILESTONES_MINUTES", [])
    monkeypatch.setattr(config, "NOTIFY_MUTED_COURSES", ["Xác suất thống kê"])
    monkeypatch.setattr(config, "NOTIFY_DND_ENABLE", True)
    monkeypatch.setattr(config, "NOTIFY_DND_START", 23)
    monkeypatch.setattr(config, "NOTIFY_DND_END", 6)
    monkeypatch.setattr(config, "NOTIFY_IGNORE_SUBMITTED", True)

@pytest.fixture
def manager(tmp_path):
    cache_file = tmp_path / "test_notifications.json"
    mgr = NotificationManager(cache_file=str(cache_file))
    mgr._cache_path = str(cache_file)
    return mgr

def test_dnd_logic(manager, mock_config, monkeypatch):
    mock_now = datetime(2025, 1, 1, 1, 30, 0)
    import notifiers.manager
    
    # Simple monkeypatch
    class MockDatetime:
        @classmethod
        def now(cls):
            return mock_now
            
    monkeypatch.setattr(notifiers.manager, "datetime", MockDatetime)
    assert manager._is_in_dnd() is True

    mock_now = datetime(2025, 1, 1, 10, 30, 0)
    MockDatetime.now = classmethod(lambda cls: datetime(2025, 1, 1, 10, 30, 0))
    assert manager._is_in_dnd() is False

def test_milestone_filtering(manager, mock_config):
    now = datetime.now()
    
    task_25h = {
        "url": "http://test.com/1",
        "title": "Task 1",
        "course_name": "Toán 1",
        "deadline": (now + timedelta(hours=25)).isoformat(),
        "submission_status": "not_submitted",
        "is_open": True
    }
    
    task_overdue = {
        "url": "http://test.com/2",
        "title": "Task 2",
        "course_name": "Toán 2",
        "deadline": (now - timedelta(hours=1)).isoformat(),
        "submission_status": "not_submitted",
        "is_open": True
    }
    
    filtered = manager._filter_assignments([task_25h, task_overdue])
    
    assert len(filtered) == 1
    assert filtered[0]["assignment"]["url"] == "http://test.com/1"
    assert filtered[0]["milestone"] == 72

    manager._mark_assignments_notified([filtered[0]])
    filtered_again = manager._filter_assignments([task_25h])
    assert len(filtered_again) == 0

def test_muted_courses(manager, mock_config):
    now = datetime.now()
    task_muted = {
        "url": "http://test.com/3",
        "title": "Bài tập XSTK",
        "course_name": "Xác suất thống kê",
        "deadline": (now + timedelta(hours=20)).isoformat(),
        "submission_status": "not_submitted",
        "is_open": True
    }
    
    task_normal = {
        "url": "http://test.com/4",
        "title": "Bài tập Toán",
        "course_name": "Toán 2",
        "deadline": (now + timedelta(hours=20)).isoformat(),
        "submission_status": "not_submitted",
        "is_open": True
    }

    filtered = manager._filter_assignments([task_muted, task_normal])
    assert len(filtered) == 1
    assert filtered[0]["assignment"]["url"] == "http://test.com/4"

def test_ignore_submitted(manager, mock_config):
    now = datetime.now()
    task_submitted = {
        "url": "http://test.com/5",
        "title": "Bài tập đã nộp",
        "course": "Cơ sở dữ liệu",
        "deadline": (now + timedelta(hours=5)).isoformat(),
        "submission_status": "submitted",
        "is_open": True
    }
    
    task_graded = {
        "url": "http://test.com/6",
        "title": "Bài tập đã chấm",
        "course": "Cơ sở dữ liệu",
        "deadline": (now + timedelta(hours=5)).isoformat(),
        "submission_status": "graded",
        "is_open": True
    }

    filtered = manager._filter_assignments([task_submitted, task_graded])
    assert len(filtered) == 0


def test_telegram_notifier_sanitizes_bot_token_from_error_logs(monkeypatch, caplog):
    import logging
    import httpx
    from notifiers import telegram as telegram_module
    from notifiers.telegram import TelegramNotifier

    secret_token = "987654321:SECRET_TELEGRAM_TOKEN_XYZ"
    monkeypatch.setattr(telegram_module.settings, "ENABLE_TELEGRAM", True)
    monkeypatch.setattr(telegram_module.settings, "TELEGRAM_BOT_TOKEN", secret_token)
    monkeypatch.setattr(telegram_module.settings, "TELEGRAM_CHAT_ID", "112233")

    sample_task = {
        "title": "Lab 1",
        "course_name": "CS101",
        "deadline": datetime.now() + timedelta(hours=4),
        "urgency": "critical",
        "type": "assignment",
    }

    req = httpx.Request("POST", f"https://api.telegram.org/bot{secret_token}/sendMessage")
    resp = httpx.Response(401, request=req, text="Unauthorized")
    http_err = httpx.HTTPStatusError("401 Unauthorized for url", request=req, response=resp)

    notifier = TelegramNotifier()
    with caplog.at_level(logging.ERROR):
        monkeypatch.setattr(
            telegram_module.httpx,
            "post",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(http_err),
        )
        assert notifier.notify([sample_task]) is False

        conn_err = httpx.ConnectError(
            f"connection failed to https://api.telegram.org/bot{secret_token}/sendMessage",
            request=req,
        )
        monkeypatch.setattr(
            telegram_module.httpx,
            "post",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(conn_err),
        )
        assert notifier.notify([sample_task]) is False

    assert secret_token not in caplog.text
    assert "HTTP 401" in caplog.text
    assert "ConnectError" in caplog.text


def test_discord_notifier_validates_https_and_sanitizes_webhook_logs(monkeypatch, caplog):
    import logging
    import httpx
    from notifiers import discord as discord_module
    from notifiers.discord import DiscordNotifier

    secret_webhook = "https://discord.com/api/webhooks/9999/DISCORD_SECRET_TOKEN_XYZ"
    sample_task = {
        "title": "Quiz 1",
        "course_name": "CS102",
        "deadline": datetime.now() + timedelta(hours=6),
        "urgency": "warning",
        "type": "quiz",
    }

    notifier = DiscordNotifier()
    monkeypatch.setattr(discord_module.settings, "ENABLE_DISCORD", True)

    post_calls = []
    monkeypatch.setattr(
        discord_module.httpx,
        "post",
        lambda *args, **kwargs: post_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        discord_module.settings,
        "DISCORD_WEBHOOK_URL",
        "http://discord.com/api/webhooks/9999/INSECURE_WEBHOOK",
    )
    with caplog.at_level(logging.ERROR):
        assert notifier.notify([sample_task]) is False
    assert post_calls == []
    assert "INSECURE_WEBHOOK" not in caplog.text

    caplog.clear()
    monkeypatch.setattr(discord_module.settings, "DISCORD_WEBHOOK_URL", secret_webhook)
    req = httpx.Request("POST", secret_webhook)
    resp = httpx.Response(500, request=req, text="Server Error")
    http_err = httpx.HTTPStatusError("500 error", request=req, response=resp)

    with caplog.at_level(logging.ERROR):
        monkeypatch.setattr(
            discord_module.httpx,
            "post",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(http_err),
        )
        assert notifier.notify([sample_task]) is False

        conn_err = httpx.ConnectError(f"failed {secret_webhook}", request=req)
        monkeypatch.setattr(
            discord_module.httpx,
            "post",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(conn_err),
        )
        assert notifier.notify([sample_task]) is False

    assert "DISCORD_SECRET_TOKEN_XYZ" not in caplog.text
    assert secret_webhook not in caplog.text
    assert "HTTP 500" in caplog.text
    assert "ConnectError" in caplog.text


def test_lazy_httpx_not_imported_on_cold_notifier_module_import():
    import subprocess

    code = (
        "import sys; sys.path.insert(0, 'src'); "
        "import notifiers.manager, notifiers.discord, notifiers.telegram; "
        "assert 'httpx' not in sys.modules, 'httpx should be lazy-imported'"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    assert proc.returncode == 0, proc.stderr


def test_notification_manager_concurrent_dispatch_delivers_once(tmp_path, mock_config, monkeypatch):
    import asyncio
    from notifiers.base import BaseNotifier

    monkeypatch.setattr(config, "NOTIFY_DND_ENABLE", False)
    mgr = NotificationManager(cache_file=str(tmp_path / "concurrent_notif.json"))
    mgr.notifiers = []

    deliveries = []

    class SlowNotifier(BaseNotifier):
        async def notify(self, assignments):
            await asyncio.sleep(0.03)
            deliveries.append(len(assignments))
            return True

    mgr.register(SlowNotifier())
    task = {
        "url": "https://courses.ut.edu.vn/mod/assign/view.php?id=999",
        "title": "Lab Race",
        "course_name": "OS",
        "deadline": (datetime.now() + timedelta(hours=10)).isoformat(),
        "submission_status": "not_submitted",
        "is_open": True,
    }

    async def _run():
        return await asyncio.gather(mgr.dispatch([task]), mgr.dispatch([task]))

    r1, r2 = asyncio.run(_run())
    assert deliveries == [1]
    assert (r1.delivered + r2.delivered) == 1


def test_notification_manager_namespace_and_clear_account_state_and_windows_close(tmp_path, monkeypatch):
    import config as app_cfg
    from notifiers.windows import WindowsNotifier

    monkeypatch.setattr(app_cfg, "_USER_DATA_DIR", tmp_path)
    mgr = NotificationManager(namespace="student-a")
    mgr.notifiers = []

    win = WindowsNotifier.__new__(WindowsNotifier)
    win._ensure_schedule_runtime()
    win._schedule_state_path = tmp_path / "win_sched.json"
    win._receipt_path = tmp_path / "win_receipts.json"
    win._class_receipt_path = tmp_path / "win_class_receipts.json"
    win._schedule_state = {"act:1": {"activity_id": "1", "scheduled_at": datetime.now().isoformat()}}
    win._start_schedule_worker()
    assert win._scheduler_thread is not None and win._scheduler_thread.is_alive()
    mgr.register(win)

    mgr.history.add([{"title": "A-only", "course": "C1", "url": "https://x/1"}], ["WindowsNotifier"])
    assert len(mgr.history.get_all()) == 1

    mgr.set_namespace("student-b")
    assert mgr.history.get_all() == []

    mgr.clear_account_state()
    assert win._schedule_state == {}

    mgr.close()
    assert win._scheduler_enabled is False
    assert win._scheduler_thread is None


def test_discord_telegram_email_grade_alerts_include_scores_and_reject_rogue_webhook_host(monkeypatch):
    import asyncio
    import smtplib
    from core.grade_monitor import GradeChange
    from notifiers import discord as discord_module
    from notifiers import telegram as telegram_module
    from notifiers import email as email_module
    from notifiers.discord import DiscordNotifier
    from notifiers.telegram import TelegramNotifier
    from notifiers.email import EmailNotifier

    monkeypatch.setattr(config, "NOTIFY_DND_ENABLE", False)

    # Empty list contract on TelegramNotifier
    monkeypatch.setattr(telegram_module.settings, "ENABLE_TELEGRAM", True)
    monkeypatch.setattr(telegram_module.settings, "TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setattr(telegram_module.settings, "TELEGRAM_CHAT_ID", "999")
    assert TelegramNotifier().notify([]) is True

    # Rogue webhook host rejected by DiscordNotifier
    monkeypatch.setattr(discord_module.settings, "ENABLE_DISCORD", True)
    monkeypatch.setattr(
        discord_module.settings,
        "DISCORD_WEBHOOK_URL",
        "https://evil.example/api/webhooks/1/secret",
    )
    assert DiscordNotifier().notify([{"title": "T", "course_name": "C"}]) is False

    # Capture payloads across Discord, Telegram, and Email during dispatch_grade_alert
    discord_payloads = []
    telegram_payloads = []
    sent_emails = []

    monkeypatch.setattr(
        discord_module.settings,
        "DISCORD_WEBHOOK_URL",
        "https://discord.com/api/webhooks/1234/validtoken",
    )
    def _fake_http_post(url, json=None, timeout=5):
        if "discord.com" in url:
            discord_payloads.append(json)
        elif "telegram.org" in url:
            telegram_payloads.append(json)

    monkeypatch.setattr(discord_module.httpx, "post", _fake_http_post)
    monkeypatch.setattr(email_module.settings, "ENABLE_GMAIL", True)
    monkeypatch.setattr(email_module.settings, "GMAIL_ADDRESS", "u@example.com")
    monkeypatch.setattr(email_module.settings, "GMAIL_APP_PASSWORD", "app-pwd")

    class DummySMTP:
        def __init__(self, *_a, **_kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def login(self, *_a):
            pass

        def send_message(self, msg):
            sent_emails.append(msg)

    monkeypatch.setattr(smtplib, "SMTP_SSL", DummySMTP)

    mgr = NotificationManager(namespace="grade-alert-user")
    mgr.notifiers = [DiscordNotifier(), TelegramNotifier(), EmailNotifier()]
    change = GradeChange(
        course_name="Hệ điều hành",
        item_name="Giữa kỳ",
        old_grade="7.0",
        new_grade="9.25",
        timestamp="2026-11-24T09:00:00",
    )
    res = asyncio.run(mgr.dispatch_grade_alert([change]))
    assert res.delivered == 1
    assert set(res.successful_channels) == {"DiscordNotifier", "TelegramNotifier", "EmailNotifier"}

    assert discord_payloads
    embed_fields = {f["name"]: f["value"] for f in discord_payloads[0]["embeds"][0]["fields"]}
    assert embed_fields["📉 Điểm cũ"] == "7.0"
    assert embed_fields["📈 Điểm mới"] == "9.25"

    assert telegram_payloads
    assert "7.0" in telegram_payloads[0]["text"]
    assert "9.25" in telegram_payloads[0]["text"]
    assert "Giữa kỳ" in telegram_payloads[0]["text"]

    assert sent_emails
    raw_email = sent_emails[0].as_string()
    assert "7.0" in raw_email and "9.25" in raw_email
