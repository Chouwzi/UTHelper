import sys, os, re
sys.path.append(os.path.abspath('src'))

path = r'E:\Projects\UTH-Elearning-Alert\src\notifiers\manager.py'
with open(path, 'r', encoding='utf-8') as f: text = f.read()

old_dispatch = '''    def dispatch(self, assignments: List[Assignment]):
        """
        Gửi tất cả các thông báo theo điều kiện tới những kênh đã đăng ký.
        """
        for notifier in self.notifiers:
            try:
                notifier.notify(assignments)
            except Exception as e:
                logger.error(f"Lỗi khi gửi qua kênh {notifier.__class__.__name__}: {e}")'''

new_dispatch = '''    def dispatch(self, assignments: List[Any]):
        """
        Gửi tất cả các thông báo theo điều kiện tới những kênh đã đăng ký.
        Nhận vào dạng List[Dict] (khi parse từ JSON cache) hoặc List[Assignment].
        """
        if not assignments: return
        
        # Lọc ra các ID đã thông báo trong phiên này để tránh spam liên tục
        if not hasattr(self, '_notified_ids'):
            self._notified_ids = set()

        to_notify = []
        for a in assignments:
            # Tuỳ source, nếu a là dict thì lấy ID theo key
            a_id = a.get("id") if isinstance(a, dict) else a.id
            if a_id in self._notified_ids:
                continue
            to_notify.append(a)
            self._notified_ids.add(a_id)

        if not to_notify:
            return

        # Chuyển đổi dict thành dict dummy có key class attribute (giả lập models.Assignment) cho base logic
        class DummyAssign:
            def __init__(self, data):
                self.id = data.get("id")
                self.title = data.get("title")
                self.urgency_str = data.get("urgency", "safe")
                # map string urgency to enum
                from models import UrgencyLevel
                if self.urgency_str == "critical":
                    self.urgency = UrgencyLevel.CRITICAL
                elif self.urgency_str == "warning":
                    self.urgency = UrgencyLevel.WARNING
                else:
                    self.urgency = UrgencyLevel.SAFE

        mapped = [DummyAssign(a) if isinstance(a, dict) else a for a in to_notify]

        for notifier in self.notifiers:
            try:
                notifier.notify(mapped)
            except Exception as e:
                logger.error(f"Lỗi khi gửi qua kênh {notifier.__class__.__name__}: {e}")'''

text = text.replace(old_dispatch, new_dispatch).replace("from typing import List", "from typing import List, Any")
with open(path, 'w', encoding='utf-8') as f: f.write(text)
print("done map")
