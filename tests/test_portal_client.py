from __future__ import annotations

import gzip
import json
from datetime import date
from email.message import Message
from urllib.error import HTTPError

from core.portal_client import PortalAuthenticationError, PortalClient


class _Response:
    def __init__(self, payload: dict, status: int = 200, *, compressed: bool = False):
        raw = json.dumps(payload).encode("utf-8")
        self._body = gzip.compress(raw) if compressed else raw
        self.status = status
        self.headers = Message()
        if compressed:
            self.headers["Content-Encoding"] = "gzip"

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_portal_client_logs_in_then_fetches_daily_schedule_with_bearer_only():
    requests = []

    def opener(request, timeout):
        requests.append((request, timeout))
        if request.full_url.endswith("/user/login"):
            return _Response({"success": True, "token": "jwt-token"}, compressed=True)
        return _Response(
            {
                "success": True,
                "body": [
                    {
                        "id": 1,
                        "tenMonHoc": "Cơ sở dữ liệu",
                        "tuGio": "07:00",
                        "denGio": "09:30",
                    }
                ],
            }
        )

    client = PortalClient(
        "https://portal-publicapp.ut.edu.vn/api/v1",
        opener=opener,
        timeout=9,
    )

    sessions = client.fetch_day(date(2026, 8, 11), "student", "secret")

    assert len(sessions) == 1
    login_request, login_timeout = requests[0]
    assert login_request.method == "POST"
    assert login_timeout == 9
    assert json.loads(login_request.data) == {
        "username": "student",
        "password": "secret",
    }
    schedule_request, schedule_timeout = requests[1]
    assert schedule_timeout == 9
    assert schedule_request.full_url.endswith("/lichhoc/ngay?date=2026-08-11")
    assert schedule_request.get_header("Authorization") == "Bearer jwt-token"
    assert schedule_request.get_header("Cookie") is None
    assert schedule_request.get_header("X-browser-token") is None


def test_portal_client_reauthenticates_once_after_unauthorized_schedule():
    paths = []
    login_count = 0

    def opener(request, timeout):
        del timeout
        nonlocal login_count
        paths.append(request.full_url)
        if request.full_url.endswith("/user/login"):
            login_count += 1
            return _Response({"success": True, "token": f"token-{login_count}"})
        if login_count == 1:
            raise HTTPError(request.full_url, 401, "unauthorized", {}, None)
        return _Response({"success": True, "body": []})

    client = PortalClient("https://portal-publicapp.ut.edu.vn/api/v1", opener=opener)

    assert client.fetch_day(date(2026, 8, 11), "student", "secret") == ()
    assert login_count == 2
    assert sum(path.endswith("/user/login") for path in paths) == 2


def test_portal_client_rejects_login_without_token():
    client = PortalClient(
        "https://portal-publicapp.ut.edu.vn/api/v1",
        opener=lambda *_args, **_kwargs: _Response(
            {"success": False, "message": "invalid"}
        ),
    )

    try:
        client.fetch_day(date(2026, 8, 11), "student", "secret")
    except PortalAuthenticationError:
        pass
    else:
        raise AssertionError("missing Portal token was accepted")


def test_portal_client_fetches_current_semester_courses_with_fallback_and_401_retry():
    paths: list[str] = []
    login_count = 0

    def opener(request, timeout):
        del timeout
        nonlocal login_count
        paths.append(request.full_url)
        if request.full_url.endswith("/user/login"):
            login_count += 1
            return _Response({"success": True, "token": f"tok-{login_count}"})
        if request.full_url.endswith("/hoctap/hocky"):
            if login_count == 1:
                raise HTTPError(request.full_url, 401, "unauthorized", {}, None)
            return _Response(
                {
                    "success": True,
                    "body": [
                        {"id": 77, "tenDot": "Học kỳ 1 năm học 2026-2027"},
                        {"id": 78, "tenDot": "Học kỳ 2 năm học 2026-2027"},
                    ],
                }
            )
        if request.full_url.endswith("/hoctap/montheoky/78"):
            # Newest dot has no registered courses yet -> must fall back to id=77
            return _Response({"success": True, "body": []})
        if request.full_url.endswith("/hoctap/montheoky/77"):
            return _Response(
                {
                    "success": True,
                    "body": [
                        {
                            "id": 180519,
                            "maHocPhan": "0120121137",
                            "tenMonHoc": "Quản trị doanh nghiệp CNTT",
                            "soTinChi": 3,
                        },
                        {
                            "id": 180520,
                            "maHocPhan": "0120005107",
                            "tenMonHoc": "Chủ nghĩa xã hội khoa học",
                            "soTinChi": 2,
                        },
                    ],
                }
            )
        raise AssertionError(f"Unexpected URL: {request.full_url}")

    client = PortalClient("https://portal-publicapp.ut.edu.vn/api/v1", opener=opener)
    ten_dot, courses = client.fetch_current_semester_courses("student", "secret")

    assert login_count == 2
    assert ten_dot == "Học kỳ 1 năm học 2026-2027"
    assert courses == [
        {
            "maHocPhan": "0120121137",
            "tenMonHoc": "Quản trị doanh nghiệp CNTT",
            "soTinChi": 3,
        },
        {
            "maHocPhan": "0120005107",
            "tenMonHoc": "Chủ nghĩa xã hội khoa học",
            "soTinChi": 2,
        },
    ]
    assert any(p.endswith("/hoctap/montheoky/78") for p in paths)
    assert any(p.endswith("/hoctap/montheoky/77") for p in paths)

