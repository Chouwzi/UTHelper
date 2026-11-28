"""Allow-listed zero-PII schemas, config validators, and coarse bucket helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
import ipaddress
import json
import math
import re
from typing import Any, Literal
from urllib.parse import unquote, urlsplit
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

from analytics.uuid7 import extract_uuid7_ms

ANALYTICS_SCHEMA_VERSION = 1
ANALYTICS_LIB_NAME = "uthelper-python"
MAX_EVENT_BYTES = 4 * 1024
MAX_HOST_LENGTH = 2048

_POSTHOG_PUBLIC_KEY_RE = re.compile(r"^phc_[A-Za-z0-9_-]{20,128}$")
_POSTHOG_WEB_UI_HOSTS = frozenset(
    {
        "app.posthog.com",
        "us.posthog.com",
        "eu.posthog.com",
    }
)
_FORBIDDEN_KEY_MARKERS = (
    "auth_token",
    "authtoken",
    "bearer",
    "management-token",
    "management_token",
    "personal",
    "phs_",
    "phx_",
    "secret",
    "sentry_auth_token",
    "sntrys_",
)
_HOSTNAME_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_APP_VERSION_RE = re.compile(
    r"^(?:(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:[-+][A-Za-z0-9._-]{1,32})?|unknown)$"
)
_UTC_MS_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"
)


class AnalyticsConfigError(ValueError):
    """Raised when a PostHog public key or ingestion host is unsafe or malformed."""


def validate_posthog_api_key(value: str) -> str:
    """Validate a write-only PostHog project key (``phc_...``), rejecting secrets."""
    if not isinstance(value, str):
        raise AnalyticsConfigError("PostHog project API key must be text")
    if not value or value != value.strip():
        raise AnalyticsConfigError(
            "PostHog project API key is empty or contains surrounding whitespace"
        )
    if any(ord(ch) < 0x21 or ord(ch) > 0x7E for ch in value):
        raise AnalyticsConfigError(
            "PostHog project API key contains whitespace or control characters"
        )

    lowered = value.casefold()
    decoded = unquote(value).casefold()
    if any(marker in lowered or marker in decoded for marker in _FORBIDDEN_KEY_MARKERS):
        raise AnalyticsConfigError(
            "Personal/secret/management tokens are forbidden in public analytics config"
        )
    if not _POSTHOG_PUBLIC_KEY_RE.fullmatch(value):
        raise AnalyticsConfigError(
            "PostHog project API key must match ^phc_[A-Za-z0-9_-]{20,128}$"
        )
    return value


def validate_posthog_host(
    value: str,
    *,
    allow_private_dev: bool = False,
) -> str:
    """Validate a canonical HTTPS PostHog ingestion origin or reverse-proxy base URL."""
    if not isinstance(value, str):
        raise AnalyticsConfigError("PostHog host must be text")
    if not value or len(value) > MAX_HOST_LENGTH or value != value.strip():
        raise AnalyticsConfigError("PostHog host is empty, padded, or too long")
    if any(ord(ch) < 0x21 or ord(ch) > 0x7E for ch in value):
        raise AnalyticsConfigError(
            "PostHog host contains whitespace or control characters"
        )
    if "\\" in value or "%" in value:
        raise AnalyticsConfigError(
            "PostHog host must not contain backslashes or percent-encoding"
        )

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise AnalyticsConfigError("PostHog host is not a valid URL") from exc

    if parsed.scheme != "https" or not value.startswith("https://"):
        raise AnalyticsConfigError("PostHog ingestion requires canonical lowercase https://")
    if parsed.username is not None or parsed.password is not None:
        raise AnalyticsConfigError("PostHog host must not contain credentials")
    if parsed.query or parsed.fragment:
        raise AnalyticsConfigError(
            "PostHog host must not contain query or fragment components"
        )
    raw_netloc_host = parsed.netloc.rsplit("@", 1)[-1]
    if raw_netloc_host.startswith("["):
        raw_netloc_host = raw_netloc_host.split("]", 1)[0][1:]
    elif ":" in raw_netloc_host:
        raw_netloc_host = raw_netloc_host.split(":", 1)[0]
    hostname = parsed.hostname
    if (
        not hostname
        or hostname != hostname.casefold()
        or raw_netloc_host != raw_netloc_host.casefold()
    ):
        raise AnalyticsConfigError("PostHog host must use a canonical lowercase hostname")
    if any(ord(ch) >= 0x80 for ch in hostname):
        raise AnalyticsConfigError("PostHog host must be ASCII/punycode canonical")
    if port is not None and not 1 <= port <= 65535:
        raise AnalyticsConfigError("PostHog host port is invalid")
    if hostname in _POSTHOG_WEB_UI_HOSTS:
        raise AnalyticsConfigError(
            f"{hostname!r} is a PostHog web dashboard host; use https://us.i.posthog.com, "
            "https://eu.i.posthog.com, or an HTTPS ingestion reverse proxy"
        )

    is_local_domain = hostname == "localhost" or hostname.endswith(
        (".localhost", ".local")
    )
    if is_local_domain and not allow_private_dev:
        raise AnalyticsConfigError(
            "PostHog host must not target localhost or .local domains in release mode"
        )

    ip_literal: ipaddress.IPv4Address | ipaddress.IPv6Address | None = None
    try:
        ip_literal = ipaddress.ip_address(hostname)
    except ValueError:
        ip_literal = None

    if ip_literal is not None:
        effective_ip: ipaddress.IPv4Address | ipaddress.IPv6Address = ip_literal
        if (
            isinstance(ip_literal, ipaddress.IPv6Address)
            and ip_literal.ipv4_mapped is not None
        ):
            effective_ip = ip_literal.ipv4_mapped
        if (
            ip_literal.is_unspecified
            or ip_literal.is_multicast
            or effective_ip.is_unspecified
            or effective_ip.is_multicast
        ):
            raise AnalyticsConfigError(
                "PostHog host must not target unspecified or multicast IPs"
            )
        if (
            not ip_literal.is_global or not effective_ip.is_global
        ) and not allow_private_dev:
            raise AnalyticsConfigError(
                "PostHog host must not target private, loopback, or reserved IPs"
            )
    elif hostname != "localhost":
        labels = hostname.split(".")
        if (
            len(labels) < 2
            or any(not _HOSTNAME_LABEL_RE.fullmatch(label) for label in labels)
            or labels[-1].isdigit()
        ):
            raise AnalyticsConfigError("PostHog hostname is invalid")

    raw_path = parsed.path or ""
    raw_segments = raw_path.split("/")
    # Allow empty path or single trailing slash, reject middle '//' empty segments.
    middle_segments = raw_segments[1:-1] if raw_path.endswith("/") else raw_segments[1:]
    if any(seg == "" for seg in middle_segments if raw_path not in {"", "/"}):
        raise AnalyticsConfigError("PostHog host path must not contain empty '//' segments")

    clean_segments = [seg for seg in raw_segments if seg]
    if any(seg in {".", ".."} for seg in clean_segments):
        raise AnalyticsConfigError("PostHog host path must not contain dot segments")
    if any(
        not re.fullmatch(r"[A-Za-z0-9._-]+", seg)
        for seg in clean_segments
    ):
        raise AnalyticsConfigError("PostHog host path contains unsafe characters")

    host_netloc = f"[{hostname}]" if isinstance(ip_literal, ipaddress.IPv6Address) else hostname
    if port is not None:
        host_netloc = f"{host_netloc}:{port}"
    base_path = ("/" + "/".join(clean_segments)) if clean_segments else ""
    return f"https://{host_netloc}{base_path}"


class AnalyticsConsent(StrEnum):
    """Independent user choice controlling anonymous PostHog usage analytics."""

    NOT_ASKED = "not_asked"
    ENABLED = "enabled"
    DISABLED = "disabled"


class ReleaseChannel(StrEnum):
    RELEASE = "release"
    DEVELOP = "develop"
    DEVELOPMENT = "development"


class DistributionChannelName(StrEnum):
    CHPLAY = "chplay"
    APK_LTS = "apk_lts"
    MSSTORE = "msstore"
    EXE_LTS = "exe_lts"
    IPA_LTS = "ipa_lts"
    SOURCE = "source"


class PackageFormatName(StrEnum):
    AAB = "aab"
    APK = "apk"
    MSIX = "msix"
    EXE_MSI = "exe_msi"
    EXE = "exe"
    MSI = "msi"
    IPA = "ipa"
    IOS_APP = "ios_app"
    SOURCE = "source"


class OsFamily(StrEnum):
    WINDOWS = "Windows"
    ANDROID = "Android"
    IOS = "iOS"
    LINUX = "Linux"
    DARWIN = "Darwin"
    UNKNOWN = "unknown"


class DeviceClass(StrEnum):
    DESKTOP = "desktop"
    MOBILE = "mobile"
    UNKNOWN = "unknown"


class EventName(StrEnum):
    """Strict 12-event allow-list for UTHelper anonymous usage analytics."""

    SESSION_STARTED = "uth_session_started"
    SESSION_HEARTBEAT = "uth_session_heartbeat"
    SESSION_ENDED = "uth_session_ended"
    SCREEN_VIEWED = "uth_screen_viewed"
    FILTER_APPLIED = "uth_filter_applied"
    SYNC_COMPLETED = "uth_sync_completed"
    DOCUMENT_OPENED = "uth_document_opened"
    COURSE_ACTIVITY_OPENED = "uth_course_activity_opened"
    QUIZ_ACTION = "uth_quiz_action"
    SUBMISSION_ACTION = "uth_submission_action"
    GRADES_INTERACTION = "uth_grades_interaction"
    NOTIFICATION_SUMMARY = "uth_notification_summary"


class ScreenName(StrEnum):
    HOME = "home"
    CALENDAR = "calendar"
    COURSES = "courses"
    GRADES = "grades"
    SETTINGS = "settings"
    DETAIL = "detail"
    QUIZ_PLAYER = "quiz_player"


AnalyticsScreen = ScreenName


class LaunchTrigger(StrEnum):
    COLD_FOREGROUND = "cold_foreground"
    TRAY_RESTORE = "tray_restore"
    WINDOW_RESTORE = "window_restore"
    MOBILE_RESUME = "mobile_resume"
    IDLE_RESUME = "idle_resume"
    LOGIN_COMPLETED = "login_completed"


class BackgroundReason(StrEnum):
    TRAY_HIDE = "tray_hide"
    WINDOW_MINIMIZE = "window_minimize"
    MOBILE_BACKGROUND = "mobile_background"
    BACKGROUND = "background"


class HeartbeatElapsedBucket(StrEnum):
    M_5_15 = "5m_15m"
    M_15_30 = "15m_30m"
    M_30_60 = "30m_60m"
    GTE_60M = "gte_60m"


class SessionEndReason(StrEnum):
    BACKGROUND_TIMEOUT = "background_timeout"
    IDLE_TIMEOUT = "idle_timeout"
    MAX_AGE_ROTATE = "max_age_rotate"
    ACCOUNT_CHANGE = "account_change"
    OPT_OUT = "opt_out"
    APP_EXIT = "app_exit"


class SessionDurationBucket(StrEnum):
    LT_10S = "lt_10s"
    S_10_1M = "10s_1m"
    M_1_5 = "1m_5m"
    M_5_15 = "5m_15m"
    M_15_30 = "15m_30m"
    GTE_30M = "gte_30m"


class ScreensVisitedBucket(StrEnum):
    ONE = "1"
    TWO_TO_THREE = "2_3"
    FOUR_TO_FIVE = "4_5"
    GTE_SIX = "gte_6"


class NavSource(StrEnum):
    TAB_BAR = "tab_bar"
    RAIL = "rail"
    CARD_TAP = "card_tap"
    BACK = "back"
    SHORTCUT = "shortcut"


class FilterSurface(StrEnum):
    HOME = "home"
    CALENDAR = "calendar"
    COURSES = "courses"


class FilterKind(StrEnum):
    URGENCY = "urgency"
    ACTIVITY_TYPE = "activity_type"
    COURSE_SELECT = "course_select"
    SEMESTER_SCOPE = "semester_scope"
    SEARCH_USED = "search_used"
    CALENDAR_MODE = "calendar_mode"
    RESET = "reset"


class SyncProvider(StrEnum):
    ACTIVITIES = "activities"
    TODAY_SCHEDULE = "today_schedule"
    COURSES_CATALOG = "courses_catalog"
    ACADEMIC_GRADES = "academic_grades"


class SyncTrigger(StrEnum):
    STARTUP_FG = "startup_fg"
    MANUAL = "manual"
    FOREGROUND_POLL = "foreground_poll"


class SyncOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    OFFLINE_CACHED = "offline_cached"
    MAINTENANCE = "maintenance"
    AUTH_ERROR = "auth_error"
    FAILED = "failed"


class DurationBucket(StrEnum):
    LT_250MS = "lt_250ms"
    MS_250_1S = "250ms_1s"
    S_1_5 = "1s_5s"
    GTE_5S = "gte_5s"


class ItemCountBucket(StrEnum):
    ZERO = "0"
    ONE_TO_TEN = "1_10"
    ELEVEN_TO_FIFTY = "11_50"
    GT_FIFTY = "gt_50"


class DocumentFormat(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    DOC = "doc"
    PPTX = "pptx"
    PPT = "ppt"
    XLSX = "xlsx"
    HTML = "html"
    IMAGE = "image"
    ARCHIVE = "archive"
    OTHER = "other"


class ViewerKind(StrEnum):
    PDFJS_WEBVIEW = "pdfjs_webview"
    NATIVE_PDF = "native_pdf"
    OFFICE_WEBVIEW = "office_webview"
    OFFICE_PDF_BRIDGE = "office_pdf_bridge"
    MARKDOWN_HTML = "markdown_html"
    ARCHIVE_EXPLORER = "archive_explorer"
    EXTERNAL = "external"


class DocumentSourceSite(StrEnum):
    COURSES = "courses"
    THNN = "thnn"


class CourseActivityKind(StrEnum):
    ASSIGNMENT = "assignment"
    QUIZ_OVERVIEW = "quiz_overview"
    ATTENDANCE = "attendance"
    FORUM = "forum"
    GROUP_CHOICE = "group_choice"
    RICH_CONTENT = "rich_content"
    IN_APP_WEBVIEW = "in_app_webview"


class ActivitySourceSite(StrEnum):
    COURSES = "courses"
    THNN = "thnn"
    PORTAL = "portal"
    EXTERNAL = "external"


class QuizActionKind(StrEnum):
    REVIEW_OPENED = "review_opened"
    PLAYER_STARTED = "player_started"
    PLAYER_CLOSED = "player_closed"
    PLAYER_BLOCKED_SEB = "player_blocked_seb"
    PLAYER_UNAVAILABLE = "player_unavailable"


class SubmissionActionKind(StrEnum):
    UPLOAD_ADD = "upload_add"
    UPLOAD_REPLACE = "upload_replace"
    DELETE_FILE = "delete_file"
    RENAME_FILE = "rename_file"
    FINALIZE = "finalize"


class SubmissionOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    MAINTENANCE = "maintenance"
    FAILED = "failed"


class FileCountBucket(StrEnum):
    ONE = "1"
    TWO_TO_THREE = "2_3"
    GTE_FOUR = "gte_4"


class GradesInteractionKind(StrEnum):
    SEMESTER_SWITCHED = "semester_switched"
    COURSE_POPUP_OPENED = "course_popup_opened"
    RADAR_METRIC_OPENED = "radar_metric_opened"
    GPA_PLANNER_OPENED = "gpa_planner_opened"


class NotificationSummaryKind(StrEnum):
    ACTIVITY_DEADLINE = "activity_deadline"
    CLASS_SCHEDULE = "class_schedule"


class DeliveredCountBucket(StrEnum):
    ZERO = "0"
    ONE_TO_THREE = "1_3"
    GTE_FOUR = "gte_4"


class ScheduledCountBucket(StrEnum):
    ZERO = "0"
    ONE_TO_FIVE = "1_5"
    GTE_SIX = "gte_6"


# ── Coarse Bucket Helpers ──


def _finite_non_negative(value: float | int) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("bucket numeric input must be int or float")
    fval = float(value)
    if math.isnan(fval) or fval < 0.0:
        return 0.0
    return fval


def duration_bucket(seconds: float | int) -> DurationBucket:
    """Map elapsed seconds into a coarse low-cardinality ``DurationBucket``."""
    val = _finite_non_negative(seconds)
    if val < 0.25:
        return DurationBucket.LT_250MS
    if val < 1.0:
        return DurationBucket.MS_250_1S
    if val < 5.0:
        return DurationBucket.S_1_5
    return DurationBucket.GTE_5S


def duration_ms_bucket(milliseconds: float | int) -> DurationBucket:
    """Map elapsed milliseconds into a coarse ``DurationBucket``."""
    return duration_bucket(_finite_non_negative(milliseconds) / 1000.0)


def session_duration_bucket(seconds: float | int) -> SessionDurationBucket:
    """Map active foreground session seconds into ``SessionDurationBucket``."""
    val = _finite_non_negative(seconds)
    if val < 10.0:
        return SessionDurationBucket.LT_10S
    if val < 60.0:
        return SessionDurationBucket.S_10_1M
    if val < 300.0:
        return SessionDurationBucket.M_1_5
    if val < 900.0:
        return SessionDurationBucket.M_5_15
    if val < 1800.0:
        return SessionDurationBucket.M_15_30
    return SessionDurationBucket.GTE_30M


def heartbeat_elapsed_bucket(seconds: float | int) -> HeartbeatElapsedBucket:
    """Map foreground session elapsed seconds into ``HeartbeatElapsedBucket``."""
    val = _finite_non_negative(seconds)
    if val < 900.0:
        return HeartbeatElapsedBucket.M_5_15
    if val < 1800.0:
        return HeartbeatElapsedBucket.M_15_30
    if val < 3600.0:
        return HeartbeatElapsedBucket.M_30_60
    return HeartbeatElapsedBucket.GTE_60M


def _non_negative_int(count: int) -> int:
    if isinstance(count, bool) or not isinstance(count, int):
        raise TypeError("count must be an integer")
    return max(0, count)


def screens_visited_bucket(count: int) -> ScreensVisitedBucket:
    """Map distinct visited screens count in one session into ``ScreensVisitedBucket``."""
    n = _non_negative_int(count)
    if n <= 1:
        return ScreensVisitedBucket.ONE
    if n <= 3:
        return ScreensVisitedBucket.TWO_TO_THREE
    if n <= 5:
        return ScreensVisitedBucket.FOUR_TO_FIVE
    return ScreensVisitedBucket.GTE_SIX


def item_count_bucket(count: int) -> ItemCountBucket:
    """Map synced item count into ``ItemCountBucket``."""
    n = _non_negative_int(count)
    if n == 0:
        return ItemCountBucket.ZERO
    if n <= 10:
        return ItemCountBucket.ONE_TO_TEN
    if n <= 50:
        return ItemCountBucket.ELEVEN_TO_FIFTY
    return ItemCountBucket.GT_FIFTY


def file_count_bucket(count: int) -> FileCountBucket:
    """Map submitted file count into ``FileCountBucket``."""
    n = _non_negative_int(count)
    if n <= 1:
        return FileCountBucket.ONE
    if n <= 3:
        return FileCountBucket.TWO_TO_THREE
    return FileCountBucket.GTE_FOUR


def delivered_count_bucket(count: int) -> DeliveredCountBucket:
    """Map delivered notification count into ``DeliveredCountBucket``."""
    n = _non_negative_int(count)
    if n == 0:
        return DeliveredCountBucket.ZERO
    if n <= 3:
        return DeliveredCountBucket.ONE_TO_THREE
    return DeliveredCountBucket.GTE_FOUR


def scheduled_count_bucket(count: int) -> ScheduledCountBucket:
    """Map scheduled notification count into ``ScheduledCountBucket``."""
    n = _non_negative_int(count)
    if n == 0:
        return ScheduledCountBucket.ZERO
    if n <= 5:
        return ScheduledCountBucket.ONE_TO_FIVE
    return ScheduledCountBucket.GTE_SIX


def channels_bitmask(
    *,
    os_toast: bool = False,
    discord: bool = False,
    telegram: bool = False,
    email: bool = False,
) -> int:
    """Encode active notification channels into ``0..15`` bitmask."""
    return (
        (1 if os_toast else 0)
        | (2 if discord else 0)
        | (4 if telegram else 0)
        | (8 if email else 0)
    )


_EXT_TO_DOC_FORMAT: dict[str, DocumentFormat] = {
    "pdf": DocumentFormat.PDF,
    "docx": DocumentFormat.DOCX,
    "doc": DocumentFormat.DOC,
    "pptx": DocumentFormat.PPTX,
    "ppt": DocumentFormat.PPT,
    "xlsx": DocumentFormat.XLSX,
    "xls": DocumentFormat.XLSX,
    "csv": DocumentFormat.XLSX,
    "html": DocumentFormat.HTML,
    "htm": DocumentFormat.HTML,
    "png": DocumentFormat.IMAGE,
    "jpg": DocumentFormat.IMAGE,
    "jpeg": DocumentFormat.IMAGE,
    "gif": DocumentFormat.IMAGE,
    "webp": DocumentFormat.IMAGE,
    "bmp": DocumentFormat.IMAGE,
    "svg": DocumentFormat.IMAGE,
    "zip": DocumentFormat.ARCHIVE,
    "rar": DocumentFormat.ARCHIVE,
    "7z": DocumentFormat.ARCHIVE,
    "tar": DocumentFormat.ARCHIVE,
    "gz": DocumentFormat.ARCHIVE,
    "tgz": DocumentFormat.ARCHIVE,
}


def classify_document_format(filename_or_ext: str | None) -> DocumentFormat:
    """Map a filename or extension to ``DocumentFormat`` without retaining filenames."""
    if not isinstance(filename_or_ext, str) or not filename_or_ext.strip():
        return DocumentFormat.OTHER
    cleaned = filename_or_ext.strip().rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." in cleaned:
        ext = cleaned.rsplit(".", 1)[-1].casefold()
    else:
        ext = cleaned.casefold()
    return _EXT_TO_DOC_FORMAT.get(ext, DocumentFormat.OTHER)


# ── Strict Event Property Schemas (extra="forbid") ──


class _StrictEventProperties(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SessionStartedProperties(_StrictEventProperties):
    launch_trigger: LaunchTrigger
    initial_screen: ScreenName


class SessionHeartbeatProperties(_StrictEventProperties):
    elapsed_bucket: HeartbeatElapsedBucket
    active_screen: ScreenName


class SessionEndedProperties(_StrictEventProperties):
    end_reason: SessionEndReason
    duration_bucket: SessionDurationBucket
    screens_visited_bucket: ScreensVisitedBucket


class ScreenViewedProperties(_StrictEventProperties):
    screen: ScreenName
    nav_source: NavSource


class FilterAppliedProperties(_StrictEventProperties):
    surface: FilterSurface
    filter_kind: FilterKind


class SyncCompletedProperties(_StrictEventProperties):
    provider: SyncProvider
    trigger: SyncTrigger
    outcome: SyncOutcome
    duration_bucket: DurationBucket
    item_count_bucket: ItemCountBucket


class DocumentOpenedProperties(_StrictEventProperties):
    doc_format: DocumentFormat
    viewer_kind: ViewerKind
    source_site: DocumentSourceSite
    cache_hit: StrictBool
    load_duration_bucket: DurationBucket


class CourseActivityOpenedProperties(_StrictEventProperties):
    activity_kind: CourseActivityKind
    source_site: ActivitySourceSite


class QuizActionProperties(_StrictEventProperties):
    action: QuizActionKind
    source_site: DocumentSourceSite
    duration_bucket: DurationBucket | SessionDurationBucket | None = None


class SubmissionActionProperties(_StrictEventProperties):
    action: SubmissionActionKind
    outcome: SubmissionOutcome
    file_count_bucket: FileCountBucket


class GradesInteractionProperties(_StrictEventProperties):
    action: GradesInteractionKind


class NotificationSummaryProperties(_StrictEventProperties):
    kind: NotificationSummaryKind
    delivered_bucket: DeliveredCountBucket
    scheduled_bucket: ScheduledCountBucket
    had_channel_failure: StrictBool
    channels_mask: StrictInt = Field(ge=0, le=15)


EVENT_PROPERTIES_MODELS: dict[EventName, type[_StrictEventProperties]] = {
    EventName.SESSION_STARTED: SessionStartedProperties,
    EventName.SESSION_HEARTBEAT: SessionHeartbeatProperties,
    EventName.SESSION_ENDED: SessionEndedProperties,
    EventName.SCREEN_VIEWED: ScreenViewedProperties,
    EventName.FILTER_APPLIED: FilterAppliedProperties,
    EventName.SYNC_COMPLETED: SyncCompletedProperties,
    EventName.DOCUMENT_OPENED: DocumentOpenedProperties,
    EventName.COURSE_ACTIVITY_OPENED: CourseActivityOpenedProperties,
    EventName.QUIZ_ACTION: QuizActionProperties,
    EventName.SUBMISSION_ACTION: SubmissionActionProperties,
    EventName.GRADES_INTERACTION: GradesInteractionProperties,
    EventName.NOTIFICATION_SUMMARY: NotificationSummaryProperties,
}

_SUPER_PROPERTY_KEYS = frozenset(
    {
        "$session_id",
        "$process_person_profile",
        "$geoip_disable",
        "$ip",
        "$lib",
        "$lib_version",
        "schema_version",
        "app_version",
        "release_channel",
        "distribution_channel",
        "package_format",
        "os_family",
        "device_class",
    }
)


def validate_uuid4_string(value: str, *, field_name: str = "uuid") -> str:
    """Require a canonical 36-char lowercase RFC 4122 UUIDv4 string."""
    if not isinstance(value, str) or len(value) != 36 or value != value.lower():
        raise ValueError(f"{field_name} must be a canonical lowercase UUIDv4 string")
    parsed = UUID(value)
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError(f"{field_name} must be a valid RFC 4122 UUIDv4")
    return value


def _validate_version_label(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not _APP_VERSION_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a semantic version or 'unknown'")
    return value


class AnalyticsConfig(BaseModel):
    """Validated runtime configuration and coarse non-fingerprinting metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    api_key: str | None = None
    host: str | None = None
    app_version: str = "unknown"
    release_channel: ReleaseChannel = ReleaseChannel.RELEASE
    distribution_channel: DistributionChannelName = DistributionChannelName.SOURCE
    package_format: PackageFormatName = PackageFormatName.SOURCE
    os_family: OsFamily = OsFamily.UNKNOWN
    device_class: DeviceClass = DeviceClass.UNKNOWN
    allow_private_host: bool = False
    web_mode: bool = False
    test_mode: bool = False

    @field_validator("app_version")
    @classmethod
    def _check_app_version(cls, value: str) -> str:
        return _validate_version_label(value, field_name="app_version")

    @model_validator(mode="after")
    def _validate_ingestion_pair(self) -> "AnalyticsConfig":
        validated_key: str | None = None
        validated_host: str | None = None
        if self.api_key is not None and self.api_key != "":
            validated_key = validate_posthog_api_key(self.api_key)
        if self.host is not None and self.host != "":
            validated_host = validate_posthog_host(
                self.host,
                allow_private_dev=self.allow_private_host,
            )
        object.__setattr__(self, "api_key", validated_key)
        object.__setattr__(self, "host", validated_host)
        return self

    @property
    def configured(self) -> bool:
        """Return True iff BOTH PostHog API key and HTTPS host are valid non-empty strings."""
        return bool(self.api_key and self.host)

    @property
    def active_eligible(self) -> bool:
        """Return True when PostHog is configured and neither web_mode nor test_mode suppresses it."""
        return self.configured and not self.web_mode and not self.test_mode

    def build_super_properties(self, session_id: str) -> dict[str, Any]:
        """Build the mandatory PostHog privacy + coarse context super-properties."""
        extract_uuid7_ms(session_id)
        return {
            "$session_id": session_id,
            "$process_person_profile": False,
            "$geoip_disable": True,
            "$ip": None,
            "$lib": ANALYTICS_LIB_NAME,
            "$lib_version": self.app_version,
            "schema_version": ANALYTICS_SCHEMA_VERSION,
            "app_version": self.app_version,
            "release_channel": self.release_channel.value,
            "distribution_channel": self.distribution_channel.value,
            "package_format": self.package_format.value,
            "os_family": self.os_family.value,
            "device_class": self.device_class.value,
        }


