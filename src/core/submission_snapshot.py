"""Parse Moodle assignment responses and validate desired submission files."""

from collections.abc import Iterable, Mapping
from pathlib import PurePath

from core.submission_models import (
    RemoteFile,
    SelectedFile,
    SubmissionIssue,
    SubmissionIssueCode,
    SubmissionSnapshot,
    normalize_filepath,
)


# Moodle file-type groups observed in assignsubmission_file configuration.  The
# mapping is intentionally small: it provides useful early feedback while the
# server remains the authority for uncommon types and site-specific policies.
_FILE_TYPE_GROUPS = {
    "document": {
        ".csv", ".doc", ".docx", ".odt", ".pdf", ".ppt", ".pptx", ".rtf", ".txt", ".xls", ".xlsx",
    },
    "image": {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".svg", ".tif", ".tiff", ".webp"},
    "web_image": {".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"},
}
_GROUP_MIME_PREFIXES = {
    "document": ("application/", "text/"),
    "image": ("image/",),
    "web_image": ("image/",),
}


def _as_mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _as_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if value is None:
        return default
    return bool(value)


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _config_index(assignment: Mapping[str, object]) -> dict[tuple[str, str, str], object]:
    configs = assignment.get("configs", ())
    if not isinstance(configs, Iterable) or isinstance(configs, (str, bytes, Mapping)):
        return {}

    return {
        (
            str(config.get("subtype", "")).strip().lower(),
            str(config.get("plugin", "")).strip().lower(),
            str(config.get("name", "")).strip().lower(),
        ): config.get("value")
        for item in configs
        if isinstance(item, Mapping) and (config := _as_mapping(item))
    }


def _config_value(
    index: Mapping[tuple[str, str, str], object], names: tuple[str, ...], plugins: tuple[str, ...] = ("assign", "file", "assignsubmission_file")
) -> object:
    for name in names:
        for subtype, plugin, config_name in index:
            if config_name == name and plugin in plugins and subtype in {"assign", "assignsubmission", ""}:
                return index[(subtype, plugin, config_name)]
    return None


def _accepted_file_types(value: object) -> tuple[str, ...]:
    if not isinstance(value, str):
        return ()
    tokens = value.replace(",", " ").replace(";", " ").split()
    normalized = []
    for token in tokens:
        token = token.strip().lower()
        if not token or token in {"*", "all"}:
            return ()
        normalized.append(token if token.startswith(".") or "/" in token or token in _FILE_TYPE_GROUPS else f".{token}")
    return tuple(dict.fromkeys(normalized))


def _remote_files(plugins: object) -> tuple[RemoteFile, ...]:
    if not isinstance(plugins, Iterable) or isinstance(plugins, (str, bytes, Mapping)):
        return ()
    files = []
    for plugin_item in plugins:
        plugin = _as_mapping(plugin_item)
        if str(plugin.get("type", "")).lower() != "file":
            continue
        areas = plugin.get("fileareas", ())
        if not isinstance(areas, Iterable) or isinstance(areas, (str, bytes, Mapping)):
            continue
        for area_item in areas:
            area = _as_mapping(area_item)
            area_files = area.get("files", ())
            if not isinstance(area_files, Iterable) or isinstance(area_files, (str, bytes, Mapping)):
                continue
            for file_item in area_files:
                file_data = _as_mapping(file_item)
                files.append(
                    RemoteFile(
                        name=str(file_data.get("filename", file_data.get("name", ""))),
                        filepath=normalize_filepath(str(file_data.get("filepath", "/"))),
                        size=max(0, _as_int(file_data.get("filesize", file_data.get("size", 0)))),
                        mimetype=str(file_data.get("mimetype", "")),
                        modified_time=max(0, _as_int(file_data.get("timemodified", 0))),
                        url=str(file_data.get("fileurl", file_data.get("url", ""))),
                    )
                )
    return tuple(files)


def _online_text(plugins: object) -> tuple[str, int]:
    if not isinstance(plugins, Iterable) or isinstance(plugins, (str, bytes, Mapping)):
        return "", 0
    for plugin_item in plugins:
        plugin = _as_mapping(plugin_item)
        if str(plugin.get("type", "")).lower() != "onlinetext":
            continue
        editor_fields = plugin.get("editorfields", ())
        if not isinstance(editor_fields, Iterable) or isinstance(editor_fields, (str, bytes, Mapping)):
            continue
        for field_item in editor_fields:
            field = _as_mapping(field_item)
            if str(field.get("name", "onlinetext")).lower() == "onlinetext":
                return str(field.get("text", "")), _as_int(field.get("format", 0))
    return "", 0


