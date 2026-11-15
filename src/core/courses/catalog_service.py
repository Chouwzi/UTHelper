"""Orchestration service for 0ms course catalog loading, sorting, and delta sync."""

from __future__ import annotations

import html
import logging
import math
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar, get_args

from core.courses.cache_store import (
    CatalogCacheSnapshot,
    CourseCacheStore,
    CourseDetailSnapshot,
)
from core.courses.models import (
    CourseGradeItem,
    CourseModuleItem,
    CourseSection,
    CourseSource,
    CourseSummary,
    RecentMaterialEntry,
)
from core.courses.preview_cache import PreviewFileCache
from core.courses.providers import CourseCatalogProvider, CourseProviderError
from core.courses.shortname_parser import extract_teacher_and_meet_info

logger = logging.getLogger(__name__)

_VALID_SOURCES = frozenset(get_args(CourseSource))
_T = TypeVar("_T")


@dataclass(frozen=True)
class CourseSyncResult:
    """Outcome of synchronizing sections, delta updates, and cached grades."""

    sections: list[CourseSection]
    grades: list[CourseGradeItem]
    changed: bool
    new_cmids: set[int]
    last_synced_at: int
    error: str = ""


def _validate_source(source: Any) -> CourseSource:
    if source not in _VALID_SOURCES:
        raise ValueError(f"Invalid course source: {source!r}")
    return source


def _validate_course_id(course_id: Any) -> int:
    if isinstance(course_id, bool) or not isinstance(course_id, int) or course_id < 0:
        raise ValueError(f"Invalid course_id: {course_id!r}")
    return course_id


def _safe_finite_int(value: Any, *, fallback: int) -> int:
    if value is None or isinstance(value, bool):
        return fallback
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(numeric):
        return fallback
    return int(numeric)


def _safe_max_age_seconds(max_age_hours: Any) -> int:
    hours = _safe_finite_int(max_age_hours, fallback=1)
    return max(1, hours) * 3600


def _format_error(exc: BaseException) -> str:
    return str(exc).strip() or type(exc).__name__


def _apply_course_flags(
    courses: list[CourseSummary],
    pinned_keys: list[str],
    unseen_keys: list[str],
) -> list[CourseSummary]:
    pin_rank = {key: idx for idx, key in enumerate(pinned_keys)}
    unseen = set(unseen_keys)
    default_rank = len(pinned_keys)
    enriched = [
        replace(
            course,
            is_pinned=(course.course_key in pin_rank),
            has_unseen_updates=(course.course_key in unseen) or course.has_unseen_updates,
        )
        for course in courses
    ]
    enriched.sort(
        key=lambda course: (
            0 if course.is_pinned else 1,
            pin_rank.get(course.course_key, default_rank),
            -course.semester_sort_key[0],
            -course.semester_sort_key[1],
            0 if course.source == "courses" else 1,
            course.display_name.lower(),
        )
    )
    return enriched


def _merge_course_with_cached(
    fresh: CourseSummary, prev: Optional[CourseSummary]
) -> CourseSummary:
    if prev is None:
        return fresh
    return replace(
        fresh,
        teacher_name=fresh.teacher_name or prev.teacher_name,
        teacher_email=fresh.teacher_email or prev.teacher_email,
        teacher_phone=fresh.teacher_phone or prev.teacher_phone,
        meet_url=fresh.meet_url or prev.meet_url,
        completed_count=prev.completed_count,
        total_trackable=prev.total_trackable,
        progress=fresh.progress if fresh.progress is not None else prev.progress,
        last_synced_at=prev.last_synced_at,
    )


def _apply_progress_to_course(
    summary: CourseSummary, sections: list[CourseSection]
) -> CourseSummary:
    trackable = [mod for sec in sections for mod in sec.modules if mod.completion_mode > 0]
    done = sum(1 for mod in trackable if mod.completion_state > 0)
    progress = (done / len(trackable)) if trackable else summary.progress
    return replace(
        summary,
        completed_count=done,
        total_trackable=len(trackable),
        progress=progress,
    )


