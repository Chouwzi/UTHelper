import os
import sys
import threading
import time
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))


class _LatencyStatusService:
    def __init__(self, latency_seconds=0.02):
        self.latency_seconds = latency_seconds
        self.calls = []
        self._lock = threading.Lock()

    def _record(self, endpoint, activity_id):
        time.sleep(self.latency_seconds)
        with self._lock:
            self.calls.append((endpoint, activity_id))

    def get_submission_status(self, assignment_id):
        self._record("assignment_status", assignment_id)
        return {"lastattempt": {"submission": {"status": "submitted"}}}

    def get_quiz_attempts(self, quiz_id):
        self._record("quiz_attempts", quiz_id)
        return [{"state": "finished"}]


def test_status_prefetch_request_budget_and_parallel_latency():
    with patch("core.data_orchestrator.MoodleClient"), \
         patch("core.data_orchestrator.GradeMonitor"):
        from core.data_orchestrator import DataOrchestrator
        orchestrator = DataOrchestrator()

    service = _LatencyStatusService()
    orchestrator.moodle_service = service
    assignments = [
        {
            "url": f"https://courses.ut.edu.vn/mod/assign/view.php?id={index}",
            "type": "assignment",
            "assignment_id": 10_000 + index,
        }
        for index in range(17)
    ]
    quizzes = [
        {
            "url": f"https://courses.ut.edu.vn/mod/quiz/view.php?id={index}",
            "type": "quiz",
            "quiz_id": 20_000 + index,
        }
        for index in range(20)
    ]

    started = time.perf_counter()
    result = orchestrator.prefetch_activity_statuses(
        assignments + quizzes,
        workers=4,
    )
    elapsed = time.perf_counter() - started

    # The old full-detail path made two calls per item (74 calls here). Direct
    # IDs must need exactly one status call per item and execute concurrently.
    assert len(service.calls) == 37
    assert len(result) == 37
    assert elapsed < (37 * service.latency_seconds) / 2
