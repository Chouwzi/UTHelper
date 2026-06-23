"""Grade change monitor — detects new/changed grades across courses.

Compares current grades against a persisted snapshot to detect changes.
Snapshot is stored as JSON in the user data directory.
"""
import json
import logging
import os
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Callable, Dict, List, Optional, Any

logger = logging.getLogger(__name__)


@dataclass
class GradeChange:
    """Represents a grade that changed."""
    course_name: str
    item_name: str
    old_grade: Optional[str]
    new_grade: str
    timestamp: str  # ISO format


class GradeMonitor:
    """Monitors grades across enrolled courses and detects changes.

    Stores a snapshot of grades in a JSON file. On each check, compares
    current grades against the snapshot to find new/changed grades.
    """

    def __init__(self, snapshot_path: Optional[str] = None):
        if snapshot_path is None:
            from config import _USER_DATA_DIR
            snapshot_path = str(_USER_DATA_DIR / "grade_snapshot.json")
        self._snapshot_path = snapshot_path
        self._snapshot: Dict[str, Dict[str, str]] = self._load_snapshot()

    def _load_snapshot(self) -> Dict[str, Dict[str, str]]:
        """Load the grade snapshot from disk.

        Format: { "course_id": { "item_name": "grade_value", ... }, ... }
        """
        if not os.path.exists(self._snapshot_path):
            return {}
        try:
            with open(self._snapshot_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("Cannot load grade snapshot: %s", e)
            return {}

    def _save_snapshot(self) -> None:
        """Persist the grade snapshot to disk."""
        try:
            tmp = f"{self._snapshot_path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._snapshot, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._snapshot_path)
        except Exception as e:
            logger.error("Cannot save grade snapshot: %s", e)

    def check_for_changes(
        self,
        call_api: Callable,
        userid: int,
    ) -> List[GradeChange]:
        """Compare current grades against snapshot and return changes.

        Args:
            call_api: WS API call function.
            userid: Moodle user ID.

        Returns:
            List of GradeChange objects for grades that changed.
        """
        from core import ws_functions

        changes: List[GradeChange] = []

        try:
            # Get grade overview for all courses
            courses_grades = ws_functions.get_course_grades(call_api, userid)
            if not courses_grades:
                return changes

            for course_grade in courses_grades:
                try:
                    course_id = int(course_grade.get('courseid', 0))
                except (ValueError, TypeError):
                    continue
                if not course_id:
                    continue
                course_id_str = str(course_id)  # JSON keys are strings
                course_name = course_grade.get('coursename', f'Course {course_id}')
                grade_str = course_grade.get('grade', '')

                if not grade_str or grade_str == '-':
                    continue

                # Check if course-level grade changed
                old_course = self._snapshot.get(course_id_str, {})
                old_overall = old_course.get('_overall', '')

                if grade_str != old_overall and old_overall != '':
                    # Overall course grade changed
                    changes.append(GradeChange(
                        course_name=course_name,
                        item_name="Tổng kết",
                        old_grade=old_overall if old_overall else None,
                        new_grade=grade_str,
                        timestamp=datetime.now().isoformat(),
                    ))

                # Try to get detailed grade items for this course
                try:
                    items = ws_functions.get_grade_items(call_api, course_id, userid)
                    if items:
                        for item in items:
                            item_name = item.get('itemname', '')
                            item_grade = item.get('gradeformatted', '')
                            if not item_name or not item_grade or item_grade == '-':
                                continue

                            old_item_grade = old_course.get(item_name, '')
                            if item_grade != old_item_grade and old_item_grade != '':
                                changes.append(GradeChange(
                                    course_name=course_name,
                                    item_name=item_name,
                                    old_grade=old_item_grade if old_item_grade else None,
                                    new_grade=item_grade,
                                    timestamp=datetime.now().isoformat(),
                                ))

                            # Update snapshot for this item
                            if course_id_str not in self._snapshot:
                                self._snapshot[course_id_str] = {}
                            self._snapshot[course_id_str][item_name] = item_grade
                except Exception as e:
                    logger.debug("Grade items fetch failed for course %s: %s", course_id, e)

                # Update overall course grade in snapshot (only valid grades)
                if grade_str and grade_str != '-':
                    if course_id_str not in self._snapshot:
                        self._snapshot[course_id_str] = {}
                    self._snapshot[course_id_str]['_overall'] = grade_str

            # Persist updated snapshot
            self._save_snapshot()

        except Exception as e:
            logger.error("Grade check failed: %s", e)

        if changes:
            logger.info("Detected %d grade changes", len(changes))
        return changes
