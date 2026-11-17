"""Namespaced atomic JSON persistence for Courses & THNN metadata."""

from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar, get_args

from core.courses.models import (
    CourseAttachment,
    CourseGradeItem,
    CourseModuleItem,
    CourseSection,
    CourseSource,
    CourseSummary,
    OpenStrategy,
    PreviewKind,
    RecentMaterialEntry,
)
from core.safe_file_io import SafeFileIO, get_memory_lock

logger = logging.getLogger(__name__)

_VALID_SOURCES = frozenset(get_args(CourseSource))
_VALID_OPEN_STRATEGIES = frozenset(get_args(OpenStrategy))
_VALID_PREVIEW_KINDS = frozenset(get_args(PreviewKind))
_T = TypeVar("_T")


@dataclass(frozen=True)
class CatalogCacheSnapshot:
    """Persisted course catalog, pin/unseen markers, and recent materials."""

    courses: list[CourseSummary] = field(default_factory=list)
    pinned_keys: list[str] = field(default_factory=list)
    unseen_keys: list[str] = field(default_factory=list)
    recent_items: list[RecentMaterialEntry] = field(default_factory=list)
    catalog_synced_at: int = 0


@dataclass(frozen=True)
class CourseDetailSnapshot:
    """Persisted section tree, grade items, and known module IDs for one course."""

    sections: list[CourseSection] = field(default_factory=list)
    grades: list[CourseGradeItem] = field(default_factory=list)
    last_synced_at: int = 0
    known_cmids: list[int] = field(default_factory=list)


def _safe_opt_int(value: Any, *, minimum: Optional[int] = None) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, float) and not math.isfinite(value):
            return None
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if minimum is not None and parsed < minimum:
        return None
    return parsed


def _safe_int(value: Any, default: int = 0) -> int:
    parsed = _safe_opt_int(value)
    return default if parsed is None else parsed


def _safe_opt_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _validate_source(source: Any) -> CourseSource:
    if source not in _VALID_SOURCES:
        raise ValueError(f"Invalid course source: {source!r}")
    return source


def _validate_course_id(course_id: Any) -> int:
    parsed = _safe_opt_int(course_id)
    if parsed is None or parsed < 0:
        raise ValueError(f"Invalid course_id: {course_id!r}")
    return parsed


def _string_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, str) and item]


def _parse_dict_items(
    raw: Any, parser: Callable[[dict[str, Any]], Optional[_T]]
) -> list[_T]:
    if not isinstance(raw, list):
        return []
    parsed: list[_T] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        model = parser(item)
        if model is not None:
            parsed.append(model)
    return parsed


def _summary_from_dict(raw: dict[str, Any]) -> Optional[CourseSummary]:
    course_id = _safe_opt_int(raw.get("id"))
    source = raw.get("source")
    if course_id is None or course_id < 0 or source not in _VALID_SOURCES:
        return None

    sort_raw = raw.get("semester_sort_key")
    sort_key = (0, 0)
    if isinstance(sort_raw, (list, tuple)) and len(sort_raw) == 2:
        k0 = _safe_opt_int(sort_raw[0])
        k1 = _safe_opt_int(sort_raw[1])
        if k0 is not None and k1 is not None:
            sort_key = (k0, k1)

    return CourseSummary(
        id=course_id,
        source=source,
        fullname=str(raw.get("fullname") or ""),
        shortname=str(raw.get("shortname") or ""),
        display_name=str(raw.get("display_name") or ""),
        course_code=str(raw.get("course_code") or ""),
        semester_label=str(raw.get("semester_label") or "Khác"),
        semester_sort_key=sort_key,
        program_tag=str(raw.get("program_tag") or ""),
        teacher_name=str(raw.get("teacher_name") or ""),
        teacher_email=str(raw.get("teacher_email") or ""),
        teacher_phone=str(raw.get("teacher_phone") or ""),
        meet_url=str(raw.get("meet_url") or ""),
        progress=_safe_opt_float(raw.get("progress")),
        completed_count=_safe_int(raw.get("completed_count"), 0),
        total_trackable=_safe_int(raw.get("total_trackable"), 0),
        summary_text=str(raw.get("summary_text") or ""),
        web_url=str(raw.get("web_url") or ""),
        last_synced_at=_safe_int(raw.get("last_synced_at"), 0),
        has_unseen_updates=bool(raw.get("has_unseen_updates", False)),
        is_pinned=bool(raw.get("is_pinned", False)),
    )


