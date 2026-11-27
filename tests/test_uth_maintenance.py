"""TDD test suite for Task 4: UTH Maintenance Detection (01h00 - 04h00),
Offline Cache Preservation, and 1-Time Popup + Top Banner."""

from __future__ import annotations

import asyncio
import gzip
import io
import json
import threading
import zlib
from datetime import date, datetime, time, timezone
from email.message import Message
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock
from urllib.error import HTTPError

import flet as ft
import pytest

from config import settings
from core.academic_radar import AcademicGradeService
from core.client import MoodleClient
from core.data_orchestrator import DataOrchestrator
from core.maintenance import (
    MaintenanceNotice,
    UthMaintenanceError,
    detect_uth_maintenance,
    read_bounded_error_body,
)
from core.portal_client import (
    PortalAuthenticationError,
    PortalClient,
    PortalError,
    PortalMaintenanceError,
)
from core.today_schedule import (
    ClassSession,
    ScheduleAuthenticationError,
    ScheduleLoadStatus,
    TodayScheduleCache,
    TodayScheduleCoordinator,
    TodayScheduleSnapshot,
)
from gui.app_controller import AppController
from gui.components.courses.embedded_webview_host import (
    _ACTIVE_OCCLUSION_REASONS,
    _ACTIVE_OCCLUSION_TOKENS,
    clear_global_webview_occlusion,
)

LIVE_UTH_403_MAINTENANCE_HTML = """<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="UTF-8">
  <title>Hệ thống tạm ngưng</title>
  <style>body { font-family: system-ui, sans-serif; }</style>
</head>
<body>
  <div class="card">
    <h1>Tạm ngưng hoạt động 🛠️</h1>
    <p>Hệ thống đang trong thời gian <b>bảo trì (01h00 – 04h00)</b>.</p>
    <div class="info">Thời gian tạm ngưng: <b>01h00 – 04h00</b> (giờ Việt Nam)<br>Hệ thống mở lại lúc: <b id="openAt">04h00</b></div>
  </div>
  <script>
    var OPEN_HOUR = 4;
    var TZ_OFFSET = 7;
    (function () {
      var el = document.getElementById("openAt");
      if (el) { el.textContent = "04h00"; }
    })();
  </script>
</body>
</html>
"""

CLOUDFLARE_403_CHALLENGE_HTML = """<!DOCTYPE html>
<html lang="en-US">
<head>
  <meta charset="UTF-8">
  <title>Just a moment...</title>
</head>
<body>
  <div class="cf-browser-verification">Checking if the site connection is secure (maintenance check)...</div>
  <script>window._cf_chl_opt = { cType: 'managed' };</script>
</body>
</html>
"""


def _make_http_error(
    url: str,
    code: int,
    body_text: str | bytes,
    *,
    gzip_compress: bool = False,
    content_type: str = "text/html; charset=UTF-8",
) -> HTTPError:
    raw = body_text.encode("utf-8") if isinstance(body_text, str) else body_text
    payload = gzip.compress(raw) if gzip_compress else raw
    headers = Message()
    headers["Content-Type"] = content_type
    if gzip_compress:
        headers["Content-Encoding"] = "gzip"
    return HTTPError(url, code, "Forbidden", headers, io.BytesIO(payload))


def _has_occlusion_reason(reason: str) -> bool:
    if _ACTIVE_OCCLUSION_REASONS.get(reason, 0) > 0:
        return True
    return any(r == reason for r, _tok in _ACTIVE_OCCLUSION_TOKENS)


