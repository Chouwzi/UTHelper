"""Verified exact-set workflow for Moodle assignment file submissions."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlparse

from core.moodle_service import MoodleService
from core.submission_models import (
    FileIdentity,
    FileMutationIntent,
    MutationOperation,
    RemoteFile,
    SelectedFile,
    SubmissionIssueCode,
    SubmissionSnapshot,
    normalize_filepath,
)
from core.submission_snapshot import parse_submission_snapshot, validate_desired_files

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SubmissionTarget:
    url: str
    course_id: int


@dataclass(frozen=True)
class SelectedSubmissionFile:
    """Byte-backed picker content kept outside intent reprs and logs."""

    name: str
    bytes: bytes = field(repr=False)
    filepath: str = "/"


@dataclass(frozen=True)
class SubmittedFile:
    """Legacy display value retained until the GUI consumes snapshots."""

    name: str
    url: str
    filepath: str = "/"


@dataclass(frozen=True)
class FileMetadataUpdate:
    """Legacy rename input; author/license are intentionally not transported."""

    new_name: str
    author: str = ""
    license: str = "unknown"
    filepath: str = "/"


@dataclass(frozen=True)
class SubmittedFilesResult:
    files: list[SubmittedFile]
    last_server_status: Optional[str] = None


class SubmissionErrorCode(str, Enum):
    INVALID_TARGET = "invalid_target"
    ASSIGNMENT_NOT_FOUND = "assignment_not_found"
    SNAPSHOT_LOAD_FAILED = "snapshot_load_failed"
    SUBMISSIONS_CLOSED = "submissions_closed"
    LOCKED = "locked"
    GRADED = "graded"
    NOT_EDITABLE = "not_editable"
    STALE_SNAPSHOT = "stale_snapshot"
    DUPLICATE_FILENAME = "duplicate_filename"
    TOO_MANY_FILES = "too_many_files"
    FILE_TOO_LARGE = "file_too_large"
    FILE_TYPE_NOT_ALLOWED = "file_type_not_allowed"
    UNSUPPORTED_OPERATION = "unsupported_operation"
    DOWNLOAD_FAILED = "download_failed"
    DOWNLOAD_SIZE_MISMATCH = "download_size_mismatch"
    LOCAL_FILE_READ_FAILED = "local_file_read_failed"
    DRAFT_ALLOCATION_FAILED = "draft_allocation_failed"
    UPLOAD_FAILED = "upload_failed"
    SAVE_REJECTED = "save_rejected"
    FINALIZE_REJECTED = "finalize_rejected"
    STATEMENT_NOT_ACCEPTED = "statement_not_accepted"
    VERIFICATION_FAILED = "verification_failed"


@dataclass(frozen=True)
class SubmissionError:
    code: SubmissionErrorCode
    message: str
    identity: FileIdentity | None = None


class MutationOutcome(str, Enum):
    DRAFT_SAVED = "draft_saved"
    SUBMISSION_SAVED = "submission_saved"
    SUBMITTED_FOR_GRADING = "submitted_for_grading"


@dataclass(frozen=True)
class SubmissionSnapshotResult:
    ok: bool
    snapshot: SubmissionSnapshot | None = None
    issue: SubmissionError | None = None

    @classmethod
    def success(cls, snapshot: SubmissionSnapshot) -> SubmissionSnapshotResult:
        return cls(ok=True, snapshot=snapshot)

    @classmethod
    def failure(cls, issue: SubmissionError) -> SubmissionSnapshotResult:
        return cls(ok=False, issue=issue)

    def as_mutation_failure(self) -> SubmissionMutationResult:
        if self.issue is None:
            issue = _error(SubmissionErrorCode.SNAPSHOT_LOAD_FAILED)
        else:
            issue = self.issue
        return SubmissionMutationResult.failure(issue, self.snapshot)


@dataclass(frozen=True)
class SubmissionMutationResult:
    ok: bool
    snapshot: SubmissionSnapshot | None
    issue: SubmissionError | None = None
    outcome: MutationOutcome | None = None
    partial: bool = False

    @classmethod
    def success(
        cls,
        snapshot: SubmissionSnapshot,
        outcome: MutationOutcome,
    ) -> SubmissionMutationResult:
        return cls(ok=True, snapshot=snapshot, outcome=outcome)

    @classmethod
    def failure(
        cls,
        issue: SubmissionError,
        snapshot: SubmissionSnapshot | None,
        *,
        partial: bool = False,
    ) -> SubmissionMutationResult:
        return cls(ok=False, snapshot=snapshot, issue=issue, partial=partial)


_ERROR_MESSAGES = {
    SubmissionErrorCode.INVALID_TARGET: "The assignment URL is invalid.",
    SubmissionErrorCode.ASSIGNMENT_NOT_FOUND: "The assignment could not be found.",
    SubmissionErrorCode.SNAPSHOT_LOAD_FAILED: "The latest submission state could not be loaded.",
    SubmissionErrorCode.SUBMISSIONS_CLOSED: "Submissions are not enabled for this assignment.",
    SubmissionErrorCode.LOCKED: "The submission is locked.",
    SubmissionErrorCode.GRADED: "The submission has already been graded.",
    SubmissionErrorCode.NOT_EDITABLE: "The submission is not editable.",
    SubmissionErrorCode.STALE_SNAPSHOT: "The submission changed on Moodle. Refresh and try again.",
    SubmissionErrorCode.DUPLICATE_FILENAME: "Two files have the same path and filename.",
    SubmissionErrorCode.TOO_MANY_FILES: "The desired submission contains too many files.",
    SubmissionErrorCode.FILE_TOO_LARGE: "A file exceeds the assignment size limit.",
    SubmissionErrorCode.FILE_TYPE_NOT_ALLOWED: "A file type is not allowed for this assignment.",
    SubmissionErrorCode.UNSUPPORTED_OPERATION: "The requested file change is not supported.",
    SubmissionErrorCode.DOWNLOAD_FAILED: "An existing submitted file could not be downloaded.",
    SubmissionErrorCode.DOWNLOAD_SIZE_MISMATCH: "An existing file did not match Moodle's recorded size.",
    SubmissionErrorCode.LOCAL_FILE_READ_FAILED: "A selected local file could not be read safely.",
    SubmissionErrorCode.DRAFT_ALLOCATION_FAILED: "Moodle could not allocate a draft area.",
    SubmissionErrorCode.UPLOAD_FAILED: "A file could not be uploaded to the Moodle draft area.",
    SubmissionErrorCode.SAVE_REJECTED: "Moodle rejected the submission save.",
    SubmissionErrorCode.FINALIZE_REJECTED: "The draft was saved, but Moodle did not finalize it.",
    SubmissionErrorCode.STATEMENT_NOT_ACCEPTED: "The submission statement must be accepted explicitly.",
    SubmissionErrorCode.VERIFICATION_FAILED: "Moodle's refreshed submission does not match the requested files.",
}


def _error(
    code: SubmissionErrorCode,
    identity: FileIdentity | None = None,
) -> SubmissionError:
    return SubmissionError(code, _ERROR_MESSAGES[code], identity)


@dataclass(frozen=True)
class _SnapshotContext:
    assignment_id: int
    assignment: dict[str, Any]
    snapshot: SubmissionSnapshot


@dataclass(frozen=True)
class _PlannedFile:
    metadata: SelectedFile
    remote: RemoteFile | None = None


_VALIDATION_CODES = {
    SubmissionIssueCode.DUPLICATE_FILENAME: SubmissionErrorCode.DUPLICATE_FILENAME,
    SubmissionIssueCode.TOO_MANY_FILES: SubmissionErrorCode.TOO_MANY_FILES,
    SubmissionIssueCode.FILE_TOO_LARGE: SubmissionErrorCode.FILE_TOO_LARGE,
    SubmissionIssueCode.FILE_TYPE_NOT_ALLOWED: SubmissionErrorCode.FILE_TYPE_NOT_ALLOWED,
}


class SubmissionWorkflow:
    """Own Moodle file mutation policy and return only refreshed server truth."""

    def __init__(self, client: Any, moodle_service: Optional[MoodleService] = None):
        self.client = client
        self.moodle_service = moodle_service or MoodleService(client.call_ws_api)

    def load_snapshot(
        self,
        target: SubmissionTarget,
        prefetched_status: Optional[dict[str, Any]] = None,
    ) -> SubmissionSnapshotResult:
        loaded = self._load_context(target, prefetched_status)
        if isinstance(loaded, SubmissionError):
            return SubmissionSnapshotResult.failure(loaded)
        return SubmissionSnapshotResult.success(loaded.snapshot)

    def mutate_files(
        self,
        target: SubmissionTarget,
        intent: FileMutationIntent,
        *,
        selected_files: tuple[SelectedSubmissionFile, ...] = (),
        safety_guard: Callable[[SubmissionSnapshot], bool] | None = None,
    ) -> SubmissionMutationResult:
        selected_bytes = {
            (normalize_filepath(item.filepath), item.name): item.bytes
            for item in selected_files
        }
        return self._mutate_files(target, intent, selected_bytes, safety_guard)

    def _mutate_files(
        self,
        target: SubmissionTarget,
        intent: FileMutationIntent,
        selected_bytes: dict[FileIdentity, bytes],
        safety_guard: Callable[[SubmissionSnapshot], bool] | None = None,
    ) -> SubmissionMutationResult:
        loaded = self._load_context(target, None)
        if isinstance(loaded, SubmissionError):
            return SubmissionMutationResult.failure(loaded, None)
        snapshot = loaded.snapshot

        issue = self._permission_issue(snapshot)
        if issue is not None:
            return SubmissionMutationResult.failure(issue, snapshot)
        if intent.expected_fingerprint and intent.expected_fingerprint != snapshot.fingerprint:
            return SubmissionMutationResult.failure(
                _error(SubmissionErrorCode.STALE_SNAPSHOT), snapshot
            )
        if safety_guard is not None:
            try:
                safe = safety_guard(snapshot)
            except Exception:
                safe = False
            if not safe:
                return SubmissionMutationResult.failure(
                    _error(SubmissionErrorCode.STALE_SNAPSHOT), snapshot
                )

        plan_or_issue = self._build_plan(snapshot, intent)
        if isinstance(plan_or_issue, SubmissionError):
            return SubmissionMutationResult.failure(plan_or_issue, snapshot)
        plan = plan_or_issue
        validation = validate_desired_files(snapshot, (item.metadata for item in plan))
        if validation:
            first = validation[0]
            code = _VALIDATION_CODES[first.code]
            return SubmissionMutationResult.failure(_error(code, first.identity), snapshot)

        materialized = self._materialize(plan, selected_bytes)
        if isinstance(materialized, SubmissionError):
            return SubmissionMutationResult.failure(materialized, snapshot)
        return self._upload_save_verify(loaded, plan, materialized, intent)

    def _load_context(
        self,
        target: SubmissionTarget,
        prefetched_status: Optional[dict[str, Any]],
    ) -> _SnapshotContext | SubmissionError:
        cmid = self._extract_cmid(target.url)
        if cmid is None:
            return _error(SubmissionErrorCode.INVALID_TARGET)
        try:
            assignment_id = self.moodle_service.resolve_cmid_to_assign_id(
                cmid, target.course_id
            )
        except Exception:
            logger.exception("Could not resolve Moodle assignment")
            return _error(SubmissionErrorCode.ASSIGNMENT_NOT_FOUND)
        if not assignment_id:
            return _error(SubmissionErrorCode.ASSIGNMENT_NOT_FOUND)

        try:
            response = self.moodle_service.get_assignments([target.course_id])
            assignment = self._find_assignment(response, int(assignment_id))
            status = prefetched_status
            if status is None:
                status = self.moodle_service.get_submission_status(int(assignment_id))
        except Exception:
            logger.exception("Could not load Moodle submission snapshot")
            return _error(SubmissionErrorCode.SNAPSHOT_LOAD_FAILED)
        if assignment is None or not isinstance(status, dict):
            return _error(SubmissionErrorCode.SNAPSHOT_LOAD_FAILED)
        snapshot = parse_submission_snapshot(int(assignment_id), assignment, status)
        return _SnapshotContext(int(assignment_id), assignment, snapshot)

    @staticmethod
    def _extract_cmid(url: str) -> int | None:
        try:
            values = parse_qs(urlparse(url).query).get("id", ())
            cmid = int(values[0])
            return cmid if cmid > 0 else None
        except (IndexError, TypeError, ValueError):
            return None

    @classmethod
    def _find_assignment(
        cls,
        response: object,
        assignment_id: int,
    ) -> dict[str, Any] | None:
        if isinstance(response, dict):
            try:
                is_assignment = int(response.get("id", 0)) == assignment_id
            except (TypeError, ValueError):
                is_assignment = False
            if is_assignment and "assignments" not in response:
                return response
            for key in ("assignments", "courses"):
                found = cls._find_assignment(response.get(key), assignment_id)
                if found is not None:
                    return found
        elif isinstance(response, (list, tuple)):
            for item in response:
                found = cls._find_assignment(item, assignment_id)
                if found is not None:
                    return found
        return None

    @staticmethod
    def _permission_issue(snapshot: SubmissionSnapshot) -> SubmissionError | None:
        if not snapshot.submissions_enabled:
            return _error(SubmissionErrorCode.SUBMISSIONS_CLOSED)
        if snapshot.locked:
            return _error(SubmissionErrorCode.LOCKED)
        if snapshot.graded:
            return _error(SubmissionErrorCode.GRADED)
        if not snapshot.can_edit:
            return _error(SubmissionErrorCode.NOT_EDITABLE)
        return None

    @staticmethod
    def _build_plan(
        snapshot: SubmissionSnapshot,
        intent: FileMutationIntent,
    ) -> tuple[_PlannedFile, ...] | SubmissionError:
        remote_items = [
            _PlannedFile(
                SelectedFile(
                    name=item.name,
                    size=item.size,
                    mimetype=item.mimetype,
                    filepath=item.filepath,
                ),
                remote=item,
            )
            for item in snapshot.remote_files
        ]
        local_items = [_PlannedFile(item) for item in intent.selected_files]

        if intent.operation is MutationOperation.ADD:
            return tuple(remote_items + local_items)
        if intent.operation is MutationOperation.REPLACE:
            return tuple(local_items)
        if intent.operation is MutationOperation.CLEAR:
            return ()
        if intent.operation is MutationOperation.REMOVE:
            wanted = {
                (normalize_filepath(filepath), name)
                for filepath, name in intent.remove_identities
            }
            existing = {item.remote.identity for item in remote_items if item.remote}
            if not wanted or not wanted.issubset(existing):
                return _error(SubmissionErrorCode.UNSUPPORTED_OPERATION)
            return tuple(
                item for item in remote_items if item.remote and item.remote.identity not in wanted
            )
        if intent.operation is MutationOperation.RENAME:
            if intent.rename_identity is None or not intent.new_name:
                return _error(SubmissionErrorCode.UNSUPPORTED_OPERATION)
            target = (normalize_filepath(intent.rename_identity[0]), intent.rename_identity[1])
            changed = False
            result = []
            for item in remote_items:
                if item.remote and item.remote.identity == target:
                    changed = True
                    result.append(
                        _PlannedFile(
                            SelectedFile(
                                name=intent.new_name,
                                size=item.metadata.size,
                                mimetype=item.metadata.mimetype,
                                filepath=normalize_filepath(intent.new_filepath),
                            ),
                            remote=item.remote,
                        )
                    )
                else:
                    result.append(item)
            if not changed:
                return _error(SubmissionErrorCode.UNSUPPORTED_OPERATION, target)
            return tuple(result)
        return _error(SubmissionErrorCode.UNSUPPORTED_OPERATION)

    def _materialize(
        self,
        plan: tuple[_PlannedFile, ...],
        selected_bytes: dict[FileIdentity, bytes],
    ) -> tuple[tuple[_PlannedFile, bytes], ...] | SubmissionError:
        materialized: list[tuple[_PlannedFile, bytes]] = []
        for item in plan:
            if item.remote is not None:
                if not item.remote.url:
                    return _error(SubmissionErrorCode.DOWNLOAD_FAILED, item.remote.identity)
                try:
                    content = self.client.download_file(item.remote.url)
                except Exception:
                    logger.exception("Could not download retained submission file")
                    return _error(SubmissionErrorCode.DOWNLOAD_FAILED, item.remote.identity)
                if not isinstance(content, bytes) or not content:
                    return _error(SubmissionErrorCode.DOWNLOAD_FAILED, item.remote.identity)
                if item.remote.size and len(content) != item.remote.size:
                    return _error(
                        SubmissionErrorCode.DOWNLOAD_SIZE_MISMATCH,
                        item.remote.identity,
                    )
            else:
                content = selected_bytes.get(item.metadata.identity)
                if content is None:
                    try:
                        with Path(item.metadata.source_path).open("rb") as selected:
                            content = selected.read(item.metadata.size + 1)
                    except (OSError, ValueError):
                        return _error(
                            SubmissionErrorCode.LOCAL_FILE_READ_FAILED,
                            item.metadata.identity,
                        )
                if len(content) != item.metadata.size:
                    return _error(
                        SubmissionErrorCode.LOCAL_FILE_READ_FAILED,
                        item.metadata.identity,
                    )
            materialized.append((item, content))
        return tuple(materialized)

    def _upload_save_verify(
        self,
        context: _SnapshotContext,
        plan: tuple[_PlannedFile, ...],
        materialized: tuple[tuple[_PlannedFile, bytes], ...],
        intent: FileMutationIntent,
    ) -> SubmissionMutationResult:
        snapshot = context.snapshot
        try:
            draft_id = self.moodle_service.get_unused_draft_itemid()
        except Exception:
            draft_id = None
        if not draft_id:
            return SubmissionMutationResult.failure(
                _error(SubmissionErrorCode.DRAFT_ALLOCATION_FAILED), snapshot
            )

        tracked: list[FileIdentity] = []
        save_attempted = False
        failure: SubmissionError | None = None
        try:
            for item, content in materialized:
                expected_identity = item.metadata.identity
                try:
                    record = self.client.upload_draft_file_record(
                        item.metadata.name,
                        content,
                        itemid=draft_id,
                        filepath=item.metadata.filepath,
                    )
                except Exception:
                    logger.error("Moodle draft upload failed")
                    record = None
                if record is None:
                    failure = _error(SubmissionErrorCode.UPLOAD_FAILED, expected_identity)
                    break
                tracked.append((normalize_filepath(record.filepath), record.filename))
                if record.itemid != draft_id or tracked[-1] != expected_identity:
                    failure = _error(SubmissionErrorCode.UPLOAD_FAILED, expected_identity)
                    break

            if failure is None:
                try:
                    text_draft_id = self.moodle_service.get_unused_draft_itemid()
                except Exception:
                    text_draft_id = None
                if not text_draft_id:
                    failure = _error(SubmissionErrorCode.DRAFT_ALLOCATION_FAILED)

            if failure is not None:
                return SubmissionMutationResult.failure(failure, snapshot)

            save_attempted = True
            try:
                saved = self.moodle_service.save_assignment_submission_result(
                    context.assignment_id,
                    draft_id,
                    snapshot.online_text,
                    snapshot.online_text_format,
                    text_draft_id,
                )
            except Exception:
                logger.exception("Moodle submission save failed")
                return SubmissionMutationResult.failure(
                    _error(SubmissionErrorCode.SAVE_REJECTED), snapshot
                )
            if not saved.ok or saved.warnings:
                return SubmissionMutationResult.failure(
                    _error(SubmissionErrorCode.SAVE_REJECTED), snapshot
                )
        finally:
            if not save_attempted and tracked:
                try:
                    cleanup_ok = self.moodle_service.delete_draft_files(draft_id, tracked)
                except Exception:
                    cleanup_ok = False
                if not cleanup_ok:
                    logger.warning("Could not clean tracked Moodle draft files")

        refreshed = self._refresh_snapshot(context)
        if isinstance(refreshed, SubmissionError):
            return SubmissionMutationResult.failure(
                refreshed, None, partial=True
            )
        if not self._files_match(plan, refreshed):
            return SubmissionMutationResult.failure(
                _error(SubmissionErrorCode.VERIFICATION_FAILED), refreshed
            )

        if not snapshot.submission_drafts:
            if refreshed.raw_status != "submitted":
                return SubmissionMutationResult.failure(
                    _error(SubmissionErrorCode.VERIFICATION_FAILED), refreshed
                )
            return SubmissionMutationResult.success(
                refreshed, MutationOutcome.SUBMISSION_SAVED
            )
        if not intent.finalize:
            if refreshed.raw_status != "draft":
                return SubmissionMutationResult.failure(
                    _error(SubmissionErrorCode.VERIFICATION_FAILED), refreshed
                )
            return SubmissionMutationResult.success(refreshed, MutationOutcome.DRAFT_SAVED)
        if not refreshed.can_submit:
            return SubmissionMutationResult.failure(
                _error(SubmissionErrorCode.FINALIZE_REJECTED),
                refreshed,
                partial=True,
            )
        if refreshed.statement_required and not intent.accept_statement:
            return SubmissionMutationResult.failure(
                _error(SubmissionErrorCode.STATEMENT_NOT_ACCEPTED),
                refreshed,
                partial=True,
            )

        try:
            finalized = self.moodle_service.submit_for_grading_result(
                context.assignment_id, intent.accept_statement
            )
        except Exception:
            logger.exception("Moodle finalization failed")
            finalized = None
        if finalized is None or not finalized.ok or finalized.warnings:
            return SubmissionMutationResult.failure(
                _error(SubmissionErrorCode.FINALIZE_REJECTED),
                refreshed,
                partial=True,
            )

        final_snapshot = self._refresh_snapshot(context)
        if isinstance(final_snapshot, SubmissionError):
            return SubmissionMutationResult.failure(
                final_snapshot, None, partial=True
            )
        if final_snapshot.raw_status != "submitted" or not self._files_match(
            plan, final_snapshot
        ):
            return SubmissionMutationResult.failure(
                _error(SubmissionErrorCode.VERIFICATION_FAILED),
                final_snapshot,
                partial=True,
            )
        return SubmissionMutationResult.success(
            final_snapshot, MutationOutcome.SUBMITTED_FOR_GRADING
        )

    def _refresh_snapshot(
        self,
        context: _SnapshotContext,
    ) -> SubmissionSnapshot | SubmissionError:
        try:
            status = self.moodle_service.get_submission_status(context.assignment_id)
        except Exception:
            logger.exception("Could not refresh Moodle submission status")
            return _error(SubmissionErrorCode.SNAPSHOT_LOAD_FAILED)
        if not isinstance(status, dict):
            return _error(SubmissionErrorCode.SNAPSHOT_LOAD_FAILED)
        return parse_submission_snapshot(
            context.assignment_id, context.assignment, status
        )

    @staticmethod
    def _files_match(
        plan: tuple[_PlannedFile, ...],
        snapshot: SubmissionSnapshot,
    ) -> bool:
        expected = sorted(
            (item.metadata.identity[0], item.metadata.name, item.metadata.size)
            for item in plan
        )
        actual = sorted(
            (item.identity[0], item.name, item.size)
            for item in snapshot.remote_files
        )
        return expected == actual

    # Deprecated adapters retained until the submission GUI migration.
    def load_submitted_files(
        self,
        target: SubmissionTarget,
        prefetched_status: Optional[dict[str, Any]] = None,
    ) -> SubmittedFilesResult:
        result = self.load_snapshot(target, prefetched_status)
        if not result.ok or result.snapshot is None:
            return SubmittedFilesResult([], None)
        return SubmittedFilesResult(
            [
                SubmittedFile(item.name, item.url, item.filepath)
                for item in result.snapshot.remote_files
            ],
            self._map_submission_status(result.snapshot.raw_status),
        )

    def submit_files(
        self,
        target: SubmissionTarget,
        selected_files: list[SelectedSubmissionFile],
        submitted_files: list[SubmittedFile],
        overwrite: bool,
        accept_submission_statement: bool = False,
    ) -> bool:
        del submitted_files
        metadata = tuple(
            SelectedFile(item.name, len(item.bytes), source_path="")
            for item in selected_files
        )
        byte_map = {item.identity: legacy.bytes for item, legacy in zip(metadata, selected_files)}
        result = self._mutate_files(
            target,
            FileMutationIntent(
                operation=MutationOperation.REPLACE if overwrite else MutationOperation.ADD,
                selected_files=metadata,
                finalize=accept_submission_statement,
                accept_statement=accept_submission_statement,
            ),
            byte_map,
            None,
        )
        return result.ok

    def remove_files(
        self,
        target: SubmissionTarget,
        files_to_keep: list[SubmittedFile],
        accept_submission_statement: bool = False,
    ) -> bool:
        if not files_to_keep:
            raise ValueError(
                "Bài tập này không hỗ trợ xóa thông qua app. "
                "Vui lòng mở trình duyệt để xóa bài làm."
            )
        current = self.load_snapshot(target)
        if not current.ok or current.snapshot is None:
            return False
        keep = {
            (normalize_filepath(item.filepath), item.name) for item in files_to_keep
        }
        remove = tuple(
            item.identity for item in current.snapshot.remote_files if item.identity not in keep
        )
        if not remove:
            return True
        result = self.mutate_files(
            target,
            FileMutationIntent(
                operation=MutationOperation.REMOVE,
                remove_identities=remove,
                finalize=accept_submission_statement,
                accept_statement=accept_submission_statement,
            ),
        )
        if not result.ok:
            raise ValueError("Moodle từ chối lưu bài nộp. Thử lại hoặc mở trình duyệt.")
        return True

    def update_file_metadata(
        self,
        target: SubmissionTarget,
        submitted_files: list[SubmittedFile],
        target_idx: int,
        meta: FileMetadataUpdate,
        accept_submission_statement: bool = False,
    ) -> bool:
        if target_idx < 0 or target_idx >= len(submitted_files):
            return False
        selected = submitted_files[target_idx]
        result = self.mutate_files(
            target,
            FileMutationIntent(
                operation=MutationOperation.RENAME,
                rename_identity=(normalize_filepath(selected.filepath), selected.name),
                new_name=meta.new_name,
                new_filepath=meta.filepath,
                finalize=accept_submission_statement,
                accept_statement=accept_submission_statement,
            ),
        )
        return result.ok

    @staticmethod
    def _map_submission_status(raw_status: str) -> str:
        return {
            "submitted": "Đã nộp",
            "new": "Chưa nộp",
            "draft": "Bản nháp",
            "reopened": "Được mở lại",
        }.get(raw_status, "Chưa nộp")
