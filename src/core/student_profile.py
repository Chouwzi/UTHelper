"""Student profile & tuition models, Portal parsers and offline cache.

The screen "Hồ sơ & Học phí" renders three Portal sources:

* ``user/getSummaryProfile`` — student identity (họ tên, MSSV, khoa/ngành...);
* ``user/getCredit`` — the UTH account balance as a display string;
* ``hocphi?idDot=`` + ``hocphi/khoanthukhac`` — per-course tuition rows and
  other collections, with the field names used by the Portal's own
  ``/tuition`` table.

Everything is parsed defensively (optional fields may be missing) and cached
per account so the screen keeps working offline after the first successful
fetch.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, get_type_hints

from core.portal_client import PortalAuthenticationError
from core.safe_file_io import SafeFileIO

logger = logging.getLogger(__name__)


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _number(value: Any) -> int:
    """Coerce Portal money/credit fields (int, float, ``"4500000"``, None)."""
    if value is None or isinstance(value, bool):
        return 0
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return 0


@dataclass(frozen=True)
class StudentProfile:
    """Identity card fields; empty strings mean the Portal omitted them."""

    student_id: str = ""
    full_name: str = ""
    cohort: str = ""
    birth_date: str = ""
    hometown: str = ""
    phone: str = ""
    education_type: str = ""
    program: str = ""
    major: str = ""
    faculty: str = ""
    specialization: str = ""
    email: str = ""
    class_name: str = ""


@dataclass(frozen=True)
class TuitionRow:
    """One course row of ``hocphi?idDot=`` (Portal /tuition table)."""

    semester: str = ""
    course_code: str = ""
    subject: str = ""
    credits: int = 0
    tuition: int = 0  # mucPhiBanDau
    pay_level: int = 0  # mucNop
    registration_status: str = ""
    paid_date: str = ""
    paid_amount: int = 0  # soTienNop
    deduction: int = 0  # khauTru
    debt_offset: int = 0  # truNo
    debt: int = 0  # congNo
    paid: bool = False  # daDongHocPhi

    @property
    def status(self) -> str:
        """``debt`` > ``paid`` > ``unpaid`` — money truth wins over the flag."""
        if self.debt > 0:
            return "debt"
        if self.paid or self.paid_amount > 0:
            return "paid"
        return "unpaid"


@dataclass(frozen=True)
class OtherFeeRow:
    """One row of ``hocphi/khoanthukhac`` (bảo hiểm, ký túc xá, ...)."""

    year: str = ""
    code: str = ""
    name: str = ""
    amount: int = 0  # mucNop
    mandatory: bool = False  # isBatBuoc
    paid_date: str = ""  # ngayThu
    paid_amount: int = 0  # soTienNop
    debt: int = 0  # congNo

    @property
    def status(self) -> str:
        if self.debt > 0:
            return "debt"
        if self.paid_amount > 0:
            return "paid"
        return "unpaid"


@dataclass(frozen=True)
class SemesterRef:
    """Dropdown entry from ``hoctap/hocky``."""

    id: int
    name: str


@dataclass(frozen=True)
class TuitionTotals:
    """Aggregated money figures for the rows currently on screen."""

    credits: int = 0
    tuition: int = 0
    pay_level: int = 0
    paid_amount: int = 0
    debt: int = 0
    unpaid_rows: int = 0

    @property
    def settled(self) -> bool:
        """No remaining debt on the selected rows."""
        return self.debt <= 0


@dataclass(frozen=True)
class ProfileSnapshot:
    """Everything the screen renders, captured at one point in time."""

    profile: StudentProfile = field(default_factory=StudentProfile)
    credit: str = ""
    semesters: tuple[SemesterRef, ...] = ()
    tuition_rows: tuple[TuitionRow, ...] = ()
    other_fees: tuple[OtherFeeRow, ...] = ()
    fetched_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    account: str = ""
    # Portal avatar as a ``data:image/...;base64,...`` URI ("" when absent).
    avatar: str = ""


@dataclass(frozen=True)
class ProfileLoadResult:
    """Load outcome: fresh network data or the cached offline copy."""

    snapshot: ProfileSnapshot
    from_cache: bool


class ProfileLoadCancelled(RuntimeError):
    """A profile refresh was invalidated by logout or an account change."""


def parse_summary_profile(raw: Any) -> StudentProfile:
    """Normalize ``user/getSummaryProfile``; missing fields become empty."""
    if not isinstance(raw, dict):
        return StudentProfile()
    full_name = " ".join(
        part for part in (_text(raw.get("hoDem")), _text(raw.get("ten"))) if part
    )
    # The documented payload has no class field; accept it if the Portal adds
    # one under any of the names it uses elsewhere.
    class_name = ""
    for key in ("lopHoc", "tenLopHoc", "maLop", "lop", "className", "classCode"):
        class_name = _text(raw.get(key))
        if class_name:
            break
    return StudentProfile(
        student_id=_text(raw.get("maSinhVien")),
        full_name=full_name,
        cohort=_text(raw.get("khoaHoc")),
        birth_date=_text(raw.get("ngaySinh2") or raw.get("ngaySinh")),
        hometown=_text(raw.get("noiSinhTinh")),
        phone=_text(raw.get("soDienThoai")),
        education_type=_text(raw.get("heDaoTao")),
        program=_text(raw.get("loaiHinhDT")),
        major=_text(raw.get("nganh")),
        faculty=_text(raw.get("khoa")),
        specialization=_text(raw.get("chuyenNganh")),
        email=_text(raw.get("email")),
        class_name=class_name,
    )


def parse_semesters(items: Any) -> tuple[SemesterRef, ...]:
    """Normalize ``hoctap/hocky`` rows, keeping the Portal's own order."""
    if not isinstance(items, (list, tuple)):
        return ()
    refs: list[SemesterRef] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        raw_id = raw.get("id") if raw.get("id") is not None else raw.get("idDot")
        try:
            sem_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        name = _text(raw.get("tenDot") or raw.get("name"))
        if not name:
            continue
        refs.append(SemesterRef(id=sem_id, name=name))
    return tuple(refs)


