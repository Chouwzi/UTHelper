from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from diagnostics.models import AppPhase, DiagnosticFrame, DiagnosticReport
from diagnostics.spool import MAX_TOTAL_BYTES, DiagnosticSpool


NOW = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)


def _report(
    index: int,
    *,
    occurred_at: datetime | None = None,
    fingerprint: str | None = None,
    large: bool = False,
) -> DiagnosticReport:
    frame_text = "🛡" * 240 if large else "worker"
    frames = tuple(
        DiagnosticFrame(
            module=frame_text,
            function=frame_text,
            relative_path=frame_text,
            line=position + 1,
        )
        for position in range(40 if large else 1)
    )
    return DiagnosticReport(
        schema_version=1,
        event_id=UUID(int=index + 1),
        fingerprint=fingerprint or f"{index + 1:064x}",
        occurred_at=occurred_at or NOW + timedelta(seconds=index),
        app_version="2.2.0",
        release_channel="stable",
        install_type="msi",
        os_family="Windows",
        os_version="10",
        architecture="AMD64",
        python_version="3.12",
        flet_version="0.86.5",
        flutter_version="3",
        exception_type="RuntimeError",
        frames=frames,
        phase=AppPhase.GUI,
        window_state="tray",
        unclean_previous_exit=False,
    )


def _symlink(source: Path, destination: Path, *, directory: bool = False) -> None:
    try:
        os.symlink(source, destination, target_is_directory=directory)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable in this environment: {exc}")


def test_spool_accepts_only_validated_reports(tmp_path: Path) -> None:
    spool = DiagnosticSpool(tmp_path, clock=lambda: NOW)

    with pytest.raises(TypeError, match="DiagnosticReport"):
        spool.enqueue(_report(0).model_dump())  # type: ignore[arg-type]

    assert spool.pending() == ()


def test_spool_deduplicates_fingerprints_and_prunes_oldest_to_event_cap(
    tmp_path: Path,
) -> None:
    spool = DiagnosticSpool(tmp_path, clock=lambda: NOW + timedelta(minutes=1))
    first = _report(0)

    assert spool.enqueue(first).stored is True
    duplicate = first.model_copy(update={"event_id": UUID(int=999)})
    result = spool.enqueue(duplicate)
    assert result.stored is False
    assert result.deduplicated is True

    for index in range(1, 30):
        assert spool.enqueue(_report(index)).stored is True

    pending = spool.pending()
    assert len(pending) == 20
    assert [item.report.event_id for item in pending] == [
        UUID(int=index + 1) for index in range(10, 30)
    ]


def test_spool_prunes_reports_older_than_seven_days_but_keeps_boundary(
    tmp_path: Path,
) -> None:
    spool = DiagnosticSpool(tmp_path, clock=lambda: NOW)
    expired = _report(0, occurred_at=NOW - timedelta(days=7, microseconds=1))
    boundary = _report(1, occurred_at=NOW - timedelta(days=7))
    fresh = _report(2, occurred_at=NOW)

    assert spool.enqueue(expired).stored is False
    assert spool.enqueue(boundary).stored is True
    assert spool.enqueue(fresh).stored is True

    assert [item.report.event_id for item in spool.pending()] == [
        boundary.event_id,
        fresh.event_id,
    ]


def test_spool_prunes_oldest_until_total_payload_is_at_most_one_mib(
    tmp_path: Path,
) -> None:
    spool = DiagnosticSpool(tmp_path, clock=lambda: NOW + timedelta(minutes=1))

    for index in range(20):
        spool.enqueue(_report(index, large=True))

    pending = spool.pending()
    assert len(pending) < 20
    assert pending[-1].report.event_id == UUID(int=20)
    assert sum(item.path.stat().st_size for item in pending) <= MAX_TOTAL_BYTES


def test_pending_deletes_invalid_direct_json_and_orphan_owned_temp_only(
    tmp_path: Path,
) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    invalid = tmp_path / "bad.json"
    invalid.write_text('{"username":"secret"}', encoding="utf-8")
    orphan = tmp_path / ".orphan.tmp"
    orphan.write_text("partial-secret", encoding="utf-8")
    unrelated = tmp_path / "notes.txt"
    unrelated.write_text("keep", encoding="utf-8")
    nested_invalid = nested / "bad.json"
    nested_invalid.write_text("keep", encoding="utf-8")

    assert DiagnosticSpool(tmp_path, clock=lambda: NOW).pending() == ()
    assert not invalid.exists()
    assert not orphan.exists()
    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert nested_invalid.read_text(encoding="utf-8") == "keep"


