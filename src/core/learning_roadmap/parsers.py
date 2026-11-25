"""Tolerant parsers for Portal payloads used by the Learning Roadmap.

IMPORTANT: ``docs/api/portal.md`` documents these endpoints' *purpose* but not
their response schemas (only ``tiendo``, ``hocky`` and ``montheoky`` have
samples). The parsers therefore locate data structurally and via key aliases,
in the same spirit as ``SemesterNormalizer``, instead of assuming one layout.
Alias lists are the single place to adjust once real payloads are captured.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterator

from core.learning_roadmap.models import FrameworkCourse

CODE_KEYS = ("maHocPhan", "maMonHoc", "maMon", "maHP", "courseCode", "course_code")
NAME_KEYS = ("tenMonHoc", "tenHocPhan", "tenMon", "tenHP", "courseName", "course_name", "name")
CREDIT_KEYS = ("soTinChi", "stc", "tinChi", "soTC", "credits")
SEMESTER_KEYS = ("hocKy", "hocKyDuKien", "hocKyThu", "kyHoc", "hocKySo", "semester", "hk")
REQUIRED_KEYS = ("batBuoc", "isBatBuoc", "required", "bb")
OPTIONAL_KEYS = ("tuChon", "isTuChon", "optional")
CERT_LABEL_KEYS = ("tenChungChi", "loaiChungChi", "chungChi", "tenLoai", "name", "ten")
CERT_VALID_KEYS = ("isDat", "dat", "hopLe", "isValid", "daXacNhan", "daDuyet")

_MAX_DEPTH = 12


# ----------------------------------------------------------------- text utils
def fold(text: Any) -> str:
    """Lowercase, strip Vietnamese diacritics, collapse punctuation to spaces."""
    s = unicodedata.normalize("NFD", str(text or ""))
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = s.replace("đ", "d").replace("Đ", "d").lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def fold_key(text: Any) -> str:
    """``fold`` with no separators - for codes and dictionary keys."""
    return fold(text).replace(" ", "")


def base_code(code: str) -> str:
    """Strip a class-section suffix: ``0120121003.01`` -> ``0120121003``."""
    return re.split(r"[.\s/_-]", (code or "").strip(), maxsplit=1)[0]


# ------------------------------------------------------------ value coercion
def _first(node: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for k in keys:
        v = node.get(k)
        if v is not None and v != "":
            return v
    return None


def to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def to_int_prefix(value: Any) -> int | None:
    """``3`` / ``"3"`` / ``"Học kỳ 3"`` / ``"HK3"`` -> 3 (first digit run)."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    m = re.search(r"\d+", str(value))
    return int(m.group()) if m else None


def to_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        f = fold(value)
        if f in ("true", "yes", "co", "1", "x"):
            return True
        if f in ("false", "no", "khong", "0"):
            return False
    return None


# --------------------------------------------------------------- course walker
def _looks_like_course(node: dict[str, Any]) -> bool:
    has_name = isinstance(_first(node, NAME_KEYS), str)
    has_code = _first(node, CODE_KEYS) is not None
    has_credit = _first(node, CREDIT_KEYS) is not None
    return has_name and (has_code or has_credit)


def iter_course_dicts(
    node: Any, inherited_semester: int | None = None, _depth: int = 0
) -> Iterator[tuple[dict[str, Any], int | None]]:
    """Yield ``(course_dict, semester_no)`` from an arbitrarily nested payload."""
    if _depth > _MAX_DEPTH:
        return
    if isinstance(node, list):
        for item in node:
            yield from iter_course_dicts(item, inherited_semester, _depth + 1)
    elif isinstance(node, dict):
        sem = to_int_prefix(_first(node, SEMESTER_KEYS))
        sem = sem if sem is not None else inherited_semester
        if _looks_like_course(node):
            yield node, sem
        else:
            for value in node.values():
                if isinstance(value, (list, dict)):
                    yield from iter_course_dicts(value, sem, _depth + 1)


def parse_curriculum(body: Any) -> list[FrameworkCourse]:
    seen: set[str] = set()
    out: list[FrameworkCourse] = []
    for node, sem in iter_course_dicts(body):
        code = str(_first(node, CODE_KEYS) or "").strip()
        name = str(_first(node, NAME_KEYS) or "").strip()
        identity = fold_key(code) or fold_key(name)
        if not identity or identity in seen:
            continue
        seen.add(identity)
        required = to_bool(_first(node, REQUIRED_KEYS))
        if required is None:
            optional = to_bool(_first(node, OPTIONAL_KEYS))
            required = (not optional) if optional is not None else None
        out.append(
            FrameworkCourse(
                code=code,
                name=name or code,
                credits=to_float(_first(node, CREDIT_KEYS)) or 0.0,
                semester_no=sem,
                required=required,
            )
        )
    return out