class AnalyticsSuperProperties(BaseModel):
    """Strict validator for required super-properties inside ``event.properties``."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    session_id: str = Field(alias="$session_id")
    process_person_profile: StrictBool = Field(alias="$process_person_profile")
    geoip_disable: StrictBool = Field(alias="$geoip_disable")
    ip: None = Field(default=None, alias="$ip")
    lib: Literal["uthelper-python"] = Field(alias="$lib")
    lib_version: str = Field(alias="$lib_version")
    schema_version: StrictInt
    app_version: str
    release_channel: ReleaseChannel
    distribution_channel: DistributionChannelName
    package_format: PackageFormatName
    os_family: OsFamily
    device_class: DeviceClass

    @field_validator("session_id")
    @classmethod
    def _check_session_uuid7(cls, value: str) -> str:
        extract_uuid7_ms(value)
        return value

    @field_validator("process_person_profile")
    @classmethod
    def _require_personless(cls, value: bool) -> bool:
        if value is not False:
            raise ValueError("$process_person_profile must be strictly False")
        return value

    @field_validator("geoip_disable")
    @classmethod
    def _require_geoip_disabled(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("$geoip_disable must be strictly True")
        return value

    @field_validator("schema_version")
    @classmethod
    def _require_schema_version(cls, value: int) -> int:
        if value != ANALYTICS_SCHEMA_VERSION:
            raise ValueError(f"schema_version must equal {ANALYTICS_SCHEMA_VERSION}")
        return value

    @field_validator("lib_version", "app_version")
    @classmethod
    def _check_versions(cls, value: str) -> str:
        return _validate_version_label(value, field_name="version")


class AnalyticsEvent(BaseModel):
    """One allow-listed PostHog wire event with mandatory personless & IP-null flags."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    uuid: str
    event: EventName
    distinct_id: str
    timestamp: str
    properties: dict[str, Any]

    @field_validator("uuid")
    @classmethod
    def _check_uuid(cls, value: str) -> str:
        return validate_uuid4_string(value, field_name="uuid")

    @field_validator("distinct_id")
    @classmethod
    def _check_distinct_id(cls, value: str) -> str:
        return validate_uuid4_string(value, field_name="distinct_id")

    @field_validator("timestamp")
    @classmethod
    def _check_timestamp(cls, value: str) -> str:
        if not isinstance(value, str) or not _UTC_MS_TIMESTAMP_RE.fullmatch(value):
            raise ValueError(
                "timestamp must be UTC millisecond RFC 3339 (YYYY-MM-DDTHH:MM:SS.sssZ)"
            )
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
        return value

    @model_validator(mode="after")
    def _validate_properties_allowlist(self) -> "AnalyticsEvent":
        raw_props = self.properties
        if not isinstance(raw_props, dict):
            raise ValueError("properties must be a dict")
        missing_super = _SUPER_PROPERTY_KEYS - set(raw_props.keys())
        if missing_super:
            raise ValueError(
                f"properties missing mandatory super-properties: {sorted(missing_super)}"
            )
        if raw_props.get("$ip", "missing") is not None:
            raise ValueError("$ip must be explicitly present and None")

        super_subset = {k: raw_props[k] for k in _SUPER_PROPERTY_KEYS}
        validated_super = AnalyticsSuperProperties.model_validate(super_subset)
        if validated_super.lib_version != validated_super.app_version:
            raise ValueError("$lib_version must match app_version")

        event_subset = {
            k: v for k, v in raw_props.items() if k not in _SUPER_PROPERTY_KEYS
        }
        props_model = EVENT_PROPERTIES_MODELS[self.event]
        validated_event_props = props_model.model_validate(event_subset)

        normalized_props: dict[str, Any] = {
            "$session_id": validated_super.session_id,
            "$process_person_profile": False,
            "$geoip_disable": True,
            "$ip": None,
            "$lib": ANALYTICS_LIB_NAME,
            "$lib_version": validated_super.lib_version,
            "schema_version": ANALYTICS_SCHEMA_VERSION,
            "app_version": validated_super.app_version,
            "release_channel": validated_super.release_channel.value,
            "distribution_channel": validated_super.distribution_channel.value,
            "package_format": validated_super.package_format.value,
            "os_family": validated_super.os_family.value,
            "device_class": validated_super.device_class.value,
        }
        normalized_props.update(validated_event_props.model_dump(mode="json"))

        wire_dict = {
            "uuid": self.uuid,
            "event": self.event.value,
            "distinct_id": self.distinct_id,
            "timestamp": self.timestamp,
            "properties": normalized_props,
        }
        encoded = json.dumps(
            wire_dict, ensure_ascii=True, separators=(",", ":")
        ).encode("utf-8")
        if len(encoded) > MAX_EVENT_BYTES:
            raise ValueError(f"serialized event exceeds {MAX_EVENT_BYTES} bytes")

        object.__setattr__(self, "properties", normalized_props)
        return self

    def timestamp_utc(self) -> datetime:
        """Return ``timestamp`` parsed as a timezone-aware UTC ``datetime``."""
        return datetime.strptime(self.timestamp, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=UTC
        )

    def to_wire_dict(self) -> dict[str, Any]:
        """Return the exact PostHog wire dict (retaining ``$ip: None``)."""
        return {
            "uuid": self.uuid,
            "event": self.event.value,
            "distinct_id": self.distinct_id,
            "timestamp": self.timestamp,
            "properties": dict(self.properties),
        }

    @classmethod
    def build(
        cls,
        *,
        event: EventName | str,
        event_properties: BaseModel | dict[str, Any],
        distinct_id: str,
        session_id: str,
        timestamp: str,
        config: AnalyticsConfig,
        event_uuid: str | None = None,
    ) -> "AnalyticsEvent":
        """Construct and validate one ``AnalyticsEvent`` from typed event properties."""
        from uuid import uuid4

        selected_event = EventName(event)
        props_model = EVENT_PROPERTIES_MODELS[selected_event]
        if isinstance(event_properties, props_model):
            validated_specific = event_properties
        elif isinstance(event_properties, BaseModel):
            raise TypeError(
                f"event_properties for {selected_event.value} must match {props_model.__name__}"
            )
        else:
            validated_specific = props_model.model_validate(event_properties)

        merged_props = config.build_super_properties(session_id)
        merged_props.update(validated_specific.model_dump(mode="json"))
        return cls(
            uuid=str(uuid4()) if event_uuid is None else event_uuid,
            event=selected_event,
            distinct_id=distinct_id,
            timestamp=timestamp,
            properties=merged_props,
        )