def _enrich_course_contact(
    summary: CourseSummary, sections: list[CourseSection]
) -> CourseSummary:
    fragments: list[str] = []
    if summary.summary_text:
        fragments.append(f"<p>{html.escape(summary.summary_text)}</p>")
    for sec in sections[:2]:
        if sec.summary_html:
            fragments.append(sec.summary_html)
        for mod in sec.modules:
            if mod.inline_html:
                fragments.append(mod.inline_html)
            if mod.description_html:
                fragments.append(mod.description_html)
            if mod.name:
                fragments.append(f"<p>{html.escape(mod.name)}</p>")
            if mod.modname in {"url", "googlemeet"} and mod.url:
                fragments.append(f'<a href="{html.escape(mod.url, quote=True)}">link</a>')

    info = extract_teacher_and_meet_info(fragments)
    updated = _apply_progress_to_course(summary, sections)

    return replace(
        updated,
        teacher_name=info.teacher_name or updated.teacher_name,
        teacher_email=info.teacher_email or updated.teacher_email,
        teacher_phone=info.teacher_phone or updated.teacher_phone,
        meet_url=info.meet_url or updated.meet_url,
    )


def _mark_new_modules(
    sections: list[CourseSection], new_cmids: set[int]
) -> list[CourseSection]:
    if not new_cmids:
        return [
            replace(sec, modules=tuple(replace(mod, is_new=False) for mod in sec.modules))
            for sec in sections
        ]
    return [
        replace(
            sec,
            modules=tuple(replace(mod, is_new=(mod.cmid in new_cmids)) for mod in sec.modules),
        )
        for sec in sections
    ]


