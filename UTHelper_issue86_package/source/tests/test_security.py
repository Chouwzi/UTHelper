import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from core.security import HTMLSanitizer


def test_sanitizer_removes_srcdoc_content():
    html = '<iframe srcdoc="<script>alert(1)</script>"></iframe><p>safe</p>'

    sanitized = HTMLSanitizer.sanitize(html)

    assert "srcdoc" not in sanitized.lower()
    assert "script" not in sanitized.lower()
    assert "safe" in sanitized


def test_sanitizer_removes_unsafe_url_schemes():
    html = (
        '<a href="vbscript:msgbox(1)">bad</a>'
        '<img src="data:text/html,<script>alert(1)</script>">'
        '<a href="https://courses.ut.edu.vn/mod/assign/view.php?id=1">safe</a>'
    )

    sanitized = HTMLSanitizer.sanitize(html)

    assert "vbscript:" not in sanitized.lower()
    assert "data:text/html" not in sanitized.lower()
    assert "https://courses.ut.edu.vn/mod/assign/view.php?id=1" in sanitized


# ─── Credential Security Tests ───────────────────────────────────

def test_secrets_excluded_from_json_dump():
    """All sensitive fields must be excluded from model_dump (JSON serialization)."""
    from config import Settings
    s = Settings(
        UTH_PASSWORD="secret_pass",
        MOODLE_WS_TOKEN="secret_token",
        GMAIL_APP_PASSWORD="secret_gmail",
        TELEGRAM_BOT_TOKEN="secret_telegram",
        DISCORD_WEBHOOK_URL="secret_discord",
    )
    dump = s.model_dump()
    
    # None of these should appear in the JSON dump
    secret_fields = [
        'UTH_PASSWORD', 'MOODLE_WS_TOKEN', 'GMAIL_APP_PASSWORD',
        'TELEGRAM_BOT_TOKEN', 'DISCORD_WEBHOOK_URL',
    ]
    for field in secret_fields:
        assert field not in dump, f"{field} should be excluded from model_dump()"
    
    # And none of the values should appear either
    import json
    json_str = json.dumps(dump)
    for secret_value in ['secret_pass', 'secret_token', 'secret_gmail', 'secret_telegram', 'secret_discord']:
        assert secret_value not in json_str, f"Secret value '{secret_value}' leaked into JSON"


def test_non_secret_fields_present_in_dump():
    """Non-secret fields should still be present in model_dump."""
    from config import Settings
    s = Settings(UTH_USERNAME="testuser", CHECK_INTERVAL_MINUTES=30)
    dump = s.model_dump()
    
    assert 'UTH_USERNAME' in dump
    assert dump['UTH_USERNAME'] == 'testuser'
    assert dump['CHECK_INTERVAL_MINUTES'] == 30
