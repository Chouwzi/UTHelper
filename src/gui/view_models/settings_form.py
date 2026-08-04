"""Canonical, safe-to-compare state for editable settings controls."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Literal, Mapping, Protocol


CrashReportingConsent = Literal["not_asked", "enabled", "disabled"]


class SettingsLike(Protocol):
    """A persisted settings object; deliberately independent from ``config``."""


class SettingsFormValidationError(ValueError):
    """A validation error which identifies a control but never echoes its value."""


DEFAULT_THEME = "midnight_blue"
DEFAULT_COLORS = {
    "color_critical": "#EF4444",
    "color_warning": "#F59E0B",
    "color_safe": "#10B981",
    "color_quiz": "#7C3AED",
    "color_assignment": "#2563EB",
    "color_attendance": "#D97706",
    "color_open": "#0891B2",
    "color_other": "#6B7280",
}
DEFAULT_NOTIFY_TYPES = ("assignment", "attendance", "quiz")
DEFAULT_MILESTONES = (4320, 1440, 180, 60, 30, 5)
_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}\Z")


def _is_blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _parse_int(
    name: str,
    value: object,
    default: int,
    minimum: int,
    maximum: int | None,
) -> int:
    """Return a bounded integer without ever including supplied data in errors."""
    if _is_blank(value):
        return default
    if isinstance(value, bool):
        raise SettingsFormValidationError(f"{name} must be an integer")
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        parsed = None
    if parsed is None:
        raise SettingsFormValidationError(f"{name} must be an integer") from None
    if isinstance(value, float) and not value.is_integer():
        raise SettingsFormValidationError(f"{name} must be an integer")
    if parsed < minimum or (maximum is not None and parsed > maximum):
        raise SettingsFormValidationError(f"{name} is outside its allowed range")
    return parsed


def _normalize_color(value: object, default: str) -> str:
    """Return uppercase #RRGGBB, or the documented default for blank input."""
    if _is_blank(value):
        return default
    normalized = str(value).strip().upper()
    if not _HEX_COLOR.fullmatch(normalized):
        raise SettingsFormValidationError("color must be a #RRGGBB value")
    return normalized


def _normalize_csv_strings(value: object) -> tuple[str, ...]:
    """Trim, de-duplicate and case-insensitively order a CSV or iterable."""
    if _is_blank(value):
        return ()
    parts = value.split(",") if isinstance(value, str) else value
    try:
        raw_values = list(parts)  # type: ignore[arg-type]
    except TypeError:
        raw_values = None
    if raw_values is None:
        raise SettingsFormValidationError("list value must be comma-separated") from None
    unique: dict[str, str] = {}
    for item in raw_values:
        normalized = str(item).strip()
        if normalized:
            casefolded = normalized.casefold()
            previous = unique.get(casefolded)
            unique[casefolded] = (
                normalized if previous is None else min(previous, normalized)
            )
    return tuple(sorted(unique.values(), key=lambda item: (item.casefold(), item)))


def _normalize_notify_types(value: object) -> tuple[str, ...]:
    """Canonicalize the set-like notification type control."""
    if _is_blank(value):
        return DEFAULT_NOTIFY_TYPES
    normalized = tuple(item.casefold() for item in _normalize_csv_strings(value))
    return tuple(sorted(set(normalized)))


def _normalize_milestones(value: object) -> tuple[int, ...]:
    """Normalize positive unique notification minute milestones descending."""
    if _is_blank(value):
        return DEFAULT_MILESTONES
    if isinstance(value, str):
        raw_values = value.split(",")
    else:
        try:
            raw_values = list(value)  # type: ignore[arg-type]
        except TypeError:
            raw_values = [value]
    milestones: set[int] = set()
    for item in raw_values:
        if isinstance(item, bool) or (
            isinstance(item, float) and not item.is_integer()
        ):
            raise SettingsFormValidationError(
                "notify_milestones_minutes must be positive integers"
            )
        try:
            milestone = int(item)
        except (TypeError, ValueError):
            milestone = None
        if milestone is None:
            raise SettingsFormValidationError(
                "notify_milestones_minutes must be positive integers"
            ) from None
        if milestone <= 0:
            raise SettingsFormValidationError(
                "notify_milestones_minutes must be positive integers"
            )
        milestones.add(milestone)
    return tuple(sorted(milestones, reverse=True))


def _parse_bool(name: str, value: object, default: bool) -> bool:
    if _is_blank(value):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    raise SettingsFormValidationError(f"{name} must be true or false")


