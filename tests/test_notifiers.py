import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))
import json
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