def test_detect_uth_maintenance_parses_live_403_html_and_computes_utc7_reopen_date_across_midnight():
    # 1. Plain HTML at 02:05 VN on 28/11/2026 (2026-11-27T19:05:00Z) -> same day 28/11/2026
    now_0205_vn = datetime(2026, 11, 27, 19, 5, 0, tzinfo=timezone.utc)
    notice = detect_uth_maintenance(
        status=403,
        body=LIVE_UTH_403_MAINTENANCE_HTML,
        content_type="text/html; charset=UTF-8",
        source="courses",
        now_utc=now_0205_vn,
    )
    assert notice is not None
    assert notice.source == "courses"
    assert notice.window_text == "01h00 - 04h00 (giờ Việt Nam)"
    assert "–" not in notice.window_text
    assert "—" not in notice.window_text
    assert notice.reopen_text == "04h00 ngày 28/11/2026"
    assert notice.dedupe_key == "2026-11-28|01h00-04h00|04h00"

    # 2. Gzip-compressed HTTPError body at 23:55 VN on 27/11/2026 (2026-11-27T16:55:00Z) -> next day 28/11/2026
    err_gzip = _make_http_error(
        "https://portal.ut.edu.vn/api/v1/lichhoc/lichTuan",
        403,
        LIVE_UTH_403_MAINTENANCE_HTML,
        gzip_compress=True,
    )
    decoded_bytes, ct = read_bounded_error_body(err_gzip)
    err_gzip.close()
    now_2355_vn = datetime(2026, 11, 27, 16, 55, 0, tzinfo=timezone.utc)
    notice_midnight = detect_uth_maintenance(
        status=403,
        body=decoded_bytes,
        content_type=ct,
        source="portal",
        now_utc=now_2355_vn,
    )
    assert notice_midnight is not None
    assert notice_midnight.source == "portal"
    assert notice_midnight.window_text == "01h00 - 04h00 (giờ Việt Nam)"
    assert notice_midnight.reopen_text == "04h00 ngày 28/11/2026"
    assert notice_midnight.dedupe_key == "2026-11-28|01h00-04h00|04h00"


def test_read_bounded_error_body_bounds_raw_and_gzip_bomb_safely():
    # Oversized gzip payload (2 MB of zeros compressed to a few KB) must stop at <= 256 KB
    comp_obj = zlib.compressobj(level=9, wbits=16 + zlib.MAX_WBITS)
    bomb_bytes = comp_obj.compress(b"A" * (2 * 1024 * 1024)) + comp_obj.flush()
    assert len(bomb_bytes) < 65536
    headers = Message()
    headers["Content-Type"] = "text/html; charset=utf-8"
    headers["Content-Encoding"] = "gzip"
    bomb_err = HTTPError(
        "https://courses.ut.edu.vn/", 403, "Forbidden", headers, io.BytesIO(bomb_bytes)
    )
    try:
        body, content_type = read_bounded_error_body(bomb_err)
    finally:
        bomb_err.close()

    assert len(body) <= 262144
    assert "text/html" in content_type.lower()


def test_detect_uth_maintenance_detects_moodle_sitemaintenance_and_rejects_cloudflare_challenge():
    # 1. Moodle native WS JSON sitemaintenance
    moodle_json = json.dumps(
        {
            "exception": "moodle_exception",
            "errorcode": "sitemaintenance",
            "message": "The site is undergoing maintenance and is currently not available",
        }
    ).encode("utf-8")
    notice_ws = detect_uth_maintenance(
        status=200,
        body=moodle_json,
        content_type="application/json",
        source="courses",
        now_utc=datetime(2026, 11, 27, 19, 10, 0, tzinfo=timezone.utc),
    )
    assert notice_ws is not None
    assert notice_ws.source == "courses"
    assert notice_ws.dedupe_key

    # 2. Moodle native HTTP 503 HTML sitemaintenance
    moodle_503_html = (
        "<html><head><title>Site maintenance</title></head>"
        "<body id='page-site-maintenance'>The site is undergoing maintenance</body></html>"
    )
    notice_503 = detect_uth_maintenance(
        status=503,
        body=moodle_503_html,
        content_type="text/html",
        source="thnn",
        now_utc=datetime(2026, 11, 27, 19, 10, 0, tzinfo=timezone.utc),
    )
    assert notice_503 is not None
    assert notice_503.source == "thnn"

    # 3. Cloudflare 403 challenge MUST NEVER be classified as maintenance
    assert (
        detect_uth_maintenance(
            status=403,
            body=CLOUDFLARE_403_CHALLENGE_HTML,
            content_type="text/html; charset=UTF-8",
            source="courses",
        )
        is None
    )

    # 4. Normal Moodle 200 HTML course page mentioning "bảo trì" in lesson text MUST return None
    normal_course_html = (
        "<html><head><title>Khóa học: Nhập môn Công nghệ phần mềm</title></head>"
        "<body><h1>Chương 5: Bảo trì hệ thống phần mềm</h1>"
        "<p>Hệ thống đang hoạt động bình thường.</p></body></html>"
    )
    assert (
        detect_uth_maintenance(
            status=200,
            body=normal_course_html,
            content_type="text/html; charset=UTF-8",
            source="courses",
        )
        is None
    )


