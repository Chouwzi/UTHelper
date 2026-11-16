import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))


def test_status_hydration_candidates_are_selective_and_courses_only():
    from gui.controllers.refresh_coordinator import status_hydration_candidates

    unknown_assignment = {
        "url": "https://courses.ut.edu.vn/mod/assign/view.php?id=1",
        "type": "assignment",
        "site_identity": "courses",
        "submission_status": "unknown",
    }
    stale_quiz = {
        "url": "https://courses.ut.edu.vn/mod/quiz/view.php?id=2",
        "type": "quiz",
        "site_identity": "courses",
        "submission_status": "not_submitted",
    }
    stable_assignment = {
        "url": "https://courses.ut.edu.vn/mod/assign/view.php?id=3",
        "type": "assignment",
        "site_identity": "courses",
        "submission_status": "not_submitted",
    }
    submitted_quiz = {
        "url": "https://courses.ut.edu.vn/mod/quiz/view.php?id=4",
        "type": "quiz",
        "site_identity": "courses",
        "submission_status": "submitted",
    }
    thnn_unknown = {
        "url": "https://thnn.ut.edu.vn/mod/quiz/view.php?id=5",
        "type": "quiz",
        "site_identity": "thnn",
        "submission_status": "unknown",
    }

    assert status_hydration_candidates(
        [unknown_assignment, stale_quiz, stable_assignment, submitted_quiz, thnn_unknown]
    ) == [unknown_assignment, stale_quiz]
