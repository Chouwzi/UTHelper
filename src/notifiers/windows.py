# Windows Desktop Toast Notifications
# Requires windows-toasts library, with fallback to pystray balloon
import logging
import os
import sys
from typing import List
from models import Assignment
from config import BASE_DIR
from core.time_utils import format_remaining_time
from core.display_utils import get_type_display, get_urgency_display, urgency_str, clean_course_name

logger = logging.getLogger(__name__)


def _get(obj, key, default=''):
    """Get attribute from both Assignment objects and dicts."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class WindowsNotifier:
    def __init__(self, tray_app=None):
        self.tray_app = tray_app
        self.app_id = "UTHelper"
        self.aumid = "UTHelper.App"
        self._icon_path = os.path.abspath(os.path.join(BASE_DIR, "src", "assets", "icon.ico"))
        if not os.path.exists(self._icon_path):
            self._icon_path = os.path.abspath(os.path.join(BASE_DIR, "assets", "icon.ico"))
            if not os.path.exists(self._icon_path):
                self._icon_path = os.path.abspath(os.path.join(BASE_DIR, "src", "assets", "icon.png"))

        try:
            self._ensure_shortcut()
        except Exception as exc:
            logger.warning("Shortcut AUMID setup failed: %r", exc)

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
            return

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
            
            toaster.show_toast(toast)
            logger.info(f"[Windows] Đã gửi thông báo: {msg}")
        except Exception as e:
            logger.warning(f"windows-toasts lỗi, dùng tray mặc định: {e}")
            if self.tray_app:
                self.tray_app.notify(title, msg)
            else:
                logger.info(f"THÔNG BÁO (Log): {title} - {msg}")