def _attachment_from_dict(raw: dict[str, Any]) -> Optional[CourseAttachment]:
    file_name = raw.get("file_name")
    file_url = raw.get("file_url")
    if not isinstance(file_name, str) and not isinstance(file_url, str):
        return None
    preview_raw = raw.get("preview_kind")
    preview_kind: PreviewKind = (
        preview_raw if preview_raw in _VALID_PREVIEW_KINDS else "unsupported"
    )
    return CourseAttachment(
        file_name=file_name if isinstance(file_name, str) else "",
        file_url=file_url if isinstance(file_url, str) else "",
        file_size=_safe_int(raw.get("file_size"), 0),
        mime_type=str(raw.get("mime_type") or ""),
        time_modified=_safe_int(raw.get("time_modified"), 0),
        extension=str(raw.get("extension") or ""),
        preview_kind=preview_kind,
        is_archive=bool(raw.get("is_archive", False)),
    )


def _module_from_dict(raw: dict[str, Any]) -> Optional[CourseModuleItem]:
    cmid = _safe_opt_int(raw.get("cmid"))
    if cmid is None or cmid < 0:
        return None
    atts = _parse_dict_items(raw.get("attachments"), _attachment_from_dict)
    strategy_raw = raw.get("open_strategy")
    open_strategy: OpenStrategy = (
        strategy_raw if strategy_raw in _VALID_OPEN_STRATEGIES else "web_fallback"
    )
    return CourseModuleItem(
        cmid=cmid,
        instance_id=_safe_int(raw.get("instance_id"), 0),
        modname=str(raw.get("modname") or "unknown"),
        name=str(raw.get("name") or ""),
        section_id=_safe_int(raw.get("section_id"), 0),
        url=str(raw.get("url") or ""),
        web_url=str(raw.get("web_url") or ""),
        description_html=str(raw.get("description_html") or ""),
        inline_html=str(raw.get("inline_html") or ""),
        completion_mode=_safe_int(raw.get("completion_mode"), 0),
        completion_state=_safe_int(raw.get("completion_state"), 0),
        timemodified=_safe_int(raw.get("timemodified"), 0),
        attachments=tuple(atts),
        open_strategy=open_strategy,
        is_new=bool(raw.get("is_new", False)),
    )


def _section_from_dict(raw: dict[str, Any]) -> Optional[CourseSection]:
    sec_id = _safe_opt_int(raw.get("id"))
    course_id = _safe_opt_int(raw.get("course_id"))
    source = raw.get("source")
    if sec_id is None or course_id is None or course_id < 0 or source not in _VALID_SOURCES:
        return None
    mods = _parse_dict_items(raw.get("modules"), _module_from_dict)
    return CourseSection(
        id=sec_id,
        course_id=course_id,
        source=source,
        section_num=_safe_int(raw.get("section_num"), 0),
        name=str(raw.get("name") or ""),
        summary_html=str(raw.get("summary_html") or ""),
        visible=True if raw.get("visible") is None else bool(raw.get("visible")),
        modules=tuple(mods),
    )


def _grade_from_dict(raw: dict[str, Any]) -> Optional[CourseGradeItem]:
    grade_id = _safe_opt_int(raw.get("id"))
    if grade_id is None:
        return None
    grade_formatted = raw.get("grade_formatted")
    return CourseGradeItem(
        id=grade_id,
        item_name=str(raw.get("item_name") or ""),
        item_module=str(raw.get("item_module") or ""),
        grade_formatted="-" if grade_formatted is None else str(grade_formatted),
        range_formatted=str(raw.get("range_formatted") or ""),
        percentage=str(raw.get("percentage") or ""),
        weight=str(raw.get("weight") or ""),
        feedback_html=str(raw.get("feedback_html") or ""),
    )


def _recent_from_dict(raw: dict[str, Any]) -> Optional[RecentMaterialEntry]:
    cmid = _safe_opt_int(raw.get("cmid"), minimum=0)
    opened_at = _safe_opt_int(raw.get("opened_at"))
    course_key = raw.get("course_key")
    attachment_url = raw.get("attachment_url")
    if (
        cmid is None
        or opened_at is None
        or not isinstance(course_key, str)
        or not isinstance(attachment_url, str)
    ):
        return None
    source_raw = raw.get("source")
    source: CourseSource = source_raw if source_raw in _VALID_SOURCES else "courses"
    return RecentMaterialEntry(
        course_key=course_key,
        course_name=str(raw.get("course_name") or ""),
        cmid=cmid,
        title=str(raw.get("title") or ""),
        attachment_url=attachment_url,
        file_name=str(raw.get("file_name") or ""),
        opened_at=opened_at,
        source=source,
        course_id=_safe_int(raw.get("course_id"), 0),
    )


