from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
from typing import Optional
from enum import Enum

DEFAULT_URGENCY_CRITICAL_HOURS = 24.0
DEFAULT_URGENCY_WARNING_HOURS = 72.0

# Sentinel date: dùng cho bài tập không có deadline, các nơi khác kiểm tra year >= 2099
NO_DEADLINE_DATE = datetime(2099, 12, 31, 23, 59, 59)

class UrgencyLevel(str, Enum):
    OVERDUE = "overdue"   # Đã quá hạn
    CRITICAL = "critical" # Gấp lắm rồi (dưới 24h)
    WARNING = "warning"   # Sắp đến hạn (dưới 3 ngày)
    SAFE = "safe"         # Vẫn còn thong dong (trên 3 ngày)

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
    event_type: str = "other"  # phân loại: hạn chót, mở bài, điểm danh...
    deadline: datetime
    url: str
    submission_status: str = "unknown" # tình trạng: đã nộp, chưa nộp, đã chấm điểm...
    site_identity: str = "courses" # thnn, courses
    details: Optional[ActivityDetail] = None
    
    @property
    def hours_remaining(self) -> float:
        delta = self.deadline - datetime.now()
        return delta.total_seconds() / 3600.0

    def urgency_for(
        self,
        critical_hours: float = DEFAULT_URGENCY_CRITICAL_HOURS,
        warning_hours: float = DEFAULT_URGENCY_WARNING_HOURS,
    ) -> UrgencyLevel:
        hours = self.hours_remaining
        if hours < critical_hours:
            return UrgencyLevel.CRITICAL
        if hours < warning_hours:
            return UrgencyLevel.WARNING
        return UrgencyLevel.SAFE

    @property
    def urgency(self) -> UrgencyLevel:
        return self.urgency_for()
