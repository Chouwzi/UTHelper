"""Python facade for the native Android background synchronization service."""

from dataclasses import dataclass
from typing import Any

from flet.controls.base_control import control
from flet.controls.services.service import Service


@control("UthBackgroundSync")
@dataclass
class AndroidBackgroundSync(Service):
    """Configure and inspect the native worker without running Python in it."""

    async def configure(self, settings: dict[str, Any]) -> dict[str, Any]:
        return await self._invoke_method("configure", {"settings": settings})

    async def set_credentials(self, base_url: str, token: str) -> None:
        await self._invoke_method(
            "set_credentials", {"base_url": base_url, "token": token}
        )

    async def schedule_periodic(self, interval_minutes: int) -> dict[str, Any]:
        return await self._invoke_method(
            "schedule_periodic", {"interval_minutes": interval_minutes}
        )

    async def sync_now(self, force: bool = False) -> str:
        return await self._invoke_method("sync_now", {"force": force})

    async def get_cached_activities(self) -> list[dict[str, Any]]:
        return await self._invoke_method("get_cached_activities")

    async def import_activities(
        self,
        activities: list[dict[str, Any]],
        authoritative: bool = True,
    ) -> dict[str, Any]:
        """Commit a foreground Moodle fetch into the native source of truth."""
        return await self._invoke_method(
            "import_activities",
            {"activities": activities, "authoritative": authoritative},
        )

    async def get_diagnostics(self) -> dict[str, Any]:
        return await self._invoke_method("get_diagnostics")

    async def reconcile_cached(self) -> dict[str, Any]:
        return await self._invoke_method("reconcile_cached")

    async def request_exact_alarm_access(self) -> bool:
        """Open Android's special-access screen after an explicit user action."""
        return await self._invoke_method("request_exact_alarm_access")

    async def show_notification(
        self,
        notification_id: int,
        title: str,
        body: str,
        payload: str = "",
    ) -> bool:
        return bool(
            await self._invoke_method(
                "show_notification",
                {
                    "notification_id": notification_id,
                    "title": title,
                    "body": body,
                    "payload": payload,
                },
            )
        )

    async def install_update(
        self,
        url: str,
        sha256: str,
        expected_size: int,
        expected_package_id: str,
        expected_version_code: int,
        expected_certificate_sha256: str,
    ) -> dict[str, Any]:
        return await self._invoke_method(
            "install_update",
            {
                "url": url,
                "sha256": sha256,
                "expected_size": expected_size,
                "expected_package_id": expected_package_id,
                "expected_version_code": expected_version_code,
                "expected_certificate_sha256": expected_certificate_sha256,
            },
        )

    async def cancel_update(self) -> None:
        await self._invoke_method("cancel_update")

    async def cancel_periodic(self) -> None:
        await self._invoke_method("cancel_periodic")

    async def logout(self) -> None:
        await self._invoke_method("logout")