class CourseCacheStore:
    """Account-namespaced atomic JSON store for course catalogs and trees."""

    SCHEMA_VERSION = 1

    def __init__(self, cache_dir: Optional[Path] = None, namespace: Optional[str] = None) -> None:
        if cache_dir is None:
            from config import _USER_DATA_DIR

            cache_dir = _USER_DATA_DIR
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        normalized = (str(namespace).strip().lower() if namespace else "") or "default"
        self._digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
        self._catalog_path = self._dir / f"courses_catalog_{self._digest}.json"
        self._mem_catalog: Optional[tuple[tuple[int, int, int], CatalogCacheSnapshot]] = None
        self._mem_details: dict[Path, tuple[tuple[int, int, int], CourseDetailSnapshot]] = {}

    @staticmethod
    def _stat_sig(path: Path) -> Optional[tuple[int, int, int]]:
        try:
            st = path.stat()
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_ctime_ns, st.st_size)

    def _detail_path(self, source: CourseSource, course_id: int) -> Path:
        valid_source = _validate_source(source)
        valid_id = _validate_course_id(course_id)
        return self._dir / f"course_tree_{valid_source}_{valid_id}_{self._digest}.json"

    def _read_catalog_dict(self) -> dict[str, Any]:
        payload = SafeFileIO.read_json_safe(self._catalog_path, dict)
        return dict(payload) if isinstance(payload, dict) else {}

    def _write_catalog_payload(self, payload: dict[str, Any]) -> bool:
        payload["version"] = self.SCHEMA_VERSION
        self._mem_catalog = None
        if not SafeFileIO.write_json_atomic(self._catalog_path, payload):
            logger.warning("Failed to persist courses catalog cache: %s", self._catalog_path)
            return False
        return True

    def load_catalog(self) -> CatalogCacheSnapshot:
        """Load the cached course catalog, pins, unseen flags, and recent items (RAM-accelerated)."""
        with get_memory_lock(self._catalog_path):
            sig = self._stat_sig(self._catalog_path)
            if sig is None:
                self._mem_catalog = None
                return CatalogCacheSnapshot()
            if self._mem_catalog is not None and self._mem_catalog[0] == sig:
                return self._mem_catalog[1]

            payload = self._read_catalog_dict()
            if not payload:
                self._mem_catalog = None
                return CatalogCacheSnapshot()

            snap = CatalogCacheSnapshot(
                courses=_parse_dict_items(payload.get("courses"), _summary_from_dict),
                pinned_keys=_string_list(payload.get("pinned_keys")),
                unseen_keys=_string_list(payload.get("unseen_keys")),
                recent_items=_parse_dict_items(payload.get("recent_items"), _recent_from_dict),
                catalog_synced_at=_safe_int(payload.get("catalog_synced_at"), 0),
            )
            post_sig = self._stat_sig(self._catalog_path)
            if post_sig is not None:
                self._mem_catalog = (post_sig, snap)
            return snap

    def save_catalog(self, courses: list[CourseSummary], *, synced_at: int) -> None:
        """Persist the latest course catalog while preserving pins/unseen/recent metadata."""
        with get_memory_lock(self._catalog_path):
            self._dir.mkdir(parents=True, exist_ok=True)
            with SafeFileIO.transaction(self._catalog_path):
                existing = self._read_catalog_dict()
                existing["courses"] = [asdict(course) for course in courses]
                existing["catalog_synced_at"] = int(synced_at)
                self._write_catalog_payload(existing)

    def _update_catalog_string_list(
        self,
        field_name: str,
        course_key: str,
        present: bool,
        *,
        prepend: bool = False,
    ) -> list[str]:
        if not isinstance(course_key, str) or not course_key.strip():
            raise ValueError("course_key must be non-empty")
        with get_memory_lock(self._catalog_path):
            self._dir.mkdir(parents=True, exist_ok=True)
            with SafeFileIO.transaction(self._catalog_path):
                existing = self._read_catalog_dict()
                previous_keys = _string_list(existing.get(field_name))
                keys = [k for k in previous_keys if k != course_key]
                if present:
                    if prepend:
                        keys.insert(0, course_key)
                    else:
                        keys.append(course_key)
                existing[field_name] = keys
                if not self._write_catalog_payload(existing):
                    return previous_keys
                return keys

    def set_pinned(self, course_key: str, pinned: bool) -> list[str]:
        """Pin or unpin a course key and return the updated ordered pin list."""
        return self._update_catalog_string_list(
            "pinned_keys", course_key, pinned, prepend=True
        )

    def set_unseen(self, course_key: str, unseen: bool) -> list[str]:
        """Mark or clear an unseen-update flag for a course key."""
        return self._update_catalog_string_list(
            "unseen_keys", course_key, unseen, prepend=False
        )

    def record_recent(
        self, entry: RecentMaterialEntry, limit: int = 10
    ) -> list[RecentMaterialEntry]:
        """Prepend a recently opened material, deduplicated by attachment URL."""
        max_items = max(1, int(limit))
        with get_memory_lock(self._catalog_path):
            self._dir.mkdir(parents=True, exist_ok=True)
            with SafeFileIO.transaction(self._catalog_path):
                existing = self._read_catalog_dict()
                previous_entries = _parse_dict_items(
                    existing.get("recent_items"), _recent_from_dict
                )
                filtered = [
                    item
                    for item in previous_entries
                    if item.attachment_url != entry.attachment_url
                ]
                entries = [entry, *filtered][:max_items]
                existing["recent_items"] = [asdict(item) for item in entries]
                if not self._write_catalog_payload(existing):
                    return previous_entries
                return entries

    def load_course_detail(self, source: CourseSource, course_id: int) -> CourseDetailSnapshot:
        """Load cached sections, grades, and known cmids for a single course (RAM-accelerated)."""
        detail_path = self._detail_path(source, course_id)
        with get_memory_lock(detail_path):
            sig = self._stat_sig(detail_path)
            if sig is None:
                self._mem_details.pop(detail_path, None)
                return CourseDetailSnapshot()
            cached_mem = self._mem_details.get(detail_path)
            if cached_mem is not None and cached_mem[0] == sig:
                return cached_mem[1]

            payload = SafeFileIO.read_json_safe(detail_path, dict)
            if not isinstance(payload, dict) or not payload:
                self._mem_details.pop(detail_path, None)
                return CourseDetailSnapshot()

            known_cmids: list[int] = []
            raw_cmids = payload.get("known_cmids")
            if isinstance(raw_cmids, list):
                for raw_cmid in raw_cmids:
                    cmid = _safe_opt_int(raw_cmid)
                    if cmid is not None:
                        known_cmids.append(cmid)

            snap = CourseDetailSnapshot(
                sections=_parse_dict_items(payload.get("sections"), _section_from_dict),
                grades=_parse_dict_items(payload.get("grades"), _grade_from_dict),
                last_synced_at=_safe_int(payload.get("last_synced_at"), 0),
                known_cmids=known_cmids,
            )
            post_sig = self._stat_sig(detail_path)
            if post_sig is not None:
                self._mem_details[detail_path] = (post_sig, snap)
            return snap

    def save_course_detail(
        self,
        source: CourseSource,
        course_id: int,
        sections: list[CourseSection],
        grades: list[CourseGradeItem],
        *,
        synced_at: int,
        known_cmids: Optional[list[int]] = None,
    ) -> None:
        """Persist sections, grades, and known cmids for a single course."""
        detail_path = self._detail_path(source, course_id)
        with get_memory_lock(detail_path):
            self._mem_details.pop(detail_path, None)
            self._dir.mkdir(parents=True, exist_ok=True)
            resolved_cmids = (
                [m.cmid for s in sections for m in s.modules]
                if known_cmids is None
                else [
                    cmid
                    for cmid in (_safe_opt_int(x, minimum=0) for x in known_cmids)
                    if cmid is not None
                ]
            )
            payload = {
                "version": self.SCHEMA_VERSION,
                "sections": [asdict(section) for section in sections],
                "grades": [asdict(grade) for grade in grades],
                "last_synced_at": int(synced_at),
                "known_cmids": resolved_cmids,
            }
            if not SafeFileIO.write_json_atomic(detail_path, payload):
                logger.warning("Failed to persist course detail cache: %s", detail_path)
            else:
                post_sig = self._stat_sig(detail_path)
                if post_sig is not None:
                    self._mem_details[detail_path] = (
                        post_sig,
                        CourseDetailSnapshot(
                            sections=list(sections),
                            grades=list(grades),
                            last_synced_at=int(synced_at),
                            known_cmids=resolved_cmids,
                        ),
                    )
