"""Grade change monitor - detects new/changed grades across courses.

Compares current grades against a persisted snapshot to detect changes.
Snapshot is stored as JSON in the user data directory.
"""
import hashlib
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _default_grade_namespace() -> str:
    try:
        from config import settings

        base = str(getattr(settings, "MOODLE_BASE_URL", "") or "").strip()
        user = str(getattr(settings, "UTH_USERNAME", "") or "").strip()
        return f"{base}|{user or '__logged_out__'}"
    except Exception:
        return "__logged_out__"


def _grade_namespace_digest(namespace: str) -> str:
    return hashlib.sha256(namespace.strip().casefold().encode("utf-8")).hexdigest()[:12]


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

    def __init__(
        self,
        snapshot_path: Optional[str] = None,
        namespace: Optional[str] = None,
    ):
        self._explicit_path = snapshot_path is not None
        self._lock = threading.RLock()
        if snapshot_path is None:
            from config import _USER_DATA_DIR

            self._base_dir = Path(_USER_DATA_DIR)
            digest = _grade_namespace_digest(
                namespace if namespace is not None else _default_grade_namespace()
            )
            snapshot_path = str(self._base_dir / f"grade_snapshot_{digest}.json")
        else:
            self._base_dir = Path(snapshot_path).parent
        self._snapshot_path = snapshot_path
        self._snapshot: Dict[str, Dict[str, str]] = self._load_snapshot()

    def set_namespace(self, namespace: Optional[str]) -> None:
        """Switch the persisted grade snapshot to another account namespace."""
        if self._explicit_path:
            return
        resolved = namespace if (namespace and namespace.strip()) else _default_grade_namespace()
        digest = _grade_namespace_digest(resolved)
        with self._lock:
            self._snapshot_path = str(self._base_dir / f"grade_snapshot_{digest}.json")
            self._snapshot = self._load_snapshot()

    def reset(self) -> None:
        """Clear in-memory snapshot state (and detach from active account on logout)."""
        with self._lock:
            self._snapshot = {}
            if self._explicit_path:
                self._save_snapshot()
            else:
                digest = _grade_namespace_digest("__logged_out__")
                self._snapshot_path = str(self._base_dir / f"grade_snapshot_{digest}.json")

    def _load_snapshot(self) -> Dict[str, Dict[str, str]]:
        """Load the grade snapshot from disk.

        Format: { "course_id": { "item_name": "grade_value", ... }, ... }
        """
        if not os.path.exists(self._snapshot_path):
            return {}
        try:
            from core.safe_file_io import SafeFileIO
            from pathlib import Path
            return SafeFileIO.read_json_safe(Path(self._snapshot_path), dict)
        except Exception as e:
            logger.warning("Cannot load grade snapshot: %s", e)
            return {}

    def _save_snapshot(self) -> None:
        """Persist the grade snapshot to disk."""
        try:
            from core.safe_file_io import SafeFileIO
            from pathlib import Path
            SafeFileIO.write_json_atomic(Path(self._snapshot_path), self._snapshot)
        except Exception as e:
            logger.error("Cannot save grade snapshot: %s", e)


    def check_for_changes(
        self,
        grade_source: Any,
        userid: int,
    ) -> List[GradeChange]:
        """Compare current grades against snapshot and return changes.

        Args:
            grade_source: MoodleService-like object, or legacy WS API call function.
            userid: Moodle user ID.

        Returns:
            List of GradeChange objects for grades that changed.
        """
        changes: List[GradeChange] = []
        service = self._grade_service(grade_source)

        with self._lock:
            try:
                # Get grade overview for all courses
                courses_grades = service.get_course_grades(userid)
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
                    is_initial_baseline = old_overall == ''
                    overall_changed = grade_str != old_overall and not is_initial_baseline

                    if overall_changed:
                        changes.append(GradeChange(
                            course_name=course_name,
                            item_name="Tổng kết",
                            old_grade=old_overall if old_overall else None,
                            new_grade=grade_str,
                            timestamp=datetime.now().isoformat(),
                        ))

                    # Baseline grade_items on initial snapshot AND re-fetch when overall grade changes
                    # so the first component grade update is never missed.
                    if is_initial_baseline or overall_changed:
                        try:
                            items = service.get_grade_items(course_id, userid)
                            items_were_baselined = old_course.get("_items_baselined") == "1"
                            if course_id_str not in self._snapshot:
                                self._snapshot[course_id_str] = {}
                            if items is not None:
                                for item in items:
                                    item_name = item.get('itemname', '')
                                    item_grade = item.get('gradeformatted', '')
                                    if not item_name or not item_grade or item_grade == '-':
                                        continue

                                    old_item_grade = old_course.get(item_name, '')
                                    if overall_changed and item_grade != old_item_grade:
                                        if old_item_grade != '' or items_were_baselined:
                                            changes.append(GradeChange(
                                                course_name=course_name,
                                                item_name=item_name,
                                                old_grade=old_item_grade if old_item_grade else None,
                                                new_grade=item_grade,
                                                timestamp=datetime.now().isoformat(),
                                            ))

                                    self._snapshot[course_id_str][item_name] = item_grade
                                self._snapshot[course_id_str]["_items_baselined"] = "1"
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

    @staticmethod
    def _grade_service(grade_source: Any):
        from core.moodle_service import MoodleService

        if (
            isinstance(grade_source, MoodleService)
            or (
                not callable(grade_source)
                and hasattr(grade_source, "get_course_grades")
                and hasattr(grade_source, "get_grade_items")
            )
            or "get_course_grades" in getattr(grade_source, "__dict__", {})
            or "get_course_grades" in getattr(grade_source, "_mock_children", {})
        ):
            return grade_source
        if callable(grade_source):
            return MoodleService(grade_source)

        raise TypeError("grade_source must be a MoodleService-like object or WS API callable")