def parse_tuition_rows(items: Any) -> tuple[TuitionRow, ...]:
    """Normalize ``hocphi?idDot=`` rows; non-dict entries are dropped."""
    if not isinstance(items, (list, tuple)):
        return ()
    rows: list[TuitionRow] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        rows.append(
            TuitionRow(
                semester=_text(raw.get("tenDot")),
                course_code=_text(raw.get("maLopHocPhan")),
                subject=_text(raw.get("noiDung") or raw.get("tenMonHoc")),
                credits=_number(raw.get("soTinChi")),
                tuition=_number(raw.get("mucPhiBanDau")),
                pay_level=_number(raw.get("mucNop")),
                registration_status=_text(raw.get("trangThaiDangKy")),
                paid_date=_text(raw.get("ngayNop")),
                paid_amount=_number(raw.get("soTienNop")),
                deduction=_number(raw.get("khauTru")),
                debt_offset=_number(raw.get("truNo")),
                debt=_number(raw.get("congNo")),
                paid=bool(raw.get("daDongHocPhi")),
            )
        )
    return tuple(rows)


def parse_other_fee_rows(items: Any) -> tuple[OtherFeeRow, ...]:
    """Normalize ``hocphi/khoanthukhac`` rows."""
    if not isinstance(items, (list, tuple)):
        return ()
    rows: list[OtherFeeRow] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        rows.append(
            OtherFeeRow(
                year=_text(raw.get("namHoc")),
                code=_text(raw.get("maKhoanThuKhac")),
                name=_text(raw.get("tenKhoanThuKhac")),
                amount=_number(raw.get("mucNop")),
                mandatory=bool(raw.get("isBatBuoc")),
                paid_date=_text(raw.get("ngayThu") or raw.get("ngayNop")),
                paid_amount=_number(raw.get("soTienNop")),
                debt=_number(raw.get("congNo")),
            )
        )
    return tuple(rows)