class AnalyticsSpoolBatch(BaseModel):
    """Immutable on-disk spool envelope containing 1..25 validated ``AnalyticsEvent``s."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = ANALYTICS_SCHEMA_VERSION
    batch_id: UUID
    created_at: datetime
    events: tuple[AnalyticsEvent, ...] = Field(min_length=1, max_length=25)

    @field_validator("batch_id")
    @classmethod
    def _check_batch_uuid(cls, value: UUID) -> UUID:
        if value.version not in {4, 7}:
            raise ValueError("batch_id must be UUIDv4 or UUIDv7")
        return value

    @field_validator("created_at")
    @classmethod
    def _normalize_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


__all__ = [
    "ANALYTICS_LIB_NAME",
    "ANALYTICS_SCHEMA_VERSION",
    "ActivitySourceSite",
    "AnalyticsConfig",
    "AnalyticsConfigError",
    "AnalyticsConsent",
    "AnalyticsEvent",
    "AnalyticsScreen",
    "AnalyticsSpoolBatch",
    "AnalyticsSuperProperties",
    "BackgroundReason",
    "CourseActivityKind",
    "CourseActivityOpenedProperties",
    "DeliveredCountBucket",
    "DeviceClass",
    "DistributionChannelName",
    "DocumentFormat",
    "DocumentOpenedProperties",
    "DocumentSourceSite",
    "DurationBucket",
    "EVENT_PROPERTIES_MODELS",
    "EventName",
    "FileCountBucket",
    "FilterAppliedProperties",
    "FilterKind",
    "FilterSurface",
    "GradesInteractionKind",
    "GradesInteractionProperties",
    "HeartbeatElapsedBucket",
    "ItemCountBucket",
    "LaunchTrigger",
    "MAX_EVENT_BYTES",
    "NavSource",
    "NotificationSummaryKind",
    "NotificationSummaryProperties",
    "OsFamily",
    "PackageFormatName",
    "QuizActionKind",
    "QuizActionProperties",
    "ReleaseChannel",
    "ScheduledCountBucket",
    "ScreenName",
    "ScreenViewedProperties",
    "ScreensVisitedBucket",
    "SessionDurationBucket",
    "SessionEndReason",
    "SessionEndedProperties",
    "SessionHeartbeatProperties",
    "SessionStartedProperties",
    "SubmissionActionKind",
    "SubmissionActionProperties",
    "SubmissionOutcome",
    "SyncCompletedProperties",
    "SyncOutcome",
    "SyncProvider",
    "SyncTrigger",
    "ViewerKind",
    "channels_bitmask",
    "classify_document_format",
    "delivered_count_bucket",
    "duration_bucket",
    "duration_ms_bucket",
    "file_count_bucket",
    "heartbeat_elapsed_bucket",
    "item_count_bucket",
    "scheduled_count_bucket",
    "screens_visited_bucket",
    "session_duration_bucket",
    "validate_posthog_api_key",
    "validate_posthog_host",
    "validate_uuid4_string",
]
