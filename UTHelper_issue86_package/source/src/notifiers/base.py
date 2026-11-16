from abc import ABC, abstractmethod
from collections.abc import Awaitable
from typing import Dict, List, TypedDict


class NotificationTask(TypedDict, total=False):
    """
    Cấu trúc dữ liệu chuẩn cho mỗi thông báo, được tạo bởi NotificationManager.
    Notifier chỉ cần đọc dict này, không cần biết Assignment hay DummyAssign.
    """
    id: str               # URL hoặc ID của bài tập
    title: str            # Tên bài tập
    course: str           # Tên môn học
    event_type: str       # Loại sự kiện (bài tập, điểm danh...)
    url: str              # Link đến trang bài tập
    deadline: str         # Deadline dạng chuỗi "HH:MM DD/MM/YYYY"
    open_time: str        # Thời gian mở bài (nếu có)
    remaining: str        # Thời gian còn lại (dạng text)
    urgency: str          # "critical" | "warning" | "safe"
    submission_status: str  # Trạng thái nộp bài


class BaseNotifier(ABC):
    """
    Abstract Base Class for all Notifiers (Clean Architecture).
    """
    @abstractmethod
    def notify(self, tasks: List[Dict]) -> bool | Awaitable[bool]:
        pass
