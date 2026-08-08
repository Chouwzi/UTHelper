"""Representative Moodle 4.3 assignment and submission-status responses."""

from urllib.parse import quote

from core.client import DraftFileRecord
from core.submission_models import normalize_filepath


class FakeMoodle43:
    """Small stateful Moodle 4.3 transport with observed endpoint contracts."""

    assignment_id = 77
    course_id = 456
    cmid = 123

    def __init__(self, *, drafts: bool, statement: bool):
        self.moodle_site_origin = "https://courses.ut.edu.vn"
        self.remote_files: dict[tuple[str, str], bytes] = {}
        self.drafts: dict[int, dict[tuple[str, str], bytes]] = {}
        self.submission_status = "new"
        self.submission_drafts = drafts
        self.statement_required = statement
        self.online_text = "<p>Preserve this text</p>"
        self.online_text_format = 1
        self.submission_modified_time = 1_700_000_000

        self.save_calls = 0
        self.finalize_attempts: list[bool] = []
        self.cleanup_calls: list[tuple[int, tuple[tuple[str, str], ...]]] = []
        self.duplicate_collisions: list[tuple[int, tuple[str, str]]] = []
        self.allocated_itemids: list[int] = []
        self.remote_sets_before_save: list[dict[tuple[str, str], bytes]] = []
        self.preseed_duplicate_upload_number = 0

        self._next_itemid = 900
        self._upload_count = 0
        self._download_identities: dict[str, tuple[str, str]] = {}

    def call_ws_api(self, function: str, **params):
        """Dispatch the Moodle functions used by ``SubmissionWorkflow``."""
        if function == "mod_assign_get_assignments":
            assert params == {"courseids[0]": self.course_id}
            return {"courses": [self._course_response()]}
        if function == "mod_assign_get_submission_status":
            assert params == {"assignid": self.assignment_id}
            return self._status_response()
        if function == "core_files_get_unused_draft_itemid":
            assert params == {}
            itemid = self._next_itemid
            self._next_itemid += 1
            self.allocated_itemids.append(itemid)
            self.drafts[itemid] = {}
            return {"itemid": itemid}
        if function == "mod_assign_save_submission":
            return self._save_submission(params)
        if function == "mod_assign_submit_for_grading":
            return self._submit_for_grading(params)
        if function == "core_files_delete_draft_files":
            return self._delete_draft_files(params)
        raise AssertionError(f"Unexpected Moodle function: {function}")

    def upload(
        self,
        itemid: int,
        filepath: str,
        filename: str,
        content: bytes,
    ) -> list[dict] | dict:
        """Model the multipart upload endpoint, including item-local duplicates."""
        assert itemid in self.drafts
        self._upload_count += 1
        key = (normalize_filepath(filepath), filename)
        item_files = self.drafts[itemid]
        hook_seeded = self._upload_count == self.preseed_duplicate_upload_number
        if hook_seeded:
            item_files[key] = b"synthetic same-identity collision"
        if key in item_files:
            self.duplicate_collisions.append((itemid, key))
            if hook_seeded:
                item_files.pop(key)
            return {"errorcode": "filenameexist", "error": "already exists"}
        item_files[key] = content
        return [{"itemid": itemid, "filepath": key[0], "filename": key[1]}]

    def upload_draft_file_record(
        self,
        filename: str,
        content: bytes,
        itemid: int,
        filepath: str,
    ) -> DraftFileRecord | None:
        response = self.upload(itemid, filepath, filename, content)
        if not isinstance(response, list) or not response:
            return None
        uploaded = response[0]
        return DraftFileRecord(
            itemid=int(uploaded["itemid"]),
            filepath=normalize_filepath(uploaded["filepath"]),
            filename=str(uploaded["filename"]),
        )

    def download_file(self, url: str) -> bytes | None:
        identity = self._download_identities.get(url)
        return self.remote_files.get(identity) if identity is not None else None

    def _course_response(self) -> dict:
        return {
            "id": self.course_id,
            "assignments": [
                {
                    "id": self.assignment_id,
                    "cmid": self.cmid,
                    "submissiondrafts": int(self.submission_drafts),
                    "teamsubmission": 0,
                    "duedate": 0,
                    "cutoffdate": 0,
                    "allowsubmissionsfromdate": 0,
                    "configs": [
                        {
                            "subtype": "assign",
                            "plugin": "assign",
                            "name": "submissiondrafts",
                            "value": int(self.submission_drafts),
                        },
                        {
                            "subtype": "assign",
                            "plugin": "assign",
                            "name": "requiresubmissionstatement",
                            "value": int(self.statement_required),
                        },
                        {
                            "subtype": "assignsubmission",
                            "plugin": "file",
                            "name": "enabled",
                            "value": 1,
                        },
                        {
                            "subtype": "assignsubmission",
                            "plugin": "file",
                            "name": "maxfilesubmission",
                            "value": 10,
                        },
                        {
                            "subtype": "assignsubmission",
                            "plugin": "file",
                            "name": "maxsubmissionsizebytes",
                            "value": 1_048_576,
                        },
                        {
                            "subtype": "assignsubmission",
                            "plugin": "file",
                            "name": "acceptedfiletypes",
                            "value": ".pdf",
                        },
                    ],
                }
            ],
        }

    def _status_response(self) -> dict:
        files = []
        self._download_identities.clear()
        for (filepath, filename), content in self.remote_files.items():
            url = (
                "moodle-fake://submission/"
                f"{quote(filepath, safe='')}/{quote(filename, safe='')}"
            )
            self._download_identities[url] = (filepath, filename)
            files.append(
                {
                    "filename": filename,
                    "filepath": filepath,
                    "filesize": len(content),
                    "mimetype": "application/pdf",
                    "timemodified": self.submission_modified_time,
                    "fileurl": url,
                }
            )
        return {
            "lastattempt": {
                "submission": {
                    "id": 333,
                    "status": self.submission_status,
                    "timemodified": self.submission_modified_time,
                    "plugins": [
                        {"type": "file", "fileareas": [{"files": files}]},
                        {
                            "type": "onlinetext",
                            "editorfields": [
                                {
                                    "name": "onlinetext",
                                    "text": self.online_text,
                                    "format": self.online_text_format,
                                }
                            ],
                        },
                    ],
                },
                "attemptnumber": 0,
                "canedit": True,
                "cansubmit": True,
                "locked": False,
                "graded": False,
                "submissionsenabled": True,
            }
        }

    def _save_submission(self, params: dict) -> list:
        assert params["assignmentid"] == self.assignment_id
        file_itemid = params["plugindata[files_filemanager]"]
        text_itemid = params["plugindata[onlinetext_editor][itemid]"]
        assert file_itemid in self.drafts
        assert text_itemid in self.drafts
        assert file_itemid != text_itemid
        assert params["plugindata[onlinetext_editor][text]"] == self.online_text
        assert (
            params["plugindata[onlinetext_editor][format]"]
            == self.online_text_format
        )

        self.save_calls += 1
        self.remote_sets_before_save.append(dict(self.remote_files))
        self.remote_files = dict(self.drafts[file_itemid])
        self.submission_status = "draft" if self.submission_drafts else "submitted"
        self.submission_modified_time += 1
        return []

    def _submit_for_grading(self, params: dict) -> list | dict:
        assert params["assignmentid"] == self.assignment_id
        accepted = bool(params["acceptsubmissionstatement"])
        self.finalize_attempts.append(accepted)
        if self.statement_required and not accepted:
            return {
                "warnings": [
                    {
                        "warningcode": "submissionstatementnotaccepted",
                        "message": "Submission statement was not accepted",
                    }
                ]
            }
        self.submission_status = "submitted"
        self.submission_modified_time += 1
        return []

    def _delete_draft_files(self, params: dict) -> dict:
        itemid = params["draftitemid"]
        identities = []
        index = 0
        while f"files[{index}][filename]" in params:
            identity = (
                normalize_filepath(params[f"files[{index}][filepath]"]),
                params[f"files[{index}][filename]"],
            )
            identities.append(identity)
            self.drafts[itemid].pop(identity, None)
            index += 1
        self.cleanup_calls.append((itemid, tuple(identities)))
        return {"parentpaths": []}


