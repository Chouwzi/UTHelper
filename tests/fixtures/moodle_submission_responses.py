"""Representative Moodle 4.3 assignment and submission-status responses."""


def assignment_fixture():
    return {
        "id": 77,
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
