"""
ntfy.sh push notification notifier.
Sends push notifications via ntfy.sh HTTP API — works on any platform.
Students install the ntfy.sh Android app and subscribe to their topic.

No server, no Firebase, no Google account needed.
"""
import logging
import httpx
from notifiers.base import BaseNotifier
from config import settings

logger = logging.getLogger(__name__)


class NtfyNotifier(BaseNotifier):
    """
    Push notifications via ntfy.sh (https://ntfy.sh).
    
    Configuration:
        settings.NTFY_TOPIC: str — Topic name (e.g., "uth-student-12345")
        settings.NTFY_SERVER: str — Server URL (default: https://ntfy.sh)
    """

    def __init__(self):
        self._topic = getattr(settings, 'NTFY_TOPIC', '')
        self._server = getattr(settings, 'NTFY_SERVER', 'https://ntfy.sh')
        if self._topic:
            logger.info("NtfyNotifier: configured for topic '%s'", self._topic)

    def notify(self, assignments) -> bool:
        """Send push notifications via ntfy.sh for each assignment."""
        if not self._topic:
            logger.debug("NtfyNotifier: no topic configured, skipping")
            return False

        if not assignments:
            return True

        url = f"{self._server.rstrip('/')}/{self._topic}"
        success = False

        for a in assignments:
            title = getattr(a, 'title', '') or (a.get('title', '') if isinstance(a, dict) else '')
            course = getattr(a, 'course_name', '') or (a.get('course', '') if isinstance(a, dict) else '')
            remaining = getattr(a, 'remaining', '') or (a.get('remaining', '') if isinstance(a, dict) else '')
            activity_url = getattr(a, 'url', '') or (a.get('url', '') if isinstance(a, dict) else '')

            body = f"📚 {course}"
            if remaining:
                body += f"\n⏰ Còn {remaining}"

            headers = {
                "Title": f"UTHelper: {title}",
                "Priority": "high",
                "Tags": "warning,school",
            }
            if activity_url:
                headers["Click"] = activity_url

            try:
                resp = httpx.post(url, content=body.encode('utf-8'), headers=headers, timeout=10)
                if resp.status_code == 200:
                    success = True
                else:
                    logger.warning("ntfy.sh returned status %d for topic '%s'", resp.status_code, self._topic)
            except Exception as e:
                logger.warning("ntfy.sh push failed: %s", e)

        return success