def assignment_fixture():
    return {
        "id": 77,
        "teamsubmission": 0,
        "duedate": 0,
        "cutoffdate": 0,
        "allowsubmissionsfromdate": 0,
        "configs": [
            {
                "subtype": "assign",
                "plugin": "assign",
                "name": "submissiondrafts",
                "value": "1",
            },
            {
                "subtype": "assign",
                "plugin": "assign",
                "name": "requiresubmissionstatement",
                "value": "1",
            },
            {
                "subtype": "assignsubmission",
                "plugin": "file",
                "name": "maxfilesubmission",
                "value": "2",
            },
            {
                "subtype": "assignsubmission",
                "plugin": "file",
                "name": "maxsubmissionsizebytes",
                "value": "1048576",
            },
            {
                "subtype": "assignsubmission",
                "plugin": "file",
                "name": "acceptedfiletypes",
                "value": ".pdf",
            },
        ],
    }


def captured_real_submission_shape_fixture():
    """Captured Moodle 4.3 shape with no assignment plugin configs exposed."""
    return (
        {
            "id": 77,
            "submissiondrafts": 1,
            "requiresubmissionstatement": 1,
            "teamsubmission": 0,
            "duedate": 0,
            "cutoffdate": 0,
            "allowsubmissionsfromdate": 0,
            "configs": [],
        },
        {
            "lastattempt": {
                "submission": {
                    "id": 333,
                    "status": "draft",
                    "timemodified": 1_700_000_000,
                    "plugins": [{"type": "file", "fileareas": []}],
                },
                "attemptnumber": 0,
                "canedit": True,
                "cansubmit": True,
                "locked": False,
                "graded": False,
                "submissionsenabled": True,
            }
        },
    )


def editable_status_fixture(url_query: str = ""):
    return {
        "lastattempt": {
            "submission": {
                "id": 333,
                "status": "draft",
                "timemodified": 1_700_000_000,
                "plugins": [
                    {
                        "type": "file",
                        "fileareas": [
                            {
                                "files": [
                                    {
                                        "filename": "old.pdf",
                                        "filepath": "",
                                        "filesize": 512,
                                        "mimetype": "application/pdf",
                                        "timemodified": 1_700_000_001,
                                        "fileurl": f"https://moodle.example/file{url_query}",
                                    }
                                ]
                            }
                        ],
                    },
                    {
                        "type": "onlinetext",
                        "editorfields": [
                            {"name": "onlinetext", "text": "<p>Keep <em>this</em></p>", "format": 1}
                        ],
                    },
                ],
            },
            "attemptnumber": 2,
            "canedit": True,
            "cansubmit": True,
            "locked": False,
            "graded": False,
        },
    }