def test_portal_client_403_maintenance_raises_portal_maintenance_error_and_preserves_token():
    mode = {"state": "maintenance"}

    class _JsonOkResponse:
        def __init__(self, url: str, payload: dict) -> None:
            self.status = 200
            self._url = url
            self._raw = json.dumps(payload).encode("utf-8")
            self.headers = Message()
            self.headers["Content-Type"] = "application/json"

        def geturl(self) -> str:
            return self._url

        def read(self, _amt: int = -1) -> bytes:
            return self._raw

        def __enter__(self) -> "_JsonOkResponse":
            return self

        def __exit__(self, *_args) -> bool:
            return False

    def opener(request, timeout):
        del timeout
        if mode["state"] == "maintenance":
            raise _make_http_error(
                request.full_url,
                403,
                LIVE_UTH_403_MAINTENANCE_HTML,
                gzip_compress=True,
            )
        if mode["state"] == "auth_403":
            raise _make_http_error(
                request.full_url,
                403,
                json.dumps({"success": False, "message": "Invalid credentials"}),
                content_type="application/json",
            )
        return _JsonOkResponse(request.full_url, {"success": True, "body": []})

    client = PortalClient("https://portal.ut.edu.vn/api/v1", opener=opener)
    client._store_token("preserved-jwt-token", "2251120001")

    with pytest.raises(PortalMaintenanceError) as exc_info:
        client.fetch_week(date(2026, 11, 28), "2251120001", "secret")

    err = exc_info.value
    assert isinstance(err, PortalError)
    assert isinstance(err, UthMaintenanceError)
    assert not isinstance(err, PortalAuthenticationError)
    assert not isinstance(err, ScheduleAuthenticationError)
    assert err.notice.source == "portal"
    assert err.notice.window_text == "01h00 - 04h00 (giờ Việt Nam)"
    assert client._cached_token("2251120001") == "preserved-jwt-token"
    assert client.last_maintenance_notice == err.notice

    # authenticate() during maintenance also raises PortalMaintenanceError and preserves token
    with pytest.raises(PortalMaintenanceError):
        client.authenticate("2251120001", "secret")
    assert client._cached_token("2251120001") == "preserved-jwt-token"

    # Recovery clears last_maintenance_notice
    mode["state"] = "ok"
    assert client.fetch_week(date(2026, 11, 28), "2251120001", "secret") == {}
    assert client.last_maintenance_notice is None
    assert client._cached_token("2251120001") == "preserved-jwt-token"

    # Genuine non-maintenance 403 on login still raises PortalAuthenticationError and clears token
    mode["state"] = "auth_403"
    with pytest.raises(PortalAuthenticationError):
        client.authenticate("2251120001", "wrong-secret")
    assert client._cached_token("2251120001") == ""


