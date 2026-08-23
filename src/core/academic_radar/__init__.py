"""Academic Radar core module.

Provides data normalization, course eligibility resolution, pure scoring calculations,
and color scales for the 6-axis academic radar.
"""
from core.academic_radar.config import RADAR_CONFIG, RadarConfig
from core.academic_radar.models import (
    CourseEligibility,
    CourseGradeItem,
    RadarMetric,
    RadarProfile,
    SemesterGradeData,
)
from core.academic_radar.calculator import calculate_radar_profile
from core.academic_radar.normalizer import SemesterNormalizer
from core.academic_radar.service import AcademicGradeService

__all__ = [
    "RADAR_CONFIG",
    "RadarConfig",
    "CourseEligibility",
    "CourseGradeItem",
    "RadarMetric",
    "RadarProfile",
    "SemesterGradeData",
    "calculate_radar_profile",
    "SemesterNormalizer",
    "AcademicGradeService",
]
