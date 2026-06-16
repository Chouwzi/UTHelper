# Ghi chú: Yêu cầu win11toast hoặc winotify, nhưng hiện tại chúng tôi sẽ sử dụng in chuẩn 
# hoặc cách tiếp cận đơn giản hơn nếu không có sẵn các thư viện này.
# Vì chúng tôi không thêm win11toast vào danh sách yêu cầu, tạm thời sẽ dùng một 
# thông báo cơ bản hoặc chuông hệ thống, nhưng về mặt kiến trúc, phần này được tách riêng 
# để có thể dễ dàng cập nhật sang các thư viện chuyên dụng như `windows-toasts` hoặc `win11toast`.
import logging
import os
import sys
from typing import List
from models import Assignment, UrgencyLevel
from config import BASE_DIR

logger = logging.getLogger(__name__)

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
        """
        Gửi thông báo desktop sử dụng windows-toasts.
        """
        if not assignments:
            return

        critical = [a for a in assignments if getattr(a, 'urgency', None) == UrgencyLevel.CRITICAL]
        warnings = [a for a in assignments if getattr(a, 'urgency', None) == UrgencyLevel.WARNING]
        safes = [a for a in assignments if getattr(a, 'urgency', None) == UrgencyLevel.SAFE]
        other_count = len(assignments) - len(critical) - len(warnings) - len(safes)
        title = "UTHelper - Thông báo"
        if len(assignments) == 1:
            a = assignments[0]
            title = getattr(a, 'title', 'Bài tập mới')
            course = getattr(a, 'course_name', getattr(a, 'course', 'Không rõ môn'))
            
            remaining = "Không rõ"
            if hasattr(a, 'deadline') and a.deadline:
                import datetime
                delta = a.deadline - datetime.datetime.now()
                d, s = delta.days, delta.seconds
                if d < 0:
                    remaining = "Quá hạn!"
                elif d > 0:
                    remaining = f"Còn {d} ngày {s//3600}h"
                else:
                    remaining = f"Còn {s//3600}h {(s%3600)//60}p"
            
            msg = f"Môn: {course}\nThời hạn: {remaining} | {getattr(a, 'urgency_str', getattr(a, 'urgency', '...'))}"
        else:
            msg = f"Bạn có {len(critical)} bài cực gấp, {len(warnings)} sắp tới hạn và {len(safes) + other_count} bài khác."

        try:
            from windows_toasts import InteractableWindowsToaster, Toast
            
            toaster = InteractableWindowsToaster(self.app_id, notifierAUMID=self.aumid)
            toast = Toast()
            toast.text_fields = [title, msg]
            
            toaster.show_toast(toast)
            logger.info(f"Đã gửi thông báo bằng windows-toasts: {msg}")
        except Exception as e:
            logger.warning(f"windows-toasts lỗi, dùng tray mặc định: {e}")
            if self.tray_app:
                self.tray_app.notify(title, msg)
            else:
                logger.info(f"THÔNG BÁO (Log): {title} - {msg}")
