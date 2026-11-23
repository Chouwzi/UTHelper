"""Tests for student profile & tuition parsing, offline cache and service."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from core.student_profile import (
    ProfileSnapshot,
    StudentProfileCache,
    StudentProfileService,
    parse_other_fee_rows,
    parse_semesters,
    parse_summary_profile,
    parse_tuition_rows,
    summarize_tuition,
)

PROFILE_PAYLOAD = {
    "maSinhVien": "SV001",
    "khoaHoc": "2024",
    "hoDem": "Nguyễn Văn",
    "ten": "A",
    "ngaySinh2": "01/01/2005",
    "noiSinhTinh": "TP.HCM",
    "soDienThoai": "0900000000",
    "heDaoTao": "Đại học - chính quy",
    "loaiHinhDT": "Tiên tiến",
    "nganh": "Công nghệ thông tin",
    "khoa": "Ban Công nghệ số",
    "chuyenNganh": "Công nghệ thông tin",
    "email": "student@example.com",
}

TUITION_ROWS = [
    {
        "tenDot": "Học kỳ 1 năm học 2025-2026",
        "maLopHocPhan": "0120005105",
        "noiDung": "Triết học Mác - Lênin",
        "soTinChi": 3,
        "mucPhiBanDau": "4500000",
        "mucNop": 4500000.0,
        "trangThaiDangKy": "Đã đăng ký",
        "ngayNop": "12/09/2025",
        "soTienNop": "4500000",
        "khauTru": None,
        "truNo": 0,
        "congNo": 0,
        "daDongHocPhi": True,
    },
    {
        "tenDot": "Học kỳ 1 năm học 2025-2026",
        "maLopHocPhan": "0120122010",
        "noiDung": "XD phần mềm hướng đối tượng",
        "soTinChi": 3,
        "mucPhiBanDau": 4500000,
        "mucNop": 4500000,
        "ngayNop": "",
        "soTienNop": 1500000,
        "khauTru": 0,
        "truNo": 0,
        "congNo": 3000000,
        "daDongHocPhi": False,
    },
]


def test_parse_summary_profile_builds_identity_card_fields():
    profile = parse_summary_profile(PROFILE_PAYLOAD)

    assert profile.student_id == "SV001"
    assert profile.full_name == "Nguyễn Văn A"
    assert profile.faculty == "Ban Công nghệ số"
    assert profile.major == "Công nghệ thông tin"
    assert profile.cohort == "2024"
    assert profile.email == "student@example.com"
    # No class field in the documented payload → empty, not a crash.
    assert profile.class_name == ""


def test_parse_summary_profile_tolerates_missing_and_bogus_payloads():
    profile = parse_summary_profile({"maSinhVien": "SV002", "hoDem": "  "})
    assert profile.student_id == "SV002"
    assert profile.full_name == ""

    assert parse_summary_profile(None).student_id == ""
    assert parse_summary_profile("nope").student_id == ""
    # Class field picked up when the Portal provides it under another name.
    assert parse_summary_profile({"lopHoc": "CNTT24A1"}).class_name == "CNTT24A1"


def test_parse_tuition_rows_coerces_money_and_flags_status():
    rows = parse_tuition_rows(TUITION_ROWS + ["junk", None])

    assert len(rows) == 2
    paid, partial = rows
    assert paid.tuition == 4500000  # "4500000" string coerced
    assert paid.pay_level == 4500000  # 4500000.0 float coerced
    assert paid.deduction == 0  # None → 0
    assert paid.status == "paid"
    assert paid.semester == "Học kỳ 1 năm học 2025-2026"

    assert partial.debt == 3000000
    assert partial.status == "debt"  # debt beats the paid flag


def test_tuition_row_status_prefers_money_truth():
    rows = parse_tuition_rows(
        [
            {"noiDung": "a", "daDongHocPhi": True, "congNo": 1000},
            {"noiDung": "b", "daDongHocPhi": False, "congNo": 0},
            {"noiDung": "c", "daDongHocPhi": False, "congNo": 0, "soTienNop": 500},
        ]
    )

    assert [row.status for row in rows] == ["debt", "unpaid", "paid"]


def test_summarize_tuition_aggregates_only_the_given_rows():
    rows = parse_tuition_rows(TUITION_ROWS)
    totals = summarize_tuition(rows)

    assert totals.credits == 6
    assert totals.tuition == 9000000
    assert totals.pay_level == 9000000
    assert totals.paid_amount == 6000000
    assert totals.debt == 3000000
    assert totals.unpaid_rows == 1
    assert totals.settled is False
    assert summarize_tuition([]).settled is True


def test_parse_semesters_keeps_portal_order_and_valid_ids():
    semesters = parse_semesters(
        [
            {"id": 76, "tenDot": "Học kỳ hè"},
            {"id": "75", "tenDot": "Học kỳ 2"},
            {"idDot": 74, "tenDot": "Học kỳ 1"},
            {"id": "x", "tenDot": "broken"},
            {"id": 73, "tenDot": ""},
            "junk",
        ]
    )

    assert [(ref.id, ref.name) for ref in semesters] == [
        (76, "Học kỳ hè"),
        (75, "Học kỳ 2"),
        (74, "Học kỳ 1"),
    ]
    assert parse_semesters(None) == ()


def test_parse_other_fee_rows_reads_khoan_thu_khac():
    rows = parse_other_fee_rows(
        [
            {
                "namHoc": "2025-2026",
                "maKhoanThuKhac": "BHYT",
                "tenKhoanThuKhac": "Bảo hiểm y tế",
                "mucNop": 450000,
                "isBatBuoc": True,
                "ngayThu": "01/10/2025",
                "soTienNop": 450000,
                "congNo": 0,
            },
            "junk",
        ]
    )

    assert len(rows) == 1
    fee = rows[0]
    assert fee.name == "Bảo hiểm y tế"
    assert fee.mandatory is True
    assert fee.status == "paid"
    assert parse_other_fee_rows({}) == ()


def test_cache_roundtrip_preserves_snapshot(tmp_path):
    cache = StudentProfileCache(cache_dir=tmp_path, namespace="SV001")
    snapshot = ProfileSnapshot(
        profile=parse_summary_profile(PROFILE_PAYLOAD),
        credit="125000.00Đ (UTH)",
        semesters=parse_semesters([{"id": 76, "tenDot": "Học kỳ hè"}]),
        tuition_rows=parse_tuition_rows(TUITION_ROWS),
        other_fees=parse_other_fee_rows([]),
        fetched_at=datetime(2026, 11, 23, 8, 0, tzinfo=timezone.utc),
        account="SV001",
    )

    assert cache.save(snapshot) is True
    loaded = cache.load()

    assert loaded is not None
    assert loaded.account == "SV001"
    assert loaded.credit == "125000.00Đ (UTH)"
    assert loaded.profile.full_name == "Nguyễn Văn A"
    assert len(loaded.tuition_rows) == 2
    assert loaded.tuition_rows[1].status == "debt"
    assert loaded.semesters[0].id == 76
    assert loaded.fetched_at == snapshot.fetched_at


def test_cache_is_scoped_per_account_and_rejects_stale_schema(tmp_path):
    first = StudentProfileCache(cache_dir=tmp_path, namespace="sv001")
    second = StudentProfileCache(cache_dir=tmp_path, namespace="sv002")
    assert first.path != second.path

    first.save(ProfileSnapshot(account="sv001"))
    assert second.load() is None

    # Wrong schema version → ignored instead of crashing.
    first.path.write_text('{"version": 99}', encoding="utf-8")
    assert first.load() is None


def test_cache_survives_corrupt_file(tmp_path):
    cache = StudentProfileCache(cache_dir=tmp_path, namespace="sv001")
    cache.path.write_text("{not json", encoding="utf-8")
    assert cache.load() is None


class _FakePortalClient:
    """Records calls; raises when ``fail`` is set."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[str] = []

    def _maybe_fail(self, name: str) -> None:
        self.calls.append(name)
        if self.fail:
            raise OSError("network down")

    def fetch_summary_profile(self, username, password):
        self._maybe_fail("profile")
        return dict(PROFILE_PAYLOAD)

    def fetch_credit(self, username, password):
        self._maybe_fail("credit")
        return "125000.00Đ (UTH)"

    def fetch_semesters(self, username, password):
        self._maybe_fail("semesters")
        return [{"id": 76, "tenDot": "Học kỳ hè"}]

    def fetch_tuition(self, id_dot, username, password):
        self._maybe_fail(f"tuition:{id_dot}")
        return list(TUITION_ROWS)

    def fetch_tuition_other(self, username, password):
        self._maybe_fail("other")
        return []


