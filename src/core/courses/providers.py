"""Data providers for unified Courses (REST WS) and THNN (AJAX/HTML session)."""

from __future__ import annotations

import logging
import posixpath
import urllib.error
from typing import TYPE_CHECKING, Any, Callable, Optional, Protocol
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from core.courses.models import (
    CourseAttachment,
    CourseGradeItem,
    CourseModuleItem,
    CourseSection,
    CourseSource,
    CourseSummary,
    OpenStrategy,
    PreviewKind,
)
from core.courses.shortname_parser import extract_teacher_and_meet_info, parse_course_identity
from core.html_compat import BS4_PARSER
from core.security import HTMLSanitizer

if TYPE_CHECKING:
    from core.moodle_service import MoodleService

logger = logging.getLogger(__name__)

_ARCHIVE_EXTS = frozenset({".zip", ".rar", ".tar", ".gz", ".tgz"})
_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg"})
_TEXT_EXTS = frozenset(
    {
        ".txt",
        ".md",
        ".markdown",
        ".json",
        ".xml",
        ".html",
        ".htm",
        ".yaml",
        ".yml",
        ".py",
        ".js",
        ".ts",
        ".java",
        ".c",
        ".cpp",
        ".h",
        ".cs",
        ".sql",
        ".css",
        ".log",
    }
)
_WEBVIEW_ACTIVITY_MODS = frozenset(
    {"quiz", "attendance", "choice", "feedback", "groupselect", "scorm"}
)
_EXTERNAL_LINK_SELECTORS = (
    ".urlworkaround a[href]",
    ".resourceworkaround a[href]",
    "a[href*='pluginfile.php']",
    "a[href*='meet.google.com']",
    "a[href*='drive.google.com']",
)


def classify_preview_kind(filename: str, mimetype: str = "") -> PreviewKind:
    """Classify a file attachment into a supported preview category."""
    ext = posixpath.splitext((filename or "").strip().lower())[1]
    mime = (mimetype or "").lower()
    if ext == ".pdf" or "pdf" in mime:
        return "pdf"
    if ext == ".docx":
        return "docx"
    if ext == ".xlsx":
        return "xlsx"
    if ext in {".csv", ".tsv"} or "csv" in mime:
        return "csv"
    if ext == ".pptx":
        return "pptx"
    if ext in _IMAGE_EXTS or mime.startswith("image/"):
        return "image"
    if ext in _TEXT_EXTS or mime.startswith("text/"):
        return "text"
    if ext in _ARCHIVE_EXTS or "zip" in mime or "rar" in mime or "tar" in mime:
        return "archive"
    return "unsupported"


def classify_open_strategy(
    modname: str, attachments: tuple[CourseAttachment, ...]
) -> OpenStrategy:
    """Determine how a Moodle course module should be opened inside UTHelper."""
    mod = (modname or "").lower()
    if mod == "assign":
        return "assignment_detail"
    if mod in _WEBVIEW_ACTIVITY_MODS:
        return "webview_activity"
    if mod in {"url", "googlemeet"}:
        return "external_url"
    if mod in {"page", "label"}:
        return "inline_page"
    if mod == "folder":
        return "folder_files"
    if mod == "resource" and attachments:
        if any(att.is_archive for att in attachments):
            return "archive_tree"
        return "preview_file"
    return "web_fallback"


def _as_int(value: Any, default: int = 0) -> int:
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _build_attachment(raw: dict[str, Any]) -> CourseAttachment:
    file_name = str(raw.get("filename") or raw.get("file_name") or "file").strip()
    file_url = str(raw.get("fileurl") or raw.get("file_url") or "").strip()
    mime = str(raw.get("mimetype") or raw.get("mime_type") or "").strip()
    ext = posixpath.splitext(file_name.lower())[1]
    kind = classify_preview_kind(file_name, mime)
    return CourseAttachment(
        file_name=file_name,
        file_url=file_url,
        file_size=_as_int(raw.get("filesize")),
        mime_type=mime,
        time_modified=_as_int(raw.get("timemodified")),
        extension=ext,
        preview_kind=kind,
        is_archive=(kind == "archive"),
    )


def _same_origin(url_a: str, url_b: str) -> bool:
    a, b = urlsplit(url_a), urlsplit(url_b)
    return (a.scheme.lower(), a.netloc.lower()) == (b.scheme.lower(), b.netloc.lower())


class CourseCatalogProvider(Protocol):
    """Unified interface for reading course catalogs and course contents across sites."""

    source: CourseSource

    def fetch_courses(self) -> list[CourseSummary]: ...
    def fetch_course_sections(self, course_id: int) -> list[CourseSection]: ...
    def fetch_course_grades(self, course_id: int) -> list[CourseGradeItem]: ...
    def download_file(self, url: str) -> Optional[bytes]: ...


class CoursesWsProvider:
    """Course catalog and material provider backed by Moodle REST Web Services."""

    source: CourseSource = "courses"

    def __init__(
        self,
        *,
        service: MoodleService | Any,
        client: Any = None,
        origin: str = "https://courses.ut.edu.vn",
        userid_resolver: Optional[Callable[[], Optional[int]]] = None,
    ) -> None:
        self._service = service
        self._client = client
        self._origin = origin.rstrip("/")
        self._userid_resolver = userid_resolver

    def _resolve_userid(self) -> Optional[int]:
        if self._userid_resolver:
            return self._userid_resolver()
        info = self._service.get_site_info()
        if not isinstance(info, dict):
            return None
        uid = info.get("userid")
        if uid is None or isinstance(uid, bool):
            return None
        try:
            return int(uid)
        except (TypeError, ValueError):
            return None

    def fetch_courses(self) -> list[CourseSummary]:
        userid = self._resolve_userid()
        if not userid:
            return []
        raw_courses = self._service.get_user_courses(userid)
        if not isinstance(raw_courses, list):
            return []
        summaries: list[CourseSummary] = []
        for raw in raw_courses:
            if not isinstance(raw, dict):
                continue
            cid = _as_int(raw.get("id"))
            if not cid:
                continue
            fullname = str(raw.get("fullname") or "")
            shortname = str(raw.get("shortname") or "")
            parsed = parse_course_identity(shortname, fullname)
            summary_html = HTMLSanitizer.sanitize(str(raw.get("summary") or ""))
            summary_text = BeautifulSoup(summary_html, BS4_PARSER).get_text(" ", strip=True)
            contact = extract_teacher_and_meet_info([summary_html])
            progress_val = raw.get("progress")
            progress = (
                float(progress_val) / 100.0
                if isinstance(progress_val, (int, float)) and not isinstance(progress_val, bool)
                else None
            )
            summaries.append(
                CourseSummary(
                    id=cid,
                    source="courses",
                    fullname=fullname,
                    shortname=shortname,
                    display_name=parsed.display_name,
                    course_code=parsed.course_code,
                    semester_label=parsed.semester_label,
                    semester_sort_key=parsed.semester_sort_key,
                    program_tag=parsed.program_tag,
                    teacher_name=contact.teacher_name,
                    teacher_email=contact.teacher_email,
                    teacher_phone=contact.teacher_phone,
                    meet_url=contact.meet_url,
                    progress=progress,
                    summary_text=summary_text,
                    web_url=f"{self._origin}/course/view.php?id={cid}",
                )
            )
        return summaries

    def _parse_ws_module(
        self, mod: dict[str, Any], sec_id: int, pages_by_cmid: dict[int, str]
    ) -> CourseModuleItem:
        cmid = _as_int(mod.get("id"))
        modname = str(mod.get("modname") or "unknown").lower()
        contents = tuple(
            _build_attachment(att)
            for att in (mod.get("contents") or [])
            if isinstance(att, dict) and (att.get("fileurl") or att.get("file_url"))
        )
        desc_html = HTMLSanitizer.sanitize(str(mod.get("description") or ""))
        inline_html = pages_by_cmid.get(cmid, "") or desc_html
        external_url = ""
        if modname == "url" and contents:
            candidate_url = contents[0].file_url
            if urlsplit(candidate_url).scheme.lower() in {"http", "https"}:
                external_url = candidate_url
        web_url = str(mod.get("url") or f"{self._origin}/mod/{modname}/view.php?id={cmid}")
        completion_data = mod.get("completiondata")
        completion_state = (
            _as_int(completion_data.get("state")) if isinstance(completion_data, dict) else 0
        )
        mod_times = [int(att.time_modified or 0) for att in contents]
        return CourseModuleItem(
            cmid=cmid,
            instance_id=_as_int(mod.get("instance")),
            modname=modname,
            name=str(mod.get("name") or ""),
            section_id=sec_id,
            url=external_url or web_url,
            web_url=web_url,
            description_html=desc_html,
            inline_html=inline_html,
            completion_mode=_as_int(mod.get("completion")),
            completion_state=completion_state,
            timemodified=max(mod_times, default=0),
            attachments=contents,
            open_strategy=classify_open_strategy(modname, contents),
        )

    def fetch_course_sections(self, course_id: int) -> list[CourseSection]:
        raw_sections = self._service.get_course_contents(course_id)
        if not isinstance(raw_sections, list):
            return []
        pages = self._service.get_pages_by_courses(course_id)
        pages_by_cmid: dict[int, str] = {}
        if isinstance(pages, list):
            for p in pages:
                if not isinstance(p, dict):
                    continue
                cmid = _as_int(p.get("coursemodule"))
                if cmid:
                    pages_by_cmid[cmid] = HTMLSanitizer.sanitize(str(p.get("content") or ""))

        sections: list[CourseSection] = []
        for sec in raw_sections:
            if not isinstance(sec, dict):
                continue
            sec_id = _as_int(sec.get("id"))
            sec_num = _as_int(sec.get("section"))
            sec_name = str(sec.get("name") or f"Chủ đề {sec_num}")
            sec_summary = HTMLSanitizer.sanitize(str(sec.get("summary") or ""))
            modules = tuple(
                self._parse_ws_module(mod, sec_id, pages_by_cmid)
                for mod in (sec.get("modules") or [])
                if isinstance(mod, dict)
            )
            sections.append(
                CourseSection(
                    id=sec_id,
                    course_id=course_id,
                    source="courses",
                    section_num=sec_num,
                    name=sec_name,
                    summary_html=sec_summary,
                    visible=bool(sec.get("visible", 1)),
                    modules=modules,
                )
            )
        return sections

    def fetch_updates_since(self, course_id: int, since: int) -> set[int]:
        instances = self._service.get_course_updates_since(course_id, since)
        if not isinstance(instances, list):
            return set()
        return {
            cid
            for item in instances
            if isinstance(item, dict) and (cid := _as_int(item.get("id"))) > 0
        }

    def fetch_course_grades(self, course_id: int) -> list[CourseGradeItem]:
        userid = self._resolve_userid()
        if not userid:
            return []
        raw_items = self._service.get_grade_items(course_id, userid)
        if not isinstance(raw_items, list):
            return []
        grades: list[CourseGradeItem] = []
        for idx, item in enumerate(raw_items):
            if not isinstance(item, dict):
                continue
            name = str(item.get("itemname") or item.get("itemtype") or "").strip()
            if not name:
                continue
            grades.append(
                CourseGradeItem(
                    id=_as_int(item.get("id"), idx),
                    item_name=name,
                    item_module=str(item.get("itemmodule") or ""),
                    grade_formatted=str(item.get("gradeformatted") or "-"),
                    range_formatted=str(item.get("rangeformatted") or ""),
                    percentage=str(item.get("percentageformatted") or ""),
                    weight=str(item.get("weightformatted") or ""),
                    feedback_html=HTMLSanitizer.sanitize(str(item.get("feedback") or "")),
                )
            )
        return grades

    def set_manual_completion(self, cmid: int, completed: bool) -> bool:
        return bool(self._service.set_activity_completion_manually(cmid, completed))

    def download_file(self, url: str) -> Optional[bytes]:
        if self._client is None:
            return None
        return self._client.download_file(url)


class ThnnCourseProvider:
    """Course catalog and section provider backed by THNN Moodle AJAX/HTML session."""

    source: CourseSource = "thnn"

    def __init__(self, *, client: Any) -> None:
        self._client = client

    def _base_url(self) -> str:
        # Real MoodleClient exposes moodle_site_origin while lightweight fakes may define base_url.
        for attr in ("moodle_site_origin", "base_url"):
            val = getattr(self._client, attr, None)
            if isinstance(val, str) and val.strip():
                return val.strip().rstrip("/")
        return "https://thnn.ut.edu.vn"

    def fetch_courses(self) -> list[CourseSummary]:
        res = self._client.call_ajax_api(
            "core_course_get_enrolled_courses_by_timeline_classification",
            classification="all",
            limit=50,
            offset=0,
            sort="fullname",
        )
        if not isinstance(res, dict) or res.get("error"):
            return []
        data = res.get("data")
        if not isinstance(data, dict):
            return []
        raw_courses = data.get("courses")
        if not isinstance(raw_courses, list):
            return []

        base = self._base_url()
        summaries: list[CourseSummary] = []
        for raw in raw_courses:
            if not isinstance(raw, dict):
                continue
            cid = _as_int(raw.get("id"))
            if not cid:
                continue
            fullname = str(raw.get("fullname") or "")
            shortname = str(raw.get("shortname") or "")
            parsed = parse_course_identity(shortname, fullname)
            summaries.append(
                CourseSummary(
                    id=cid,
                    source="thnn",
                    fullname=fullname,
                    shortname=shortname,
                    display_name=parsed.display_name,
                    course_code=parsed.course_code,
                    semester_label=parsed.semester_label,
                    semester_sort_key=parsed.semester_sort_key,
                    program_tag="THNN",
                    web_url=str(raw.get("viewurl") or f"{base}/course/view.php?id={cid}"),
                )
            )
        return summaries

    def fetch_course_sections(self, course_id: int) -> list[CourseSection]:
        res = self._client.call_ajax_api("core_courseformat_get_state", courseid=course_id)
        if not isinstance(res, dict) or res.get("error"):
            return []
        data = res.get("data")
        if not isinstance(data, dict):
            return []

        cm_map = {
            cid: c
            for c in (data.get("cm") or [])
            if isinstance(c, dict) and (cid := _as_int(c.get("id"))) > 0
        }
        base = self._base_url()
        sections: list[CourseSection] = []
        for sec in data.get("section") or []:
            if not isinstance(sec, dict):
                continue
            sec_id = _as_int(sec.get("id"))
            sec_num = _as_int(sec.get("section"))
            sec_name = str(sec.get("title") or f"Chủ đề {sec_num}")
            mods: list[CourseModuleItem] = []
            for cmid_raw in sec.get("cmlist") or []:
                cmid_key = _as_int(cmid_raw)
                if not cmid_key:
                    continue
                cm = cm_map.get(cmid_key)
                if not cm:
                    continue
                cmid = _as_int(cm.get("id"))
                modname = str(cm.get("module") or "unknown").lower()
                raw_url = str(cm.get("url") or f"{base}/mod/{modname}/view.php?id={cmid}")
                mods.append(
                    CourseModuleItem(
                        cmid=cmid,
                        instance_id=0,
                        modname=modname,
                        name=str(cm.get("name") or ""),
                        section_id=sec_id,
                        url=raw_url,
                        web_url=raw_url,
                        completion_mode=(
                            1
                            if cm.get("isoverallcomplete") is not None or cm.get("hascompletion")
                            else 0
                        ),
                        completion_state=_as_int(cm.get("completionstate")),
                        open_strategy=classify_open_strategy(modname, ()),
                    )
                )
            sections.append(
                CourseSection(
                    id=sec_id,
                    course_id=course_id,
                    source="thnn",
                    section_num=sec_num,
                    name=sec_name,
                    visible=bool(sec.get("visible", True)),
                    modules=tuple(mods),
                )
            )
        return sections

    def resolve_module_external_url(self, module: CourseModuleItem) -> str:
        """Lazily resolve external target URL from THNN mod_url/mod_googlemeet/mod_resource HTML."""
        if not module.web_url:
            return ""
        if getattr(self._client, "_sesskey", None) == "" and hasattr(
            self._client, "login_session"
        ):
            self._client.login_session()
        base = self._base_url()
        parsed = urlsplit(module.web_url)
        path = f"{parsed.path}?{parsed.query}" if parsed.query else parsed.path
        try:
            final_url, html_text = self._client._open_html(path)
        except urllib.error.HTTPError as exc:
            # Moodle mod_url "Automatic" mode redirects cross-origin, which MoodleClient blocks safely.
            redirected_url = str(getattr(exc, "url", "") or "")
            if (
                exc.msg == "Untrusted Moodle redirect"
                and redirected_url
                and urlsplit(redirected_url).scheme.lower() in {"http", "https"}
            ):
                return redirected_url
            raise
        resolved_final = urljoin(f"{base}/", final_url) if final_url else ""
        if resolved_final and not _same_origin(resolved_final, base):
            return resolved_final

        soup = BeautifulSoup(html_text or "", BS4_PARSER)
        for selector in _EXTERNAL_LINK_SELECTORS:
            anchor = soup.select_one(selector)
            href = str(anchor.get("href") or "").strip() if anchor else ""
            if not href:
                continue
            candidate = urljoin(f"{base}/", href)
            if urlsplit(candidate).scheme.lower() in {"http", "https"}:
                return candidate
        return resolved_final or module.web_url

    def fetch_course_grades(self, course_id: int) -> list[CourseGradeItem]:
        return []

    def download_file(self, url: str) -> Optional[bytes]:
        if self._client is None:
            return None
        return self._client.download_file(url)
