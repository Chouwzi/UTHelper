from __future__ import annotations

import hashlib
import logging
import math
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from config import get_data_dir, settings
from core.academic_radar.calculator import calculate_radar_profile
from core.academic_radar.gpa_planner import CreditProgress, parse_credit_progress
from core.academic_radar.models import (
    CourseEligibility,
    CourseGradeItem,
    RadarProfile,
    SemesterGradeData,
)
from core.academic_radar.normalizer import SemesterNormalizer
from core.demo_sandbox import is_demo_account, normalize_demo_username
from core.portal_client import PortalAuthenticationError, PortalClient
from core.safe_file_io import SafeFileIO

logger = logging.getLogger(__name__)


def _resolve_credentials(
    username: str | None = "", password: str | None = None
) -> tuple[str, str]:
    if username is not None and username != "" and not username.strip():
        return "", ""
    explicit_username = bool(username and username.strip())
    stored_user = str(getattr(settings, "UTH_USERNAME", "") or "").strip()
    resolved_user = username.strip() if explicit_username and username is not None else stored_user
    if not explicit_username and (password is None or password == ""):
        resolved_pwd = str(getattr(settings, "UTH_PASSWORD", "") or "")
    elif password is None:
        active_norm = normalize_demo_username(stored_user).casefold()
        target_norm = normalize_demo_username(resolved_user).casefold()
        if active_norm and target_norm == active_norm:
            resolved_pwd = str(getattr(settings, "UTH_PASSWORD", "") or "")
        else:
            resolved_pwd = ""
    else:
        resolved_pwd = str(password)
    return resolved_user, resolved_pwd


def _safe_moodle_float(val: Any) -> float | None:
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, str):
        stripped = val.strip()
        if not stripped or stripped == "-":
            return None
        val = stripped.replace(",", ".")
    try:
        numeric = float(val)
    except (ValueError, TypeError):
        return None
    return numeric if math.isfinite(numeric) else None


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

    def clear_session(self) -> None:
        """Clear cached Portal authentication token/session on account switch or logout."""
        clear_token = getattr(self._portal_client, "clear_token", None)
        if callable(clear_token):
            clear_token()
        clear_sess = getattr(self._portal_client, "clear_session", None)
        if callable(clear_sess):
            clear_sess()

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
        username: str = "",
        password: str | None = None,
        force_refresh: bool = False,
    ) -> list[SemesterGradeData]:
        """Loads all semesters.

        Uses daily cache if available (fetched once per day) unless force_refresh is True.
        """
        username, password = _resolve_credentials(username, password)

        if is_demo_account(username):
            raw_transcript = self._portal_client.fetch_full_transcript(
                username, password
            )
            return (
                self._normalizer.normalize_transcript(raw_transcript)
                if raw_transcript
                else []
            )

        if not username or not password:
            return []

        today_str = date.today().isoformat()
        cache = self._read_cache(username)

        if not force_refresh and cache and cache.get("cached_date") == today_str:
            transcript = cache.get("transcript")
            if isinstance(transcript, list) and transcript:
                logger.info("Using cached academic transcript for today (%s)", today_str)
                return self._normalizer.normalize_transcript(transcript)

        try:
            logger.info("Fetching full academic transcript from Portal...")
            raw_transcript = self._portal_client.fetch_full_transcript(username, password)
            if raw_transcript:
                logger.info("Successfully fetched %d semesters from Portal", len(raw_transcript))
                self._write_cache(raw_transcript, username)
                return self._normalizer.normalize_transcript(raw_transcript)
        except PortalAuthenticationError:
            self.clear_session()
            raise
        except Exception as exc:
            logger.warning("Portal academic fetch failed: %s; checking stale cache", exc)

        # Fallback to stale cache if network failed
        if cache and isinstance(cache.get("transcript"), list):
            logger.info("Falling back to stale academic transcript cache")
            return self._normalizer.normalize_transcript(cache["transcript"])

        return []

    def load_credit_progress(
        self,
        username: str = "",
        password: str | None = None,
        force_refresh: bool = False,
    ) -> CreditProgress | None:
        """Loads graduation credit progress (canDat/hienTai); daily cache, stale fallback."""
        username, password = _resolve_credentials(username, password)

        if is_demo_account(username):
            body = self._portal_client.fetch_credit_progress(username, password)
            return parse_credit_progress(body)

        if not username or not password:
            return None

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
        except PortalAuthenticationError:
            self.clear_session()
            raise
        except Exception as exc:
            logger.warning("Portal credit progress fetch failed: %s", exc)

        if cache:
            return parse_credit_progress(cache.get("body"))
        return None

    def create_semester_from_moodle_grades(
        self,
        courses_grades: list[dict[str, Any]],
        grade_items: dict[int | str, list[dict[str, Any]]],
    ) -> list[SemesterGradeData]:
        """Converts raw Moodle courses and grade items into a SemesterGradeData."""
        if not courses_grades:
            return []

        courses: list[CourseGradeItem] = []
        for cg in courses_grades:
            if not isinstance(cg, dict):
                continue
            cid = cg.get("courseid") if cg.get("courseid") is not None else (cg.get("id") or 0)
            cname = (
                cg.get("coursename")
                or cg.get("fullname")
                or cg.get("display_name")
                or "Chưa có tên"
            )
            ccode = cg.get("shortname") or cg.get("coursecode") or ""
            raw_grade = cg.get("rawgrade") if cg.get("rawgrade") is not None else cg.get("grade")
            final_grade = _safe_moodle_float(raw_grade)

            # Inspect grade items for QT / Thi (keys in fetch_all_grades are str(courseid))
            items: list[dict[str, Any]] = []
            if isinstance(grade_items, dict):
                resolved_items = (
                    grade_items.get(str(cid))
                    if str(cid) in grade_items
                    else grade_items.get(cid, [])
                )
                if isinstance(resolved_items, list):
                    items = resolved_items

            process_grade = None
            exam_grade = None

            for it in items:
                if not isinstance(it, dict):
                    continue
                iname = str(it.get("itemname") or "").lower()
                iraw = (
                    it.get("graderaw")
                    if it.get("graderaw") is not None
                    else it.get("gradeformatted")
                )
                gval = _safe_moodle_float(iraw)
                if gval is None:
                    continue
                if any(k in iname for k in ("quá trình", "qt", "chuyên cần", "thường kỳ", "giữa kỳ")):
                    process_grade = gval
                elif any(k in iname for k in ("thi", "cuối kỳ", "ck", "exam")):
                    exam_grade = gval

            item = CourseGradeItem(
                course_id=str(cid),
                course_code=str(ccode),
                course_name=str(cname),
                credits=0.0,
                process_grade=process_grade,
                exam_grade=exam_grade,
                final_grade=final_grade,
                eligibility=CourseEligibility(
                    status="unknown",
                    reason="Điểm tạm thời từ Moodle (chưa đồng bộ số tín chỉ Portal)",
                ),
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