class CourseCatalogService:
    """Thread-safe coordinator for cached course catalogs, delta sync, and THNN lock."""

    COURSES_DELTA_DEBOUNCE_SECONDS = 15
    THNN_TTL_SECONDS = 300

    def __init__(
        self,
        *,
        cache_store: CourseCacheStore,
        preview_cache: PreviewFileCache,
        courses_provider: Optional[CourseCatalogProvider] = None,
        thnn_provider: Optional[CourseCatalogProvider] = None,
        time_fn: Callable[[], int] = lambda: int(time.time()),
    ) -> None:
        self._cache = cache_store
        self._preview_cache = preview_cache
        self._courses_provider = courses_provider
        self._thnn_provider = thnn_provider
        self._time_fn = time_fn
        self._thnn_lock = threading.Lock()
        self._catalog_lock = threading.Lock()
        self._last_delta_check: dict[str, int] = {}
        self.last_thnn_error: str = ""
        self.last_courses_error: str = ""

    def _now_seconds(self) -> int:
        return max(0, _safe_finite_int(self._time_fn(), fallback=int(time.time())))

    def _provider_for(self, source: CourseSource) -> Optional[CourseCatalogProvider]:
        return self._courses_provider if source == "courses" else self._thnn_provider

    def _call_provider(self, source: CourseSource, fn: Callable[[], _T]) -> _T:
        if source == "thnn":
            with self._thnn_lock:
                return fn()
        return fn()

    def _record_provider_error(
        self, source: CourseSource, context: str, exc: BaseException
    ) -> str:
        msg = _format_error(exc)
        if source == "thnn":
            self.last_thnn_error = msg
        else:
            self.last_courses_error = msg
        logger.warning("%s %s failed: %s", source.upper(), context, msg)
        return msg

    def _clear_provider_error(self, source: CourseSource) -> None:
        if source == "thnn":
            self.last_thnn_error = ""
        else:
            self.last_courses_error = ""

    def load_cached_catalog(self) -> tuple[list[CourseSummary], list[RecentMaterialEntry], int]:
        """Return cached courses with pin/unseen flags, recent items, and last sync time."""
        snap: CatalogCacheSnapshot = self._cache.load_catalog()
        courses = _apply_course_flags(snap.courses, snap.pinned_keys, snap.unseen_keys)
        return courses, snap.recent_items, snap.catalog_synced_at

    def is_catalog_stale(self, max_age_hours: int) -> bool:
        """Return True if the catalog is missing, expired, or clock-skewed backward."""
        snap = self._cache.load_catalog()
        if not snap.catalog_synced_at or not snap.courses:
            return True
        delta = self._now_seconds() - snap.catalog_synced_at
        if delta < 0:
            return True
        return delta >= _safe_max_age_seconds(max_age_hours)

    def refresh_catalog(self, include_thnn: bool = False) -> list[CourseSummary]:
        """Fetch course summaries and merge with cache without losing data on failure."""
        fresh_courses: Optional[list[CourseSummary]] = None
        if self._courses_provider is not None:
            try:
                candidate = self._call_provider(
                    "courses", self._courses_provider.fetch_courses
                )
                if candidate:
                    fresh_courses = list(candidate)
                    self._clear_provider_error("courses")
                else:
                    logger.warning(
                        "COURSES provider returned empty catalog; preserving cached courses"
                    )
            except CourseProviderError as exc:
                self._record_provider_error("courses", "fetch_courses", exc)

        fresh_thnn: Optional[list[CourseSummary]] = None
        if include_thnn and self._thnn_provider is not None:
            try:
                fresh_thnn = list(
                    self._call_provider("thnn", self._thnn_provider.fetch_courses)
                )
                self._clear_provider_error("thnn")
            except CourseProviderError as exc:
                self._record_provider_error("thnn", "fetch_courses", exc)

        with self._catalog_lock:
            snap = self._cache.load_catalog()
            existing_map = {course.course_key: course for course in snap.courses}
            merged: list[CourseSummary] = []

            for source, fresh_items in (
                ("courses", fresh_courses),
                ("thnn", fresh_thnn),
            ):
                if fresh_items is not None:
                    merged.extend(
                        _merge_course_with_cached(item, existing_map.get(item.course_key))
                        for item in fresh_items
                    )
                else:
                    merged.extend(
                        course for course in snap.courses if course.source == source
                    )

            if fresh_courses is not None or fresh_thnn is not None:
                self._cache.save_catalog(merged, synced_at=self._now_seconds())
            return _apply_course_flags(merged, snap.pinned_keys, snap.unseen_keys)

    def open_course_cached(self, source: CourseSource, course_id: int) -> CourseDetailSnapshot:
        """Load cached sections, grades, and known module IDs for immediate rendering."""
        valid_source = _validate_source(source)
        valid_id = _validate_course_id(course_id)
        return self._cache.load_course_detail(valid_source, valid_id)

    def _should_skip_by_window(
        self,
        source: CourseSource,
        course_key: str,
        cached: CourseDetailSnapshot,
        now: int,
    ) -> bool:
        with self._catalog_lock:
            last_probe = self._last_delta_check.get(course_key)
        if (
            last_probe is not None
            and 0 <= (now - last_probe) < self.COURSES_DELTA_DEBOUNCE_SECONDS
        ):
            return True
        if (
            source == "thnn"
            and cached.last_synced_at > 0
            and 0 <= (now - cached.last_synced_at) < self.THNN_TTL_SECONDS
        ):
            return True
        return False

    def _update_catalog_course_locked(
        self,
        course_key: str,
        updater: Callable[[CourseSummary], CourseSummary],
        *,
        synced_at_fallback: int = 0,
    ) -> None:
        snap = self._cache.load_catalog()
        updated_catalog: list[CourseSummary] = []
        matched = False
        for course in snap.courses:
            if course.course_key == course_key:
                updated_catalog.append(updater(course))
                matched = True
            else:
                updated_catalog.append(course)
        if matched:
            self._cache.save_catalog(
                updated_catalog, synced_at=snap.catalog_synced_at or synced_at_fallback
            )

    def sync_course_delta(
        self,
        source: CourseSource,
        course_id: int,
        force: bool = False,
    ) -> CourseSyncResult:
        """Sync course sections respecting debounce, THNN TTL, and delta update checks."""
        valid_source = _validate_source(source)
        valid_id = _validate_course_id(course_id)
        course_key = f"{valid_source}:{valid_id}"
        cached = self._cache.load_course_detail(valid_source, valid_id)
        now = self._now_seconds()
        provider = self._provider_for(valid_source)

        def _cached_result(error: str = "") -> CourseSyncResult:
            return CourseSyncResult(
                sections=cached.sections,
                grades=cached.grades,
                changed=False,
                new_cmids=set(),
                last_synced_at=cached.last_synced_at,
                error=error,
            )

        if provider is None:
            return _cached_result()

        if not force and (cached.sections or cached.last_synced_at > 0):
            if self._should_skip_by_window(valid_source, course_key, cached, now):
                return _cached_result()

            if valid_source == "courses" and 0 < cached.last_synced_at <= now:
                updates_fn = getattr(provider, "fetch_updates_since", None)
                if callable(updates_fn):
                    try:
                        changed_ids = self._call_provider(
                            valid_source,
                            lambda: updates_fn(valid_id, cached.last_synced_at),
                        )
                    except CourseProviderError as exc:
                        err = self._record_provider_error(
                            valid_source, "fetch_updates_since", exc
                        )
                        return _cached_result(error=err)
                    with self._catalog_lock:
                        self._last_delta_check[course_key] = now
                    if not changed_ids:
                        return _cached_result()

        try:
            fresh_sections = list(
                self._call_provider(
                    valid_source,
                    lambda: provider.fetch_course_sections(valid_id),
                )
            )
        except CourseProviderError as exc:
            err = self._record_provider_error(valid_source, "fetch_course_sections", exc)
            return _cached_result(error=err)

        self._clear_provider_error(valid_source)
        has_prior_sync = cached.last_synced_at > 0 or bool(cached.known_cmids)
        old_cmids = set(cached.known_cmids)
        fresh_cmids = [mod.cmid for sec in fresh_sections for mod in sec.modules]
        new_cmids = (set(fresh_cmids) - old_cmids) if has_prior_sync else set()
        marked_sections = _mark_new_modules(fresh_sections, new_cmids)

        with self._catalog_lock:
            self._last_delta_check[course_key] = now
            latest_detail = self._cache.load_course_detail(valid_source, valid_id)
            self._cache.save_course_detail(
                valid_source,
                valid_id,
                marked_sections,
                latest_detail.grades,
                synced_at=now,
                known_cmids=fresh_cmids,
            )
            self._update_catalog_course_locked(
                course_key,
                lambda course: replace(
                    _enrich_course_contact(course, marked_sections),
                    last_synced_at=now,
                ),
                synced_at_fallback=now,
            )

        return CourseSyncResult(
            sections=marked_sections,
            grades=latest_detail.grades,
            changed=True,
            new_cmids=new_cmids,
            last_synced_at=now,
        )

    def load_course_grades(
        self, source: CourseSource, course_id: int, force: bool = False
    ) -> list[CourseGradeItem]:
        """Return cached course grades or fetch fresh grade items when forced or empty."""
        valid_source = _validate_source(source)
        valid_id = _validate_course_id(course_id)
        cached = self._cache.load_course_detail(valid_source, valid_id)
        if cached.grades and not force:
            return cached.grades

        provider = self._provider_for(valid_source)
        if provider is None:
            return cached.grades

        try:
            grades = list(
                self._call_provider(
                    valid_source, lambda: provider.fetch_course_grades(valid_id)
                )
            )
        except CourseProviderError as exc:
            self._record_provider_error(valid_source, "fetch_course_grades", exc)
            return cached.grades

        self._clear_provider_error(valid_source)
        with self._catalog_lock:
            latest = self._cache.load_course_detail(valid_source, valid_id)
            self._cache.save_course_detail(
                valid_source,
                valid_id,
                latest.sections,
                grades,
                synced_at=latest.last_synced_at,
                known_cmids=latest.known_cmids,
            )
        return grades

    def toggle_pin(self, course_key: str) -> list[CourseSummary]:
        """Toggle a course's pinned state and return the newly sorted catalog."""
        with self._catalog_lock:
            snap = self._cache.load_catalog()
            currently_pinned = course_key in snap.pinned_keys
            pins = self._cache.set_pinned(course_key, not currently_pinned)
            return _apply_course_flags(snap.courses, pins, snap.unseen_keys)

    def mark_course_seen(self, course_key: str) -> list[CourseSummary]:
        """Clear the unseen-updates indicator for a course and return sorted summaries."""
        with self._catalog_lock:
            snap = self._cache.load_catalog()
            unseen = self._cache.set_unseen(course_key, False)
            updated_courses = [
                replace(course, has_unseen_updates=False)
                if course.course_key == course_key
                else course
                for course in snap.courses
            ]
            if updated_courses != snap.courses:
                self._cache.save_catalog(
                    updated_courses, synced_at=snap.catalog_synced_at
                )
            return _apply_course_flags(updated_courses, snap.pinned_keys, unseen)

    def record_recent(self, entry: RecentMaterialEntry) -> list[RecentMaterialEntry]:
        """Record a recently opened course material for quick access."""
        return self._cache.record_recent(entry)

    def toggle_completion(
        self, source: CourseSource, course_id: int, cmid: int, completed: bool
    ) -> bool:
        """Update manual completion status on the provider and reflect it in cache."""
        valid_source = _validate_source(source)
        valid_id = _validate_course_id(course_id)
        if isinstance(cmid, bool) or not isinstance(cmid, int) or cmid <= 0:
            return False

        provider = self._provider_for(valid_source)
        setter = getattr(provider, "set_manual_completion", None)
        if not callable(setter):
            return False

        try:
            ok = bool(
                self._call_provider(valid_source, lambda: setter(cmid, bool(completed)))
            )
        except CourseProviderError as exc:
            self._record_provider_error(valid_source, "set_manual_completion", exc)
            return False
        if not ok:
            return False

        new_state = 1 if completed else 0
        course_key = f"{valid_source}:{valid_id}"
        with self._catalog_lock:
            cached = self._cache.load_course_detail(valid_source, valid_id)
            updated_sections = [
                replace(
                    sec,
                    modules=tuple(
                        replace(mod, completion_state=new_state)
                        if mod.cmid == cmid
                        else mod
                        for mod in sec.modules
                    ),
                )
                for sec in cached.sections
            ]
            self._cache.save_course_detail(
                valid_source,
                valid_id,
                updated_sections,
                cached.grades,
                synced_at=cached.last_synced_at,
                known_cmids=cached.known_cmids,
            )
            self._update_catalog_course_locked(
                course_key,
                lambda course: _apply_progress_to_course(course, updated_sections),
            )
        return True

    def _download_bytes(self, source: CourseSource, file_url: str) -> Optional[bytes]:
        valid_source = _validate_source(source)
        if not isinstance(file_url, str) or not file_url.strip():
            return None
        provider = self._provider_for(valid_source)
        if provider is None:
            return None
        raw = self._call_provider(valid_source, lambda: provider.download_file(file_url))
        return raw if raw else None

    def fetch_attachment_bytes(self, source: CourseSource, file_url: str) -> bytes:
        """Download raw attachment bytes from the site provider under session guards."""
        raw = self._download_bytes(source, file_url)
        if not raw:
            raise CourseProviderError(f"Failed to download attachment from {source}")
        return raw

    def fetch_or_cache_preview_file(
        self, source: CourseSource, file_url: str, time_modified: int, file_name: str
    ) -> Optional[Path]:
        """Return a cached preview file path or download and persist it on miss."""
        valid_source = _validate_source(source)
        if not isinstance(file_url, str) or not file_url.strip():
            return None
        cached_path = self._preview_cache.get_cached_path(
            file_url, time_modified, file_name
        )
        if cached_path is not None:
            return cached_path

        try:
            raw = self._download_bytes(valid_source, file_url)
        except CourseProviderError as exc:
            self._record_provider_error(valid_source, "download_file", exc)
            return None
        if not raw:
            return None
        return self._preview_cache.put_bytes(file_url, time_modified, file_name, raw)

    def resolve_module_url(self, source: CourseSource, module: CourseModuleItem) -> str:
        """Resolve an external target URL for a module, using THNN lazy resolution when needed."""
        valid_source = _validate_source(source)
        fallback = module.url or module.web_url
        if valid_source == "thnn" and self._thnn_provider is not None:
            resolver = getattr(self._thnn_provider, "resolve_module_external_url", None)
            if callable(resolver):
                try:
                    resolved = self._call_provider("thnn", lambda: resolver(module))
                except CourseProviderError as exc:
                    self._record_provider_error("thnn", "resolve_module_external_url", exc)
                    return fallback
                except Exception as exc:
                    logger.warning(
                        "THNN resolve_module_external_url failed (cmid=%s): %s",
                        module.cmid,
                        exc,
                    )
                    return fallback
                return resolved or fallback
        return fallback