def test_moodle_client_and_orchestrator_surface_uth_maintenance_without_clearing_ws_token(
    monkeypatch,
):
    monkeypatch.setattr(settings, "MOODLE_BASE_URL", "https://courses.ut.edu.vn")
    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "secret-pass")
    monkeypatch.setattr(settings, "UTH_CREDENTIALS_ORIGIN", "https://courses.ut.edu.vn")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN", "keep-this-ws-token")
    monkeypatch.setattr(
        settings, "MOODLE_WS_TOKEN_ORIGIN", "https://courses.ut.edu.vn"
    )
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN_USERNAME", "2251120001")

    mode = {"state": "maintenance"}

    def fake_urlopen(req, timeout=15):
        del timeout
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if mode["state"] == "maintenance":
            raise _make_http_error(
                url, 403, LIVE_UTH_403_MAINTENANCE_HTML, gzip_compress=True
            )
        headers = Message()
        headers["Content-Type"] = "application/json"
        raw = json.dumps({"userid": 99, "fullname": "SV UTH"}).encode("utf-8")
        return SimpleNamespace(
            status=200,
            headers=headers,
            info=lambda: headers,
            geturl=lambda: url,
            read=lambda _amt=-1: raw,
            close=lambda: None,
        )

    monkeypatch.setattr("core.client._urlopen", fake_urlopen)

    orchestrator = DataOrchestrator()
    client: MoodleClient = orchestrator.client

    # 1. WS API call during maintenance populates last_maintenance_notice without wiping WS token
    assert client.call_ws_api("core_webservice_get_site_info") is None
    assert client.last_maintenance_notice is not None
    assert client.last_maintenance_notice.source == "courses"
    assert client.last_login_error == "maintenance"
    assert settings.MOODLE_WS_TOKEN == "keep-this-ws-token"

    # 2. Forced login during maintenance ALSO preserves existing MOODLE_WS_TOKEN and sets "maintenance"
    assert client.login("2251120001", "secret-pass", force=True, persist=True) is False
    assert client.last_login_error == "maintenance"
    assert settings.MOODLE_WS_TOKEN == "keep-this-ws-token"

    # 3. DataOrchestrator sync raises UthMaintenanceError(notice)
    with pytest.raises(UthMaintenanceError) as exc_info:
        orchestrator.get_latest_activities()
    assert exc_info.value.notice.source == "courses"

    with pytest.raises(UthMaintenanceError):
        asyncio.run(orchestrator.fetch_activity_outcome_async())

    # 4. Subsequent normal 200 response clears last_maintenance_notice
    mode["state"] = "ok"
    info = client.call_ws_api("core_webservice_get_site_info")
    assert info == {"userid": 99, "fullname": "SV UTH"}
    assert client.last_maintenance_notice is None