def parse_submission_snapshot(assign_id: int, assignment: Mapping[str, object], status: Mapping[str, object]) -> SubmissionSnapshot:
    """Create a pure snapshot from flexible Moodle 4.3 WS response mappings."""
    assignment = _as_mapping(assignment)
    status = _as_mapping(status)
    last_attempt = _as_mapping(status.get("lastattempt"))
    submission = _as_mapping(last_attempt.get("submission"))
    configs = _config_index(assignment)
    plugins = submission.get("plugins", ())
    submission_drafts = _as_bool(_config_value(configs, ("submissiondrafts",)))
    statement_required = _as_bool(_config_value(configs, ("requiresubmissionstatement",)))
    maximum_file_count = max(0, _as_int(_config_value(configs, ("maxfilesubmission", "maxfiles"))))
    maximum_file_bytes = max(0, _as_int(_config_value(configs, ("maxsubmissionsizebytes", "maxbytes"))))
    assignment_submissions_enabled = not _as_bool(assignment.get("nosubmissions"))
    submissions_enabled = _as_bool(
        last_attempt.get("submissionsenabled"),
        assignment_submissions_enabled,
    )

    online_text, online_text_format = _online_text(plugins)
    return SubmissionSnapshot(
        assignment_id=assign_id,
        raw_status=str(submission.get("status", "")),
        can_edit=_as_bool(last_attempt.get("canedit", status.get("canedit"))),
        can_submit=_as_bool(last_attempt.get("cansubmit", status.get("cansubmit"))),
        locked=_as_bool(last_attempt.get("locked", status.get("locked"))),
        graded=_as_bool(last_attempt.get("graded", status.get("graded"))),
        submissions_enabled=submissions_enabled,
        submission_drafts=submission_drafts,
        statement_required=statement_required,
        maximum_file_count=maximum_file_count,
        maximum_file_bytes=maximum_file_bytes,
        accepted_file_types=_accepted_file_types(_config_value(configs, ("acceptedfiletypes", "filetypeslist"))),
        remote_files=_remote_files(plugins),
        online_text=online_text,
        online_text_format=online_text_format,
        attempt_number=max(0, _as_int(last_attempt.get("attemptnumber", submission.get("attemptnumber", 0)))),
        submission_id=max(0, _as_int(submission.get("id", 0))),
        submission_modified_time=max(0, _as_int(submission.get("timemodified", 0))),
    )


def _matches_file_type(file: SelectedFile, accepted_types: tuple[str, ...]) -> bool:
    if not accepted_types:
        return True
    extension = PurePath(file.name).suffix.lower()
    mimetype = file.mimetype.lower()
    for allowed in accepted_types:
        if allowed in _FILE_TYPE_GROUPS:
            if extension in _FILE_TYPE_GROUPS[allowed] or mimetype.startswith(_GROUP_MIME_PREFIXES[allowed]):
                return True
        elif allowed.endswith("/*") and mimetype.startswith(allowed[:-1]):
            return True
        elif allowed.startswith(".") and extension == allowed:
            return True
        elif "/" in allowed and mimetype == allowed:
            return True
    return False


def validate_desired_files(snapshot: SubmissionSnapshot, files: Iterable[SelectedFile]) -> tuple[SubmissionIssue, ...]:
    """Return deterministic local preflight issues for a desired file collection.

    This intentionally does not claim to reproduce Moodle's full policy engine;
    the server remains authoritative when saving the submission.
    """
    desired = tuple(files)
    issues: list[SubmissionIssue] = []
    if snapshot.maximum_file_count and len(desired) > snapshot.maximum_file_count:
        issues.append(SubmissionIssue(SubmissionIssueCode.TOO_MANY_FILES, "Too many files for this assignment."))

    seen: set[tuple[str, str]] = set()
    for file in desired:
        normalized_identity = (normalize_filepath(file.filepath), file.name.casefold())
        if normalized_identity in seen:
            issues.append(
                SubmissionIssue(
                    SubmissionIssueCode.DUPLICATE_FILENAME,
                    f"Duplicate filename: {file.name}",
                    (normalized_identity[0], file.name),
                )
            )
        seen.add(normalized_identity)
        if snapshot.maximum_file_bytes and file.size > snapshot.maximum_file_bytes:
            issues.append(SubmissionIssue(SubmissionIssueCode.FILE_TOO_LARGE, f"File is too large: {file.name}", file.identity))
        if not _matches_file_type(file, snapshot.accepted_file_types):
            issues.append(
                SubmissionIssue(SubmissionIssueCode.FILE_TYPE_NOT_ALLOWED, f"File type is not allowed: {file.name}", file.identity)
            )
    return tuple(issues)
