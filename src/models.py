from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional
from enum import Enum

class UrgencyLevel(str, Enum):
    CRITICAL = "critical" # < 24h
    WARNING = "warning"   # < 3 days
    SAFE = "safe"         # > 3 days

class Course(BaseModel):
    id: str
    name: str

class Assignment(BaseModel):
    id: str
    course_id: str
    course_name: str
    title: str
    deadline: datetime
    url: str
    
    @property
    def hours_remaining(self) -> float:
        delta = self.deadline - datetime.now()
        return delta.total_seconds() / 3600.0

    @property
    def urgency(self) -> UrgencyLevel:
        hours = self.hours_remaining
        if hours < 24:
            return UrgencyLevel.CRITICAL
        elif hours < 72:
            return UrgencyLevel.WARNING
        return UrgencyLevel.SAFE

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }
