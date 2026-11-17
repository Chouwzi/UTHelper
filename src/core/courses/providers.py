"""Data providers for unified Courses (REST WS) and THNN (AJAX/HTML session)."""

from __future__ import annotations

import html
import json
import logging
import math
import posixpath
from typing import TYPE_CHECKING, Any, Callable, Optional, Protocol
from urllib.parse import unquote, urljoin, urlsplit

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

_ARCHIVE_EXTS = frozenset(
    {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".tbz2", ".xz", ".txz"}
)
_DOC_EXTS = frozenset({".docx", ".doc", ".docm"})
_XLS_EXTS = frozenset({".xlsx", ".xls", ".xlsm"})
_PPT_EXTS = frozenset({".pptx", ".ppt", ".pptm"})
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
_PDF_MIMES = frozenset({"application/pdf", "application/x-pdf"})
_CSV_MIMES = frozenset({"text/csv", "application/csv", "text/tab-separated-values"})
_DOC_MIMES = frozenset(
    {
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-word.document.macroenabled.12",
    }
)
_XLS_MIMES = frozenset(
    {
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel.sheet.macroenabled.12",
    }
)
_PPT_MIMES = frozenset(
    {
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.ms-powerpoint.presentation.macroenabled.12",
    }
)
_ARCHIVE_MIMES = frozenset(
    {
        "application/zip",
        "application/x-zip-compressed",
        "application/x-zip",
        "application/vnd.rar",
        "application/x-rar-compressed",
        "application/x-rar",
        "application/x-7z-compressed",
        "application/x-tar",
        "application/gzip",
        "application/x-gzip",
        "application/x-bzip2",
        "application/x-xz",
        "application/x-compressed",
    }
)
_WEBVIEW_ACTIVITY_MODS = frozenset(
    {
        "quiz",
        "attendance",
        "choice",
        "choicegroup",
        "feedback",
        "groupselect",
        "scorm",
        "vpl",
        "lesson",
        "h5pactivity",
        "lti",
        "workshop",
    }
)
_EXTERNAL_LINK_SELECTORS = (
    ".urlworkaround a[href]",
    ".resourceworkaround a[href]",
    "a[href*='pluginfile.php']",
    "a[href*='meet.google.com']",
    "a[href*='drive.google.com']",
)
_NON_CONTENT_TAGS = ("script", "style", "noscript", "template")


class CourseProviderError(RuntimeError):
    """Raised when a course catalog provider encounters a transport or API failure."""


class ThnnSessionError(CourseProviderError):
    """Raised when the THNN Moodle session is invalid or re-login fails."""


def classify_preview_kind(filename: str, mimetype: str = "") -> PreviewKind:
    """Classify a file attachment into a supported preview category."""
    ext = posixpath.splitext((filename or "").strip().lower())[1]
    mime = (mimetype or "").split(";", 1)[0].strip().lower()
    if ext == ".pdf" or mime in _PDF_MIMES:
        return "pdf"
    if ext in _DOC_EXTS or mime in _DOC_MIMES:
        return "docx"
    if ext in _XLS_EXTS or mime in _XLS_MIMES:
        return "xlsx"
    if ext in {".csv", ".tsv"} or mime in _CSV_MIMES:
        return "csv"
    if ext in _PPT_EXTS or mime in _PPT_MIMES:
        return "pptx"
    if ext in _IMAGE_EXTS or mime.startswith("image/"):
        return "image"
    if ext in _TEXT_EXTS or mime.startswith("text/"):
        return "text"
    if ext in _ARCHIVE_EXTS or mime in _ARCHIVE_MIMES:
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


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _unescape_text(value: Any) -> str:
    return html.unescape(str(value or "")).strip()


def _safe_http_url(url: Any, fallback: str = "") -> str:
    if not isinstance(url, str):
        return fallback
    raw = url.strip()
    if not raw or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        return fallback
    parsed = urlsplit(raw)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return fallback
    return raw


def _sanitize_course_html(raw_html: Any) -> str:
    if not raw_html:
        return ""
    soup = BeautifulSoup(str(raw_html), BS4_PARSER)
    for tag in list(soup.find_all(_NON_CONTENT_TAGS)):
        if not getattr(tag, "decomposed", False):
            tag.decompose()
    return HTMLSanitizer.sanitize(str(soup))


def _same_origin(url_a: str, url_b: str) -> bool:
    a, b = urlsplit(url_a), urlsplit(url_b)
    return (a.scheme.lower(), a.netloc.lower()) == (b.scheme.lower(), b.netloc.lower())


def _is_ajax_session_error(res: dict[str, Any]) -> bool:
    exc = res.get("exception")
    exc_code = exc.get("errorcode", "") if isinstance(exc, dict) else ""
    exc_msg = exc.get("message", "") if isinstance(exc, dict) else ""
    errorcode = f"{res.get('errorcode') or ''} {exc_code}".lower()
    message = f"{res.get('message') or ''} {exc_msg}".lower()
    return (
        "requirelogin" in errorcode
        or "invalidsesskey" in errorcode
        or "sesskey" in message
    )


def _resolve_thnn_completion_mode(cm: dict[str, Any]) -> int:
    raw_completion = cm.get("completion")
    if (
        raw_completion is not None
        and not isinstance(raw_completion, bool)
        and _as_int(raw_completion, -1) in {0, 1, 2}
    ):
        return _as_int(raw_completion)
    if not (cm.get("hascompletion") or cm.get("isoverallcomplete") is not None):
        return 0
    if cm.get("isautomatic") is False:
        return 1
    return 2


def _build_attachment(raw: dict[str, Any]) -> CourseAttachment:
    file_name = _unescape_text(raw.get("filename") or raw.get("file_name")) or "file"
    file_url = _safe_http_url(raw.get("fileurl") or raw.get("file_url"), fallback="")
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
        self._cached_userid: Optional[int] = None

    def _resolve_userid(self) -> Optional[int]:
        if self._userid_resolver:
            uid = _as_int(self._userid_resolver())
            return uid if uid > 0 else None
        if self._cached_userid and self._cached_userid > 0:
            return self._cached_userid
        info = self._service.get_site_info()
        if not isinstance(info, dict):
            return None
        uid = _as_int(info.get("userid"))
        if uid > 0:
            self._cached_userid = uid
            return uid
        return None

    def fetch_courses(self) -> list[CourseSummary]:
        userid = self._resolve_userid()
        if not userid:
            return []
        raw_courses = _as_list(self._service.get_user_courses(userid))
        summaries: list[CourseSummary] = []
        for raw in raw_courses:
            if not isinstance(raw, dict):
                continue
            cid = _as_int(raw.get("id"))
            if cid <= 0:
                continue
            fullname = _unescape_text(raw.get("fullname"))
            shortname = _unescape_text(raw.get("shortname"))
            parsed = parse_course_identity(shortname, fullname)
            summary_html = _sanitize_course_html(raw.get("summary"))
            summary_text = BeautifulSoup(summary_html, BS4_PARSER).get_text(" ", strip=True)
            contact = extract_teacher_and_meet_info([summary_html])
            progress_val = raw.get("progress")
            progress: Optional[float] = None
            if isinstance(progress_val, (int, float)) and not isinstance(progress_val, bool):
                numeric_progress = float(progress_val)
                if math.isfinite(numeric_progress):
                    progress = min(1.0, max(0.0, numeric_progress / 100.0))
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
                    web_url=_safe_http_url(f"{self._origin}/course/view.php?id={cid}"),
                )
            )
        return summaries

    def _parse_ws_module(
        self, mod: dict[str, Any], sec_id: int, pages_by_cmid: dict[int, str]
    ) -> CourseModuleItem:
        cmid = _as_int(mod.get("id"))
        modname = str(mod.get("modname") or "unknown").lower()
        attachments: list[CourseAttachment] = []
        for raw_att in _as_list(mod.get("contents")):
            if not isinstance(raw_att, dict):
                continue
            att = _build_attachment(raw_att)
            if att.file_url:
                attachments.append(att)
        contents = tuple(attachments)

        desc_html = _sanitize_course_html(mod.get("description"))
        inline_html = pages_by_cmid.get(cmid, "") or desc_html
        external_url = contents[0].file_url if modname == "url" and contents else ""
        fallback_web_url = _safe_http_url(f"{self._origin}/mod/{modname}/view.php?id={cmid}")
        web_url = _safe_http_url(mod.get("url"), fallback=fallback_web_url)
        completion_data = mod.get("completiondata")
        completion_state = (
            _as_int(completion_data.get("state")) if isinstance(completion_data, dict) else 0
        )
        mod_times = [int(att.time_modified or 0) for att in contents]
        return CourseModuleItem(
            cmid=cmid,
            instance_id=_as_int(mod.get("instance")),
            modname=modname,
            name=_unescape_text(mod.get("name")),
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
        raw_sections = _as_list(self._service.get_course_contents(course_id))
        if not raw_sections:
            return []
        has_page_mod = any(
            isinstance(mod, dict) and str(mod.get("modname") or "").lower() == "page"
            for sec in raw_sections
            if isinstance(sec, dict)
            for mod in _as_list(sec.get("modules"))
        )
        pages = _as_list(self._service.get_pages_by_courses(course_id)) if has_page_mod else []
        pages_by_cmid: dict[int, str] = {}
        for p in pages:
            if not isinstance(p, dict):
                continue
            cmid = _as_int(p.get("coursemodule"))
            if cmid > 0:
                pages_by_cmid[cmid] = _sanitize_course_html(p.get("content"))

        sections: list[CourseSection] = []
        for sec in raw_sections:
            if not isinstance(sec, dict):
                continue
            sec_id = _as_int(sec.get("id"))
            sec_num = _as_int(sec.get("section"))
            sec_name = _unescape_text(sec.get("name")) or f"Chủ đề {sec_num}"
            sec_summary = _sanitize_course_html(sec.get("summary"))
            modules = tuple(
                self._parse_ws_module(mod, sec_id, pages_by_cmid)
                for mod in _as_list(sec.get("modules"))
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
        instances = _as_list(self._service.get_course_updates_since(course_id, since))
        return {
            cid
            for item in instances
            if isinstance(item, dict) and (cid := _as_int(item.get("id"))) > 0
        }

    def fetch_course_grades(self, course_id: int) -> list[CourseGradeItem]:
        userid = self._resolve_userid()
        if not userid:
            return []
        raw_items = _as_list(self._service.get_grade_items(course_id, userid))
        grades: list[CourseGradeItem] = []
        for idx, item in enumerate(raw_items):
            if not isinstance(item, dict):
                continue
            name = _unescape_text(item.get("itemname") or item.get("itemtype"))
            if not name:
                continue
            grades.append(
                CourseGradeItem(
                    id=_as_int(item.get("id"), idx),
                    item_name=name,
                    item_module=_unescape_text(item.get("itemmodule")),
                    grade_formatted=_unescape_text(item.get("gradeformatted")) or "-",
                    range_formatted=_unescape_text(item.get("rangeformatted")),
                    percentage=_unescape_text(item.get("percentageformatted")),
                    weight=_unescape_text(item.get("weightformatted")),
                    feedback_html=_sanitize_course_html(item.get("feedback")),
                )
            )
        return grades

    def set_manual_completion(self, cmid: int, completed: bool) -> bool:
        return bool(self._service.set_activity_completion_manually(cmid, completed))

    def download_file(
        self,
        url: str,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> Optional[bytes]:
        if self._client is None:
            return None
        safe_url = _safe_http_url(url)
        if not safe_url or not _same_origin(safe_url, self._origin):
            return None
        if progress_callback is not None:
            try:
                return self._client.download_file(
                    safe_url, progress_callback=progress_callback
                )
            except TypeError:
                pass
        return self._client.download_file(safe_url)

    def fetch_user_groups(self, course_id: int) -> list[dict[str, Any]]:
        getter = getattr(self._service, "get_course_user_groups", None)
        if not callable(getter):
            return []
        userid = self._resolve_userid() or 0
        try:
            groups = getter(int(course_id), user_id=int(userid))
        except TypeError:
            groups = getter(int(course_id))
        except Exception as exc:
            logger.warning("Failed to fetch user groups (course=%s): %s", course_id, exc)
            return []
        return [dict(g) for g in _as_list(groups) if isinstance(g, dict)]


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

    def _call_ajax_with_relogin(self, fn: str, **params: Any) -> dict[str, Any]:
        res = self._client.call_ajax_api(fn, **params)
        if isinstance(res, dict) and res.get("error") and _is_ajax_session_error(res):
            if not self._client.login_session(force=True):
                raise ThnnSessionError(f"THNN session re-login failed for {fn}")
            res = self._client.call_ajax_api(fn, **params)
        if not isinstance(res, dict):
            raise CourseProviderError(f"THNN AJAX call returned invalid response for {fn}")
        if res.get("error"):
            msg = str(res.get("message") or res.get("errorcode") or "AJAX error")
            if _is_ajax_session_error(res):
                raise ThnnSessionError(f"THNN session error in {fn}: {msg}")
            raise CourseProviderError(f"THNN AJAX error in {fn}: {msg}")
        return res

    def fetch_courses(self) -> list[CourseSummary]:
        base = self._base_url()
        limit = 50
        max_pages = 20
        offset = 0
        seen_ids: set[int] = set()
        summaries: list[CourseSummary] = []

        for _ in range(max_pages):
            res = self._call_ajax_with_relogin(
                "core_course_get_enrolled_courses_by_timeline_classification",
                classification="all",
                limit=limit,
                offset=offset,
                sort="fullname",
            )
            data = res.get("data")
            raw_courses = _as_list(data.get("courses")) if isinstance(data, dict) else []
            if not raw_courses:
                break
            for raw in raw_courses:
                if not isinstance(raw, dict):
                    continue
                cid = _as_int(raw.get("id"))
                if cid <= 0 or cid in seen_ids:
                    continue
                seen_ids.add(cid)
                fullname = _unescape_text(raw.get("fullname"))
                shortname = _unescape_text(raw.get("shortname"))
                parsed = parse_course_identity(shortname, fullname)
                fallback_view = _safe_http_url(f"{base}/course/view.php?id={cid}")
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
                        web_url=_safe_http_url(raw.get("viewurl"), fallback=fallback_view),
                    )
                )
            if len(raw_courses) < limit:
                break
            offset += limit
        return summaries

    def fetch_course_sections(self, course_id: int) -> list[CourseSection]:
        res = self._call_ajax_with_relogin("core_courseformat_get_state", courseid=course_id)
        data = res.get("data")
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:
                return []
        if not isinstance(data, dict):
            return []

        cm_map = {
            cid: c
            for c in _as_list(data.get("cm"))
            if isinstance(c, dict) and (cid := _as_int(c.get("id"))) > 0
        }
        base = self._base_url()
        sections: list[CourseSection] = []
        for sec in _as_list(data.get("section")):
            if not isinstance(sec, dict):
                continue
            sec_id = _as_int(sec.get("id"))
            sec_num = _as_int(sec.get("section"))
            sec_name = _unescape_text(sec.get("title")) or f"Chủ đề {sec_num}"
            mods: list[CourseModuleItem] = []
            for cmid_raw in _as_list(sec.get("cmlist")):
                cmid_key = _as_int(cmid_raw)
                if cmid_key <= 0:
                    continue
                cm = cm_map.get(cmid_key)
                if not cm:
                    continue
                cmid = _as_int(cm.get("id"))
                modname = str(cm.get("module") or "unknown").lower()
                fallback_url = _safe_http_url(f"{base}/mod/{modname}/view.php?id={cmid}")
                raw_url = _safe_http_url(cm.get("url"), fallback=fallback_url)
                mods.append(
                    CourseModuleItem(
                        cmid=cmid,
                        instance_id=0,
                        modname=modname,
                        name=_unescape_text(cm.get("name")),
                        section_id=sec_id,
                        url=raw_url,
                        web_url=raw_url,
                        completion_mode=_resolve_thnn_completion_mode(cm),
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
        web_url = _safe_http_url(module.web_url)
        if not web_url:
            return ""
        base = self._base_url()
        if not _same_origin(web_url, base):
            return web_url
        try:
            final_url, html_text, redirected_cross_origin = self._client.open_session_url(web_url)
        except Exception as exc:
            logger.warning("Failed to resolve THNN module URL (%s): %s", web_url, exc)
            return web_url

        safe_final = _safe_http_url(urljoin(f"{base}/", final_url) if final_url else "")
        if redirected_cross_origin or (safe_final and not _same_origin(safe_final, base)):
            return safe_final or web_url
        if safe_final and urlsplit(safe_final).path == "/login/index.php":
            return web_url

        soup = BeautifulSoup(html_text or "", BS4_PARSER)
        for selector in _EXTERNAL_LINK_SELECTORS:
            anchor = soup.select_one(selector)
            href = str(anchor.get("href") or "").strip() if anchor else ""
            if not href:
                continue
            candidate = _safe_http_url(urljoin(f"{base}/", href))
            if candidate:
                return candidate
        return safe_final or web_url

    def resolve_module_attachments(
        self, module: CourseModuleItem
    ) -> tuple[CourseAttachment, ...]:
        """Extract downloadable ``pluginfile.php`` attachments from a THNN resource/folder page."""
        if module.attachments:
            return module.attachments
        if self._client is None:
            return ()
        web_url = _safe_http_url(module.web_url or module.url)
        base = self._base_url()
        if not web_url or not _same_origin(web_url, base):
            return ()
        try:
            final_url, html_text, redirected_cross_origin = self._client.open_session_url(web_url)
        except Exception as exc:
            logger.warning("THNN attachment resolution failed (%s): %s", web_url, exc)
            return ()
        if redirected_cross_origin:
            return ()

        attachments: list[CourseAttachment] = []
        seen_urls: set[str] = set()

        def _append_file(file_url: str, preferred_name: str = "") -> None:
            safe_file = _safe_http_url(urljoin(f"{base}/", file_url))
            if not safe_file or not _same_origin(safe_file, base) or safe_file in seen_urls:
                return
            seen_urls.add(safe_file)
            path_name = unquote(posixpath.basename(urlsplit(safe_file).path) or "").strip()
            clean_name = preferred_name.strip() or path_name or module.name or "Tài liệu"
            if "." not in clean_name and "." in path_name:
                clean_name = path_name
            ext = posixpath.splitext(clean_name.lower())[1]
            kind = classify_preview_kind(clean_name)
            attachments.append(
                CourseAttachment(
                    file_name=clean_name,
                    file_url=safe_file,
                    extension=ext,
                    preview_kind=kind,
                    is_archive=(kind == "archive"),
                )
            )

        if "/pluginfile.php/" in (final_url or ""):
            _append_file(final_url, module.name)

        soup = BeautifulSoup(html_text or "", BS4_PARSER)
        for selector in ("a[href*='pluginfile.php']", ".resourceworkaround a[href]"):
            for anchor in soup.select(selector):
                href = str(anchor.get("href") or "").strip()
                if not href or "pluginfile.php" not in href:
                    continue
                link_text = " ".join(anchor.get_text(" ", strip=True).split())
                _append_file(href, link_text)
        return tuple(attachments)

    def fetch_course_grades(self, course_id: int) -> list[CourseGradeItem]:
        """Parse THNN user gradebook HTML from ``/grade/report/user/index.php``."""
        if self._client is None:
            return []
        base = self._base_url()
        url = f"{base}/grade/report/user/index.php?id={int(course_id)}"
        try:
            _final_url, html_text, redirected_cross_origin = self._client.open_session_url(url)
        except Exception as exc:
            logger.warning("THNN gradebook fetch failed (course=%s): %s", course_id, exc)
            return []
        if redirected_cross_origin or not html_text:
            return []

        soup = BeautifulSoup(html_text, BS4_PARSER)
        table = soup.select_one("table.user-grade")
        if table is None:
            return []

        def _cell_text(row: Any, selector: str) -> str:
            cell = row.select_one(selector)
            if cell is None:
                return ""
            return " ".join(cell.get_text(" ", strip=True).split())

        grades: list[CourseGradeItem] = []
        for idx, row in enumerate(table.select("tbody tr")):
            classes = {str(cls) for cls in (row.get("class") or [])}
            name_cell = row.select_one("th.column-itemname, td.column-itemname")
            if name_cell is None:
                continue
            item_name = " ".join(name_cell.get_text(" ", strip=True).split())
            if not item_name:
                continue
            grade_val = _cell_text(row, "td.column-grade")
            range_val = _cell_text(row, "td.column-range")
            pct_val = _cell_text(row, "td.column-percentage")
            weight_val = _cell_text(row, "td.column-weight")
            feedback_val = _cell_text(row, "td.column-feedback")
            # Skip pure category header rows that carry neither grade nor range nor percentage
            is_course_total = "level1" in classes and "baggt" in classes
            if not any((grade_val, range_val, pct_val, weight_val, feedback_val)) and not is_course_total:
                continue
            item_module = (
                "course"
                if is_course_total
                else ("category" if "baggt" in classes else "mod")
            )
            grades.append(
                CourseGradeItem(
                    id=idx + 1,
                    item_name=item_name,
                    item_module=item_module,
                    grade_formatted=grade_val or "-",
                    range_formatted=range_val,
                    percentage=pct_val,
                    weight=weight_val,
                    feedback_html=_sanitize_course_html(feedback_val),
                )
            )
        return grades

    def download_file(
        self,
        url: str,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> Optional[bytes]:
        if self._client is None:
            return None
        safe_url = _safe_http_url(url)
        base = self._base_url()
        if not safe_url or not _same_origin(safe_url, base):
            return None
        if progress_callback is not None:
            try:
                return self._client.download_session_file(
                    safe_url, progress_callback=progress_callback
                )
            except TypeError:
                pass
        return self._client.download_session_file(safe_url)