def test_service_first_load_fetches_saves_and_flags_network_data(tmp_path):
    client = _FakePortalClient()
    service = StudentProfileService(
        client, StudentProfileCache(cache_dir=tmp_path, namespace="sv001")
    )

    result = service.load("sv001", "secret", force_refresh=True)

    assert result.from_cache is False
    assert result.snapshot.profile.student_id == "SV001"
    assert len(result.snapshot.tuition_rows) == 2
    # idDot=0 → all semesters in one request, filtered locally later.
    assert client.calls == [
        "profile",
        "credit",
        "semesters",
        "tuition:0",
        "other",
    ]
    # Persisted for the offline path.
    assert service.load_cached() is not None


def test_service_serves_cache_without_network_when_not_forced(tmp_path):
    client = _FakePortalClient()
    service = StudentProfileService(
        client, StudentProfileCache(cache_dir=tmp_path, namespace="sv001")
    )
    service.load("sv001", "secret", force_refresh=True)
    client.calls.clear()

    result = service.load("sv001", "secret")

    assert result.from_cache is True
    assert client.calls == []  # zero requests: instant, works offline


def test_service_falls_back_to_cache_when_refresh_fails(tmp_path):
    cache = StudentProfileCache(cache_dir=tmp_path, namespace="sv001")
    service = StudentProfileService(_FakePortalClient(), cache)
    service.load("sv001", "secret", force_refresh=True)

    offline = StudentProfileService(_FakePortalClient(fail=True), cache)
    result = offline.load("sv001", "secret", force_refresh=True)

    assert result.from_cache is True
    assert result.snapshot.profile.student_id == "SV001"


def test_service_raises_when_refresh_fails_without_any_cache(tmp_path):
    service = StudentProfileService(
        _FakePortalClient(fail=True),
        StudentProfileCache(cache_dir=tmp_path, namespace="sv001"),
    )

    with pytest.raises(OSError, match="network down"):
        service.load("sv001", "secret", force_refresh=True)


def test_service_without_credentials_uses_cache_or_raises(tmp_path):
    cache = StudentProfileCache(cache_dir=tmp_path, namespace="sv001")
    service = StudentProfileService(_FakePortalClient(), cache)

    with pytest.raises(ValueError, match="credentials"):
        service.load("", "", force_refresh=True)

    service.load("sv001", "secret", force_refresh=True)
    client = _FakePortalClient(fail=True)
    result = StudentProfileService(client, cache).load("", "")
    assert result.from_cache is True
    assert client.calls == []
