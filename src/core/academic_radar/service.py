from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from config import get_data_dir, settings
from core.academic_radar.calculator import calculate_radar_profile
from core.academic_radar.gpa_planner import CreditProgress, parse_credit_progress
from core.academic_radar.models import CourseGradeItem, RadarProfile, SemesterGradeData
from core.academic_radar.normalizer import SemesterNormalizer
from core.demo_sandbox import is_demo_account, normalize_demo_username
from core.portal_client import PortalClient
from core.safe_file_io import SafeFileIO

logger = logging.getLogger(__name__)


def _namespace_digest(username: str) -> str:
    base = str(getattr(settings, "PORTAL_API_BASE", "") or "").strip()
    owner = normalize_demo_username(username) or "anonymous"
    return hashlib.sha256(f"{base}|{owner}".casefold().encode("utf-8")).hexdigest()[:12]


class AcademicGradeService:
    """Service for loading, caching, and analyzing student academic grades and radar profiles."""

    def __init__(
        self,
        portal_client: Optional[PortalClient] = None,
        cache_dir: Optional[Path] = None,
        normalizer: Optional[SemesterNormalizer] = None,
    ) -> None:
        self._portal_client = portal_client or PortalClient(settings.PORTAL_API_BASE)
        self._cache_dir = cache_dir or get_data_dir()
        self._normalizer = normalizer or SemesterNormalizer()

    def _cache_paths(self, username: str) -> tuple[Path, Path]:
        digest = _namespace_digest(username)
        return (
            self._cache_dir / f"portal_grades_cache_{digest}.json",
            self._cache_dir / f"portal_progress_cache_{digest}.json",
        )

    def _read_cache(self, username: str = "") -> dict[str, Any] | None:
        """Reads cached raw transcript from disk if exists and valid JSON."""
        cache_file, _ = self._cache_paths(username)
        if not cache_file.exists():
            return None
        try:
            data = SafeFileIO.read_json_safe(cache_file, dict)
            return data if isinstance(data, dict) and data else None
        except Exception as exc:
            logger.warning("Failed to read academic grades cache: %s", exc)
            return None

    def _write_cache(self, raw_transcript: list[dict[str, Any]], username: str = "") -> None:
        """Writes raw transcript to disk with today's cache date."""
        try:
            cache_file, _ = self._cache_paths(username)
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "cached_date": date.today().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "transcript": raw_transcript,
            }
            SafeFileIO.write_json_atomic(cache_file, payload)
        except Exception as exc:
            logger.warning("Failed to write academic grades cache: %s", exc)

    def load_academic_data(
        self,
        username: str | None = None,
        password: str | None = None,
        force_refresh: bool = False,
    ) -> list[SemesterGradeData]:
        """Loads all semesters.

        Uses daily cache if available (fetched once per day) unless force_refresh is True.
        """
        if username is None and password is None:
            username = settings.UTH_USERNAME or settings.MOODLE_USERNAME
            password = settings.UTH_PASSWORD or settings.MOODLE_PASSWORD
        else:
            username = (
                username
                if username is not None
                else (settings.UTH_USERNAME or settings.MOODLE_USERNAME)
            )
            if password is None:
                password = settings.UTH_PASSWORD or settings.MOODLE_PASSWORD

        if is_demo_account(username):
            raw_transcript = self._portal_client.fetch_full_transcript(
                username, password
            )
            return (
                self._normalizer.normalize_transcript(raw_transcript)
                if raw_transcript
                else []
            )

        today_str = date.today().isoformat()
        cache = self._read_cache(username)

        if not force_refresh and cache and cache.get("cached_date") == today_str:
            transcript = cache.get("transcript")
            if isinstance(transcript, list) and transcript:
                logger.info("Using cached academic transcript for today (%s)", today_str)
                return self._normalizer.normalize_transcript(transcript)

        # Fetch from Portal if credentials are provided
        if username and password:
            try:
                logger.info("Fetching full academic transcript from Portal...")
                raw_transcript = self._portal_client.fetch_full_transcript(username, password)
                if raw_transcript:
                    logger.info("Successfully fetched %d semesters from Portal", len(raw_transcript))
                    self._write_cache(raw_transcript, username)
                    return self._normalizer.normalize_transcript(raw_transcript)
            except Exception as exc:
                logger.warning("Portal academic fetch failed: %s; checking stale cache", exc)

        # Fallback to stale cache if network failed
        if cache and isinstance(cache.get("transcript"), list):
            logger.info("Falling back to stale academic transcript cache")
            return self._normalizer.normalize_transcript(cache["transcript"])

        return []

    def load_credit_progress(
        self,
        username: str | None = None,
        password: str | None = None,
        force_refresh: bool = False,
    ) -> CreditProgress | None:
        """Loads graduation credit progress (canDat/hienTai); daily cache, stale fallback."""
        if username is None and password is None:
            username = settings.UTH_USERNAME or settings.MOODLE_USERNAME
            password = settings.UTH_PASSWORD or settings.MOODLE_PASSWORD
        else:
            username = (
                username
                if username is not None
                else (settings.UTH_USERNAME or settings.MOODLE_USERNAME)
            )
            if password is None:
                password = settings.UTH_PASSWORD or settings.MOODLE_PASSWORD

        if is_demo_account(username):
            body = self._portal_client.fetch_credit_progress(username, password)
            return parse_credit_progress(body)

        _, progress_cache_file = self._cache_paths(username)

        cache: dict[str, Any] | None = None
        try:
            if progress_cache_file.exists():
                loaded = SafeFileIO.read_json_safe(progress_cache_file, dict)
                cache = loaded if isinstance(loaded, dict) and loaded else None
        except Exception as exc:
            logger.warning("Failed to read credit progress cache: %s", exc)

        if not force_refresh and cache and cache.get("cached_date") == date.today().isoformat():
            progress = parse_credit_progress(cache.get("body"))
            if progress:
                return progress

        if username and password:
            try:
                body = self._portal_client.fetch_credit_progress(username, password)
                progress = parse_credit_progress(body)
                if progress:
                    try:
                        self._cache_dir.mkdir(parents=True, exist_ok=True)
                        SafeFileIO.write_json_atomic(
                            progress_cache_file,
                            {"cached_date": date.today().isoformat(), "body": body},
                        )
                    except Exception as exc:
                        logger.warning("Failed to write credit progress cache: %s", exc)
                    return progress
            except Exception as exc:
                logger.warning("Portal credit progress fetch failed: %s", exc)

        if cache:
            return parse_credit_progress(cache.get("body"))
        return None

    def create_semester_from_moodle_grades(
        self,
        courses_grades: list[dict[str, Any]],
        grade_items: dict[int, list[dict[str, Any]]],
    ) -> list[SemesterGradeData]:
        """Converts raw Moodle courses and grade items into a SemesterGradeData."""
        if not courses_grades:
            return []

        courses: list[CourseGradeItem] = []
        for cg in courses_grades:
            cid = cg.get("id") or 0
            cname = cg.get("fullname") or cg.get("display_name") or "Chưa có tên"
            ccode = cg.get("shortname") or ""
            final_grade = None
            raw_grade = cg.get("grade")
            if raw_grade is not None:
                try:
                    final_grade = float(raw_grade)
                except (ValueError, TypeError):
                    pass

            # Inspect grade items for QT / Thi
            items = grade_items.get(cid, []) if isinstance(grade_items, dict) else []
            process_grade = None
            exam_grade = None

            for it in items:
                iname = (it.get("itemname") or "").lower()
                iraw = it.get("graderaw")
                if iraw is not None:
                    try:
                        gval = float(iraw)
                        if any(k in iname for k in ("quá trình", "qt", "chuyên cần", "thường kỳ")):
                            process_grade = gval
                        elif any(k in iname for k in ("thi", "cuối kỳ", "ck", "exam")):
                            exam_grade = gval
                    except (ValueError, TypeError):
                        pass

            item = CourseGradeItem(
                course_id=str(cid),
                course_code=str(ccode),
                course_name=str(cname),
                credits=3.0,
                process_grade=process_grade,
                exam_grade=exam_grade,
                final_grade=final_grade,
            )
            courses.append(item)

        if not courses:
            return []

        sem = SemesterGradeData(
            id=1,
            name="Học kỳ hiện tại",
            summary={},
            courses=courses,
        )
        return [sem]

    def get_radar_profiles(
        self,
        semesters: list[SemesterGradeData],
    ) -> list[RadarProfile]:
        """Computes RadarProfiles for all semesters, linking each to its chronologically previous semester."""
        if not semesters:
            return []

        profiles: list[RadarProfile] = []
        for i, sem in enumerate(semesters):
            prev_sem = semesters[i + 1] if i + 1 < len(semesters) else None
            profile = calculate_radar_profile(sem, previous_semester=prev_sem)
            profiles.append(profile)
        return profiles
