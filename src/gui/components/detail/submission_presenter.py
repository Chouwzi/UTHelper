"""Pure presentation policy for Moodle assignment submissions."""

from __future__ import annotations

from dataclasses import dataclass

from core.submission_models import SubmissionSnapshot
from core.use_cases.submission_workflow import (
    MutationOutcome,
    SubmissionErrorCode,
    SubmissionMutationResult,
)


_ERROR_MESSAGES = {
    SubmissionErrorCode.INVALID_TARGET: "Không thể xác định bài tập trên Moodle.",
    SubmissionErrorCode.ASSIGNMENT_NOT_FOUND: "Không tìm thấy bài tập trên Moodle.",
    SubmissionErrorCode.SNAPSHOT_LOAD_FAILED: "Không thể kiểm tra trạng thái bài nộp mới nhất.",
    SubmissionErrorCode.SUBMISSIONS_CLOSED: "Moodle hiện không cho phép nộp bài này.",
    SubmissionErrorCode.LOCKED: "Bài nộp đã bị khóa trên Moodle.",
    SubmissionErrorCode.GRADED: "Bài nộp đã được chấm trên Moodle.",
    SubmissionErrorCode.NOT_EDITABLE: "Bạn không có quyền chỉnh sửa bài nộp này.",
    SubmissionErrorCode.STALE_SNAPSHOT: "Bài nộp đã thay đổi trên Moodle. Hãy thử lại.",
    SubmissionErrorCode.DUPLICATE_FILENAME: "Có file trùng tên và đường dẫn.",
    SubmissionErrorCode.TOO_MANY_FILES: "Số file vượt quá giới hạn của bài tập.",
    SubmissionErrorCode.FILE_TOO_LARGE: "Có file vượt quá dung lượng cho phép.",
    SubmissionErrorCode.FILE_TYPE_NOT_ALLOWED: "Định dạng file không được bài tập chấp nhận.",
    SubmissionErrorCode.UNSUPPORTED_OPERATION: "Không thể thực hiện thay đổi file này.",
    SubmissionErrorCode.DOWNLOAD_FAILED: "Không thể đọc lại một file hiện có từ Moodle.",
    SubmissionErrorCode.DOWNLOAD_SIZE_MISMATCH: "Một file trên Moodle có dung lượng không khớp.",
    SubmissionErrorCode.LOCAL_FILE_READ_FAILED: "Không thể đọc an toàn file đã chọn.",
    SubmissionErrorCode.DRAFT_ALLOCATION_FAILED: "Moodle không thể tạo vùng lưu tạm.",
    SubmissionErrorCode.UPLOAD_FAILED: "Không thể tải một file lên Moodle.",
    SubmissionErrorCode.SAVE_REJECTED: "Moodle từ chối lưu bài nộp.",
    SubmissionErrorCode.FINALIZE_REJECTED: "Đã lưu bản nháp nhưng Moodle chưa nhận nộp bài.",
    SubmissionErrorCode.STATEMENT_NOT_ACCEPTED: "Bạn cần xác nhận cam kết trước khi nộp bài.",
    SubmissionErrorCode.VERIFICATION_FAILED: "Moodle trả về danh sách file khác với thay đổi yêu cầu.",
}

_OUTCOME_MESSAGES = {
    MutationOutcome.DRAFT_SAVED: "Đã lưu bản nháp trên Moodle.",
    MutationOutcome.SUBMISSION_SAVED: "Đã lưu bài nộp trên Moodle.",
    MutationOutcome.SUBMITTED_FOR_GRADING: "Đã nộp bài để chấm trên Moodle.",
}


def _format_bytes(byte_count: int) -> str:
    if byte_count <= 0:
        return "không giới hạn dung lượng"
    megabytes = byte_count / (1024 * 1024)
    amount = f"{megabytes:.1f}".rstrip("0").rstrip(".")
    return f"{amount} MB"


def _format_limits(snapshot: SubmissionSnapshot) -> str:
    count = (
        f"Tối đa {snapshot.maximum_file_count} file"
        if snapshot.maximum_file_count > 0
        else "Không giới hạn số file"
    )
    accepted = ", ".join(snapshot.accepted_file_types) or "mọi định dạng"
    return f"{count} · {_format_bytes(snapshot.maximum_file_bytes)} · {accepted}"


@dataclass(frozen=True)
class SubmissionUiPolicy:
    """Controls exposed by the UI for one authoritative server snapshot."""

    show_picker: bool
    show_file_actions: bool
    show_save_submission: bool
    show_save_draft: bool
    show_finalize: bool
    show_statement: bool
    primary_action_label: str
    edit_reason: str
    limit_text: str

    @classmethod
    def from_snapshot(cls, snapshot: SubmissionSnapshot) -> SubmissionUiPolicy:
        editable = snapshot.is_editable
        file_editable = (
            editable
            and snapshot.file_submission_enabled
            and not snapshot.team_submission
        )
        reason = ""
        if snapshot.locked:
            reason = "Bài nộp đã bị khóa trên Moodle."
        elif snapshot.graded:
            reason = "Bài nộp đã được chấm trên Moodle."
        elif not snapshot.submissions_enabled:
            reason = "Moodle hiện không cho phép nộp bài này."
        elif not snapshot.can_edit:
            reason = "Bạn không có quyền chỉnh sửa bài nộp này trên Moodle."
        elif snapshot.team_submission:
            reason = (
                "Bài nộp nhóm chỉ được chỉnh sửa trong trình duyệt để bảo vệ "
                "bài làm chung."
            )
        elif not snapshot.file_submission_enabled:
            reason = "Bài tập này không bật nộp file trên Moodle."

        draft_enabled = file_editable and snapshot.submission_drafts
        show_finalize = (
            editable
            and not snapshot.team_submission
            and snapshot.submission_drafts
            and snapshot.can_submit
        )
        return cls(
            show_picker=file_editable,
            show_file_actions=file_editable,
            show_save_submission=file_editable and not snapshot.submission_drafts,
            show_save_draft=draft_enabled,
            show_finalize=show_finalize,
            show_statement=show_finalize and snapshot.statement_required,
            primary_action_label=(
                "Lưu bản nháp" if snapshot.submission_drafts else "Lưu bài nộp"
            ),
            edit_reason=reason,
            limit_text=_format_limits(snapshot),
        )


def mutation_message(result: SubmissionMutationResult) -> str:
    """Return a concise message without exposing transport details."""
    if result.ok and result.outcome is not None:
        return _OUTCOME_MESSAGES[result.outcome]
    if result.issue is not None:
        if (
            result.partial
            and result.snapshot is None
            and result.issue.code is SubmissionErrorCode.SNAPSHOT_LOAD_FAILED
        ):
            return "Đã gửi thay đổi nhưng không thể xác minh trạng thái mới nhất trên Moodle."
        return _ERROR_MESSAGES[result.issue.code]
    return "Không thể xác nhận thay đổi bài nộp trên Moodle."