def summarize_tuition(rows: Any) -> TuitionTotals:
    """Aggregate the rows currently displayed (one semester or all)."""
    if not isinstance(rows, (list, tuple)):
        return TuitionTotals()
    credits = tuition = pay_level = paid_amount = debt = unpaid = 0
    for row in rows:
        if not isinstance(row, TuitionRow):
            continue
        credits += row.credits
        tuition += row.tuition
        pay_level += row.pay_level
        paid_amount += row.paid_amount
        debt += row.debt
        if row.status != "paid":
            unpaid += 1
    return TuitionTotals(
        credits=credits,
        tuition=tuition,
        pay_level=pay_level,
        paid_amount=paid_amount,
        debt=debt,
        unpaid_rows=unpaid,
    )


def _decode_dataclass(cls: type, raw: Any) -> Any | None:
    """Rebuild ``cls`` from its persisted dict, coercing fields back to type.

    The cache stores normalized dataclass keys (``student_id``), not the
    Portal's original ones (``maSinhVien``), so decoding must go through the
    dataclass contract instead of the Portal parsers.
    """
    if not isinstance(raw, dict):
        return None
    hints = get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in hints:
            continue
        target = hints[key]
        if target is bool:
            kwargs[key] = bool(value)
        elif target is int:
            kwargs[key] = _number(value)
        elif target is str:
            kwargs[key] = _text(value)
        else:
            kwargs[key] = value
    try:
        return cls(**kwargs)
    except (TypeError, ValueError):
        return None


def _decode_row_list(cls: type, raw: Any) -> tuple:
    """Rebuild a persisted list of dataclass rows, skipping corrupt entries."""
    if not isinstance(raw, list):
        return ()
    decoded = [_decode_dataclass(cls, item) for item in raw]
    return tuple(item for item in decoded if item is not None)


class StudentProfileCache:
    """Versioned per-account JSON cache for the profile & tuition screen."""

    SCHEMA_VERSION = 1

    def __init__(self, cache_dir: Path, namespace: str = "") -> None:
        digest = hashlib.sha256(
            namespace.strip().casefold().encode("utf-8")
        ).hexdigest()[:12]
        self._path = cache_dir / f"student_profile_{digest}.json"

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> ProfileSnapshot | None:
        payload = SafeFileIO.read_json_safe(self._path, dict)
        if not isinstance(payload, dict) or payload.get("version") != (
            self.SCHEMA_VERSION
        ):
            return None
        try:
            snapshot = ProfileSnapshot(
                profile=_decode_dataclass(
                    StudentProfile, payload.get("profile")
                )
                or StudentProfile(),
                credit=_text(payload.get("credit")),
                semesters=parse_semesters(payload.get("semesters")),
                tuition_rows=_decode_row_list(
                    TuitionRow, payload.get("tuition_rows")
                ),
                other_fees=_decode_row_list(
                    OtherFeeRow, payload.get("other_fees")
                ),
                fetched_at=datetime.fromisoformat(str(payload["fetched_at"])),
                account=_text(payload.get("account")),
                avatar=_text(payload.get("avatar")),
            )
        except (KeyError, TypeError, ValueError):
            logger.warning("Student profile cache is invalid")
            return None
        return snapshot

    def save(self, snapshot: ProfileSnapshot) -> bool:
        payload = {
            "version": self.SCHEMA_VERSION,
            "account": snapshot.account,
            "fetched_at": snapshot.fetched_at.isoformat(),
            "credit": snapshot.credit,
            "avatar": snapshot.avatar,
            "profile": vars(snapshot.profile).copy(),
            "semesters": [
                {"id": ref.id, "tenDot": ref.name} for ref in snapshot.semesters
            ],
            "tuition_rows": [
                vars(row).copy() for row in snapshot.tuition_rows
            ],
            "other_fees": [vars(row).copy() for row in snapshot.other_fees],
        }
        return bool(SafeFileIO.write_json_atomic(self._path, payload))

    def clear(self) -> None:
        try:
            if self._path.exists():
                self._path.unlink()
        except OSError:
            logger.warning("Không thể xóa student profile cache")


