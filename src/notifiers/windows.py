# Windows Desktop Toast Notifications
# Requires windows-toasts library, with fallback to pystray balloon
import logging
import os
import sys
import ctypes
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import List
from models import Assignment
from config import BASE_DIR, _USER_DATA_DIR
from core.notification_types import ScheduleResult, ScheduledReminder
from core.safe_file_io import SafeFileIO
from core.time_utils import format_remaining_time
from core.display_utils import get_type_display, get_urgency_display, urgency_str, clean_course_name
from .base import BaseNotifier

logger = logging.getLogger(__name__)


def _packaged_aumid() -> str:
    """Resolve the current MSIX AUMID, or return an empty string if unpackaged."""
    if sys.platform != "win32":
        return ""
    try:
        length = ctypes.c_uint32(0)
        get_family = ctypes.windll.kernel32.GetCurrentPackageFamilyName
        result = get_family(ctypes.byref(length), None)
        # APPMODEL_ERROR_NO_PACKAGE
        if result == 15700 or length.value == 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length.value)
        if get_family(ctypes.byref(length), buffer) != 0:
            return ""
        return f"{buffer.value}!UTHelper"
    except (AttributeError, OSError):
        return ""


def _get(obj, key, default=''):
    """Get attribute from both Assignment objects and dicts."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class WindowsNotifier(BaseNotifier):
    _SCHEDULE_RETRY_MINUTES = 5

    def __init__(self, tray_app=None, *, start_scheduler: bool = True):
        self.tray_app = tray_app
        self.app_id = "UTHelper"
        self.aumid = _packaged_aumid() or "UTHelper.App"
        self.is_packaged = self.aumid != "UTHelper.App"
        self.last_error = ""
        self.last_schedule_error = ""
        self.last_scheduled_delivery_at = ""
        self.scheduled_delivered = 0
        self._schedule_state_path = Path(_USER_DATA_DIR) / "windows_notification_schedules.json"
        self._schedule_condition = threading.Condition(threading.RLock())
        self._schedule_state: dict[str, dict] = SafeFileIO.read_json_safe(
            self._schedule_state_path, dict
        )
        self._scheduler_stopping = False
        self._scheduler_thread: threading.Thread | None = None
        self._scheduler_enabled = start_scheduler
        self._icon_path = os.path.abspath(os.path.join(BASE_DIR, "src", "assets", "icon.ico"))
        if not os.path.exists(self._icon_path):
            self._icon_path = os.path.abspath(os.path.join(BASE_DIR, "assets", "icon.ico"))
            if not os.path.exists(self._icon_path):
                self._icon_path = os.path.abspath(os.path.join(BASE_DIR, "src", "assets", "icon.png"))

        if not self.is_packaged:
            try:
                self._ensure_shortcut()
            except Exception as exc:
                logger.warning("Shortcut AUMID setup failed: %r", exc)

        if self._scheduler_enabled:
            self._start_schedule_worker()

    def _ensure_shortcut(self):
        """
        Tạo Shortcut trong Start Menu và gắn AppUserModelID để Windows 10/11 
        nhận diện đúng Tên ứng dụng và Icon ở khu vực Header (Attribution Area).
        """
        try:
            import win32com.client
            import win32com.propsys.propsys as propsys
            import win32com.propsys.pscon as pscon
            import pythoncom
            from win32com.shell import shellcon

            appdata = os.environ.get('APPDATA')
            if not appdata:
                return

            programs_path = os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs")
            shortcut_path = os.path.join(programs_path, f"{self.app_id}.lnk")

            # Tạo file shortcut cơ bản
            wsh_shell = win32com.client.Dispatch("WScript.Shell")
            shortcut = wsh_shell.CreateShortcut(shortcut_path)
            shortcut.TargetPath = sys.executable
            # Tránh console popup khi click
            if hasattr(sys, 'frozen') or sys.executable.endswith("pythonw.exe"):
                pass
            shortcut.IconLocation = self._icon_path
            shortcut.Save()

            # Gắn AppUserModelID cho file shortcut vừa tạo! Đây là bước cực kỳ quan trọng cho Toast.
            props = propsys.SHGetPropertyStoreFromParsingName(
                shortcut_path, None, shellcon.GPS_READWRITE, propsys.IID_IPropertyStore
            )
            prop_variant = propsys.PROPVARIANTType(self.aumid, pythoncom.VT_LPWSTR)
            props.SetValue(pscon.PKEY_AppUserModel_ID, prop_variant)
            props.Commit()
        except Exception as e:
            logger.warning("Shortcut AUMID setup failed: %r", e)

    def notify(self, assignments: List[Assignment]):
        """Gửi thông báo desktop sử dụng windows-toasts."""
        if not assignments:
            return True

        # Build concise, scannable notification text
        if len(assignments) == 1:
            a = assignments[0]
            title_raw = _get(a, 'title', 'Bài tập mới')
            course_raw = _get(a, 'course_name', '') or _get(a, 'course', '')
            course = clean_course_name(course_raw) if course_raw else course_raw

            # Type & urgency display
            task_type = _get(a, 'type', '') or _get(a, 'event_type', '')
            type_emoji, type_label = get_type_display(task_type)
            urg_emoji, urg_label = get_urgency_display(_get(a, 'urgency', 'safe'))
            remaining = format_remaining_time(_get(a, 'deadline', None))

            title = f"{urg_emoji} {title_raw}"
            msg = f"📚 {course} · {type_emoji} {type_label}\n⏰ {remaining}"
        else:
            # Count by urgency
            critical = sum(1 for a in assignments if urgency_str(_get(a, 'urgency', 'safe')) == 'critical')
            warning = sum(1 for a in assignments if urgency_str(_get(a, 'urgency', 'safe')) == 'warning')
            other = len(assignments) - critical - warning

            title = f"📋 UTHelper · {len(assignments)} hoạt động"

            parts = []
            if critical:
                parts.append(f"🔴 {critical} khẩn cấp")
            if warning:
                parts.append(f"🟠 {warning} sắp hạn")
            if other:
                parts.append(f"🟢 {other} khác")
            msg = " · ".join(parts)

        try:
            from windows_toasts import InteractableWindowsToaster, Toast
            
            toaster = InteractableWindowsToaster(self.app_id, notifierAUMID=self.aumid)
            toast = Toast()
            toast.text_fields = [title, msg]
            if len(assignments) == 1:
                activity_url = str(_get(assignments[0], "url", "") or "")
                if activity_url.startswith(("https://", "http://")):
                    toast.launch_action = activity_url
            
            toaster.show_toast(toast)
            logger.info(f"[Windows] Đã gửi thông báo: {msg}")
            self.last_error = ""
            return True
        except Exception as e:
            self.last_error = str(e)
            logger.warning(f"windows-toasts lỗi, dùng tray mặc định: {e}")
            if self.tray_app:
                self.tray_app.notify(title, msg)
                return True
            else:
                logger.info(f"THÔNG BÁO (Log): {title} - {msg}")
                return False

    def reconcile_schedules(
        self, reminders: list[ScheduledReminder]
    ) -> ScheduleResult:
        """Persist reminders and wake the tray-owned Windows scheduler.

        Windows does not expose the same AlarmManager API as Android through
        the current Flet adapter.  The autostart/tray process therefore owns a
        durable, non-network scheduler and delivers reminders at their known
        deadline milestones without waiting for the next Moodle poll.
        """
        self._ensure_schedule_runtime()
        result = ScheduleResult(desired=len(reminders))
        desired = {
            reminder.state_key: self._serialize_reminder(reminder)
            for reminder in reminders
        }

        with self._schedule_condition:
            previous = self._schedule_state
            result.cancelled = sum(
                1
                for key, value in previous.items()
                if key not in desired or not self._same_schedule(value, desired.get(key))
            )
            result.scheduled = sum(
                1
                for key, value in desired.items()
                if key not in previous or not self._same_schedule(previous.get(key), value)
            )

            # Preserve retry metadata only when the actual reminder is unchanged.
            for key, value in desired.items():
                old_value = previous.get(key)
                if self._same_schedule(old_value, value) and old_value:
                    value["retry_at"] = old_value.get("retry_at", "")
            self._schedule_state = desired
            try:
                self._save_schedule_state_locked()
                self.last_schedule_error = ""
            except Exception as exc:
                result.failed += 1
                result.errors.append(str(exc))
                self.last_schedule_error = str(exc)
                logger.warning("Cannot persist Windows reminder schedules: %s", exc)
            self._schedule_condition.notify_all()

        self._start_schedule_worker()
        return result

    def cancel_activity(self, activity_id: str) -> int:
        """Cancel all pending Windows reminders for one Moodle activity."""
        self._ensure_schedule_runtime()
        with self._schedule_condition:
            matching = [
                key
                for key, value in self._schedule_state.items()
                if str(value.get("activity_id", "")) == str(activity_id)
            ]
            for key in matching:
                self._schedule_state.pop(key, None)
            if matching:
                self._save_schedule_state_locked()
                self._schedule_condition.notify_all()
            return len(matching)

    def get_diagnostics(self) -> dict:
        """Return scheduler diagnostics without exposing mutable state."""
        self._ensure_schedule_runtime()
        with self._schedule_condition:
            return {
                "pending_schedules": len(self._schedule_state),
                "scheduler_running": bool(
                    self._scheduler_thread and self._scheduler_thread.is_alive()
                ),
                "scheduled_delivered": self.scheduled_delivered,
                "last_scheduled_delivery_at": self.last_scheduled_delivery_at,
                "last_schedule_error": self.last_schedule_error,
                "last_toast_error": self.last_error,
                "is_packaged": self.is_packaged,
                "aumid": self.aumid,
            }

    def close(self) -> None:
        """Stop the in-process scheduler during an orderly tray shutdown."""
        self._ensure_schedule_runtime()
        with self._schedule_condition:
            self._scheduler_stopping = True
            self._schedule_condition.notify_all()
        thread = self._scheduler_thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=1)

    def _ensure_schedule_runtime(self) -> None:
        """Initialize scheduler fields for tests constructing via ``__new__``."""
        if hasattr(self, "_schedule_condition"):
            return
        self.last_schedule_error = ""
        self.last_scheduled_delivery_at = ""
        self.scheduled_delivered = 0
        self._schedule_state_path = Path(_USER_DATA_DIR) / "windows_notification_schedules.json"
        self._schedule_condition = threading.Condition(threading.RLock())
        self._schedule_state = SafeFileIO.read_json_safe(
            self._schedule_state_path, dict
        )
        self._scheduler_stopping = False
        self._scheduler_thread = None
        self._scheduler_enabled = True

    def _start_schedule_worker(self) -> None:
        self._ensure_schedule_runtime()
        with self._schedule_condition:
            if not self._scheduler_enabled:
                return
            if self._scheduler_thread and self._scheduler_thread.is_alive():
                return
            self._scheduler_stopping = False
            self._scheduler_thread = threading.Thread(
                target=self._schedule_worker,
                daemon=True,
                name="windows-reminder-scheduler",
            )
            self._scheduler_thread.start()

    def _schedule_worker(self) -> None:
        pythoncom = None
        try:
            try:
                import pythoncom as _pythoncom

                pythoncom = _pythoncom
                pythoncom.CoInitialize()
            except ImportError:
                pass

            while True:
                with self._schedule_condition:
                    if self._scheduler_stopping:
                        return
                    key, value, due_at = self._next_pending_locked()
                    if key is None or value is None or due_at is None:
                        self._schedule_condition.wait()
                        continue
                    wait_seconds = (due_at - datetime.now()).total_seconds()
                    if wait_seconds > 0:
                        self._schedule_condition.wait(timeout=wait_seconds)
                        continue

                self._deliver_pending(key, value)
        except Exception as exc:
            self.last_schedule_error = str(exc)
            logger.exception("Windows reminder scheduler stopped unexpectedly")
        finally:
            if pythoncom is not None:
                pythoncom.CoUninitialize()

    def _next_pending_locked(self) -> tuple[str | None, dict | None, datetime | None]:
        next_item: tuple[str, dict, datetime] | None = None
        invalid: list[str] = []
        for key, value in self._schedule_state.items():
            raw_due = value.get("retry_at") or value.get("scheduled_at")
            try:
                due_at = datetime.fromisoformat(str(raw_due))
            except (TypeError, ValueError):
                invalid.append(key)
                continue
            if next_item is None or due_at < next_item[2]:
                next_item = (key, value, due_at)
        for key in invalid:
            self._schedule_state.pop(key, None)
        if invalid:
            self._save_schedule_state_locked()
        return next_item or (None, None, None)

    def _deliver_pending(self, key: str, value: dict) -> None:
        now = datetime.now()
        deadline = self._parse_iso(value.get("deadline"))
        if deadline is not None and deadline <= now:
            self._remove_schedule_if_current(key, value)
            return

        delivered = self.notify([self._scheduled_activity(value)]) is True
        with self._schedule_condition:
            if self._schedule_state.get(key) != value:
                return
            if delivered:
                self._schedule_state.pop(key, None)
                self.scheduled_delivered += 1
                self.last_scheduled_delivery_at = now.isoformat()
                self.last_schedule_error = ""
            else:
                retry_at = now + timedelta(minutes=self._SCHEDULE_RETRY_MINUTES)
                if deadline is not None and retry_at >= deadline:
                    self._schedule_state.pop(key, None)
                else:
                    value["retry_at"] = retry_at.isoformat()
                    self.last_schedule_error = self.last_error or "toast delivery failed"
            self._save_schedule_state_locked()
            self._schedule_condition.notify_all()

    def _remove_schedule_if_current(self, key: str, value: dict) -> None:
        with self._schedule_condition:
            if self._schedule_state.get(key) == value:
                self._schedule_state.pop(key, None)
                self._save_schedule_state_locked()

    def _save_schedule_state_locked(self) -> None:
        if not SafeFileIO.write_json_atomic(
            self._schedule_state_path, self._schedule_state
        ):
            raise OSError("atomic Windows schedule state write failed")

    @staticmethod
    def _same_schedule(left: dict | None, right: dict | None) -> bool:
        if not left or not right:
            return False
        ignored = {"retry_at"}
        return {
            key: value for key, value in left.items() if key not in ignored
        } == {
            key: value for key, value in right.items() if key not in ignored
        }

    @staticmethod
    def _serialize_reminder(reminder: ScheduledReminder) -> dict:
        activity = reminder.activity
        return {
            "activity_id": activity.activity_id,
            "activity_key": activity.key,
            "title": activity.title,
            "course_name": activity.course_name,
            "event_type": activity.event_type,
            "deadline": activity.deadline.isoformat() if activity.deadline else "",
            "url": activity.url,
            "milestone": reminder.milestone,
            "scheduled_at": reminder.scheduled_at.isoformat(),
            "revision": activity.revision,
            "retry_at": "",
        }

    @staticmethod
    def _scheduled_activity(value: dict) -> dict:
        return {
            "id": value.get("activity_id", ""),
            "title": value.get("title", "Hoạt động sắp đến hạn"),
            "course_name": value.get("course_name", ""),
            "event_type": value.get("event_type", "other"),
            "deadline": value.get("deadline", ""),
            "url": value.get("url", ""),
            "urgency": "critical",
        }

    @staticmethod
    def _parse_iso(value) -> datetime | None:
        try:
            return datetime.fromisoformat(str(value)) if value else None
        except (TypeError, ValueError):
            return None