def _parse_crash_reporting_consent(value: object) -> CrashReportingConsent:
    consent = _text(value, "not_asked")
    if consent == "not_asked":
        return "not_asked"
    if consent == "enabled":
        return "enabled"
    if consent == "disabled":
        return "disabled"
    raise SettingsFormValidationError("crash_reporting_consent is invalid")


def _text(value: object, default: str = "", *, strip: bool = True) -> str:
    if value is None:
        return default
    result = str(value)
    result = result.strip() if strip else result
    return result or default


@dataclass(frozen=True, slots=True)
class SettingsFormSnapshot:
    theme: str
    color_critical: str
    color_warning: str
    color_safe: str
    color_quiz: str
    color_assignment: str
    color_attendance: str
    color_open: str
    color_other: str
    uth_username: str
    uth_password: str = field(repr=False)
    always_on_top: bool
    include_submitted: bool
    include_graded: bool
    start_with_windows: bool
    start_minimized: bool
    minimize_to_tray: bool
    auto_update_enabled: bool
    crash_reporting_consent: CrashReportingConsent
    background_check_android: bool
    enable_gmail: bool
    gmail_address: str
    gmail_app_password: str = field(repr=False)
    enable_discord: bool
    discord_webhook_url: str = field(repr=False)
    enable_telegram: bool
    telegram_bot_token: str = field(repr=False)
    telegram_chat_id: str
    debug_mode: bool
    check_interval_minutes: int
    fetch_months: int
    urgency_critical_hours: int
    urgency_warning_hours: int
    opening_soon_hours: int
    prefetch_workers: int
    notify_dnd_enable: bool
    notify_dnd_start: int
    notify_dnd_end: int
    notify_ignore_submitted: bool
    notification_profile: str
    notify_types: tuple[str, ...]
    notify_milestones_minutes: tuple[int, ...]
    notify_muted_courses: tuple[str, ...]

    @classmethod
    def from_settings(cls, value: SettingsLike) -> SettingsFormSnapshot:
        """Build canonical form state from an object with persisted attributes."""
        return cls.from_form_values(
            {
                "theme": getattr(value, "THEME", DEFAULT_THEME),
                "color_critical": getattr(value, "COLOR_CRITICAL", DEFAULT_COLORS["color_critical"]),
                "color_warning": getattr(value, "COLOR_WARNING", DEFAULT_COLORS["color_warning"]),
                "color_safe": getattr(value, "COLOR_SAFE", DEFAULT_COLORS["color_safe"]),
                "color_quiz": getattr(value, "COLOR_QUIZ", DEFAULT_COLORS["color_quiz"]),
                "color_assignment": getattr(value, "COLOR_ASSIGNMENT", DEFAULT_COLORS["color_assignment"]),
                "color_attendance": getattr(value, "COLOR_ATTENDANCE", DEFAULT_COLORS["color_attendance"]),
                "color_open": getattr(value, "COLOR_OPEN", DEFAULT_COLORS["color_open"]),
                "color_other": getattr(value, "COLOR_OTHER", DEFAULT_COLORS["color_other"]),
                "uth_username": getattr(value, "UTH_USERNAME", ""),
                "uth_password": getattr(value, "UTH_PASSWORD", ""),
                "always_on_top": getattr(value, "ALWAYS_ON_TOP", False),
                "include_submitted": getattr(value, "INCLUDE_SUBMITTED", True),
                "include_graded": getattr(value, "INCLUDE_GRADED", True),
                "start_with_windows": getattr(value, "START_WITH_WINDOWS", False),
                "start_minimized": getattr(value, "START_MINIMIZED", True),
                "minimize_to_tray": getattr(value, "MINIMIZE_TO_TRAY", True),
                "auto_update_enabled": getattr(value, "AUTO_UPDATE_ENABLED", True),
                "crash_reporting_consent": getattr(value, "CRASH_REPORTING_CONSENT", "not_asked"),
                "background_check_android": getattr(value, "BACKGROUND_CHECK_ANDROID", True),
                "enable_gmail": getattr(value, "ENABLE_GMAIL", False),
                "gmail_address": getattr(value, "GMAIL_ADDRESS", ""),
                "gmail_app_password": getattr(value, "GMAIL_APP_PASSWORD", ""),
                "enable_discord": getattr(value, "ENABLE_DISCORD", False),
                "discord_webhook_url": getattr(value, "DISCORD_WEBHOOK_URL", ""),
                "enable_telegram": getattr(value, "ENABLE_TELEGRAM", False),
                "telegram_bot_token": getattr(value, "TELEGRAM_BOT_TOKEN", ""),
                "telegram_chat_id": getattr(value, "TELEGRAM_CHAT_ID", ""),
                "debug_mode": getattr(value, "DEBUG_MODE", False),
                "check_interval_minutes": getattr(value, "CHECK_INTERVAL_MINUTES", 60),
                "fetch_months": getattr(value, "FETCH_MONTHS", 1),
                "urgency_critical_hours": getattr(value, "URGENCY_CRITICAL_HOURS", 24),
                "urgency_warning_hours": getattr(value, "URGENCY_WARNING_HOURS", 72),
                "opening_soon_hours": getattr(value, "OPENING_SOON_HOURS", 72),
                "prefetch_workers": getattr(value, "PREFETCH_WORKERS", 4),
                "notify_dnd_enable": getattr(value, "NOTIFY_DND_ENABLE", False),
                "notify_dnd_start": getattr(value, "NOTIFY_DND_START", 22),
                "notify_dnd_end": getattr(value, "NOTIFY_DND_END", 7),
                "notify_ignore_submitted": getattr(value, "NOTIFY_IGNORE_SUBMITTED", True),
                "notification_profile": getattr(value, "NOTIFICATION_PROFILE", "balanced"),
                "notify_types": getattr(value, "NOTIFY_TYPES", DEFAULT_NOTIFY_TYPES),
                "notify_milestones_minutes": getattr(value, "NOTIFY_MILESTONES_MINUTES", DEFAULT_MILESTONES),
                "notify_muted_courses": getattr(value, "NOTIFY_MUTED_COURSES", ()),
            }
        )

    @classmethod
    def from_form_values(cls, values: Mapping[str, object]) -> SettingsFormSnapshot:
        """Parse controls into the canonical state, with value-safe errors."""
        return cls(
            theme=_text(values.get("theme"), DEFAULT_THEME),
            color_critical=_normalize_color(values.get("color_critical"), DEFAULT_COLORS["color_critical"]),
            color_warning=_normalize_color(values.get("color_warning"), DEFAULT_COLORS["color_warning"]),
            color_safe=_normalize_color(values.get("color_safe"), DEFAULT_COLORS["color_safe"]),
            color_quiz=_normalize_color(values.get("color_quiz"), DEFAULT_COLORS["color_quiz"]),
            color_assignment=_normalize_color(values.get("color_assignment"), DEFAULT_COLORS["color_assignment"]),
            color_attendance=_normalize_color(values.get("color_attendance"), DEFAULT_COLORS["color_attendance"]),
            color_open=_normalize_color(values.get("color_open"), DEFAULT_COLORS["color_open"]),
            color_other=_normalize_color(values.get("color_other"), DEFAULT_COLORS["color_other"]),
            uth_username=_text(values.get("uth_username")),
            uth_password=_text(values.get("uth_password"), strip=False),
            always_on_top=_parse_bool("always_on_top", values.get("always_on_top"), False),
            include_submitted=_parse_bool("include_submitted", values.get("include_submitted"), True),
            include_graded=_parse_bool("include_graded", values.get("include_graded"), True),
            start_with_windows=_parse_bool("start_with_windows", values.get("start_with_windows"), False),
            start_minimized=_parse_bool("start_minimized", values.get("start_minimized"), True),
            minimize_to_tray=_parse_bool("minimize_to_tray", values.get("minimize_to_tray"), True),
            auto_update_enabled=_parse_bool("auto_update_enabled", values.get("auto_update_enabled"), True),
            crash_reporting_consent=_parse_crash_reporting_consent(
                values.get("crash_reporting_consent")
            ),
            background_check_android=_parse_bool("background_check_android", values.get("background_check_android"), True),
            enable_gmail=_parse_bool("enable_gmail", values.get("enable_gmail"), False),
            gmail_address=_text(values.get("gmail_address")),
            gmail_app_password=_text(values.get("gmail_app_password"), strip=False),
            enable_discord=_parse_bool("enable_discord", values.get("enable_discord"), False),
            discord_webhook_url=_text(values.get("discord_webhook_url"), strip=False),
            enable_telegram=_parse_bool("enable_telegram", values.get("enable_telegram"), False),
            telegram_bot_token=_text(values.get("telegram_bot_token"), strip=False),
            telegram_chat_id=_text(values.get("telegram_chat_id")),
            debug_mode=_parse_bool("debug_mode", values.get("debug_mode"), False),
            check_interval_minutes=_parse_int("check_interval_minutes", values.get("check_interval_minutes"), 60, 0, None),
            fetch_months=_parse_int("fetch_months", values.get("fetch_months"), 1, 1, 3),
            urgency_critical_hours=_parse_int("urgency_critical_hours", values.get("urgency_critical_hours"), 24, 1, None),
            urgency_warning_hours=_parse_int("urgency_warning_hours", values.get("urgency_warning_hours"), 72, 1, None),
            opening_soon_hours=_parse_int("opening_soon_hours", values.get("opening_soon_hours"), 72, 1, None),
            prefetch_workers=_parse_int("prefetch_workers", values.get("prefetch_workers"), 4, 1, 10),
            notify_dnd_enable=_parse_bool("notify_dnd_enable", values.get("notify_dnd_enable"), False),
            notify_dnd_start=_parse_int("notify_dnd_start", values.get("notify_dnd_start"), 22, 0, 23),
            notify_dnd_end=_parse_int("notify_dnd_end", values.get("notify_dnd_end"), 7, 0, 23),
            notify_ignore_submitted=_parse_bool("notify_ignore_submitted", values.get("notify_ignore_submitted"), True),
            notification_profile=_text(values.get("notification_profile"), "balanced").casefold(),
            notify_types=_normalize_notify_types(values.get("notify_types")),
            notify_milestones_minutes=_normalize_milestones(values.get("notify_milestones_minutes")),
            notify_muted_courses=_normalize_csv_strings(values.get("notify_muted_courses")),
        )

    def to_settings_values(self) -> dict[str, object]:
        """Return an explicit config-field mapping; no implicit name conversion."""
        return {
            "THEME": self.theme,
            "COLOR_CRITICAL": self.color_critical,
            "COLOR_WARNING": self.color_warning,
            "COLOR_SAFE": self.color_safe,
            "COLOR_QUIZ": self.color_quiz,
            "COLOR_ASSIGNMENT": self.color_assignment,
            "COLOR_ATTENDANCE": self.color_attendance,
            "COLOR_OPEN": self.color_open,
            "COLOR_OTHER": self.color_other,
            "UTH_USERNAME": self.uth_username,
            "UTH_PASSWORD": self.uth_password,
            "ALWAYS_ON_TOP": self.always_on_top,
            "INCLUDE_SUBMITTED": self.include_submitted,
            "INCLUDE_GRADED": self.include_graded,
            "START_WITH_WINDOWS": self.start_with_windows,
            "START_MINIMIZED": self.start_minimized,
            "MINIMIZE_TO_TRAY": self.minimize_to_tray,
            "AUTO_UPDATE_ENABLED": self.auto_update_enabled,
            "CRASH_REPORTING_CONSENT": self.crash_reporting_consent,
            "BACKGROUND_CHECK_ANDROID": self.background_check_android,
            "ENABLE_GMAIL": self.enable_gmail,
            "GMAIL_ADDRESS": self.gmail_address,
            "GMAIL_APP_PASSWORD": self.gmail_app_password,
            "ENABLE_DISCORD": self.enable_discord,
            "DISCORD_WEBHOOK_URL": self.discord_webhook_url,
            "ENABLE_TELEGRAM": self.enable_telegram,
            "TELEGRAM_BOT_TOKEN": self.telegram_bot_token,
            "TELEGRAM_CHAT_ID": self.telegram_chat_id,
            "DEBUG_MODE": self.debug_mode,
            "CHECK_INTERVAL_MINUTES": self.check_interval_minutes,
            "FETCH_MONTHS": self.fetch_months,
            "URGENCY_CRITICAL_HOURS": self.urgency_critical_hours,
            "URGENCY_WARNING_HOURS": self.urgency_warning_hours,
            "OPENING_SOON_HOURS": self.opening_soon_hours,
            "PREFETCH_WORKERS": self.prefetch_workers,
            "NOTIFY_DND_ENABLE": self.notify_dnd_enable,
            "NOTIFY_DND_START": self.notify_dnd_start,
            "NOTIFY_DND_END": self.notify_dnd_end,
            "NOTIFY_IGNORE_SUBMITTED": self.notify_ignore_submitted,
            "NOTIFICATION_PROFILE": self.notification_profile,
            "NOTIFY_TYPES": list(self.notify_types),
            "NOTIFY_MILESTONES_MINUTES": list(self.notify_milestones_minutes),
            "NOTIFY_MUTED_COURSES": list(self.notify_muted_courses),
        }