class StudentProfileService:
    """Fetch → parse → cache with an offline-first fallback.

    ``load(..., force_refresh=True)`` always hits the Portal; when that fails
    the last good snapshot is returned instead of raising, so the screen can
    keep showing cached data during an outage. Without any cache the fetch
    error propagates so the caller can render its error state.
    """

    def __init__(self, portal_client: Any, cache: StudentProfileCache) -> None:
        self._portal_client = portal_client
        self._cache = cache

    @property
    def cache(self) -> StudentProfileCache:
        return self._cache

    def load_cached(self) -> ProfileSnapshot | None:
        """Serve the persisted copy with no network at all (offline path)."""
        return self._cache.load()

    def load(
        self,
        username: str,
        password: str,
        *,
        force_refresh: bool = False,
        is_current: Callable[[], bool] | None = None,
    ) -> ProfileLoadResult:
        def ensure_current() -> None:
            if is_current is not None and not is_current():
                raise ProfileLoadCancelled("Student profile request was invalidated")

        if not username or not password:
            raise ValueError("Portal credentials are unavailable")
        cached = self._cache.load()
        if cached is not None and not force_refresh:
            return ProfileLoadResult(cached, from_cache=True)
        try:
            ensure_current()
            profile = parse_summary_profile(
                self._portal_client.fetch_summary_profile(username, password)
            )
            ensure_current()
            credit = self._portal_client.fetch_credit(username, password)
            ensure_current()
            semesters = parse_semesters(
                self._portal_client.fetch_semesters(username, password)
            )
            ensure_current()
            # idDot=0 asks for every semester; the dropdown then filters the
            # rows locally without another round trip.
            tuition_rows = parse_tuition_rows(
                self._portal_client.fetch_tuition(0, username, password)
            )
            ensure_current()
            other_fees = parse_other_fee_rows(
                self._portal_client.fetch_tuition_other(username, password)
            )
            ensure_current()
        except ProfileLoadCancelled:
            raise
        except Exception as exc:
            status = getattr(exc, "status_code", getattr(exc, "status", None))
            if isinstance(exc, PortalAuthenticationError) or status in (401, 403):
                self._cache.clear()
                raise
            if cached is not None:
                logger.warning(
                    "Student profile refresh failed; serving cached copy: %s", exc
                )
                return ProfileLoadResult(cached, from_cache=True)
            raise
        # The avatar is optional: a broken image endpoint must not sink the
        # whole refresh, so fall back to the cached photo (or none) instead.
        avatar = ""
        try:
            ensure_current()
            avatar = self._portal_client.fetch_profile_image(username, password)
            ensure_current()
            if not avatar and cached is not None:
                avatar = cached.avatar
        except ProfileLoadCancelled:
            raise
        except Exception as exc:
            status = getattr(exc, "status_code", getattr(exc, "status", None))
            if isinstance(exc, PortalAuthenticationError) or status in (401, 403):
                self._cache.clear()
                raise
            logger.warning("Student profile avatar refresh failed: %s", exc)
            if cached is not None:
                avatar = cached.avatar
        snapshot = ProfileSnapshot(
            profile=profile,
            credit=credit,
            semesters=semesters,
            tuition_rows=tuition_rows,
            other_fees=other_fees,
            fetched_at=datetime.now(timezone.utc),
            account=username,
            avatar=avatar,
        )
        ensure_current()
        self._cache.save(snapshot)
        return ProfileLoadResult(snapshot, from_cache=False)