def test_today_schedule_and_academic_grade_service_preserve_cached_data_on_maintenance(
    tmp_path,
):
    notice = MaintenanceNotice(
        source="portal",
        window_text="01h00 - 04h00 (giờ Việt Nam)",
        reopen_text="04h00 ngày 28/11/2026",
        dedupe_key="2026-11-28|01h00-04h00|04h00",
    )

    # 1. TodayScheduleCoordinator keeps cached sessions and does NOT emit AUTH_REQUIRED
    now = datetime(2026, 11, 28, 2, 10)
    sched_cache = TodayScheduleCache(cache_dir=tmp_path, namespace="portal|2251120001")
    cached_session = ClassSession(
        session_id="s1",
        subject="Lập trình mạng",
        course_code="NET01",
        start_at=datetime(2026, 11, 28, 7, 30),
        end_at=datetime(2026, 11, 28, 10, 0),
        room="F502",
        campus="CS1",
        period_start=1,
        period_end=3,
        cancelled=False,
        note="",
    )
    snapshot = TodayScheduleSnapshot(
        schedule_date=now.date(),
        sessions=(cached_session,),
        fetched_at=datetime(2026, 11, 28, 0, 15),
    )
    sched_cache.save(snapshot)

    states = []
    maint_callbacks: list[MaintenanceNotice] = []
    coordinator = TodayScheduleCoordinator(
        fetch_day=lambda *_: (_ for _ in ()).throw(PortalMaintenanceError(notice)),
        cache=sched_cache,
        credentials_provider=lambda: ("2251120001", "secret"),
        state_sink=states.append,
        now_provider=lambda: now,
        on_maintenance=maint_callbacks.append,
    )
    final_state = asyncio.run(coordinator.refresh("manual"))
    assert final_state.status is ScheduleLoadStatus.ERROR
    assert final_state.error_code == "maintenance"
    assert final_state.from_cache is True
    assert final_state.snapshot == snapshot
    assert maint_callbacks == [notice]

    # 2. AcademicGradeService preserves Portal token & returns cached transcript on maintenance
    mock_portal = MagicMock()
    mock_portal.fetch_full_transcript.side_effect = PortalMaintenanceError(notice)
    grade_service = AcademicGradeService(portal_client=mock_portal, cache_dir=tmp_path)
    cache_file, _ = grade_service._cache_paths("2251120001")
    cache_file.write_text(
        json.dumps(
            {
                "cached_date": "2026-11-27",
                "updated_at": "2026-11-27T20:00:00",
                "transcript": [
                    {
                        "id": 88,
                        "tenDot": "HK1 2026-2027",
                        "tongKetDot": {"diemTBHocLuc": 8.4},
                        "diems": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    semesters = grade_service.load_academic_data(
        "2251120001", "secret", force_refresh=True
    )
    assert len(semesters) == 1
    assert semesters[0].name == "HK1 2026-2027"
    assert grade_service.last_maintenance_notice == notice
    mock_portal.clear_token.assert_not_called()

    # Without disk cache, load_academic_data re-raises PortalMaintenanceError
    empty_service = AcademicGradeService(
        portal_client=mock_portal, cache_dir=tmp_path / "empty"
    )
    with pytest.raises(PortalMaintenanceError):
        empty_service.load_academic_data("2251120001", "secret", force_refresh=True)
    mock_portal.clear_token.assert_not_called()


def test_app_controller_shows_popup_once_per_dedupe_key_on_auto_sync_and_forces_on_manual_refresh_then_clears_banner_on_recovery(
    monkeypatch,
):
    clear_global_webview_occlusion()
    try:
        monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
        monkeypatch.setattr(settings, "UTH_PASSWORD", "secret-pass")

        shown_dialogs: list[ft.AlertDialog] = []
        dialogs_controls: list[object] = []

        def _show_dialog(dlg):
            dlg.open = True
            dialogs_controls.append(dlg)
            if isinstance(dlg, ft.AlertDialog):
                shown_dialogs.append(dlg)

        def _pop_dialog():
            dlg = next(
                (d for d in reversed(dialogs_controls) if getattr(d, "open", False)),
                None,
            )
            if dlg is None:
                return None
            dlg.open = False
            return dlg

        page = SimpleNamespace(
            width=1120,
            height=760,
            web=False,
            platform=ft.PagePlatform.WINDOWS,
            overlay=[],
            dialog=None,
            _dialogs=SimpleNamespace(controls=dialogs_controls),
            show_dialog=_show_dialog,
            pop_dialog=_pop_dialog,
            update=Mock(),
            run_task=lambda *_a, **_kw: None,
        )

        controller = AppController.__new__(AppController)
        controller.page = page
        controller._install_page_dialog_occlusion_bridge()
        controller._init_maintenance_banner()

        notice = MaintenanceNotice(
            source="courses",
            window_text="01h00 - 04h00 (giờ Việt Nam)",
            reopen_text="04h00 ngày 28/11/2026",
            dedupe_key="2026-11-28|01h00-04h00|04h00",
        )

        # 1. Initial auto-sync / startup maintenance -> shows banner + opens popup ONCE (with WebView2 occlusion)
        controller._handle_uth_maintenance(notice, user_initiated=False)
        assert controller._maintenance_banner.visible is True
        assert (
            controller._maintenance_text.value
            == "Hệ thống UTH đang bảo trì (01h00 - 04h00 (giờ Việt Nam) · Mở lại: 04h00 ngày 28/11/2026) · Đang xem dữ liệu đã lưu"
        )
        assert len(shown_dialogs) == 1
        assert _has_occlusion_reason("modal_dialog") is True

        # User clicks "Đã hiểu" (pop_dialog) -> closes modal and releases WebView2 modal occlusion
        controller.page.pop_dialog()
        assert _has_occlusion_reason("modal_dialog") is False

        # 2. Second background auto-sync in the SAME maintenance window -> banner stays, NO repeat popup
        controller._handle_uth_maintenance(notice, user_initiated=False)
        assert controller._maintenance_banner.visible is True
        assert len(shown_dialogs) == 1

        # 3. User clicks manual Refresh / live LMS action (positional True or user_initiated=True) -> forces popup immediately!
        controller._handle_uth_maintenance(notice, True)
        assert len(shown_dialogs) == 2
        assert _has_occlusion_reason("modal_dialog") is True
        controller.page.pop_dialog()
        assert _has_occlusion_reason("modal_dialog") is False

        # 4. Partial & full _rebuild_colors() both work cleanly without error
        controller._rebuild_colors(sync_nav=False, update_page=False)
        partial_ctrl = AppController.__new__(AppController)
        partial_ctrl.page = SimpleNamespace(bgcolor=None, update=Mock())
        partial_ctrl._rebuild_colors(sync_nav=False, update_page=False)

        # 5. Live sync recovery hides banner and clears active maintenance state
        controller._clear_uth_maintenance_banner()
        assert controller._maintenance_banner.visible is False
        assert controller._active_maintenance_notice is None
    finally:
        clear_global_webview_occlusion()


def test_app_controller_perform_data_sync_preserves_cache_and_recovers_after_maintenance(
    monkeypatch, tmp_path
):
    clear_global_webview_occlusion()
    try:
        monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
        monkeypatch.setattr(settings, "UTH_PASSWORD", "secret-pass")
        monkeypatch.setattr(settings, "MOODLE_WS_TOKEN", "valid-ws-token")
        monkeypatch.setattr(
            settings, "MOODLE_WS_TOKEN_ORIGIN", "https://courses.ut.edu.vn"
        )
        monkeypatch.setattr(settings, "MOODLE_WS_TOKEN_USERNAME", "2251120001")

        from core.data_cache import DataCache
        from core.sync_coordinator import FetchOutcome

        data_cache = DataCache(cache_dir=tmp_path, namespace="courses|2251120001")
        cached_item = {
            "id": "assign_1",
            "title": "Báo cáo đồ án",
            "course": "Công nghệ phần mềm",
            "course_name": "Công nghệ phần mềm",
            "deadline": "2026-12-05 23:59:00",
            "url": "https://courses.ut.edu.vn/mod/assign/view.php?id=101",
            "type": "assignment",
            "urgency": "safe",
            "submission_status": "not_submitted",
        }
        data_cache.save_server_snapshot([cached_item])

        shown_dialogs: list[ft.AlertDialog] = []
        dialogs_controls: list[object] = []

        def _show_dialog(dlg):
            dlg.open = True
            dialogs_controls.append(dlg)
            if isinstance(dlg, ft.AlertDialog):
                shown_dialogs.append(dlg)

        def _pop_dialog():
            dlg = next(
                (d for d in reversed(dialogs_controls) if getattr(d, "open", False)),
                None,
            )
            if dlg is not None:
                dlg.open = False
            return dlg

        page = SimpleNamespace(
            width=1120,
            height=760,
            web=False,
            platform=ft.PagePlatform.WINDOWS,
            overlay=[],
            dialog=None,
            _dialogs=SimpleNamespace(controls=dialogs_controls),
            show_dialog=_show_dialog,
            pop_dialog=_pop_dialog,
            update=Mock(),
            run_task=lambda *_a, **_kw: None,
        )

        notice = MaintenanceNotice(
            source="courses",
            window_text="01h00 - 04h00 (giờ Việt Nam)",
            reopen_text="04h00 ngày 28/11/2026",
            dedupe_key="2026-11-28|01h00-04h00|04h00",
        )
        sync_state = {"mode": "maintenance"}

        async def _fake_fetch_outcome():
            if sync_state["mode"] == "maintenance":
                raise UthMaintenanceError(notice)
            return FetchOutcome.complete([cached_item])

        ctrl = AppController.__new__(AppController)
        ctrl.page = page
        ctrl._data_lock = threading.Lock()
        ctrl._cards_lock = threading.Lock()
        ctrl._account_epoch = 1
        ctrl._sync_generation = 1
        ctrl._prefetch_epoch = 0
        ctrl._is_loading = False
        ctrl._skeleton_visible = True
        ctrl._pending_updates = {}
        ctrl.all_data = []
        ctrl.active_cards = []
        ctrl._data_cache = data_cache
        ctrl._android_background = None
        ctrl.notifier = None
        ctrl.refresh_btn = ft.IconButton(ft.Icons.REFRESH_ROUNDED)
        ctrl.status_text = ft.Text("")
        ctrl.cards_column = ft.ListView()
        ctrl.empty_state = ft.Container(visible=False)
        ctrl.error_state = ft.Container(visible=False)
        ctrl._error_title = ft.Text("Không thể tải dữ liệu")
        ctrl.error_text = ft.Text("")
        ctrl._diag_breadcrumb = lambda *_a, **_kw: None
        ctrl._safe_run_task = lambda *_a, **_kw: None
        ctrl._update_footer = lambda **_kw: None
        ctrl._init_maintenance_banner()
        ctrl._install_page_dialog_occlusion_bridge()

        ctrl.orchestrator = SimpleNamespace(
            is_logged_in=True,
            client=SimpleNamespace(last_maintenance_notice=notice),
            thnn_client=SimpleNamespace(last_maintenance_notice=None),
            moodle_service=SimpleNamespace(clear_all_caches=Mock()),
            clear_detail_cache=Mock(),
            fetch_activity_outcome_async=_fake_fetch_outcome,
            get_cached_details_snapshot=lambda: {},
        )

        # 1. Startup sync hits maintenance -> loads & preserves cached activity, shows banner + 1 popup
        asyncio.run(ctrl._perform_data_sync("startup"))
        assert len(ctrl.all_data) == 1
        assert ctrl.all_data[0]["title"] == "Báo cáo đồ án"
        assert ctrl.error_state.visible is False
        assert ctrl._maintenance_banner.visible is True
        assert len(shown_dialogs) == 1
        assert settings.MOODLE_WS_TOKEN == "valid-ws-token"
        page.pop_dialog()

        # 2. Background timer sync hits same maintenance -> NO duplicate popup, cache stays intact
        asyncio.run(ctrl._perform_data_sync("timer"))
        assert len(shown_dialogs) == 1
        assert len(ctrl.all_data) == 1
        assert ctrl._maintenance_banner.visible is True

        # 3. User clicks manual refresh -> forces popup immediately!
        asyncio.run(ctrl._perform_data_sync("manual"))
        assert len(shown_dialogs) == 2
        page.pop_dialog()

        # 4. Server reopens after 04h00 -> live sync succeeds and auto-hides banner!
        sync_state["mode"] = "ok"
        asyncio.run(ctrl._perform_data_sync("timer"))
        assert ctrl._maintenance_banner.visible is False
        assert ctrl._active_maintenance_notice is None
        assert ctrl.orchestrator.client.last_maintenance_notice is None
    finally:
        clear_global_webview_occlusion()


def test_app_controller_live_lms_actions_and_grades_surface_maintenance_cleanly(
    monkeypatch,
):
    clear_global_webview_occlusion()
    try:
        monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
        monkeypatch.setattr(settings, "UTH_PASSWORD", "secret-pass")
        monkeypatch.setattr(settings, "MOODLE_BASE_URL", "https://courses.ut.edu.vn")

        shown_dialogs: list[ft.AlertDialog] = []
        dialogs_controls: list[object] = []

        def _show_dialog(dlg):
            dlg.open = True
            dialogs_controls.append(dlg)
            if isinstance(dlg, ft.AlertDialog):
                shown_dialogs.append(dlg)

        def _pop_dialog():
            dlg = next(
                (d for d in reversed(dialogs_controls) if getattr(d, "open", False)),
                None,
            )
            if dlg is not None:
                dlg.open = False
            return dlg

        page = SimpleNamespace(
            width=1120,
            height=760,
            web=False,
            platform=ft.PagePlatform.WINDOWS,
            overlay=[],
            dialog=None,
            _dialogs=SimpleNamespace(controls=dialogs_controls),
            show_dialog=_show_dialog,
            pop_dialog=_pop_dialog,
            update=Mock(),
            run_task=lambda *_a, **_kw: None,
        )

        notice = MaintenanceNotice(
            source="courses",
            window_text="01h00 - 04h00 (giờ Việt Nam)",
            reopen_text="04h00 ngày 28/11/2026",
            dedupe_key="2026-11-28|01h00-04h00|04h00",
        )

        ctrl = AppController.__new__(AppController)
        ctrl.page = page
        ctrl._account_epoch = 1
        ctrl._grades_request_epoch = 0
        ctrl._settings_identity = ("2251120001", "secret-pass")
        ctrl._seen_maintenance_keys = {notice.dedupe_key}  # Already seen on startup!
        ctrl._init_maintenance_banner()
        ctrl._install_page_dialog_occlusion_bridge()
        ctrl._diag_screen = lambda *_a, **_kw: None
        ctrl._diag_breadcrumb = lambda *_a, **_kw: None
        ctrl._sync_rail_active = lambda *_a, **_kw: None
        ctrl._safe_run_task = lambda *_a, **_kw: None

        courses_client = SimpleNamespace(
            moodle_site_origin="https://courses.ut.edu.vn",
            call_ws_api=lambda *_a, **_kw: None,
            last_maintenance_notice=notice,
        )
        ctrl.orchestrator = SimpleNamespace(
            client=courses_client,
            thnn_client=SimpleNamespace(last_maintenance_notice=None),
        )

        # 1. User opens UTH WebView while maintenance is active -> immediately forces popup without mounting WebView
        ctrl._open_url_in_app(
            "https://courses.ut.edu.vn/mod/assign/view.php?id=55", "Bài tập"
        )
        assert len(shown_dialogs) == 1
        page.pop_dialog()

        # 2. User starts a course quiz while maintenance is active -> immediately forces popup
        fake_course = SimpleNamespace(
            source="courses",
            id=10,
            display_name="Mạng máy tính",
            fullname="Mạng máy tính",
        )
        fake_mod = SimpleNamespace(
            cmid=88,
            name="Quiz 1",
            web_url="https://courses.ut.edu.vn/mod/quiz/view.php?id=88",
            url="",
        )
        ctrl._start_course_quiz(fake_course, fake_mod)
        assert len(shown_dialogs) == 2
        page.pop_dialog()

        # 3. User triggers submission mutation while maintenance is active -> immediately forces popup
        from core.submission_models import FileMutationIntent, MutationOperation
        from core.use_cases.submission_workflow import SubmissionTarget

        wf = ctrl._submission_workflow_factory(courses_client)
        assert wf is not None
        wf.mutate_files(
            SubmissionTarget(
                url="https://courses.ut.edu.vn/mod/assign/view.php?id=55",
                course_id=10,
            ),
            FileMutationIntent(operation=MutationOperation.CLEAR),
        )
        assert len(shown_dialogs) == 3
        page.pop_dialog()

        # 4. _show_grades(force_refresh=True) with cached semesters + last_maintenance_notice preserves radar AND forces popup
        from core.academic_radar.models import SemesterGradeData

        cached_sem = SemesterGradeData(id=1, name="HK1 2026-2027", summary={}, courses=[])
        portal_notice = MaintenanceNotice(
            source="portal",
            window_text="01h00 - 04h00 (giờ Việt Nam)",
            reopen_text="04h00 ngày 28/11/2026",
            dedupe_key="2026-11-28|01h00-04h00|04h00",
        )
        ctrl._academic_grade_service = SimpleNamespace(
            load_academic_data=lambda **_kw: [cached_sem],
            get_radar_profiles=lambda _sems: [],
            load_credit_progress=lambda **_kw: None,
            last_maintenance_notice=portal_notice,
        )
        radar_shown = []
        ctrl.view_manager = SimpleNamespace(
            show_grades_loading=lambda: None,
            show_academic_radar_data=lambda sems, profs, prog=None: radar_shown.append(
                (sems, profs, prog)
            ),
            show_credit_progress=lambda _p: None,
            show_grades_error=Mock(),
        )
        asyncio.run(ctrl._show_grades(force_refresh=True))
        assert len(radar_shown) == 1
        assert radar_shown[0][0] == [cached_sem]
        ctrl.view_manager.show_grades_error.assert_not_called()
        assert len(shown_dialogs) == 4
        page.pop_dialog()
    finally:
        clear_global_webview_occlusion()
