import logging
from dataclasses import dataclass
from typing import Any, Optional

from core.moodle_service import MoodleService

logger = logging.getLogger(__name__)


@dataclass
class SubmissionTarget:
    url: str
    course_id: int


@dataclass
class SelectedSubmissionFile:
    name: str
    bytes: bytes


@dataclass
class SubmittedFile:
    name: str
    url: str
    filepath: str = "/"


@dataclass
class FileMetadataUpdate:
    new_name: str
    author: str = ""
    license: str = "unknown"
    filepath: str = "/"


@dataclass
class SubmissionResult:
    success: bool
    message: str = ""


@dataclass
class SubmittedFilesResult:
    files: list[SubmittedFile]
    last_server_status: Optional[str] = None


class SubmissionWorkflow:
    """Workflow xử lý file nộp bài Moodle, tách khỏi tầng giao diện."""

    def __init__(self, client, moodle_service: Optional[MoodleService] = None):
        self.client = client
        self.moodle_service = moodle_service or MoodleService(client.call_ws_api)

    def _extract_cmid(self, url: str) -> Optional[int]:
        if "id=" not in url:
            return None
        try:
            return int(url.split("id=")[-1].split("&")[0])
        except (ValueError, IndexError):
            return None

    def _resolve_assign_id(self, url: str, course_id: int) -> Optional[int]:
        cmid = self._extract_cmid(url)
        if not cmid:
            logger.error("Không extract được cmid từ URL: %s", url)
            return None
        assign_id = self.moodle_service.resolve_cmid_to_assign_id(cmid, course_id)
        if not assign_id:
            logger.error("Không resolve được assign_id từ cmid=%d, course=%d", cmid, course_id)
            return None
        return assign_id

    def load_submitted_files(
        self,
        target: SubmissionTarget,
        prefetched_status: Optional[dict] = None,
    ) -> SubmittedFilesResult:
        assign_id = self._resolve_assign_id(target.url, target.course_id)
        if not assign_id:
            return SubmittedFilesResult(files=[], last_server_status=None)

        status = prefetched_status
        if status is None:
            try:
                status = self.moodle_service.get_submission_status(assign_id)
            except Exception:
                status = None

        last_server_status = self._map_submission_status(status)
        files = self.moodle_service.get_submitted_files(assign_id, status=status)
        return SubmittedFilesResult(
            files=[self._submitted_file_from_mapping(file_item) for file_item in files],
            last_server_status=last_server_status,
        )

    def submit_files(
        self,
        target: SubmissionTarget,
        selected_files: list[SelectedSubmissionFile],
        submitted_files: list[SubmittedFile],
        overwrite: bool,
    ) -> bool:
        assign_id = self._resolve_assign_id(target.url, target.course_id)
        if not assign_id:
            return False

        draft_itemid = 0
        if not overwrite and submitted_files:
            draft_itemid = self._reupload_existing_files(submitted_files, draft_itemid)
            if draft_itemid is None:
                return False

        for file_item in selected_files:
            if not file_item.bytes:
                logger.warning("File '%s' không có dữ liệu, bỏ qua.", file_item.name)
                continue
            result_id = self.client.upload_draft_file(
                file_item.name, file_item.bytes, itemid=draft_itemid
            )
            if result_id is None:
                logger.error("Upload thất bại cho file '%s'", file_item.name)
                return False
            draft_itemid = result_id

        if draft_itemid == 0:
            logger.error("Không có file nào được upload thành công")
            return False

        return self._save_and_submit(assign_id, draft_itemid)

    def remove_files(
        self,
        target: SubmissionTarget,
        files_to_keep: list[SubmittedFile],
    ) -> bool:
        assign_id = self._resolve_assign_id(target.url, target.course_id)
        if not assign_id:
            raise ValueError("Không tìm thấy ID bài nộp (Assignment ID) tương ứng.")

        if not files_to_keep:
            raise ValueError(
                "Bài tập này không hỗ trợ xóa thông qua app.\n"
                "Vui lòng click 'Mở trình duyệt' và chọn 'Remove submission' để xóa bài làm."
            )

        draft_itemid = self._reupload_existing_files(files_to_keep, 0, strict=True)
        if draft_itemid is None:
            return False

        save_ok = self.moodle_service.save_assignment_submission(assign_id, draft_itemid)
        if not save_ok:
            raise ValueError("Moodle từ chối lưu bài nộp. Thử lại hoặc mở trình duyệt.")

        grading_ok = self.moodle_service.submit_for_grading(assign_id)
        if not grading_ok:
            logger.warning("Save OK but submit_for_grading failed for assign_id=%d", assign_id)
        return True

    def update_file_metadata(
        self,
        target: SubmissionTarget,
        submitted_files: list[SubmittedFile],
        target_idx: int,
        meta: FileMetadataUpdate,
    ) -> bool:
        assign_id = self._resolve_assign_id(target.url, target.course_id)
        if not assign_id:
            return False

        draft_itemid = 0
        for index, submitted_file in enumerate(submitted_files):
            if not submitted_file.url:
                return False

            file_bytes = self.client.download_file(submitted_file.url)
            if not file_bytes:
                return False

            if index == target_idx:
                result_id = self.client.upload_draft_file(
                    meta.new_name,
                    file_bytes,
                    itemid=draft_itemid,
                    author=meta.author,
                    license_key=meta.license,
                )
            else:
                result_id = self.client.upload_draft_file(
                    submitted_file.name,
                    file_bytes,
                    itemid=draft_itemid,
                )

            if result_id is None:
                return False
            draft_itemid = result_id

        return self._save_and_submit(assign_id, draft_itemid)

    def _reupload_existing_files(
        self,
        submitted_files: list[SubmittedFile],
        draft_itemid: int,
        strict: bool = False,
    ) -> Optional[int]:
        for submitted_file in submitted_files:
            if not submitted_file.url:
                if strict:
                    raise ValueError(f"File '{submitted_file.name}' không có URL hợp lệ.")
                continue

            existing_bytes = self.client.download_file(submitted_file.url)
            if not existing_bytes:
                message = f"Không tải được file '{submitted_file.name}' từ máy chủ."
                logger.warning(message)
                if strict:
                    raise ValueError(message)
                continue

            result_id = self.client.upload_draft_file(
                submitted_file.name, existing_bytes, itemid=draft_itemid
            )
            if result_id is None:
                message = f"Không upload được file '{submitted_file.name}' lên vùng nháp."
                logger.error(message)
                if strict:
                    raise ValueError(message)
                return None
            draft_itemid = result_id
        return draft_itemid

    def _save_and_submit(self, assign_id: int, draft_itemid: int) -> bool:
        save_ok = self.moodle_service.save_assignment_submission(assign_id, draft_itemid)
        if save_ok:
            grading_ok = self.moodle_service.submit_for_grading(assign_id)
            if not grading_ok:
                logger.warning("Save OK but submit_for_grading failed for assign_id=%d", assign_id)
        return save_ok

    @staticmethod
    def _submitted_file_from_mapping(file_item: dict[str, Any]) -> SubmittedFile:
        return SubmittedFile(
            name=file_item.get("name", "file"),
            url=file_item.get("url", ""),
            filepath=file_item.get("filepath", "/"),
        )

    @staticmethod
    def _map_submission_status(status: Optional[dict]) -> Optional[str]:
        if not status:
            return None
        try:
            last_attempt = status.get("lastattempt", {})
            submission = last_attempt.get("submission", {})
            raw_status = submission.get("status", "")
            status_map = {
                "submitted": "Đã nộp",
                "new": "Chưa nộp",
                "draft": "Bản nháp",
                "reopened": "Được mở lại",
            }
            return status_map.get(raw_status, "Chưa nộp")
        except Exception:
            return None
