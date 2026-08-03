"""Immutable domain values for Moodle assignment submission changes.

The values in this module deliberately contain no Moodle transport behaviour.
They can therefore be compared, validated, and handed from the UI to a use
case without retaining authentication URLs or mutable response dictionaries.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import TypeAlias


FileIdentity: TypeAlias = tuple[str, str]


def normalize_filepath(filepath: str) -> str:
    """Return a Moodle file area path with one leading and trailing slash."""
    parts = str(filepath or "").replace("\\", "/").strip("/")
    return f"/{parts}/" if parts else "/"


class MutationOperation(str, Enum):
    ADD = "add"
    REPLACE = "replace"
    REMOVE = "remove"
    CLEAR = "clear"
    RENAME = "rename"


class SubmissionIssueCode(str, Enum):
    TOO_MANY_FILES = "too_many_files"
    FILE_TOO_LARGE = "file_too_large"
    FILE_TYPE_NOT_ALLOWED = "file_type_not_allowed"
    DUPLICATE_FILENAME = "duplicate_filename"


@dataclass(frozen=True)
class SubmissionIssue:
    code: SubmissionIssueCode
    message: str
    identity: FileIdentity | None = None


@dataclass(frozen=True)
class SelectedFile:
    """A locally selected file, with enough metadata for preflight checks."""

    name: str
    size: int
    mimetype: str = ""
    filepath: str = "/"
    source_path: str = field(default="", repr=False, compare=False)

    @property
    def identity(self) -> FileIdentity:
        return normalize_filepath(self.filepath), self.name


@dataclass(frozen=True)
class RemoteFile:
    name: str
    filepath: str
    size: int
    mimetype: str
    modified_time: int
    url: str = field(repr=False, compare=False)

    @property
    def identity(self) -> FileIdentity:
        return normalize_filepath(self.filepath), self.name


@dataclass(frozen=True)
class SubmissionSnapshot:
    assignment_id: int
    raw_status: str
    can_edit: bool
    can_submit: bool
    locked: bool
    graded: bool
    submissions_enabled: bool
    submission_drafts: bool
    statement_required: bool
    maximum_file_count: int
    maximum_file_bytes: int
    accepted_file_types: tuple[str, ...]
    remote_files: tuple[RemoteFile, ...]
    online_text: str
    online_text_format: int
    attempt_number: int
    submission_id: int
    submission_modified_time: int

    @property
    def is_editable(self) -> bool:
        return self.submissions_enabled and self.can_edit and not self.locked and not self.graded

    @property
    def remote_names(self) -> tuple[str, ...]:
        """Return deterministic display names for the current server file set."""
        return tuple(sorted(file.name for file in self.remote_files))

    @property
    def remote_identities(self) -> tuple[FileIdentity, ...]:
        """Return deterministic Moodle path/name identities for verification and UI use."""
        return tuple(sorted(file.identity for file in self.remote_files))

    @property
    def fingerprint(self) -> str:
        """Stable server-state fingerprint that intentionally excludes file URLs."""
        from hashlib import sha256
        import json

        payload = {
            "assignment_id": self.assignment_id,
            "submission_id": self.submission_id,
            "attempt_number": self.attempt_number,
            "raw_status": self.raw_status,
            "can_edit": self.can_edit,
            "can_submit": self.can_submit,
            "locked": self.locked,
            "graded": self.graded,
            "submission_modified_time": self.submission_modified_time,
            "files": sorted((f.filepath, f.name, f.size, f.modified_time) for f in self.remote_files),
        }
        return sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class FileMutationIntent:
    """Requested file mutation, checked against a snapshot by the use case."""

    operation: MutationOperation
    selected_files: tuple[SelectedFile, ...] = ()
    remove_identities: tuple[FileIdentity, ...] = ()
    rename_identity: FileIdentity | None = None
    new_name: str = ""
    new_filepath: str = "/"
    finalize: bool = False
    accept_statement: bool = False
    expected_fingerprint: str = ""