def test_enqueue_cleans_temporary_file_when_atomic_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spool = DiagnosticSpool(tmp_path, clock=lambda: NOW)

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr("diagnostics.spool.os.replace", fail_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        spool.enqueue(_report(0))

    assert list(tmp_path.iterdir()) == []


def test_concurrent_spool_instances_serialize_dedupe_and_pruning(
    tmp_path: Path,
) -> None:
    spools = [DiagnosticSpool(tmp_path, clock=lambda: NOW + timedelta(minutes=2)) for _ in range(4)]
    reports = [
        _report(index, fingerprint="f" * 64 if index < 16 else None)
        for index in range(64)
    ]

    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(
            executor.map(
                lambda pair: spools[pair[0] % len(spools)].enqueue(pair[1]),
                enumerate(reports),
            )
        )

    assert sum(result.stored for result in results[:16]) == 1
    assert sum(result.deduplicated for result in results[:16]) == 15
    pending = spools[0].pending()
    assert len(pending) == 20
    assert len({item.report.event_id for item in pending}) == len(pending)
    assert all(item.path.is_file() for item in pending)


def test_acknowledge_removes_only_the_matching_validated_report(tmp_path: Path) -> None:
    spool = DiagnosticSpool(tmp_path, clock=lambda: NOW)
    first = _report(0)
    second = _report(1)
    spool.enqueue(first)
    spool.enqueue(second)
    unrelated = tmp_path / "keep.txt"
    unrelated.write_text("keep", encoding="utf-8")

    spool.acknowledge(first.event_id)

    assert [item.report.event_id for item in spool.pending()] == [second.event_id]
    assert unrelated.read_text(encoding="utf-8") == "keep"


def test_clear_deletes_only_direct_regular_json_and_owned_temp_files(
    tmp_path: Path,
) -> None:
    spool = DiagnosticSpool(tmp_path, clock=lambda: NOW)
    spool.enqueue(_report(0))
    invalid = tmp_path / "invalid.json"
    invalid.write_text("invalid", encoding="utf-8")
    temporary = tmp_path / ".interrupted.tmp"
    temporary.write_text("partial", encoding="utf-8")
    unrelated_tmp = tmp_path / "keep.tmp"
    unrelated_tmp.write_text("keep", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    nested_json = nested / "keep.json"
    nested_json.write_text("keep", encoding="utf-8")

    spool.clear()

    assert not invalid.exists()
    assert not temporary.exists()
    assert unrelated_tmp.read_text(encoding="utf-8") == "keep"
    assert nested_json.read_text(encoding="utf-8") == "keep"
    assert spool.pending() == ()


def test_pending_and_clear_never_follow_direct_entry_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "spool"
    root.mkdir()
    outside_json = tmp_path / "outside.json"
    outside_json.write_text('{"private":"keep"}', encoding="utf-8")
    outside_tmp = tmp_path / "outside.tmp"
    outside_tmp.write_text("keep", encoding="utf-8")
    json_link = root / "linked.json"
    temp_link = root / ".linked.tmp"
    _symlink(outside_json, json_link)
    _symlink(outside_tmp, temp_link)
    spool = DiagnosticSpool(root, clock=lambda: NOW)

    assert spool.pending() == ()
    spool.clear()

    assert json_link.is_symlink()
    assert temp_link.is_symlink()
    assert outside_json.read_text(encoding="utf-8") == '{"private":"keep"}'
    assert outside_tmp.read_text(encoding="utf-8") == "keep"


def test_spool_rejects_a_symlink_root(tmp_path: Path) -> None:
    real_root = tmp_path / "real"
    real_root.mkdir()
    linked_root = tmp_path / "linked"
    _symlink(real_root, linked_root, directory=True)

    with pytest.raises(ValueError, match="symlink"):
        DiagnosticSpool(linked_root, clock=lambda: NOW)


def test_spool_fails_closed_if_root_is_replaced_by_symlink(tmp_path: Path) -> None:
    root = tmp_path / "spool"
    outside = tmp_path / "outside"
    spool = DiagnosticSpool(root, clock=lambda: NOW)
    outside.mkdir()
    root.rmdir()
    _symlink(outside, root, directory=True)

    with pytest.raises(RuntimeError, match="spool root"):
        spool.enqueue(_report(0))
    assert list(outside.iterdir()) == []
