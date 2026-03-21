from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
from typing import Optional
from enum import Enum
from config import settings

class UrgencyLevel(str, Enum):
    CRITICAL = "critical" # Cực kỳ gấp (< 24 giờ)
    WARNING = "warning"   # Sắp tới hạn (< 3 ngày)
    SAFE = "safe"         # An toàn (> 3 ngày)

class ActivityDetail(BaseModel):
    description_html: str = ""
    status_data: dict[str, str] = Field(default_factory=dict)
    quiz_info: list[str] = Field(default_factory=list)
    attempts_allowed: Optional[str] = None
    time_limit: Optional[str] = None
    course_full_name: str = ""
    attendance_records: list[dict[str, str]] = Field(default_factory=list)
    open_time: Optional[datetime] = None



class Assignment(BaseModel):
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        populate_by_name=True
    )

    id: str
    course_id: str
    course_name: str
    title: str
    event_type: str = "other"  # ví dụ: 'deadline' (hạn chót), 'open' (mở), 'close' (đóng), 'attendance' (điểm danh)
    deadline: datetime
    url: str
    submission_status: str = "unknown" # ví dụ: 'submitted' (đã nộp), 'not_submitted' (chưa nộp), 'graded' (đã chấm)
    details: Optional[ActivityDetail] = None
    
    @property
    def hours_remaining(self) -> float:
        delta = self.deadline - datetime.now()
        return delta.total_seconds() / 3600.0

    @property
    def urgency(self) -> UrgencyLevel:
        hours = self.hours_remaining
        if hours < settings.URGENCY_CRITICAL_HOURS:
            return UrgencyLevel.CRITICAL
        elif hours < settings.URGENCY_WARNING_HOURS:
            return UrgencyLevel.WARNING
        return UrgencyLevel.SAFE