@dataclass(frozen=True)
class CourseRef:
    code: str
    name: str
    credits: float = 0.0


def parse_course_refs(body: Any) -> list[CourseRef]:
    """Plain (code, name) references: exemptions, current-semester registrations."""
    refs: list[CourseRef] = []
    for node, _sem in iter_course_dicts(body):
        refs.append(
            CourseRef(
                code=str(_first(node, CODE_KEYS) or "").strip(),
                name=str(_first(node, NAME_KEYS) or "").strip(),
                credits=to_float(_first(node, CREDIT_KEYS)) or 0.0,
            )
        )
    return refs


# ---------------------------------------------------------------- certificates
@dataclass(frozen=True)
class Certificate:
    label: str
    folded: str   # all string values, folded - for keyword matching
    valid: bool = True


def _string_values(node: Any, _depth: int = 0) -> Iterator[str]:
    if _depth > 4:
        return
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _string_values(v, _depth + 1)
    elif isinstance(node, list):
        for v in node:
            yield from _string_values(v, _depth + 1)


def parse_certificates(body: Any) -> list[Certificate]:
    items: list[dict[str, Any]] = []
    if isinstance(body, list):
        items = [x for x in body if isinstance(x, dict)]
    elif isinstance(body, dict):
        lists = [v for v in body.values() if isinstance(v, list)]
        for lst in lists:
            items.extend(x for x in lst if isinstance(x, dict))
        if not items and any(isinstance(v, str) for v in body.values()):
            items = [body]
    certs: list[Certificate] = []
    for item in items:
        strings = [s for s in _string_values(item) if s.strip()]
        if not strings:
            continue
        label = _first(item, CERT_LABEL_KEYS)
        flag = to_bool(_first(item, CERT_VALID_KEYS))
        certs.append(
            Certificate(
                label=str(label if isinstance(label, str) else strings[0]).strip(),
                folded=fold(" ".join(strings)),
                valid=True if flag is None else flag,
            )
        )
    return certs


# -------------------------------------------------- graduation info (flat scan)
def _flatten(node: Any, _depth: int = 0) -> Iterator[tuple[str, Any]]:
    if _depth > 6:
        return
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, (dict, list)):
                yield from _flatten(v, _depth + 1)
            else:
                yield fold_key(k), v
    elif isinstance(node, list):
        for v in node:
            yield from _flatten(v, _depth + 1)


_NEGATIVE = ("chua dat", "khong dat", "chua", "thieu", "khong du", "no")
_POSITIVE = ("dat", "da dat", "hoan thanh", "da hoan thanh", "du dieu kien")


def verdict_of(value: Any) -> bool | None:
    """Interpret a Portal flag. Matching is exact/prefix on folded text, so
    "chưa đạt" can never be mistaken for "đạt"."""
    b = to_bool(value)
    if b is not None:
        return b
    if isinstance(value, str):
        f = fold(value)
        if any(f == n or f.startswith(n + " ") for n in _NEGATIVE):
            return False
        if any(f == p or f.startswith(p + " ") for p in _POSITIVE):
            return True
    return None


def find_flag(body: Any, key_tokens: tuple[str, ...]) -> bool | None:
    """First explicit verdict whose (folded) key contains any of ``key_tokens``."""
    for key, value in _flatten(body):
        if any(tok in key for tok in key_tokens):
            v = verdict_of(value)
            if v is not None:
                return v
    return None


def find_message(body: Any, key_tokens: tuple[str, ...], limit: int = 160) -> str:
    for key, value in _flatten(body):
        if isinstance(value, str) and value.strip() and any(t in key for t in key_tokens):
            return value.strip()[:limit]
    return ""


def parse_credit_progress(body: Any) -> tuple[float | None, float | None]:
    """``hoctap/tiendo`` -> (required=canDat, earned=hienTai). Documented shape."""
    if isinstance(body, dict):
        return to_float(body.get("canDat")), to_float(body.get("hienTai"))
    return None, None
