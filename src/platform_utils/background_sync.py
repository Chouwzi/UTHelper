"""Optional bridge to native mobile background/local-notification services."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Iterable, Mapping
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
        from core.demo_sandbox import is_demo_account

        interval_minutes = get_sync_interval_minutes()
        demo_active = is_demo_account(getattr(settings, "UTH_USERNAME", ""))
        return {
            "enabled": bool(
                not demo_active
                and settings.BACKGROUND_CHECK_ANDROID
                and interval_minutes > 0
            ),
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

    async def configure(
        self,
        token: str = "",
        *,
        token_origin: str = "",
    ) -> dict[str, Any]:
        if not self.service:
            return {}
        from config import settings
        from core.demo_sandbox import is_demo_account
        from core.moodle_sites import moodle_site_from_origin

        configured_site = moodle_site_from_origin(settings.MOODLE_BASE_URL)
        issuing_site = moodle_site_from_origin(token_origin)
        if (
            not is_demo_account(getattr(settings, "UTH_USERNAME", ""))
            and token
            and configured_site is not None
            and issuing_site == configured_site
        ):
            await self.service.set_credentials(configured_site.origin, token)
        else:
            await self.service.logout()
        return await self.service.configure(self.settings_payload())

    async def import_activities(
        self, activities: list[dict[str, Any]], *, authoritative: bool = True
    ) -> dict[str, Any]:
        if not self.service:
            return {}
        from config import settings
        from core.demo_sandbox import is_demo_account

        if is_demo_account(getattr(settings, "UTH_USERNAME", "")):
            return {"imported": 0, "scheduled": 0, "cancelled": 0}
        return await self.service.import_activities(activities, authoritative)

    async def schedule_class_sessions(
        self, sessions: list[dict[str, Any]], *, enabled: bool
    ) -> dict[str, Any]:
        """Replace locally scheduled reminders for the current Portal snapshot."""
        if not self.service:
            return {}
        from config import settings
        from core.demo_sandbox import is_demo_account

        if is_demo_account(getattr(settings, "UTH_USERNAME", "")):
            return await self.service.schedule_class_sessions([], False)
        return await self.service.schedule_class_sessions(sessions, enabled)

    async def cached_activities(self) -> list[dict[str, Any]]:
        if not self.service:
            return []
        from config import settings
        from core.demo_sandbox import is_demo_account

        if is_demo_account(getattr(settings, "UTH_USERNAME", "")):
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
        from config import settings
        from core.demo_sandbox import is_demo_account

        if is_demo_account(getattr(settings, "UTH_USERNAME", "")):
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
        self,
        local_path: str,
        sha256: str,
        expected_size: int,
        expected_package_id: str,
        expected_version_code: int,
        expected_certificate_sha256: str,
    ) -> dict[str, Any]:
        if not self.service:
            return {"status": "unavailable"}
        return await self.service.install_update(
            local_path,
            sha256,
            expected_size,
            expected_package_id,
            expected_version_code,
            expected_certificate_sha256,
        )

    async def cancel_update(self) -> None:
        if self.service:
            await self.service.cancel_update()

    async def start_update_download(self, *identity) -> dict[str, Any]:
        if not self.service:
            return {"status": "unavailable"}
        return await self.service.start_update_download(*identity)

    async def get_update_download_status(self, *identity) -> dict[str, Any]:
        if not self.service:
            return {"status": "unavailable"}
        return await self.service.get_update_download_status(*identity)

    async def cancel_update_download(self, *identity) -> dict[str, Any]:
        if not self.service:
            return {"status": "unavailable"}
        await self.service.cancel_update_download(*identity)
        return {"status": "cancelled"}

    async def show_notification(
        self,
        *,
        notification_id: int,
        title: str,
        body: str,
        payload: str = "",
    ) -> bool:
        if not self.service:
            return False
        return bool(
            await self.service.show_notification(
                notification_id, title, body, payload
            )
        )

    async def logout(self) -> None:
        if self.service:
            await self.service.logout()

    async def read_app_secrets(self) -> dict[str, str]:
        if not self.service:
            return {}
        read_fn = getattr(self.service, "read_app_secrets", None)
        if not callable(read_fn):
            return {}
        self._register_lazy_recovery_factory()
        raw = await read_fn()
        if not isinstance(raw, dict):
            return {}
        return {
            str(k): str(v)
            for k, v in raw.items()
            if isinstance(k, str) and v is not None and str(v) != ""
        }

    def _register_lazy_recovery_factory(self) -> None:
        """Allow off-loop save_settings() to recover if startup vault hydration fails."""
        if not self.service or not callable(getattr(self.service, "write_app_secrets", None)):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop_thread_id = threading.get_ident()
        try:
            import config

            if config._requires_native_secret_backend():
                def _lazy_factory() -> AndroidSecretBackend | None:
                    if not self.available or loop.is_closed():
                        return None
                    backend = AndroidSecretBackend(
                        loop,
                        self,
                        initial_secrets=None,
                        loop_thread_id=loop_thread_id,
                    )
                    if not backend.ensure_hydrated():
                        return None
                    snap = backend.snapshot_secrets(config._SECRET_FIELDS.values())
                    if snap:
                        config.hydrate_secrets(snap, overwrite_non_empty=False)
                    return backend

                config.register_secret_backend_factory(_lazy_factory)
        except Exception:
            logger.debug("Could not register lazy mobile secret backend factory", exc_info=True)

    async def write_app_secrets(self, secrets: Mapping[str, str]) -> bool:
        if not self.service:
            return False
        write_fn = getattr(self.service, "write_app_secrets", None)
        if not callable(write_fn):
            return False
        payload = {
            str(k): str(v)
            for k, v in secrets.items()
            if isinstance(k, str) and v is not None and str(v) != ""
        }
        return bool(await write_fn(payload))

    async def clear_app_secrets(self) -> bool:
        if not self.service:
            return False
        clear_fn = getattr(self.service, "clear_app_secrets", None)
        if not callable(clear_fn):
            return False
        return bool(await clear_fn())


class AndroidSecretBackend:
    """Thread-safe sync-to-async secret store backed by AndroidBackgroundBridge."""

    # Native vault writes replace the full secret map atomically, so a failed or
    # timed-out write_all() must not trigger a second blocking rollback call.
    atomic_batch_write: bool = True

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        bridge: AndroidBackgroundBridge,
        initial_secrets: Mapping[str, str] | None = None,
        *,
        timeout: float = 5.0,
        loop_thread_id: int | None = None,
    ) -> None:
        self._loop = loop
        self._bridge = bridge
        self._timeout = max(0.1, float(timeout))
        self._loop_thread_id = (
            loop_thread_id
            if loop_thread_id is not None
            else getattr(loop, "_thread_id", None)
        )
        self._lock = threading.RLock()
        self._cache: dict[str, str] = {}
        self._hydrated: bool = False
        if initial_secrets is not None:
            self.replace_cached_secrets(initial_secrets)

    def replace_cached_secrets(self, values: Mapping[str, str]) -> None:
        with self._lock:
            self._cache = {
                str(k): str(v)
                for k, v in values.items()
                if isinstance(k, str) and v is not None and str(v) != ""
            }
            self._hydrated = True

    def ensure_hydrated(self) -> bool:
        """Lazily read native vault secrets once off the event-loop thread."""
        with self._lock:
            if self._hydrated:
                return True
        if self._is_on_loop_thread():
            logger.warning(
                "Refusing blocking mobile secret read on the event loop thread"
            )
            return False
        if self._loop.is_closed():
            logger.warning("Mobile secret read failed: event loop is closed")
            return False
        future = asyncio.run_coroutine_threadsafe(
            self._bridge.read_app_secrets(),
            self._loop,
        )
        try:
            values = future.result(timeout=self._timeout)
        except Exception as exc:
            future.cancel()
            logger.warning(
                "Mobile secret vault lazy hydration failed: %s",
                type(exc).__name__,
            )
            return False
        if not isinstance(values, Mapping):
            return False
        self.replace_cached_secrets(values)
        return True

    def _is_on_loop_thread(self) -> bool:
        try:
            if asyncio.get_running_loop() is self._loop:
                return True
        except RuntimeError:
            pass
        tid = self._loop_thread_id
        if tid is None:
            tid = getattr(self._loop, "_thread_id", None)
        return tid is not None and threading.get_ident() == tid

    def read_secret(self, key: str) -> str:
        with self._lock:
            return self._cache.get(key, "")

    def snapshot_secrets(self, keys: Iterable[str]) -> dict[str, str] | None:
        if not self.ensure_hydrated():
            return None
        with self._lock:
            return {key: self._cache.get(key, "") for key in keys}

    def write_all(self, values: Mapping[str, str]) -> bool:
        normalized = {
            str(k): str(v)
            for k, v in values.items()
            if isinstance(k, str) and v is not None and str(v) != ""
        }
        if self._is_on_loop_thread():
            logger.warning(
                "Refusing blocking Android secret write on the event loop thread"
            )
            return False
        if self._loop.is_closed():
            logger.warning("Android secret write failed: event loop is closed")
            return False
        future = asyncio.run_coroutine_threadsafe(
            self._bridge.write_app_secrets(normalized),
            self._loop,
        )
        try:
            ok = bool(future.result(timeout=self._timeout))
        except Exception as exc:
            future.cancel()
            logger.warning(
                "Android secret vault write failed: %s", type(exc).__name__
            )
            return False
        if ok:
            with self._lock:
                self._cache = dict(normalized)
                self._hydrated = True
        return ok

    def write_secret(self, key: str, value: str) -> bool:
        with self._lock:
            updated = dict(self._cache)
            if value:
                updated[key] = str(value)
            else:
                updated.pop(key, None)
        return self.write_all(updated)

    def restore_secrets(self, snapshot: Mapping[str, str]) -> bool:
        return self.write_all(snapshot)


def create_android_background_bridge(page):
    from platform_utils import IS_ANDROID, IS_IOS

    return AndroidBackgroundBridge(page) if IS_ANDROID or IS_IOS else None


# Platform-neutral name for new call sites; keep the old factory for compatibility.
create_mobile_background_bridge = create_android_background_bridge
