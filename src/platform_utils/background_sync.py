"""Optional bridge to the native Android background synchronization service."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


class AndroidBackgroundBridge:
    def __init__(self, page):
        self.page = page
        self.service = None
        try:
            from flet_uth_background_sync import AndroidBackgroundSync

            self.service = AndroidBackgroundSync()
            page.services.append(self.service)
        except (ImportError, AttributeError, RuntimeError) as exc:
            logger.warning("Android native background sync unavailable: %s", exc)

    @property
    def available(self) -> bool:
        return self.service is not None

    @staticmethod
    def settings_payload() -> dict[str, Any]:
        from config import get_sync_interval_minutes, settings

        interval_minutes = get_sync_interval_minutes()
        return {
            "enabled": bool(settings.BACKGROUND_CHECK_ANDROID and interval_minutes > 0),
            "interval_minutes": interval_minutes,
            "fetch_months": int(settings.FETCH_MONTHS),
            "notify_types": list(settings.NOTIFY_TYPES or []),
            "muted_courses": list(settings.NOTIFY_MUTED_COURSES or []),
            "ignore_submitted": bool(settings.NOTIFY_IGNORE_SUBMITTED),
            "countdown_minutes": list(settings.NOTIFY_MILESTONES_MINUTES or []),
            "dnd_enabled": bool(settings.NOTIFY_DND_ENABLE),
            "dnd_start": int(settings.NOTIFY_DND_START),
            "dnd_end": int(settings.NOTIFY_DND_END),
        }

    async def configure(self, token: str = "") -> dict[str, Any]:
        if not self.service:
            return {}
        from config import settings

        if token:
            await self.service.set_credentials(settings.MOODLE_BASE_URL, token)
        return await self.service.configure(self.settings_payload())

    async def import_activities(
        self, activities: list[dict[str, Any]], *, authoritative: bool = True
    ) -> dict[str, Any]:
        if not self.service:
            return {}
        return await self.service.import_activities(activities, authoritative)

    async def cached_activities(self) -> list[dict[str, Any]]:
        if not self.service:
            return []
        values = await self.service.get_cached_activities() or []
        normalized = []
        for raw in values:
            item = dict(raw)
            epoch = item.pop("deadline_epoch", None)
            if epoch and not item.get("deadline"):
                try:
                    item["deadline"] = datetime.fromtimestamp(float(epoch)).isoformat()
                except (TypeError, ValueError, OSError):
                    continue
            normalized.append(item)
        return normalized

    async def sync_now(self, force: bool = False) -> str:
        if not self.service:
            return ""
        return await self.service.sync_now(force)

    async def diagnostics(self) -> dict[str, Any]:
        if not self.service:
            return {}
        return await self.service.get_diagnostics() or {}

    async def request_exact_alarm_access(self) -> bool:
        if not self.service:
            return False
        return bool(await self.service.request_exact_alarm_access())

    async def install_update(
        self, url: str, sha256: str, expected_size: int = 0
    ) -> dict[str, Any]:
        if not self.service:
            return {"status": "unavailable"}
        return await self.service.install_update(url, sha256, expected_size)

    async def logout(self) -> None:
        if self.service:
            await self.service.logout()


def create_android_background_bridge(page):
    from platform_utils import IS_ANDROID

    return AndroidBackgroundBridge(page) if IS_ANDROID else None
